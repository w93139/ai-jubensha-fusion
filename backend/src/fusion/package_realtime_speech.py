"""Single-use authenticated stream tickets and one bounded Qwen ASR connection."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import secrets
import time
from urllib.parse import urlsplit

import aiohttp

from src.fusion.package_speech_input import (
    PackageSpeechInputService, SpeechInputError, SpeechInputLedger,
    SAMPLE_RATE, speech_vocabulary, validate_request_id, validate_scene,
)

STREAM_ENDPOINT = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
STREAM_MODEL = 'qwen-audio-3.0-asr-flash-streaming'
MAX_CHUNK_BYTES = 16384


def valid_origin(origin):
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme in ('https', 'http') and bool(parsed.hostname) and not parsed.username
            and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError): return False


class StreamTickets:
    def __init__(self, settings, now=time.time):
        self.ledger, self.now = SpeechInputLedger(settings, now), now

    def table(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS asr_stream_tickets (
            request_id TEXT PRIMARY KEY, ticket_hash TEXT NOT NULL, owner INTEGER NOT NULL,
            play TEXT NOT NULL, revision INTEGER NOT NULL, channel TEXT NOT NULL, call_id TEXT,
            origin TEXT NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)''')
        db.execute('DELETE FROM asr_stream_tickets WHERE expires<?', (self.now()-120,))

    def issue(self, request_id, owner, play, revision, channel, call_id, origin):
        validate_request_id(request_id)
        if not valid_origin(origin): raise SpeechInputError('SPEECH_INPUT_ORIGIN_INVALID', 403)
        token = secrets.token_urlsafe(32)
        with self.ledger.connection(create=True) as db:
            self.table(db)
            if (db.execute('SELECT 1 FROM asr_requests WHERE request_id=?', (request_id,)).fetchone()
                    or db.execute('SELECT 1 FROM asr_stream_tickets WHERE request_id=?', (request_id,)).fetchone()):
                raise SpeechInputError('SPEECH_INPUT_REQUEST_CONFLICT')
            if db.execute('SELECT 1 FROM asr_stream_tickets WHERE owner=? AND consumed=0 AND expires>?', (owner, self.now())).fetchone():
                raise SpeechInputError('SPEECH_INPUT_BUSY')
            db.execute('INSERT INTO asr_stream_tickets VALUES (?,?,?,?,?,?,?,?,?,0)',
                (request_id, sha256(token.encode()).hexdigest(), owner, play, revision, channel, call_id, origin, self.now()+30))
        return {'ticket': token, 'expires_in': 30, 'request_id': request_id}

    def consume(self, request_id, play, token, origin):
        validate_request_id(request_id)
        if not isinstance(token, str) or len(token) != 43 or not valid_origin(origin):
            raise SpeechInputError('SPEECH_INPUT_TICKET_INVALID', 403)
        with self.ledger.connection() as db:
            if db is None: raise SpeechInputError('SPEECH_INPUT_TICKET_INVALID', 403)
            self.table(db)
            row = db.execute('SELECT * FROM asr_stream_tickets WHERE request_id=?', (request_id,)).fetchone()
            if (not row or row['consumed'] or row['expires'] <= self.now() or row['play'] != play or row['origin'] != origin
                    or not secrets.compare_digest(row['ticket_hash'], sha256(token.encode()).hexdigest())):
                raise SpeechInputError('SPEECH_INPUT_TICKET_INVALID', 403)
            db.execute('UPDATE asr_stream_tickets SET consumed=1 WHERE request_id=?', (request_id,))
            return dict(row)


class Transcript:
    """Provider events replace a whole sentence, including intermediate revisions."""
    def __init__(self):
        self.sentences = {}
        self.usage_seconds = 0

    def update(self, payload):
        try:
            sentence = payload['output']['sentence']
            if sentence.get('heartbeat') is True: return False
            identifier, text = sentence['sentence_id'], sentence['text']
            final = sentence.get('sentence_end', False)
            if type(identifier) is not int or not 0 <= identifier <= 1000 or type(text) is not str or type(final) is not bool:
                raise ValueError
            if any(ord(c) < 32 and c not in '\n\t\r' for c in text): raise ValueError
            previous = self.sentences.get(identifier)
            if previous and previous[1] and previous != (text, final): raise ValueError
            updated = {**self.sentences, identifier: (text, final)}
            if len(updated) > 200 or sum(len(item[0]) for item in updated.values()) > 6000: raise ValueError
            duration = (payload.get('usage') or {}).get('duration')
            if duration is not None:
                if type(duration) not in (int, float) or not 0 <= duration <= 120: raise ValueError
                self.usage_seconds = max(self.usage_seconds, duration)
            self.sentences = updated
            return True
        except (ValueError, KeyError, TypeError, AttributeError):
            raise SpeechInputError('SPEECH_INPUT_RESPONSE_INVALID', 502) from None

    @property
    def text(self): return ''.join(value[0] for _, value in sorted(self.sentences.items())).strip()

    @property
    def confirmed(self): return ''.join(value[0] for _, value in sorted(self.sentences.items()) if value[1]).strip()


def start_event(request_id, names):
    parameters = {'format': 'pcm', 'sample_rate': SAMPLE_RATE, 'language_hints': ['zh'],
        'semantic_punctuation_enabled': False, 'max_sentence_silence': 1300, 'heartbeat': True}
    if names: parameters['vocabulary'] = {name: 2 for name in names}
    return {'header': {'action': 'run-task', 'task_id': request_id, 'streaming': 'duplex'}, 'payload': {
        'task_group': 'audio', 'task': 'asr', 'function': 'recognition', 'model': STREAM_MODEL,
        'parameters': parameters, 'input': {}}}


def finish_event(request_id):
    return {'header': {'action': 'finish-task', 'task_id': request_id, 'streaming': 'duplex'}, 'payload': {'input': {}}}


async def no_redirect(session, context, params):
    raise SpeechInputError('SPEECH_INPUT_PROVIDER_ERROR', 502)


async def upstream_event(socket, request_id, timeout):
    message = await asyncio.wait_for(socket.receive(), timeout)
    if message.type != aiohttp.WSMsgType.TEXT: raise SpeechInputError('SPEECH_INPUT_RESULT_UNKNOWN', 502)
    try:
        if len(message.data) > 262144: raise ValueError
        value = json.loads(message.data)
        if value['header']['task_id'] != request_id: raise ValueError
        event = value['header']['event']
        if event == 'task-failed': raise SpeechInputError('SPEECH_INPUT_PROVIDER_ERROR', 502)
        if event not in ('task-started', 'result-generated', 'task-finished'): raise ValueError
        return event, value.get('payload', {})
    except SpeechInputError: raise
    except (ValueError, KeyError, TypeError):
        raise SpeechInputError('SPEECH_INPUT_RESPONSE_INVALID', 502) from None


async def run_stream(websocket, binding, settings, get_view, client_factory=aiohttp.ClientSession, now=time.time):
    """Ticket has been atomically consumed; no reconnection or audio replay here."""
    request_id = binding['request_id']
    view = get_view()
    validate_scene(view, binding['revision'], binding['channel'], binding['call_id'])
    if settings.reason or settings.provider != 'qwen' or not settings.realtime:
        raise SpeechInputError(settings.reason or 'SPEECH_INPUT_DISABLED', 503)
    ledger = SpeechInputLedger(settings, now)
    reservation = {key: binding[key] for key in ('request_id', 'owner', 'play', 'revision', 'channel', 'call_id')}
    reservation.update(audio_sha=sha256(('realtime/1:'+request_id).encode()).hexdigest(), frames=SAMPLE_RATE*60)
    _, claimed = ledger.claim(reservation, pending_seconds=60+settings.timeout_seconds+15)
    if not claimed: raise SpeechInputError('SPEECH_INPUT_REQUEST_CONFLICT')
    transcript = Transcript()
    frames, stopped, cancelled = 0, False, False
    tasks = []
    state, error = 'UNKNOWN', 'SPEECH_INPUT_RESULT_UNKNOWN'
    def check_scene():
        validate_scene(get_view(), binding['revision'], binding['channel'], binding['call_id'])
    async def emit(value):
        check_scene()
        await asyncio.wait_for(websocket.send_json({'request_id': request_id, **value}), 5)
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(no_redirect)
    try:
        async with asyncio.timeout(60+settings.timeout_seconds):
            timeout = aiohttp.ClientTimeout(total=None, connect=10)
            async with client_factory(timeout=timeout, trust_env=False, trace_configs=[trace]) as client:
                async with client.ws_connect(STREAM_ENDPOINT, headers={'Authorization': 'Bearer '+settings.api_key},
                        max_msg_size=262144, heartbeat=10) as upstream:
                    await upstream.send_json(start_event(request_id, speech_vocabulary(view)))
                    event, _ = await upstream_event(upstream, request_id, 10)
                    if event != 'task-started': raise SpeechInputError('SPEECH_INPUT_RESPONSE_INVALID', 502)
                    await emit({'type': 'READY'})
                    async def send_audio():
                        nonlocal frames, stopped, cancelled
                        started = time.monotonic()
                        while True:
                            message = await asyncio.wait_for(websocket.receive(), 15)
                            if message['type'] == 'websocket.disconnect': raise SpeechInputError('SPEECH_INPUT_RESULT_UNKNOWN')
                            raw = message.get('bytes')
                            if raw is not None:
                                if not 0 < len(raw) <= MAX_CHUNK_BYTES or len(raw)%2 or frames+len(raw)//2 > SAMPLE_RATE*60:
                                    raise SpeechInputError('SPEECH_INPUT_AUDIO_INVALID', 422)
                                if time.monotonic()-started > 65: raise SpeechInputError('SPEECH_INPUT_AUDIO_INVALID', 422)
                                check_scene()
                                # Charge before send: a lost write acknowledgement may still be billed.
                                frames += len(raw)//2
                                await asyncio.wait_for(upstream.send_bytes(raw), 5)
                            else:
                                text = message.get('text')
                                if not isinstance(text, str) or len(text) > 128: raise SpeechInputError('SPEECH_INPUT_REQUEST_INVALID', 422)
                                command = json.loads(text)
                                if command == {'type': 'CANCEL'}:
                                    cancelled = True
                                    raise SpeechInputError('SPEECH_INPUT_CANCELLED')
                                if command != {'type': 'STOP'}: raise SpeechInputError('SPEECH_INPUT_REQUEST_INVALID', 422)
                                stopped = True
                                await upstream.send_json(finish_event(request_id))
                                return
                    async def read_results():
                        while True:
                            event, payload = await upstream_event(upstream, request_id, settings.timeout_seconds)
                            if event == 'task-finished':
                                if not stopped: raise SpeechInputError('SPEECH_INPUT_RESPONSE_INVALID', 502)
                                return
                            if event != 'result-generated': raise SpeechInputError('SPEECH_INPUT_RESPONSE_INVALID', 502)
                            if transcript.update(payload):
                                await emit({'type': 'TRANSCRIPT', 'text': transcript.text, 'confirmed_text': transcript.confirmed})
                    tasks = [asyncio.create_task(send_audio()), asyncio.create_task(read_results())]
                    await asyncio.gather(*tasks)
                    check_scene()
                    if any(text and not final for text, final in transcript.sentences.values()):
                        state, error = ('PARTIAL' if transcript.confirmed else 'UNKNOWN'), 'SPEECH_INPUT_RESPONSE_INVALID'
                    else:
                        state, error = ('OK', None) if transcript.text else ('EMPTY', 'SPEECH_INPUT_NO_SPEECH')
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as exc:
        error = exc.code if isinstance(exc, SpeechInputError) else 'SPEECH_INPUT_RESULT_UNKNOWN'
        state = 'PARTIAL' if transcript.confirmed else 'UNKNOWN'
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if cancelled: state, error = 'EXPIRED', 'SPEECH_INPUT_CANCELLED'
        try: check_scene()
        except Exception: state, error = 'EXPIRED', 'SPEECH_INPUT_SCENE_CHANGED'
        text = transcript.text if state == 'OK' else transcript.confirmed if state == 'PARTIAL' else None
        ledger.finish_stream(request_id, frames, state, text, error)
    row = ledger.get(request_id, binding['owner'], binding['play'])
    result = PackageSpeechInputService.result(row, get_view())
    try: await emit({'type': 'RESULT', **result})
    except Exception: pass
    return result
