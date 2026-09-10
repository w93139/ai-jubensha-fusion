"""One-shot WAV transcription, independent of game events and role-model usage."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import time
from uuid import UUID
from zoneinfo import ZoneInfo

import aiohttp


ENDPOINT = 'https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash'
RESOURCE_ID = 'volc.bigasr.auc_turbo'
QWEN_ENDPOINT = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
QWEN_MODEL = 'qwen-audio-3.0-asr-flash'
SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = 1_920_044
TEXT_TTL_SECONDS = 600
REPO = Path(__file__).resolve().parents[3]
DEFAULT_STATE_DIR = REPO.parent / 'private-data' / 'speech-input'
ENV_NAMES = ('ENABLE_DOUBAO_ASR', 'DOUBAO_ASR_API_KEY', 'DOUBAO_ASR_APP_ID',
    'DOUBAO_ASR_ACCESS_TOKEN', 'DOUBAO_ASR_STATE_DIR', 'DOUBAO_ASR_DAILY_SECONDS',
    'DOUBAO_ASR_PLAY_SECONDS', 'DOUBAO_ASR_TIMEOUT_SECONDS',
    'SPEECH_INPUT_PROVIDER', 'ENABLE_QWEN_ASR', 'QWEN_ASR_API_KEY', 'DASHSCOPE_API_KEY',
    'SPEECH_INPUT_STATE_DIR', 'SPEECH_INPUT_DAILY_SECONDS', 'SPEECH_INPUT_PLAY_SECONDS',
    'SPEECH_INPUT_TIMEOUT_SECONDS', 'ENABLE_QWEN_REALTIME_ASR')


class SpeechInputError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


@dataclass(frozen=True)
class SpeechInputSettings:
    enabled: bool = False
    api_key: str = field(default='', repr=False)
    app_id: str = field(default='', repr=False)
    access_token: str = field(default='', repr=False)
    state_dir: Path = DEFAULT_STATE_DIR
    daily_seconds: int = 3600
    play_seconds: int = 1800
    timeout_seconds: int = 45
    invalid: bool = False
    provider: str = 'doubao'
    vocabulary: tuple[str, ...] = field(default=(), repr=False)
    realtime: bool = False

    @classmethod
    def from_environment(cls):
        try:
            provider = os.getenv('SPEECH_INPUT_PROVIDER', 'doubao').strip().lower()
            if provider not in ('doubao', 'qwen'): raise ValueError
            enabled = os.getenv('ENABLE_QWEN_ASR' if provider == 'qwen' else 'ENABLE_DOUBAO_ASR', 'false').strip().lower()
            if enabled not in ('true', 'false'): raise ValueError
            realtime = os.getenv('ENABLE_QWEN_REALTIME_ASR', 'false').strip().lower()
            if realtime not in ('true', 'false'): raise ValueError
            def number(name, default, minimum, maximum):
                value = os.getenv(name, str(default)).strip()
                if not re.fullmatch(r'[0-9]+', value) or not minimum <= int(value) <= maximum: raise ValueError
                return int(value)
            def secret(name):
                value = os.getenv(name, '').strip()
                if len(value) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in value): raise ValueError
                if value.upper().startswith(('CHANGE_ME', 'YOUR_')): return ''
                return value
            def limit(suffix, default, minimum, maximum):
                name = 'SPEECH_INPUT_' + suffix
                return number(name if name in os.environ else 'DOUBAO_ASR_' + suffix, default, minimum, maximum)
            return cls(enabled=enabled == 'true', provider=provider, realtime=realtime == 'true',
                api_key=(secret('QWEN_ASR_API_KEY') or secret('DASHSCOPE_API_KEY')) if provider == 'qwen' else secret('DOUBAO_ASR_API_KEY'),
                app_id=secret('DOUBAO_ASR_APP_ID') if provider == 'doubao' else '',
                access_token=secret('DOUBAO_ASR_ACCESS_TOKEN') if provider == 'doubao' else '',
                state_dir=Path(os.getenv('SPEECH_INPUT_STATE_DIR', os.getenv('DOUBAO_ASR_STATE_DIR', str(DEFAULT_STATE_DIR)))),
                daily_seconds=limit('DAILY_SECONDS', 3600, 1, 86400),
                play_seconds=limit('PLAY_SECONDS', 1800, 1, 86400),
                timeout_seconds=limit('TIMEOUT_SECONDS', 45, 5, 120))
        except (ValueError, TypeError, OverflowError):
            return cls(invalid=True)

    @property
    def reason(self):
        if self.invalid or self.provider not in ('doubao', 'qwen'): return 'SPEECH_INPUT_CONFIGURATION_INVALID'
        if not self.enabled: return 'SPEECH_INPUT_DISABLED'
        if not self.api_key and not (self.provider == 'doubao' and self.app_id and self.access_token): return 'SPEECH_INPUT_CREDENTIALS_MISSING'
        return None


def speech_vocabulary(view: dict) -> tuple[str, ...]:
    """Only roster names already exposed to this player; never mine private text."""
    names = []
    for character in view.get('characters', [])[:32]:
        name = character.get('name') if isinstance(character, dict) else None
        if isinstance(name, str) and re.fullmatch(r'[\w\u3400-\u9fff· ]{1,32}', name) and name.strip():
            if name.strip() not in names: names.append(name.strip())
    return tuple(names)


def validate_request_id(value: str) -> str:
    try:
        if str(UUID(value)) != value: raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise SpeechInputError('SPEECH_INPUT_REQUEST_INVALID', 422) from None
    return value


def wav_frames(data: bytes) -> int:
    """Accept exactly one canonical PCM16 mono 16kHz WAV, including sizes."""
    if type(data) is not bytes or not 44 <= len(data) <= MAX_AUDIO_BYTES:
        raise SpeechInputError('SPEECH_INPUT_AUDIO_INVALID', 422)
    try:
        header = struct.unpack('<4sI4s4sIHHIIHH4sI', data[:44])
        expected = (b'RIFF', len(data)-8, b'WAVE', b'fmt ', 16, 1, 1, SAMPLE_RATE,
            SAMPLE_RATE*2, 2, 16, b'data', len(data)-44)
        if header != expected or (len(data)-44) % 2: raise ValueError
        frames = (len(data)-44)//2
        if not SAMPLE_RATE//5 <= frames <= SAMPLE_RATE*60: raise ValueError
        return frames
    except (ValueError, struct.error):
        raise SpeechInputError('SPEECH_INPUT_AUDIO_INVALID', 422) from None


def validate_scene(view: dict, revision: int, channel: str, call_id: str | None):
    if type(revision) is not int or revision < 0 or channel not in ('PUBLIC', 'QUESTION', 'PRIVATE'):
        raise SpeechInputError('SPEECH_INPUT_REQUEST_INVALID', 422)
    if view['revision'] != revision:
        raise SpeechInputError('SPEECH_INPUT_SCENE_CHANGED')
    if view['settled'] or view.get('full_game', {}).get('phase_kind') == 'FINALE':
        raise SpeechInputError('SPEECH_INPUT_SCENE_CLOSED')
    if channel == 'PRIVATE':
        call = view.get('full_game', {}).get('call')
        if (not call_id or not call or call['id'] != call_id
                or view['selected_character_id'] not in call['character_ids']):
            raise SpeechInputError('SPEECH_INPUT_SCENE_CHANGED')
    elif call_id is not None:
        raise SpeechInputError('SPEECH_INPUT_REQUEST_INVALID', 422)


class SpeechInputLedger:
    """Audio duration is charged once on claim, including failures/unknowns.

    Quotas use exact PCM frames; daily boundaries are Asia/Shanghai. No audio,
    provider credentials, private materials or provider error bodies are saved.
    """
    def __init__(self, settings: SpeechInputSettings, now=time.time):
        self.settings, self.now = settings, now
        try:
            root = settings.state_dir
            if not root.is_absolute() or len(root.parts) < 3 or any(p.is_symlink() for p in (root, *root.parents)):
                raise ValueError
            self.root = root.resolve()
            if self.root == REPO or self.root.is_relative_to(REPO): raise ValueError
            if self.root.exists() and (not self.root.is_dir() or self.root.stat().st_mode & 0o077): raise ValueError
            self.path = self.root / 'receipts.sqlite'
            if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()): raise ValueError
        except (ValueError, OSError, RuntimeError):
            raise SpeechInputError('SPEECH_INPUT_STORAGE_UNAVAILABLE', 503) from None

    @contextmanager
    def connection(self, create=False):
        if not create and not self.path.exists():
            yield None; return
        connection = None
        try:
            if self.root.exists() and (not self.root.is_dir() or self.root.stat().st_mode & 0o077): raise OSError
            if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()): raise OSError
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            existing = self.path.exists()
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            if not existing: self.path.chmod(0o600)
            connection.execute('PRAGMA secure_delete=ON')
            connection.execute('BEGIN IMMEDIATE')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1) or (version == 0 and connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchone()): raise sqlite3.DatabaseError
            connection.execute('''CREATE TABLE IF NOT EXISTS asr_requests (
                request_id TEXT PRIMARY KEY, owner INTEGER NOT NULL, play TEXT NOT NULL,
                revision INTEGER NOT NULL, channel TEXT NOT NULL, call_id TEXT,
                audio_sha TEXT NOT NULL, frames INTEGER NOT NULL CHECK(frames BETWEEN 3200 AND 960000),
                day TEXT NOT NULL, created REAL NOT NULL, pending_until REAL NOT NULL,
                state TEXT NOT NULL, text TEXT, error_code TEXT, text_until REAL NOT NULL DEFAULT 0)''')
            connection.execute('PRAGMA user_version=1')
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error):
            if connection is not None: connection.rollback()
            raise SpeechInputError('SPEECH_INPUT_STORAGE_UNAVAILABLE', 503) from None
        finally:
            if connection is not None: connection.close()

    def _clean(self, db):
        now = self.now()
        db.execute("UPDATE asr_requests SET state='UNKNOWN', error_code='SPEECH_INPUT_RESULT_UNKNOWN' WHERE state='PENDING' AND pending_until<=?", (now,))
        db.execute("UPDATE asr_requests SET state='EXPIRED', text=NULL, error_code='SPEECH_INPUT_TEXT_EXPIRED' WHERE text IS NOT NULL AND text_until<=?", (now,))

    def cleanup(self):
        with self.connection() as db:
            if db is not None: self._clean(db)

    def get(self, request_id, owner, play):
        with self.connection() as db:
            if db is None: raise SpeechInputError('SPEECH_INPUT_RECEIPT_NOT_FOUND', 404)
            self._clean(db)
            row = db.execute('SELECT * FROM asr_requests WHERE request_id=? AND owner=? AND play=?', (request_id, owner, play)).fetchone()
            if row is None: raise SpeechInputError('SPEECH_INPUT_RECEIPT_NOT_FOUND', 404)
            return dict(row)

    def claim(self, binding, pending_seconds=None):
        with self.connection(create=True) as db:
            self._clean(db)
            row = db.execute('SELECT * FROM asr_requests WHERE request_id=?', (binding['request_id'],)).fetchone()
            if row is not None:
                if any(row[key] != value for key, value in binding.items()):
                    raise SpeechInputError('SPEECH_INPUT_REQUEST_CONFLICT')
                return dict(row), False
            if db.execute("SELECT 1 FROM asr_requests WHERE owner=? AND state='PENDING'", (binding['owner'],)).fetchone():
                raise SpeechInputError('SPEECH_INPUT_BUSY')
            now = self.now(); day = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date().isoformat()
            daily = db.execute('SELECT COALESCE(SUM(frames),0) FROM asr_requests WHERE day=?', (day,)).fetchone()[0]
            used = db.execute('SELECT COALESCE(SUM(frames),0) FROM asr_requests WHERE play=?', (binding['play'],)).fetchone()[0]
            if daily + binding['frames'] > self.settings.daily_seconds*SAMPLE_RATE:
                raise SpeechInputError('SPEECH_INPUT_DAILY_LIMIT', 429)
            if used + binding['frames'] > self.settings.play_seconds*SAMPLE_RATE:
                raise SpeechInputError('SPEECH_INPUT_PLAY_LIMIT', 429)
            value = {**binding, 'day': day, 'created': now, 'pending_until': now+(pending_seconds or self.settings.timeout_seconds+15),
                'state': 'PENDING', 'text': None, 'error_code': None, 'text_until': 0}
            db.execute('INSERT INTO asr_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(value[key] for key in (
                'request_id','owner','play','revision','channel','call_id','audio_sha','frames','day','created',
                'pending_until','state','text','error_code','text_until')))
            return value, True

    def finish(self, request_id, state, text=None, error_code=None):
        with self.connection() as db:
            if db is None: raise SpeechInputError('SPEECH_INPUT_STORAGE_UNAVAILABLE', 503)
            self._clean(db)
            db.execute("UPDATE asr_requests SET state=?, text=?, error_code=?, text_until=? WHERE request_id=? AND state='PENDING'",
                (state, text, error_code, self.now()+TEXT_TTL_SECONDS if text else 0, request_id))

    def finish_stream(self, request_id, frames, state, text=None, error_code=None):
        """Release unused reservation only when this worker has stopped sending audio."""
        if type(frames) is not int or not 0 <= frames <= SAMPLE_RATE*60: raise ValueError('Invalid stream frames')
        with self.connection() as db:
            if db is None: raise SpeechInputError('SPEECH_INPUT_STORAGE_UNAVAILABLE', 503)
            self._clean(db)
            db.execute("UPDATE asr_requests SET frames=?,state=?,text=?,error_code=?,text_until=? WHERE request_id=? AND state='PENDING'",
                (max(SAMPLE_RATE//5, frames), state, text, error_code, self.now()+TEXT_TTL_SECONDS if text else 0, request_id))


async def request_transcription(settings, request_id, uid, audio):
    """Fixed TLS-verified endpoint; exactly one POST, no redirects or retries."""
    import base64
    if settings.reason: raise SpeechInputError(settings.reason, 503)
    encoded = base64.b64encode(audio).decode('ascii')
    if settings.provider == 'qwen':
        endpoint = QWEN_ENDPOINT
        headers = {'Authorization': 'Bearer ' + settings.api_key, 'X-DashScope-SSE': 'disable'}
        parameters = {'format': 'wav', 'sample_rate': '16000', 'language_hints': ['zh']}
        if settings.vocabulary: parameters['vocabulary'] = {name: 2 for name in settings.vocabulary}
        payload = {'model': QWEN_MODEL, 'input': {'messages': [{'role': 'user', 'content': [
            {'type': 'input_audio', 'input_audio': {'data': 'data:audio/wav;base64,' + encoded}}]}]},
            'parameters': parameters}
    else:
        endpoint = ENDPOINT
        headers = {'X-Api-Resource-Id': RESOURCE_ID, 'X-Api-Request-Id': request_id, 'X-Api-Sequence': '-1'}
        if settings.api_key: headers['X-Api-Key'] = settings.api_key
        else: headers.update({'X-Api-App-Key': settings.app_id, 'X-Api-Access-Key': settings.access_token})
        payload = {'user': {'uid': uid}, 'audio': {'data': encoded},
            'request': {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True, 'enable_ddc': False}}
    timeout = aiohttp.ClientTimeout(total=settings.timeout_seconds, connect=min(10, settings.timeout_seconds))
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
        async with client.post(endpoint, headers=headers, json=payload, allow_redirects=False) as response:
            if response.status != 200: return 'FAILED', None, 'SPEECH_INPUT_PROVIDER_ERROR'
            if settings.provider == 'doubao':
                status = response.headers.get('X-Api-Status-Code')
                if status == '20000003': return 'EMPTY', None, 'SPEECH_INPUT_NO_SPEECH'
                if status != '20000000': return 'FAILED', None, 'SPEECH_INPUT_PROVIDER_ERROR'
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > 262144: return 'FAILED', None, 'SPEECH_INPUT_RESPONSE_INVALID'
            try:
                value = json.loads(raw)
                text = value['output']['text'] if settings.provider == 'qwen' else value['result']['text']
                if type(text) is not str or any(ord(c)<32 and c not in '\n\t\r' for c in text): raise ValueError
                text = text.strip()
                if len(text) > 6000: return 'FAILED', None, 'SPEECH_INPUT_TEXT_TOO_LONG'
                if not text: return 'EMPTY', None, 'SPEECH_INPUT_NO_SPEECH'
                return 'OK', text, None
            except (ValueError, KeyError, TypeError):
                return 'FAILED', None, 'SPEECH_INPUT_RESPONSE_INVALID'


class PackageSpeechInputService:
    def __init__(self, settings=None, transport=request_transcription, now=time.time):
        self.settings = settings or SpeechInputSettings.from_environment()
        self.transport, self.now = transport, now

    def availability(self, view):
        reason = self.settings.reason
        if reason is None:
            try: SpeechInputLedger(self.settings, self.now)
            except SpeechInputError: reason = 'SPEECH_INPUT_STORAGE_UNAVAILABLE'
        if view['settled'] or view.get('full_game', {}).get('phase_kind') == 'FINALE': reason = 'SPEECH_INPUT_SCENE_CLOSED'
        return {'available': reason is None, 'reason': reason, 'min_seconds': 0.2, 'max_seconds': 60, 'max_characters': 1000,
            'provider_name': {'qwen': '千问', 'doubao': '豆包'}.get(self.settings.provider),
            'realtime': self.settings.realtime and self.settings.provider == 'qwen'}

    @staticmethod
    def result(row, view):
        state, text, error = row['state'], row['text'], row['error_code']
        try: validate_scene(view, row['revision'], row['channel'], row['call_id'])
        except SpeechInputError: state, text, error = 'EXPIRED', None, 'SPEECH_INPUT_SCENE_CHANGED'
        return {'request_id': row['request_id'], 'state': state, 'text': text if state in ('OK', 'PARTIAL') else None, 'error_code': error}

    def receipt(self, request_id, owner, play, view):
        validate_request_id(request_id)
        row = SpeechInputLedger(self.settings, self.now).get(request_id, owner, play)
        return self.result(row, view)

    async def transcribe(self, request_id, owner, play, revision, channel, call_id, audio, get_view):
        validate_request_id(request_id)
        view = get_view()
        validate_scene(view, revision, channel, call_id)
        frames = wav_frames(audio)
        ledger = SpeechInputLedger(self.settings, self.now)
        binding = dict(request_id=request_id, owner=owner, play=play, revision=revision, channel=channel,
            call_id=call_id, audio_sha=sha256(audio).hexdigest(), frames=frames)
        # Read a prior receipt before availability: changing configuration never
        # turns an already claimed UUID into another provider dispatch.
        try: old = ledger.get(request_id, owner, play)
        except SpeechInputError as exc:
            if exc.status_code != 404: raise
        else:
            if any(old[k] != v for k, v in binding.items()): raise SpeechInputError('SPEECH_INPUT_REQUEST_CONFLICT')
            return self.result(old, view)
        if self.settings.reason: raise SpeechInputError(self.settings.reason, 503)
        row, claimed = ledger.claim(binding)
        if not claimed: return self.result(row, view)
        try:
            uid = 'speech-' + sha256(f'{owner}:{play}'.encode()).hexdigest()[:24]
            settings = replace(self.settings, vocabulary=speech_vocabulary(view)) if self.settings.provider == 'qwen' else self.settings
            state, text, error = await self.transport(settings, request_id, uid, audio)
            try: validate_scene(get_view(), revision, channel, call_id)
            except (SpeechInputError, ValueError): state, text, error = 'EXPIRED', None, 'SPEECH_INPUT_SCENE_CHANGED'
            ledger.finish(request_id, state, text, error)
        except asyncio.CancelledError:
            ledger.finish(request_id, 'UNKNOWN', error_code='SPEECH_INPUT_RESULT_UNKNOWN')
            raise
        except Exception:
            ledger.finish(request_id, 'UNKNOWN', error_code='SPEECH_INPUT_RESULT_UNKNOWN')
        return self.result(ledger.get(request_id, owner, play), get_view())


async def cleanup_speech_input_receipts():
    """Application lifespan task; also mount in isolated validation servers."""
    while True:
        try: SpeechInputLedger(SpeechInputSettings.from_environment()).cleanup()
        except SpeechInputError: pass
        await asyncio.sleep(60)
