"""Authenticated fixed-role opening previews. No action or model endpoints."""
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.core.auth_middleware import get_current_active_user_from_request
from src.db.models.user import User
from src.db.session import get_db_session
from src.fusion.package_runtime import PackageRuntimeError, PackageRuntimeService
from src.fusion.package_validation import parse_package_json
from src.schemas.package_runtime import CreatePackageSessionRequest


router = APIRouter(prefix="/api/fusion", tags=["固定版本开场预览"])
NO_STORE = {"Cache-Control": "no-store"}


def active_user(request: Request) -> User:
    try:
        return get_current_active_user_from_request(request)
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **NO_STORE}
        raise


def runtime_service(actor: User = Depends(active_user), db: Session = Depends(get_db_session)) -> PackageRuntimeService:
    return PackageRuntimeService(db)


def _fail(exc: PackageRuntimeError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.code, headers=NO_STORE) from None


async def _body(request: Request) -> CreatePackageSessionRequest:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 4096:
            raise HTTPException(status_code=413, detail="PACKAGE_SESSION_REQUEST_TOO_LARGE", headers=NO_STORE)
        raw.extend(chunk)
    try:
        return CreatePackageSessionRequest.model_validate(parse_package_json(bytes(raw)))
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="PACKAGE_SESSION_REQUEST_INVALID", headers=NO_STORE) from None


@router.get("/package-releases")
async def list_releases(request: Request, response: Response, service: PackageRuntimeService = Depends(runtime_service)) -> dict:
    actor = active_user(request)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.list_releases(actor.id)}
    except PackageRuntimeError as exc:
        _fail(exc)


@router.post("/package-sessions", status_code=201)
async def create_session(request: Request, response: Response, service: PackageRuntimeService = Depends(runtime_service)) -> dict:
    actor = active_user(request)
    body = await _body(request)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.create(body, actor.id)}
    except PackageRuntimeError as exc:
        _fail(exc)


@router.get("/package-sessions/{session_id}")
async def get_session(session_id: str, request: Request, response: Response,
                      service: PackageRuntimeService = Depends(runtime_service)) -> dict:
    actor = active_user(request)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.get(session_id, actor.id)}
    except PackageRuntimeError as exc:
        _fail(exc)


@router.get('/package-sessions/{session_id}/images/{visual_id}')
async def get_opening_image(session_id: str, visual_id: str, request: Request,
                            service: PackageRuntimeService = Depends(runtime_service)) -> Response:
    actor = active_user(request)
    try:
        data, media_type = service.image(session_id, visual_id, actor.id)
        return Response(content=data, media_type=media_type,
                        headers={**NO_STORE, 'X-Content-Type-Options': 'nosniff'})
    except PackageRuntimeError as exc:
        _fail(exc)
