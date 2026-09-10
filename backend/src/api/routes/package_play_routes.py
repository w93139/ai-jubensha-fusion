"""Independent text play; durable reservations precede one-shot model calls."""
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.api.routes.package_runtime_routes import NO_STORE, active_user
from src.db.models.user import User
from src.db.session import get_db_session
from src.fusion.package_play import PackagePlayError, PackagePlayService
from src.fusion.package_validation import parse_package_json
from src.schemas.package_play import CreatePackagePlayRequest, PackagePlayActionRequest, PackagePlayAskRequest, PackagePlaySpeakRequest, PackagePlayProposalRequest, PackagePlayRespondRequest, FullPlayActionRequest
from src.schemas.package_play import FullPlayDecisionRequest, FullPlayPrivateReplyRequest
from src.schemas.package_play import FullPlayPhoneRequest, FullPlayPhonePauseRequest, GuidedPlayRequest, TopicCommand


router = APIRouter(prefix="/api/fusion", tags=["角色材料问答与结尾"])


def play_service(actor: User = Depends(active_user), db: Session = Depends(get_db_session)) -> PackagePlayService:
    return PackagePlayService(db, speech_policy='role-speech/1.10', table_policy='package-table-model/1.1',
                              include_interactions=True, request_scope_policy='package-request-scope/1.0')


def _fail(exc: PackagePlayError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.code, headers=NO_STORE) from None


async def _body(request: Request, model, limit: int = 8192):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="PACKAGE_PLAY_REQUEST_TOO_LARGE", headers=NO_STORE)
        raw.extend(chunk)
    try:
        parsed = model.model_validate(parse_package_json(bytes(raw)))
        parsed.model_dump_json().encode("utf-8")
        return parsed
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="PACKAGE_PLAY_REQUEST_INVALID", headers=NO_STORE) from None


@router.post('/package-plays/{play_id}/decisions')
async def decide_role(play_id: str, request: Request, response: Response,
                      service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, FullPlayDecisionRequest)
    response.headers.update(NO_STORE)
    try:
        return {'success': True, 'data': await service.decide(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post('/package-plays/{play_id}/private-responses')
async def private_response(play_id: str, request: Request, response: Response,
                           service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, FullPlayPrivateReplyRequest)
    response.headers.update(NO_STORE)
    try:
        return {'success': True, 'data': await service.reply_private(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post('/package-plays/{play_id}/phone-step')
async def phone_step(play_id:str,request:Request,response:Response,service:PackagePlayService=Depends(play_service)):
    actor=active_user(request);body=await _body(request,FullPlayPhoneRequest);response.headers.update(NO_STORE)
    try:return {'success':True,'data':await service.phone_step(play_id,body,actor.id)}
    except PackagePlayError as exc:_fail(exc)


@router.post('/package-plays/{play_id}/phone-pause')
async def phone_pause(play_id:str,request:Request,response:Response,service:PackagePlayService=Depends(play_service)):
    actor=active_user(request);body=await _body(request,FullPlayPhonePauseRequest);response.headers.update(NO_STORE)
    try:return {'success':True,'data':service.pause_phone(play_id,body,actor.id)}
    except PackagePlayError as exc:_fail(exc)


@router.get('/package-play-library')
async def list_play_library(request: Request, response: Response, offset: str = '0', limit: str = '20',
                            service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    response.headers.update(NO_STORE)
    # Validate here so rejected query values receive the same private-cache policy.
    if any(not value.isascii() or not value.isdecimal() or len(value) > 10 for value in (offset, limit)):
        raise HTTPException(status_code=422, detail='PACKAGE_PLAY_LIBRARY_PAGE_INVALID', headers=NO_STORE)
    page_offset, page_limit = int(offset), int(limit)
    if not 1 <= page_limit <= 50:
        raise HTTPException(status_code=422, detail='PACKAGE_PLAY_LIBRARY_PAGE_INVALID', headers=NO_STORE)
    try:
        return {'success': True, 'data': service.list_library(actor.id, page_offset, page_limit)}
    except PackagePlayError as exc:
        _fail(exc)


@router.get("/package-plays")
async def find_play(request: Request, response: Response, opening_session_id: str = "",
                    service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.find_for_opening(opening_session_id, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post("/package-plays", status_code=201)
async def create_play(request: Request, response: Response,
                      service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, CreatePackagePlayRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.create(body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.get("/package-plays/{play_id}")
async def read_play(play_id: str, request: Request, response: Response,
                    service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.get(play_id, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.get('/package-plays/{play_id}/images/{visual_id}')
async def read_play_image(play_id: str, visual_id: str, request: Request,
                          service: PackagePlayService = Depends(play_service)) -> Response:
    actor = active_user(request)
    try:
        data, media = service.image(play_id, visual_id, actor.id)
        return Response(content=data, media_type=media, headers={**NO_STORE,
            'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox"})
    except PackagePlayError as exc:
        _fail(exc)


@router.post("/package-plays/{play_id}/actions")
async def act_play(play_id: str, request: Request, response: Response,
                   service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, PackagePlayActionRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.act(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post("/package-plays/{play_id}/ask")
async def ask_role(play_id: str, request: Request, response: Response,
                   service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, PackagePlayAskRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": await service.ask(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post("/package-plays/{play_id}/discussion")
async def speak_play(play_id: str, request: Request, response: Response,
                     service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, PackagePlaySpeakRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.speak(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post("/package-plays/{play_id}/proposals")
async def propose_investigation(play_id: str, request: Request, response: Response,
                               service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, PackagePlayProposalRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": await service.propose(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post("/package-plays/{play_id}/responses")
async def respond_role(play_id: str, request: Request, response: Response,
                       service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, PackagePlayRespondRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": await service.respond(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post('/package-plays/{play_id}/table')
async def table_action(play_id: str, request: Request, response: Response,
                       service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, FullPlayActionRequest, 65536)
    response.headers.update(NO_STORE)
    try:
        return {'success': True, 'data': service.table(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post('/package-plays/{play_id}/guided')
async def guided_play(play_id: str, request: Request, response: Response,
                      service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, GuidedPlayRequest)
    response.headers.update(NO_STORE)
    try:
        return {'success': True, 'data': service.guided(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)


@router.post('/package-plays/{play_id}/topic')
async def topic_play(play_id: str, request: Request, response: Response,
                     service: PackagePlayService = Depends(play_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, TopicCommand)
    response.headers.update(NO_STORE)
    try:
        return {'success': True, 'data': service.topic(play_id, body, actor.id)}
    except PackagePlayError as exc:
        _fail(exc)
