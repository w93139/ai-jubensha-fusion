"""融合版游戏 REST API。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.core.auth_middleware import get_current_active_user_from_request
from src.db.models.script_model import ScriptStatus
from src.db.session import get_db_session
from src.fusion.service import FusionGameError, FusionGameService
from src.fusion.websocket import fusion_connections
from src.schemas.fusion_game import CreateFusionSessionRequest, FusionActionRequest, SelectCharacterRequest


router = APIRouter(prefix="/api/fusion", tags=["融合版游戏"])
admin_router = APIRouter(prefix="/api/admin/fusion", tags=["融合版剧本管理"])


def service(db: Session = Depends(get_db_session)) -> FusionGameService:
    return FusionGameService(db)


def response(data, message="ok"):
    return {"success": True, "message": message, "data": data}


def invoke(callable_):
    try:
        return callable_()
    except FusionGameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/scripts")
def published_scripts(request: Request, games: FusionGameService = Depends(service)):
    get_current_active_user_from_request(request)
    return response(games.list_published_scripts())


@router.post("/sessions", status_code=201)
def create_session(body: CreateFusionSessionRequest, request: Request, games: FusionGameService = Depends(service)):
    user = get_current_active_user_from_request(request)
    return response(invoke(lambda: games.create_session(body.script_id, user.id)), "会话已创建")


@router.get("/sessions/{session_id}")
def session_state(session_id: str, request: Request, games: FusionGameService = Depends(service)):
    user = get_current_active_user_from_request(request)
    return response(invoke(lambda: games.get_state(session_id, user.id)))


@router.post("/sessions/{session_id}/select-character")
def select_character(session_id: str, body: SelectCharacterRequest, request: Request, games: FusionGameService = Depends(service)):
    user = get_current_active_user_from_request(request)
    return response(invoke(lambda: games.select_character(session_id, user.id, body.character_id, body.idempotency_key)))


@router.post("/sessions/{session_id}/actions")
async def perform_action(session_id: str, body: FusionActionRequest, request: Request, games: FusionGameService = Depends(service)):
    user = get_current_active_user_from_request(request)
    before_event_id = invoke(lambda: games.get_state(session_id, user.id))["last_event_id"]
    state = invoke(lambda: games.perform_action(session_id, user.id, body.type, body.payload, body.idempotency_key))
    try:
        if body.type == "ask_question":
            await games.answer_question(session_id, user.id, body.idempotency_key)
            state = games.get_state(session_id, user.id)
        elif body.type == "advance_phase" and state["phase"] in ("INTRODUCTION", "DISCUSSION"):
            await games.run_ai_phase(session_id, user.id, body.idempotency_key)
            state = games.get_state(session_id, user.id)
    except (FusionGameError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await fusion_connections.broadcast(session_id, {
        "type": "STATE_UPDATED", "session_id": session_id,
        "events": games.get_events(session_id, user.id, before_event_id), "payload": state,
    })
    return response(state)


@router.get("/sessions/{session_id}/events")
def session_events(session_id: str, request: Request, after: int = Query(0, ge=0), games: FusionGameService = Depends(service)):
    user = get_current_active_user_from_request(request)
    return response(invoke(lambda: games.get_events(session_id, user.id, after)))


@admin_router.post("/scripts/{script_id}/validate")
def validate_script(script_id: int, games: FusionGameService = Depends(service)):
    return response(invoke(lambda: games.validate_script(script_id)))


@admin_router.post("/scripts/{script_id}/review")
def review_script(script_id: int, games: FusionGameService = Depends(service)):
    return response(invoke(lambda: games.set_script_status(script_id, ScriptStatus.REVIEW)))


@admin_router.post("/scripts/{script_id}/publish")
def publish_script(script_id: int, games: FusionGameService = Depends(service)):
    return response(invoke(lambda: games.set_script_status(script_id, ScriptStatus.PUBLISHED)))


@admin_router.post("/scripts/{script_id}/archive")
def archive_script(script_id: int, games: FusionGameService = Depends(service)):
    return response(invoke(lambda: games.set_script_status(script_id, ScriptStatus.ARCHIVED)))
