"""No-network ASR contracts, real isolated receipts and unchanged game events."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.core.auth_middleware import UnifiedAuthMiddleware
from src.fusion.package_play import PackagePlayError
from src.fusion.package_speech_input import (
    ENDPOINT, RESOURCE_ID, ENV_NAMES, MAX_AUDIO_BYTES, SpeechInputError, SpeechInputSettings,
    SpeechInputLedger, PackageSpeechInputService, wav_frames, request_transcription,
)
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play, start, events
from tests.fusion_security.test_package_full_play import full_package


def wav(frames=16000, sample=1):
    data = struct.pack('<h', sample)*frames
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', len(data)+36, b'WAVE', b'fmt ',
        16, 1, 1, 16000, 32000, 2, 16, b'data', len(data)) + data


def scene():
    return {'play_id': 'play-'+'a'*32, 'revision': 3, 'selected_character_id': 'a',
        'settled': False, 'full_game': {'phase_kind': 'INVESTIGATION', 'call': None}}


@pytest.fixture
def asr(tmp_path):
    now = [1000.0]
    settings = SpeechInputSettings(enabled=True, api_key='fixture-key', state_dir=tmp_path.resolve()/'asr')
    sdk = AsyncMock(return_value=('OK', '我想核对那封信。', None))
    service = PackageSpeechInputService(settings, sdk, lambda: now[0])
    return SimpleNamespace(settings=settings, sdk=sdk, service=service, now=now, view=scene())


def invoke(asr, key=None, **changes):
    args = dict(request_id=key or str(uuid4()), owner=1, play=asr.view['play_id'], revision=asr.view['revision'],
        channel='PUBLIC', call_id=None, audio=wav(), get_view=lambda: deepcopy(asr.view))
    args.update(changes)
    return asr.service.transcribe(**args)


@pytest.mark.parametrize('frames', [3200, 960000])
def test_canonical_wav_accepts_exact_duration_boundaries(frames):
    assert wav_frames(wav(frames)) == frames


@pytest.mark.parametrize('bad', [b'', wav(3199), wav(960001), wav()+b'\x00', wav()[:-2],
    b'OggS'+wav()[4:], wav()[:20]+b'\x03\x00'+wav()[22:], wav()[:22]+b'\x02\x00'+wav()[24:],
    wav()[:24]+struct.pack('<I',48000)+wav()[28:], wav()[:34]+b'\x08\x00'+wav()[36:]])
def test_bad_audio_rejects_before_creating_a_receipt(asr, bad):
    with pytest.raises(SpeechInputError, match='AUDIO_INVALID'):
        asyncio.run(invoke(asr, audio=bad))
    assert not asr.settings.state_dir.exists() and asr.sdk.await_count == 0


def test_configuration_is_separate_and_does_not_leak_credentials(monkeypatch, tmp_path):
    for key in ENV_NAMES: monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('ARK_API_KEY', 'only-text-model')
    assert SpeechInputSettings.from_environment().reason == 'SPEECH_INPUT_DISABLED'
    monkeypatch.setenv('ENABLE_DOUBAO_ASR', 'true')
    assert SpeechInputSettings.from_environment().reason == 'SPEECH_INPUT_CREDENTIALS_MISSING'
    monkeypatch.setenv('DOUBAO_ASR_APP_ID', 'fixture-app')
    assert SpeechInputSettings.from_environment().reason == 'SPEECH_INPUT_CREDENTIALS_MISSING'
    monkeypatch.setenv('DOUBAO_ASR_ACCESS_TOKEN', 'fixture-token')
    assert SpeechInputSettings.from_environment().reason is None
    monkeypatch.setenv('DOUBAO_ASR_API_KEY', 'fixture-api')
    cfg = SpeechInputSettings.from_environment()
    assert cfg.reason is None and 'fixture-api' not in repr(cfg) and 'fixture-token' not in repr(cfg)
    for key, value in [('DOUBAO_ASR_DAILY_SECONDS','nan'), ('DOUBAO_ASR_PLAY_SECONDS','-1'),
            ('DOUBAO_ASR_TIMEOUT_SECONDS','121'), ('ENABLE_DOUBAO_ASR','yes')]:
        with monkeypatch.context() as env:
            env.setenv(key, value)
            assert SpeechInputSettings.from_environment().reason == 'SPEECH_INPUT_CONFIGURATION_INVALID'


class FakeResponse:
    def __init__(self, status=200, code='20000000', body=None):
        self.status, self.headers = status, {'X-Api-Status-Code': code}
        self.raw = json.dumps({'result': {'text': '请核对证词。'}} if body is None else body).encode()
        self.content = self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def iter_chunked(self, size): yield self.raw


class FakeClient:
    def __init__(self, response): self.response, self.calls = response, []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs)); return self.response


@pytest.mark.parametrize('legacy', [False, True])
def test_provider_posts_exactly_once_with_correct_auth_and_no_redirects(asr, legacy):
    client = FakeClient(FakeResponse()); key = str(uuid4())
    settings = replace(asr.settings, api_key='' if legacy else 'fixture-api', app_id='fixture-app', access_token='fixture-token')
    with patch('src.fusion.package_speech_input.aiohttp.ClientSession', return_value=client) as factory:
        assert asyncio.run(request_transcription(settings, key, 'anonymous-fixture', wav())) == ('OK','请核对证词。',None)
    assert len(client.calls) == 1 and factory.call_args.kwargs['trust_env'] is False
    url, params = client.calls[0]; assert url == ENDPOINT and params['allow_redirects'] is False
    assert params['headers']['X-Api-Resource-Id'] == RESOURCE_ID and params['headers']['X-Api-Request-Id'] == key
    assert params['headers']['X-Api-Sequence'] == '-1'
    assert ('X-Api-Key' in params['headers']) is (not legacy)
    assert ('X-Api-App-Key' in params['headers']) is legacy
    assert ('X-Api-Access-Key' in params['headers']) is legacy
    assert params['json']['request'] == dict(model_name='bigmodel', enable_itn=True, enable_punc=True, enable_ddc=False)
    assert params['json']['user'] == {'uid': 'anonymous-fixture'}
    assert set(params['json']['audio']) == {'data'}


@pytest.mark.parametrize('response, state, code', [
    (FakeResponse(code='20000003'), 'EMPTY', 'SPEECH_INPUT_NO_SPEECH'),
    (FakeResponse(status=302), 'FAILED', 'SPEECH_INPUT_PROVIDER_ERROR'),
    (FakeResponse(code='55000031'), 'FAILED', 'SPEECH_INPUT_PROVIDER_ERROR'),
    (FakeResponse(code='45000151'), 'FAILED', 'SPEECH_INPUT_PROVIDER_ERROR'),
    (FakeResponse(code=''), 'FAILED', 'SPEECH_INPUT_PROVIDER_ERROR'),
    (FakeResponse(body={'result': {'text': ' '*4}}), 'EMPTY', 'SPEECH_INPUT_NO_SPEECH'),
    (FakeResponse(body={'result': {'text': '字'*6001}}), 'FAILED', 'SPEECH_INPUT_TEXT_TOO_LONG'),
    (FakeResponse(body={'error': 'provider credential detail'}), 'FAILED', 'SPEECH_INPUT_RESPONSE_INVALID'),
])
def test_upstream_failures_use_fixed_codes_and_never_return_raw_error(asr, response, state, code):
    client = FakeClient(response)
    with patch('src.fusion.package_speech_input.aiohttp.ClientSession', return_value=client):
        result = asyncio.run(request_transcription(asr.settings, str(uuid4()), 'fixture', wav()))
    assert result == (state, None, code) and len(client.calls) == 1


@pytest.mark.parametrize('length', [1001, 6000])
def test_transcript_can_be_edited_before_merging_into_limited_draft(asr, length):
    transcript = '字' * (length - 1) + '尾'
    client = FakeClient(FakeResponse(body={'result': {'text': transcript}}))
    with patch('src.fusion.package_speech_input.aiohttp.ClientSession', return_value=client):
        result = asyncio.run(request_transcription(asr.settings, str(uuid4()), 'fixture', wav()))
    assert result == ('OK', transcript, None)
    assert len(client.calls) == 1
    assert asr.service.availability(asr.view)['max_characters'] == 1000


def test_restart_idempotency_binding_and_no_audio_storage(asr):
    key = str(uuid4()); result = asyncio.run(invoke(asr, key))
    assert result['state'] == 'OK' and result['text']
    asr.service = PackageSpeechInputService(asr.settings, asr.sdk, lambda: asr.now[0])
    assert asyncio.run(invoke(asr, key)) == result
    assert asr.service.receipt(key, 1, asr.view['play_id'], asr.view) == result
    with pytest.raises(SpeechInputError, match='REQUEST_CONFLICT'):
        asyncio.run(invoke(asr, key, audio=wav(sample=2)))
    for owner, play_id in [(2,asr.view['play_id']), (1,'other-play')]:
        with pytest.raises(SpeechInputError, match='RECEIPT_NOT_FOUND'):
            asr.service.receipt(key, owner, play_id, asr.view)
    assert asr.sdk.await_count == 1
    assert {p.name for p in asr.settings.state_dir.iterdir()} == {'receipts.sqlite'}
    with sqlite3.connect(asr.settings.state_dir/'receipts.sqlite') as db:
        row = db.execute('SELECT audio_sha,frames,text FROM asr_requests').fetchone()
    assert len(row[0]) == 64 and row[1] == 16000 and row[2] == result['text']


def test_owner_lock_and_duplicate_pending_requests_are_transactional(asr):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            entered.set(); await release.wait(); return 'OK','等待结束。',None
        asr.sdk.side_effect = delayed
        key = str(uuid4()); task = asyncio.create_task(invoke(asr, key)); await entered.wait()
        assert (await invoke(asr, key))['state'] == 'PENDING'
        with pytest.raises(SpeechInputError, match='BUSY'): await invoke(asr)
        release.set(); assert (await task)['state'] == 'OK'
    asyncio.run(run()); assert asr.sdk.await_count == 1


@pytest.mark.parametrize('failure', ['exception', 'cancel'])
def test_unknown_or_cancelled_dispatch_keeps_duration_and_never_retries(asr, failure):
    asr.sdk.side_effect = asyncio.CancelledError() if failure == 'cancel' else TimeoutError('provider detail')
    key = str(uuid4())
    if failure == 'cancel':
        with pytest.raises(asyncio.CancelledError): asyncio.run(invoke(asr, key))
    else: assert asyncio.run(invoke(asr, key))['state'] == 'UNKNOWN'
    assert asyncio.run(invoke(asr, key))['state'] == 'UNKNOWN'
    row = SpeechInputLedger(asr.settings, lambda: asr.now[0]).get(key, 1, asr.view['play_id'])
    assert row['frames'] == 16000 and row['text'] is None and asr.sdk.await_count == 1


@pytest.mark.parametrize('quota, code', [('daily_seconds','DAILY_LIMIT'), ('play_seconds','PLAY_LIMIT')])
def test_failed_calls_still_consume_exact_duration_quota(asr, quota, code):
    asr.settings = replace(asr.settings, **{quota: 1})
    asr.service = PackageSpeechInputService(asr.settings, asr.sdk, lambda: asr.now[0])
    asr.sdk.return_value = ('FAILED',None,'SPEECH_INPUT_PROVIDER_ERROR')
    assert asyncio.run(invoke(asr, audio=wav(9600)))['state'] == 'FAILED'
    with pytest.raises(SpeechInputError, match=code): asyncio.run(invoke(asr, audio=wav(9600)))
    assert asr.sdk.await_count == 1


def test_duration_records_remain_after_ttl_text_cleanup(asr):
    key = str(uuid4()); asyncio.run(invoke(asr, key)); asr.now[0] += 601
    result = asr.service.receipt(key,1,asr.view['play_id'],asr.view)
    assert result['state'] == 'EXPIRED' and result['text'] is None
    with sqlite3.connect(asr.settings.state_dir/'receipts.sqlite') as db:
        assert db.execute('SELECT text,frames FROM asr_requests').fetchone() == (None,16000)
    assert asyncio.run(invoke(asr, key))['state'] == 'EXPIRED' and asr.sdk.await_count == 1


def test_crash_pending_expiry_preserves_duration_and_releases_only_owner_lock(asr):
    ledger = SpeechInputLedger(asr.settings, lambda:asr.now[0]); key = str(uuid4())
    binding = dict(request_id=key,owner=1,play=asr.view['play_id'],revision=3,channel='PUBLIC',
        call_id=None,audio_sha='0'*64,frames=16000)
    first,claimed=ledger.claim(binding); assert claimed and first['state']=='PENDING'
    asr.now[0]+=61
    assert ledger.get(key,1,asr.view['play_id'])['state']=='UNKNOWN'
    # Restart recovery does not reset the old reservation or dispatch it.
    other,claimed=SpeechInputLedger(asr.settings,lambda:asr.now[0]).claim({**binding,'request_id':str(uuid4())})
    assert claimed and other['state']=='PENDING'
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT SUM(frames) FROM asr_requests').fetchone()[0]==32000
    assert asr.sdk.await_count==0


def test_daily_quota_uses_beijing_midnight_and_game_quota_does_not_reset(asr):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    asr.settings=replace(asr.settings,daily_seconds=1,play_seconds=2)
    asr.now[0]=datetime(2026,9,8,23,59,59,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
    asr.service=PackageSpeechInputService(asr.settings,asr.sdk,lambda:asr.now[0])
    asyncio.run(invoke(asr))
    with pytest.raises(SpeechInputError,match='DAILY_LIMIT'):asyncio.run(invoke(asr))
    asr.now[0]+=2;assert asyncio.run(invoke(asr))['state']=='OK'
    asr.now[0]+=86400
    with pytest.raises(SpeechInputError,match='PLAY_LIMIT'):asyncio.run(invoke(asr))
    assert asr.sdk.await_count==2


def test_storage_rejects_symlinks_and_existing_unrelated_database(asr,tmp_path):
    target=tmp_path.resolve()/'owned';target.mkdir(mode=0o700)
    link=tmp_path.resolve()/'link';link.symlink_to(target,target_is_directory=True)
    with pytest.raises(SpeechInputError,match='STORAGE_UNAVAILABLE'):
        SpeechInputLedger(replace(asr.settings,state_dir=link))
    with sqlite3.connect(target/'receipts.sqlite') as db:db.execute('CREATE TABLE unrelated(value TEXT)')
    before=(target/'receipts.sqlite').read_bytes()
    service=PackageSpeechInputService(replace(asr.settings,state_dir=target),asr.sdk)
    with pytest.raises(SpeechInputError,match='STORAGE_UNAVAILABLE'):
        asyncio.run(service.transcribe(str(uuid4()),1,asr.view['play_id'],3,'PUBLIC',None,wav(),lambda:asr.view))
    assert (target/'receipts.sqlite').read_bytes()==before and asr.sdk.await_count==0


def test_scene_changes_drop_draft_and_private_call_must_match(asr):
    with pytest.raises(SpeechInputError, match='SCENE_CHANGED'):
        asyncio.run(invoke(asr, channel='PRIVATE', call_id='not-a-current-call'))
    assert asr.sdk.await_count == 0
    async def changed(*args):
        asr.view['revision'] += 1; return 'OK','过期识别稿',None
    asr.sdk.side_effect = changed; key = str(uuid4())
    result = asyncio.run(invoke(asr, key))
    assert result['state'] == 'EXPIRED' and result['text'] is None
    assert asr.service.receipt(key,1,asr.view['play_id'],asr.view)['text'] is None


def test_asr_does_not_append_game_events_or_change_model_budget(play, asr):
    _, view = start(play, full_package()); pid = view['play_id']
    before = deepcopy(play.play.get(pid,1)); saved = [(e.event_hash,e.state_hash) for e in events(play)]
    result = asyncio.run(asr.service.transcribe(str(uuid4()),1,pid,view['revision'],'PUBLIC',None,wav(),lambda:play.play.get(pid,1)))
    assert result['state'] == 'OK'
    assert play.play.get(pid,1) == before and [(e.event_hash,e.state_hash) for e in events(play)] == saved
    assert play.sdk.chat_completion.await_count == 0


@pytest.fixture
def speech_http(asr):
    from src.api.routes import package_speech_input_routes as routes
    users = {'player':SimpleNamespace(id=1,is_active=True,is_admin=False), 'other':SimpleNamespace(id=2,is_active=True,is_admin=False)}
    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token): return users.get(token)
    play_service = Mock()
    def get(identifier, owner):
        if owner != 1 or identifier != asr.view['play_id']: raise PackagePlayError('PACKAGE_PLAY_NOT_FOUND',404)
        return deepcopy(asr.view)
    play_service.get.side_effect = get
    app = FastAPI(); app.add_middleware(Auth); app.include_router(routes.router)
    app.dependency_overrides[routes.play_service] = lambda:play_service
    app.dependency_overrides[routes.speech_input_service] = lambda:asr.service
    with TestClient(app) as client: yield client,play_service


def test_http_owner_checks_raw_body_receipt_and_no_store(asr, speech_http):
    client, game = speech_http; base=f'/api/fusion/package-plays/{asr.view["play_id"]}/speech-input'
    for token,status in [(None,401),('other',404)]:
        result=client.get(base,headers={'Authorization':'Bearer '+token} if token else {})
        assert result.status_code==status
    headers={'Authorization':'Bearer player'}
    available=client.get(base,headers=headers); assert available.json()['data']['available']
    key=str(uuid4()); url=f'{base}/{key}?expected_revision=3&channel=PUBLIC'
    result=client.post(url,headers={**headers,'Content-Type':'audio/wav'},content=wav())
    assert result.status_code==200 and result.json()['data']['state']=='OK'
    assert result.headers['Cache-Control']=='no-store'
    assert client.get(f'{base}/{key}',headers=headers).json()==result.json()
    assert client.get(f'{base}/{uuid4()}',headers=headers).status_code==404
    assert asr.sdk.await_count==1 and game.db.rollback.call_count>=4


@pytest.mark.parametrize('query,mime,body,status', [
    ('expected_revision=3&channel=PUBLIC','audio/webm',wav(),422),
    ('expected_revision=3&channel=PUBLIC&channel=PRIVATE','audio/wav',wav(),422),
    ('expected_revision=true&channel=PUBLIC','audio/wav',wav(),422),
    ('expected_revision=3&channel=PUBLIC&url=http://example.invalid','audio/wav',wav(),422),
    ('expected_revision=2&channel=PUBLIC','audio/wav',wav(),409),
    ('expected_revision=3&channel=PUBLIC','audio/wav',b'x'*(MAX_AUDIO_BYTES+1),413),
])
def test_http_invalid_requests_cannot_dispatch(asr,speech_http,query,mime,body,status):
    client,_=speech_http
    url=f'/api/fusion/package-plays/{asr.view["play_id"]}/speech-input/{uuid4()}?{query}'
    result=client.post(url,headers={'Authorization':'Bearer player','Content-Type':mime},content=body)
    assert result.status_code==status and asr.sdk.await_count==0
    assert result.headers['Cache-Control']=='no-store'
