"""Admin-only manual audit records, separate from publication approvals."""
import asyncio
from typing import Any, Callable, NoReturn, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from src.api.routes.source_bundle_routes import source_store
from src.core.auth_middleware import get_current_admin_user_from_request
from src.db.session import get_db_session
from src.fusion.package_import import PackageConflict, PackageNotFound
from src.fusion.package_validation import PackageInputError, parse_package_json
from src.fusion.script_review import ReviewInputError, ScriptReviewService
from src.fusion.rule_review import RuleReviewError, build_rule_review
from src.fusion.source_bundles import SourceBundleError, SourceBundleStore
from src.schemas.script_review import FindingDispositionRequest, parse_submit_audit_request


router = APIRouter(prefix="/api/admin/fusion", tags=["人工审核记录"])
Body = TypeVar("Body", bound=BaseModel)


def review_service(request: Request, db: Session = Depends(get_db_session)) -> ScriptReviewService:
    get_current_admin_user_from_request(request)
    return ScriptReviewService(db)


async def _body(request: Request, model: type[Body] | Callable[[Any], Body], limit: int) -> Body:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="审核请求超过大小限制")
        raw.extend(chunk)
    try:
        document = parse_package_json(bytes(raw))
        return model.model_validate(document) if isinstance(model, type) and issubclass(model, BaseModel) else model(document)
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="审核请求格式不符合契约") from None


def _identifier(value: str) -> int:
    if not value.isascii() or not value.isdigit() or not 0 < len(value) <= 10 or not 0 < int(value) <= 2147483647:
        raise HTTPException(status_code=422, detail="记录标识无效")
    return int(value)


def _fail(exc: Exception) -> NoReturn:
    if isinstance(exc, PackageNotFound):
        code = 404
    elif isinstance(exc, (ReviewInputError, PackageInputError)):
        code = 422
    else:
        code = 409
    raise HTTPException(status_code=code, detail=str(exc)) from None


@router.get("/review-candidates")
async def list_candidates(request: Request, response: Response, service: ScriptReviewService = Depends(review_service)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": service.list_candidates()}
    except (PackageConflict, PackageNotFound) as exc:
        _fail(exc)


@router.get("/script-packages/{version_id}/review")
async def read_review(version_id: str, request: Request, response: Response,
                      service: ScriptReviewService = Depends(review_service)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": service.get_review(_identifier(version_id))}
    except (PackageConflict, PackageNotFound) as exc:
        _fail(exc)


@router.get("/script-packages/{version_id}/rule-review")
async def read_rule_review(version_id: str, request: Request, response: Response,
                           bundle_hash: str = Query(pattern=r"^[0-9a-f]{64}$"),
                           expected_package_hash: str = Query(pattern=r"^[0-9a-f]{64}$"),
                           offset: int = Query(default=0, ge=0, le=15100),
                           limit: int = Query(default=20, ge=1, le=50),
                           service: ScriptReviewService = Depends(review_service),
                           store: SourceBundleStore = Depends(source_store)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        version = service.packages.get_version(_identifier(version_id))
        if version["package_hash"] != expected_package_hash:
            raise PackageConflict("候选版本已变化，请重新读取后核对")
        # No SQLAlchemy Session crosses the filesystem worker boundary.
        result = await asyncio.to_thread(build_rule_review, version["package"], bundle_hash, store,
                                         offset=offset, limit=limit)
        return {"success": True, "data": result}
    except RuleReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Cache-Control": "no-store"}) from None
    except (SourceBundleError, PackageConflict, PackageNotFound) as exc:
        _fail(exc)


@router.post("/script-packages/{version_id}/audits")
async def submit_audit(version_id: str, request: Request, response: Response,
                       service: ScriptReviewService = Depends(review_service),
                       store: SourceBundleStore = Depends(source_store)) -> dict:
    actor = get_current_admin_user_from_request(request)
    version = _identifier(version_id)
    body = await _body(request, parse_submit_audit_request, 512 * 1024)
    response.headers["Cache-Control"] = "no-store"
    try:
        existing, document = service.prepare_audit(version, body, actor.id)
        if existing is not None:
            return {"success": True, "data": existing}
        verified = await asyncio.to_thread(store.verify, body.bundle_hash, document=document)
        return {"success": True, "data": service.save_audit(version, body, actor.id, verified, store)}
    except (ReviewInputError, PackageInputError, PackageConflict, PackageNotFound, SourceBundleError) as exc:
        _fail(exc)


@router.post("/script-audits/{audit_id}/dispositions")
async def add_disposition(audit_id: str, request: Request, response: Response,
                          service: ScriptReviewService = Depends(review_service)) -> dict:
    actor = get_current_admin_user_from_request(request)
    audit = _identifier(audit_id)
    body = await _body(request, FindingDispositionRequest, 16384)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": service.add_disposition(audit, body, actor.id)}
    except (ReviewInputError, PackageInputError, PackageConflict, PackageNotFound) as exc:
        _fail(exc)
