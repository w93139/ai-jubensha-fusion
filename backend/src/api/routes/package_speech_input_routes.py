"""Authenticated speech drafts; no game writes or automatic role responses."""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket

from src.api.routes.package_runtime_routes import active_user, NO_STORE
from src.api.routes.package_play_routes import play_service
from src.db.session import get_db_session
from src.db.models.package_play import ScriptPackagePlayEvent
from src.fusion.package_play import PackagePlayError, PackagePlayService
from src.fusion.package_speech_input import PackageSpeechInputService, SpeechInputError, MAX_AUDIO_BYTES, validate_scene
from src.fusion.package_realtime_speech import StreamTickets, run_stream


router = APIRouter(prefix='/api/fusion', tags=['玩家语音输入'])


def speech_input_service():
    return PackageSpeechInputService()


def stream_play_service(db=Depends(get_db_session)):
    # Only ticket authentication applies to WS. Keep the same read policies;
    # do not depend on the HTTP-only active_user(Request) injection chain.
    return play_service(actor=None, db=db)


def _fail(exc):
    raise HTTPException(exc.status_code, detail=exc.code, headers=NO_STORE) from None


def _view(service, play_id, owner):
    try:
        return service.get(play_id, owner)
    finally:
        # This route only reads game state. Release its snapshot before network
        # I/O and fetch a fresh view after transcription to detect scene changes.
        service.db.rollback()


def _checkpoint(service, play_id, owner):
    try:
        row = service._row(play_id, owner)  # Re-check play ownership.
        latest = service.db.query(ScriptPackagePlayEvent.revision, ScriptPackagePlayEvent.event_hash).filter_by(
            play_id=play_id).order_by(ScriptPackagePlayEvent.revision.desc()).first()
        return (latest.revision if latest else 0, latest.event_hash if latest else None, row.binding_hash)
    finally:
        service.db.rollback()


def _stream_view(service, play_id, owner):
    # Full replay is expensive for long games. Validate it once, then query the
    # immutable binding and latest append-only event on EVERY emission/chunk.
    # Any game change invalidates the recording; no stale projection is reused.
    view = _view(service, play_id, owner)
    checkpoint = _checkpoint(service, play_id, owner)
    if checkpoint[0] != view['revision']: raise SpeechInputError('SPEECH_INPUT_SCENE_CHANGED')
    def current():
        if _checkpoint(service, play_id, owner) != checkpoint: raise SpeechInputError('SPEECH_INPUT_SCENE_CHANGED')
        return view
    return current


@router.get('/package-plays/{play_id}/speech-input')
async def availability(play_id: str, request: Request, response: Response,
        play: PackagePlayService = Depends(play_service), speech=Depends(speech_input_service)):
    owner = active_user(request).id
    response.headers.update(NO_STORE)
    try: return {'success': True, 'data': speech.availability(_view(play, play_id, owner))}
    except (PackagePlayError, SpeechInputError) as exc: _fail(exc)


@router.post('/package-plays/{play_id}/speech-stream/{request_id}/ticket')
async def stream_ticket(play_id: str, request_id: str, request: Request, response: Response,
        play: PackagePlayService = Depends(play_service), speech=Depends(speech_input_service)):
    owner = active_user(request).id
    response.headers.update(NO_STORE)
    try:
        view = _view(play, play_id, owner)
        params = request.query_params
        if (set(params) - {'expected_revision', 'channel', 'call_id'}
                or any(len(params.getlist(k)) != 1 for k in params)
                or not params.get('expected_revision', '').isascii()
                or not params.get('expected_revision', '').isdecimal()
                or len(params['expected_revision']) > 10):
            raise SpeechInputError('SPEECH_INPUT_REQUEST_INVALID', 422)
        revision, channel, call_id = int(params['expected_revision']), params.get('channel'), params.get('call_id')
        validate_scene(view, revision, channel, call_id)
        if speech.settings.reason or speech.settings.provider != 'qwen' or not speech.settings.realtime:
            raise SpeechInputError(speech.settings.reason or 'SPEECH_INPUT_DISABLED', 503)
        result = StreamTickets(speech.settings, speech.now).issue(request_id, owner, play_id,
            revision, channel, call_id, request.headers.get('origin'))
        return {'success': True, 'data': result}
    except (PackagePlayError, SpeechInputError) as exc: _fail(exc)


