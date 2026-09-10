"""Real WS route and bounded duplex speech contracts, always offline."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import aiohttp
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.routes import package_speech_input_routes as routes
from src.db.session import get_db_session
from src.fusion.package_speech_input import SpeechInputError, SpeechInputLedger, PackageSpeechInputService
from src.fusion.package_realtime_speech import StreamTickets, Transcript, run_stream, STREAM_ENDPOINT, STREAM_MODEL, upstream_event, no_redirect
from tests.fusion_security.test_package_speech_input import asr, speech_http
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play, start, action_body

ORIGIN = 'http://localhost:13032'


def configuration(asr):
    return replace(asr.settings, provider='qwen', realtime=True)


def binding(asr):
    return dict(request_id=str(uuid4()), owner=1, play=asr.view['play_id'], revision=3, channel='PUBLIC', call_id=None)


def ticket(asr, **changes):
    data = dict(**binding(asr), origin=ORIGIN); data.update(changes)
    tickets = StreamTickets(configuration(asr), lambda: asr.now[0])
    return tickets, data, tickets.issue(**data)


def sentence(text='完整句。', final=True, identifier=0, usage=None):
    return {'output': {'sentence': {'sentence_id': identifier, 'text': text, 'sentence_end': final}}, 'usage': usage}


class Upstream:
    def __init__(self, final=True, failure=False, empty=False):
        self.queue = asyncio.Queue(); self.calls = []; self.audio = []; self.final = final; self.failure = failure; self.empty = empty
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def event(self, event, payload=None):
        self.queue.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps({'header': {'task_id': self.id, 'event': event}, 'payload': payload or {}})))
    async def send_json(self, data):
        self.calls.append(data); self.id = data['header']['task_id']
        if data['header']['action'] == 'run-task': self.event('task-started')
        else:
            if not self.empty:
                self.event('result-generated', sentence('后半句', final=self.final, identifier=1))
            self.event('task-failed' if self.failure else 'task-finished')
    async def send_bytes(self, data):
        self.audio.append(data)
        if not self.empty:
            self.event('result-generated', sentence('整', False))
            self.event('result-generated', sentence('完整句。'))
    async def receive(self): return await self.queue.get()


class Client:
    def __init__(self, upstream): self.upstream = upstream; self.connections = []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def ws_connect(self, url, **kwargs): self.connections.append((url, kwargs)); return self.upstream


class Browser:
    def __init__(self, messages=None, callback=None):
        self.messages = list(messages if messages is not None else [{'type': 'websocket.receive', 'bytes': b'\1\0'*3200}, {'type': 'websocket.receive', 'text': '{"type":"STOP"}'}])
        self.sent = []; self.callback = callback
    async def receive(self):
        await asyncio.sleep(0)
        if self.messages: return self.messages.pop(0)
        await asyncio.Future()
    async def send_json(self, data):
        self.sent.append(data)
        if self.callback: self.callback(data)


def invoke(asr, upstream=None, browser=None, record=None, get_view=None):
    record = record or binding(asr); upstream = upstream or Upstream(); browser = browser or Browser(); client = Client(upstream)
    factory = Mock(return_value=client)
    output = asyncio.run(run_stream(browser, record, configuration(asr), get_view or (lambda: deepcopy(asr.view)), client_factory=factory, now=lambda: asr.now[0]))
    return output, browser, client, factory, record


def test_realtime_ticket_hash_binding_once_expiry_and_no_game_text(asr):
    tickets, data, response = ticket(asr)
    with tickets.ledger.connection() as db:
        row = dict(db.execute('SELECT * FROM asr_stream_tickets').fetchone())
        assert response['ticket'] not in str(row) and row['ticket_hash'] != response['ticket']
    for token, origin, play in [(response['ticket'], 'https://evil.invalid', data['play']), ('x'*43, ORIGIN, data['play']), (response['ticket'], ORIGIN, 'other')]:
        with pytest.raises(SpeechInputError, match='TICKET_INVALID'):
            tickets.consume(data['request_id'], play, token, origin)
    consumed = tickets.consume(data['request_id'], data['play'], response['ticket'], ORIGIN)
    assert consumed['owner'] == 1 and consumed['revision'] == 3
    with pytest.raises(SpeechInputError): tickets.consume(data['request_id'], data['play'], response['ticket'], ORIGIN)
    tickets, data, response = ticket(asr)
    asr.now[0] += 30
    with pytest.raises(SpeechInputError): tickets.consume(data['request_id'], data['play'], response['ticket'], ORIGIN)


@pytest.mark.parametrize('origin', [None, '', 'null', 'file://x', 'https://name:pass@host', 'https://host/path', 'https://host?x=1'])
def test_realtime_invalid_origin_cannot_mint(asr, origin):
    with pytest.raises(SpeechInputError, match='ORIGIN_INVALID'): ticket(asr, origin=origin)
    assert not asr.settings.state_dir.exists()


def test_realtime_ticket_busy_and_authenticated_owner_check(asr, speech_http):
    asr.service.settings = configuration(asr)
    client, game = speech_http
    path = f'/api/fusion/package-plays/{asr.view["play_id"]}/speech-stream/{uuid4()}/ticket?expected_revision=3&channel=PUBLIC'
    for token, status in [('', 401), ('other', 404), ('player', 200)]:
        response = client.post(path, headers={'Authorization': 'Bearer '+token, 'Origin': ORIGIN})
        assert response.status_code == status
    assert response.headers['cache-control'] == 'no-store'
    assert game.db.rollback.called
    response = client.post(path.replace(path.split('/')[6], str(uuid4())), headers={'Authorization': 'Bearer player', 'Origin': ORIGIN})
    assert response.status_code == 409


def test_realtime_real_ws_dependency_chain_uses_consumed_owner(asr):
    tickets, data, issued = ticket(asr)
    db = Mock(); fake_game = Mock(); fake_game.get.return_value = deepcopy(asr.view); fake_game.db = db
    app = FastAPI(); app.include_router(routes.router)
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[routes.speech_input_service] = lambda: PackageSpeechInputService(configuration(asr), now=lambda: asr.now[0])
    async def fake_run(ws, consumed, settings, get_view, **kwargs):
        assert consumed['owner'] == 1
        assert get_view() == asr.view
        await ws.send_json({'type':'READY'})
    with patch('src.api.routes.package_play_routes.PackagePlayService', return_value=fake_game), patch.object(routes, '_checkpoint', return_value=(3, 'event', 'binding')), patch.object(routes, 'run_stream', side_effect=fake_run) as runner, TestClient(app) as client:
        path = f'/api/fusion/package-plays/{data["play"]}/speech-stream/{data["request_id"]}'
        with client.websocket_connect(path, headers={'Origin': ORIGIN}) as ws:
            ws.send_json({'ticket': issued['ticket']}); assert ws.receive_json()['type'] == 'READY'
        fake_game.get.assert_called_once_with(data['play'], 1); db.rollback.assert_called_once()
        with client.websocket_connect(path, headers={'Origin': ORIGIN}) as ws:
            ws.send_json({'ticket': issued['ticket']}); assert ws.receive_json()['type'] == 'ERROR'
        assert runner.await_count == 1


def test_realtime_transcript_replacements_finality_heartbeat_and_usage():
    transcript = Transcript()
    transcript.update(sentence('我', False)); transcript.update(sentence('我想核对。', True, usage={'duration': 2}))
    transcript.update(sentence('下一句', False, 1, usage={'duration': 1}))
    assert transcript.text == '我想核对。下一句' and transcript.confirmed == '我想核对。' and transcript.usage_seconds == 2
    assert transcript.update({'output': {'sentence': {'heartbeat': True}}}) is False
    for bad in [sentence('改变定稿'), sentence('字'*6001, True, 2), sentence('bad\x00', False, 2), sentence('bad', False, True)]:
        with pytest.raises(SpeechInputError): transcript.update(bad)
        assert transcript.text == '我想核对。下一句'


def test_realtime_duplex_revisions_stop_tail_and_actual_quota(asr):
    output, browser, client, factory, data = invoke(asr)
    assert output['state'] == 'OK' and output['text'] == '完整句。后半句'
    assert [event['text'] for event in browser.sent if event['type'] == 'TRANSCRIPT'] == ['整', '完整句。', '完整句。后半句']
    assert browser.sent[0]['type'] == 'READY' and browser.sent[-1]['type'] == 'RESULT'
    assert len(client.connections) == 1 and client.connections[0][0] == STREAM_ENDPOINT
    assert factory.call_args.kwargs['trust_env'] is False
    assert client.upstream.calls[0]['payload']['model'] == STREAM_MODEL
    assert [event['header']['action'] for event in client.upstream.calls] == ['run-task', 'finish-task']
    row = SpeechInputLedger(configuration(asr), lambda: asr.now[0]).get(data['request_id'], 1, data['play'])
    assert row['frames'] == 3200 and row['pending_until'] == 1120
    with pytest.raises(SpeechInputError): invoke(asr, record=data)
    with pytest.raises(SpeechInputError): SpeechInputLedger(configuration(asr)).get(data['request_id'], 2, data['play'])


@pytest.mark.parametrize('final,failure,empty,state,text', [(False,False,False,'PARTIAL','完整句。'), (False,True,False,'PARTIAL','完整句。'), (True,False,True,'EMPTY',None)])
def test_realtime_incomplete_provider_failure_and_silence(asr, final, failure, empty, state, text):
    output, *_ = invoke(asr, upstream=Upstream(final, failure, empty))
    assert output['state'] == state and output['text'] == text


def test_realtime_failed_before_first_final_never_saves_interim(asr):
    upstream = Upstream(final=False)
    async def audio(raw): upstream.event('result-generated', sentence('未定稿', False))
    upstream.send_bytes = audio
    output, *_ = invoke(asr, upstream=upstream)
    assert output['state'] == 'UNKNOWN' and output['text'] is None


@pytest.mark.parametrize('message', [{'bytes': b'1'}, {'bytes': b'1'*16386}, {'text': '{"type":"CANCEL"}'}, {'type': 'websocket.disconnect'}, {'text': '{"type":"FAKE"}'}])
def test_realtime_bad_audio_cancel_disconnect_bounded_and_no_text(asr, message):
    output, browser, client, _, data = invoke(asr, browser=Browser([{'type':'websocket.receive', **message}]))
    assert output['state'] in ('EXPIRED','UNKNOWN') and output['text'] is None
    assert client.upstream.audio == []
    assert SpeechInputLedger(configuration(asr)).get(data['request_id'],1,data['play'])['frames'] == 3200


def test_realtime_phase_change_drops_text_and_releases_pending(asr):
    def change(data):
        if data['type'] == 'TRANSCRIPT': asr.view['revision'] += 1
    output, browser, client, _, data = invoke(asr, browser=Browser(callback=change))
    assert output['state'] == 'EXPIRED' and output['text'] is None
    row = SpeechInputLedger(configuration(asr)).get(data['request_id'],1,data['play'])
    assert row['state'] == 'EXPIRED' and row['text'] is None


def test_realtime_reserves_full_cap_rejects_parallel_and_does_not_dispatch(asr):
    cfg = replace(configuration(asr), daily_seconds=59)
    factory = Mock()
    with pytest.raises(SpeechInputError, match='DAILY_LIMIT'):
        asyncio.run(run_stream(Browser(), binding(asr), cfg, lambda: asr.view, client_factory=factory, now=lambda:asr.now[0]))
    factory.assert_not_called()


def test_realtime_60_second_pcm_limit_rejects_excess_before_provider(asr):
    messages = [{'type':'websocket.receive','bytes':b'\1\0'*8000} for _ in range(121)]
    output, _, client, _, data = invoke(asr, browser=Browser(messages), upstream=Upstream(empty=True))
    assert output['state'] == 'UNKNOWN' and len(client.upstream.audio) == 120
    assert SpeechInputLedger(configuration(asr)).get(data['request_id'],1,data['play'])['frames'] == 960000


def test_realtime_redirect_and_upstream_error_use_fixed_code():
    with pytest.raises(SpeechInputError, match='PROVIDER_ERROR'): asyncio.run(no_redirect(None,None,None))
    async def fail():
        upstream = Upstream(); upstream.id = str(uuid4()); upstream.event('task-failed', {'message':'SECRET_SENTINEL'})
        await upstream_event(upstream, upstream.id, 1)
    with pytest.raises(SpeechInputError, match='PROVIDER_ERROR'): asyncio.run(fail())


def test_realtime_lightweight_checkpoint_reuses_validated_view_but_checks_every_access(asr):
    service = Mock(); service.get.return_value = asr.view; row = SimpleNamespace(binding_hash='binding')
    service._row.return_value = row
    latest = SimpleNamespace(revision=3,event_hash='event')
    service.db.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = latest
    current = routes._stream_view(service, asr.view['play_id'], 1)
    assert current() == asr.view and current() == asr.view
    assert service.get.call_count == 1 and service._row.call_count == 3
    latest.revision = 4
    with pytest.raises(SpeechInputError, match='SCENE_CHANGED'): current()
    latest.revision = 3; latest.event_hash = 'changed'
    with pytest.raises(SpeechInputError, match='SCENE_CHANGED'): current()
    latest.event_hash = 'event'; row.binding_hash = 'changed'
    with pytest.raises(SpeechInputError, match='SCENE_CHANGED'): current()
    assert service.db.rollback.call_count == 7


def test_realtime_checkpoint_with_real_db_rejects_other_owner_and_new_game_events(play):
    from src.fusion.package_play import PackagePlayError
    _, initial = start(play)
    with patch.object(play.play, 'get', wraps=play.play.get) as full_read:
        current = routes._stream_view(play.play, initial['play_id'], 1)
        assert current()['revision'] == 0 and current()['revision'] == 0
        assert full_read.call_count == 1 and not play.db.in_transaction()
        with pytest.raises(PackagePlayError): routes._stream_view(play.play, initial['play_id'], 2)
        play.play.act(initial['play_id'], action_body(), 1); play.db.commit()
        with pytest.raises(SpeechInputError, match='SCENE_CHANGED'): current()
        assert not play.db.in_transaction() and play.sdk.chat_completion.await_count == 0
