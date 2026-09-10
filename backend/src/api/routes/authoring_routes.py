"""Admin queue/read/control endpoints; model dispatch belongs to the worker."""
from collections.abc import Callable
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from src.api.routes.script_review_routes import _body, _identifier
from src.api.routes.source_bundle_routes import source_store
from src.core.auth_middleware import get_current_admin_user_from_request
from src.db.session import db_manager
from src.fusion.authoring_jobs import AuthoringJobError, AuthoringJobStore
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError
from src.fusion.authoring_runner import submit_authoring_job
from src.fusion.authoring_sources import AuthoringSourceError
from src.fusion.provider_smoke import load_selected_config
from src.fusion.source_bundles import SourceBundleError, SourceBundleStore
from src.schemas.authoring import AuthoringRequestV12, AuthoringRequestV13, AuthoringRequestV14, AuthoringRequestV15, AuthoringRequestV16, AuthoringRequestV17, AuthoringRequestV18, AuthoringRequestV19, AuthoringRequestV110, AuthoringRequestV111, AuthoringRequestV112, parse_authoring_request
from src.schemas.script_package import PackageModel


router = APIRouter(prefix="/api/admin/fusion/authoring-jobs", tags=["编译与模型审核任务"])


class RevisionRequest(PackageModel):
    expected_revision: int = Field(ge=0, le=2147483647)


def authoring_store(request: Request) -> AuthoringJobStore:
    get_current_admin_user_from_request(request)
    return AuthoringJobStore(db_manager.get_session)


def authoring_model(request: Request, package_contract: str = "script-package/1.1", *, rule_plan_enabled: bool = False,
                    confirm_frozen_text: bool = False, indexed_audit: bool = False, bounded_audit: bool = False, direct_audit_schema: bool = False, strict_audit_schema: bool = False, portable_audit_patterns: bool = False, typed_audit_schema: bool = False, runtime_audit_context: bool = False, citation_audit: bool = False) -> AuthoringModel:
    get_current_admin_user_from_request(request)
    try:
        return AuthoringModel(load_selected_config("aliyun_bailian"), package_contract=package_contract,
                              rule_plan_enabled=rule_plan_enabled, confirm_frozen_text=confirm_frozen_text, indexed_audit=indexed_audit, bounded_audit=bounded_audit, direct_audit_schema=direct_audit_schema, strict_audit_schema=strict_audit_schema, portable_audit_patterns=portable_audit_patterns, typed_audit_schema=typed_audit_schema, runtime_audit_context=runtime_audit_context, citation_audit=citation_audit)
    except (ValueError, OSError):
        raise HTTPException(status_code=503, detail="AUTHORING_CONFIG_UNAVAILABLE") from None


def model_factory(request: Request) -> Callable[..., AuthoringModel]:
    get_current_admin_user_from_request(request)
    return lambda package_contract="script-package/1.1", **options: authoring_model(request, package_contract, **options)


def sources_factory(request: Request) -> Callable[[], SourceBundleStore]:
    get_current_admin_user_from_request(request)
    return lambda: source_store(request)


def _fail(exc: Exception) -> NoReturn:
    code = getattr(exc, "code", "AUTHORING_SOURCE_UNAVAILABLE")
    status = 404 if code == "AUTHORING_JOB_NOT_FOUND" else (409 if isinstance(exc, AuthoringJobError) else 422)
    raise HTTPException(status_code=status, detail=code) from None


@router.get("")
async def list_jobs(request: Request, response: Response, jobs: AuthoringJobStore = Depends(authoring_store)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": jobs.list()}
    except AuthoringJobError as exc:
        _fail(exc)


@router.post("", status_code=202)
@router.post("/with-rule-plan", status_code=202)
async def create_job(request: Request, response: Response, jobs: AuthoringJobStore = Depends(authoring_store),
                     sources: Callable[[], SourceBundleStore] = Depends(sources_factory),
                     model: Callable[..., AuthoringModel] = Depends(model_factory)) -> dict:
    actor = get_current_admin_user_from_request(request)
    with_rule_plan = request.url.path.endswith("/with-rule-plan")
    body = await _body(request, parse_authoring_request,
                       1024 * 1024 if with_rule_plan else 16384)
    if isinstance(body, AuthoringRequestV13) != with_rule_plan:
        raise HTTPException(status_code=422, detail="规则草案需使用专用编译入口")
    response.headers["Cache-Control"] = "no-store"
    try:
        existing = jobs.existing_request(body.model_dump(), actor.id)
        if existing is not None:
            return {"success": True, "data": existing}
        if isinstance(body, AuthoringRequestV112):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True, typed_audit_schema=True, runtime_audit_context=True, citation_audit=True)
        elif isinstance(body, AuthoringRequestV111):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True, typed_audit_schema=True, runtime_audit_context=True)
        elif isinstance(body, AuthoringRequestV110):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True, typed_audit_schema=True)
        elif isinstance(body, AuthoringRequestV19):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True, portable_audit_patterns=True)
        elif isinstance(body, AuthoringRequestV18):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True)
        elif isinstance(body, AuthoringRequestV17):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True, direct_audit_schema=True)
        elif isinstance(body, AuthoringRequestV16):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True, bounded_audit=True)
        elif isinstance(body, AuthoringRequestV15):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True)
        elif isinstance(body, AuthoringRequestV14):
            selected_model = model(body.package_contract, rule_plan_enabled=True, confirm_frozen_text=True)
        elif isinstance(body, AuthoringRequestV13):
            selected_model = model(body.package_contract, rule_plan_enabled=True)
        else:
            selected_model = model(body.package_contract) if isinstance(body, AuthoringRequestV12) else model()
        return {"success": True, "data": await submit_authoring_job(jobs, sources(), selected_model, body, actor.id)}
    except (AuthoringJobError, AuthoringModelError, AuthoringSourceError, SourceBundleError) as exc:
        _fail(exc)


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request, response: Response,
                  jobs: AuthoringJobStore = Depends(authoring_store)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": jobs.get(_identifier(job_id))}
    except AuthoringJobError as exc:
        _fail(exc)


async def _control(action: str, job_id: str, request: Request, response: Response, jobs: AuthoringJobStore) -> dict:
    get_current_admin_user_from_request(request)
    identifier = _identifier(job_id)
    body = await _body(request, RevisionRequest, 1024)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": getattr(jobs, action)(identifier, body.expected_revision)}
    except AuthoringJobError as exc:
        _fail(exc)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, response: Response,
                     jobs: AuthoringJobStore = Depends(authoring_store)) -> dict:
    return await _control("cancel", job_id, request, response, jobs)


@router.post("/{job_id}/recover")
async def recover_job(job_id: str, request: Request, response: Response,
                      jobs: AuthoringJobStore = Depends(authoring_store)) -> dict:
    return await _control("recover", job_id, request, response, jobs)
