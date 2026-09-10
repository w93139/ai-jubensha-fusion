"""Explicit administrator approval and publication of immutable candidates."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from src.api.routes.script_review_routes import _body, _identifier
from src.api.routes.source_bundle_routes import source_store
from src.core.auth_middleware import get_current_admin_user_from_request
from src.db.session import get_db_session
from src.fusion.package_import import PackageConflict, PackageNotFound
from src.fusion.script_publication import ScriptPublicationService
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.script_publication import ApprovePublicationRequest, PublishPackageRequest


router = APIRouter(prefix="/api/admin/fusion", tags=["版本确认与发布"])


def publication_service(request: Request, db: Session = Depends(get_db_session)) -> ScriptPublicationService:
    get_current_admin_user_from_request(request)
    return ScriptPublicationService(db)


def _invoke(operation):
    try:
        return {"success": True, "data": operation()}
    except PackageNotFound:
        raise HTTPException(404, "候选或发布记录不存在") from None
    except PackageConflict:
        raise HTTPException(409, "审核依据已变化或记录核验失败，请刷新后核对") from None
    except ValueError:
        raise HTTPException(409, "尚不符合确认或发布条件，请核对审核与来源") from None


@router.get("/script-packages/{version_id}/publication")
async def publication_state(version_id: str, request: Request, response: Response,
                            service: ScriptPublicationService = Depends(publication_service),
                            store: SourceBundleStore = Depends(source_store)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    version = _identifier(version_id)
    return _invoke(lambda: service.gate_state(version, store))


@router.post("/script-packages/{version_id}/approvals")
async def approve_publication(version_id: str, request: Request, response: Response,
                              service: ScriptPublicationService = Depends(publication_service),
                              store: SourceBundleStore = Depends(source_store)) -> dict:
    actor = get_current_admin_user_from_request(request)
    version = _identifier(version_id)
    body = await _body(request, ApprovePublicationRequest, 512 * 1024)
    response.headers["Cache-Control"] = "no-store"
    return _invoke(lambda: service.approve(version, body, actor.id, store))


@router.post("/script-packages/{version_id}/publish")
async def publish_package(version_id: str, request: Request, response: Response,
                         service: ScriptPublicationService = Depends(publication_service),
                         store: SourceBundleStore = Depends(source_store)) -> dict:
    actor = get_current_admin_user_from_request(request)
    version = _identifier(version_id)
    body = await _body(request, PublishPackageRequest, 16384)
    response.headers["Cache-Control"] = "no-store"
    return _invoke(lambda: service.publish(version, body, actor.id, store))
