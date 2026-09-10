"""Explicit fixed-role rules previews; no model or legacy game dispatch."""
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.api.routes.package_runtime_routes import NO_STORE, active_user
from src.db.models.user import User
from src.db.session import get_db_session
from src.fusion.package_flow import PackageFlowError, PackageFlowService
from src.fusion.package_validation import parse_package_json
from src.schemas.package_flow import CreatePackageFlowRequest, PackageFlowActionRequest


router = APIRouter(prefix="/api/fusion", tags=["固定角色阶段演练"])


def flow_service(actor: User = Depends(active_user), db: Session = Depends(get_db_session)) -> PackageFlowService:
    return PackageFlowService(db)


def _fail(exc: PackageFlowError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.code, headers=NO_STORE) from None


async def _body(request: Request, model):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 4096:
            raise HTTPException(status_code=413, detail="PACKAGE_FLOW_REQUEST_TOO_LARGE", headers=NO_STORE)
        raw.extend(chunk)
    try:
        return model.model_validate(parse_package_json(bytes(raw)))
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="PACKAGE_FLOW_REQUEST_INVALID", headers=NO_STORE) from None


@router.get("/package-flows")
async def find_flow(request: Request, response: Response, opening_session_id: str = "",
                    service: PackageFlowService = Depends(flow_service)) -> dict:
    actor = active_user(request)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.find_for_opening(opening_session_id, actor.id)}
    except PackageFlowError as exc:
        _fail(exc)


@router.post("/package-flows", status_code=201)
async def create_flow(request: Request, response: Response,
                      service: PackageFlowService = Depends(flow_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, CreatePackageFlowRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.create(body, actor.id)}
    except PackageFlowError as exc:
        _fail(exc)


@router.get("/package-flows/{flow_id}")
async def read_flow(flow_id: str, request: Request, response: Response,
                    service: PackageFlowService = Depends(flow_service)) -> dict:
    actor = active_user(request)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.get(flow_id, actor.id)}
    except PackageFlowError as exc:
        _fail(exc)


@router.post("/package-flows/{flow_id}/actions")
async def act_flow(flow_id: str, request: Request, response: Response,
                   service: PackageFlowService = Depends(flow_service)) -> dict:
    actor = active_user(request)
    body = await _body(request, PackageFlowActionRequest)
    response.headers.update(NO_STORE)
    try:
        return {"success": True, "data": service.act(flow_id, body, actor.id)}
    except PackageFlowError as exc:
        _fail(exc)