@router.websocket('/package-plays/{play_id}/speech-stream/{request_id}')
async def stream_audio(websocket: WebSocket, play_id: str, request_id: str,
        play: PackagePlayService = Depends(stream_play_service), speech=Depends(speech_input_service)):
    # HTTP auth middleware does not run for WebSockets. A short-lived, single-use
    # ticket binds the authenticated owner and scene, without credentials in URLs.
    await websocket.accept()
    try:
        message = await asyncio.wait_for(websocket.receive(), 5)
        raw = message.get('text')
        if not isinstance(raw, str) or len(raw) > 128: raise SpeechInputError('SPEECH_INPUT_TICKET_INVALID', 403)
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'ticket'}: raise SpeechInputError('SPEECH_INPUT_TICKET_INVALID', 403)
        binding = StreamTickets(speech.settings, speech.now).consume(request_id, play_id,
            data['ticket'], websocket.headers.get('origin'))
        await run_stream(websocket, binding, speech.settings, _stream_view(play, play_id, binding['owner']), now=speech.now)
    except (PackagePlayError, SpeechInputError, ValueError, asyncio.TimeoutError):
        # No provider details, authentication tokens, or private game data.
        try: await websocket.send_json({'type': 'ERROR', 'request_id': request_id, 'message': '本次语音连接已结束，请继续文字输入或重新录音。'})
        except Exception: pass
    finally:
        try: await websocket.close()
        except Exception: pass


@router.get('/package-plays/{play_id}/speech-input/{request_id}')
async def receipt(play_id: str, request_id: str, request: Request, response: Response,
        play: PackagePlayService = Depends(play_service), speech=Depends(speech_input_service)):
    owner = active_user(request).id
    response.headers.update(NO_STORE)
    try:
        view = _view(play, play_id, owner)
        return {'success': True, 'data': speech.receipt(request_id, owner, play_id, view)}
    except (PackagePlayError, SpeechInputError) as exc: _fail(exc)


@router.post('/package-plays/{play_id}/speech-input/{request_id}')
async def transcribe(play_id: str, request_id: str, request: Request, response: Response,
        play: PackagePlayService = Depends(play_service), speech=Depends(speech_input_service)):
    owner = active_user(request).id
    response.headers.update(NO_STORE)
    try:
        # Authorize before accepting audio or creating a receipt. Do not fetch
        # arbitrary URLs; only accept a strictly bounded raw WAV request body.
        _view(play, play_id, owner)
        params = request.query_params
        if (set(params) - {'expected_revision', 'channel', 'call_id'}
                or any(len(params.getlist(k)) != 1 for k in params)
                or not params.get('expected_revision', '').isascii()
                or not params.get('expected_revision', '').isdecimal()
                or len(params['expected_revision']) > 10
                or request.headers.get('content-type', '').strip().lower() != 'audio/wav'):
            raise SpeechInputError('SPEECH_INPUT_REQUEST_INVALID', 422)
        audio = bytearray()
        async for chunk in request.stream():
            if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
                raise SpeechInputError('SPEECH_INPUT_AUDIO_TOO_LARGE', 413)
            audio.extend(chunk)
        result = await speech.transcribe(request_id, owner, play_id, int(params['expected_revision']),
            params.get('channel'), params.get('call_id'), bytes(audio), lambda: _view(play, play_id, owner))
        return {'success': True, 'data': result}
    except (PackagePlayError, SpeechInputError) as exc: _fail(exc)
