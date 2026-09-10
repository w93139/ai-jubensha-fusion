"""Administrator-only candidate intake. No publishing or runtime activation."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.core.auth_middleware import get_current_admin_user_from_request
from src.db.session import get_db_session
from src.fusion.package_import import PackageConflict, PackageImportService, PackageNotFound
from src.fusion.package_validation import MAX_PACKAGE_BYTES, PackageInputError, parse_package_json
from src.schemas.script_package import ImportPackageRequest


router = APIRouter(prefix="/api/admin/fusion", tags=["剧本候选包"])


def import_service(request: Request, db: Session = Depends(get_db_session)) -> PackageImportService:
    get_current_admin_user_from_request(request)
    return PackageImportService(db)


@router.post("/script-imports")
async def submit_package(request: Request, service: PackageImportService = Depends(import_service)) -> dict:
    user = get_current_admin_user_from_request(request)
    # Read bounded chunks and validate manually. The legacy global validation
    # handler logs/echoes input values; private candidate bodies must not reach it.
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_PACKAGE_BYTES:
            raise HTTPException(status_code=413, detail="请求超过 2 MiB 限制")
        raw.extend(chunk)
    try:
        body = ImportPackageRequest.model_validate(parse_package_json(bytes(raw)))
        result = service.submit(body.package, submitted_by=user.id, idempotency_key=body.idempotency_key)
    except ValidationError:
        raise HTTPException(status_code=422, detail="需要有效的导入幂等键和 JSON 对象候选包") from None
    except PackageInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except PackageConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"success": True, "data": result}


@router.get("/script-imports/{job_id}")
async def get_import_job(job_id: int, request: Request, service: PackageImportService = Depends(import_service)) -> dict:
    get_current_admin_user_from_request(request)
    try:
        return {"success": True, "data": service.get_job(job_id)}
    except PackageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except PackageConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/script-packages/{version_id}")
async def get_candidate_version(version_id: int, request: Request, response: Response, service: PackageImportService = Depends(import_service)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": service.get_version(version_id)}
    except PackageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except PackageConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
