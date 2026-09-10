"""Read-only source browsing and deterministic verification for active admins."""
import asyncio
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from src.core.auth_middleware import get_current_admin_user_from_request
from src.api.routes.script_package_routes import import_service
from src.fusion.package_import import PackageConflict, PackageImportService, PackageNotFound
from src.fusion.package_validation import parse_package_json
from src.fusion.source_bundles import SourceBundleError, SourceBundleNotFound, SourceBundleStore
from src.schemas.source_bundle import VerifyCandidateSourcesRequest


router = APIRouter(prefix="/api/admin/fusion", tags=["私有来源核验"])


def source_store(request: Request) -> SourceBundleStore:
    get_current_admin_user_from_request(request)
    try:
        return SourceBundleStore()
    except SourceBundleError as exc:
        fail(exc)


def fail(exc: Exception) -> NoReturn:
    code = 404 if isinstance(exc, (SourceBundleNotFound, PackageNotFound)) else 409
    raise HTTPException(status_code=code, detail=str(exc)) from None


@router.get("/source-bundles")
async def list_sources(request: Request, response: Response, store: SourceBundleStore = Depends(source_store)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": await asyncio.to_thread(store.list_bundles)}
    except SourceBundleError as exc:
        fail(exc)


@router.get("/source-bundles/{bundle_hash}")
async def get_bundle(bundle_hash: str, request: Request, response: Response, store: SourceBundleStore = Depends(source_store)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": await asyncio.to_thread(store.describe, bundle_hash)}
    except SourceBundleError as exc:
        fail(exc)


@router.get("/source-bundles/{bundle_hash}/sources/{source_id}")
async def read_source(bundle_hash: str, source_id: str, request: Request, store: SourceBundleStore = Depends(source_store)) -> Response:
    get_current_admin_user_from_request(request)
    try:
        data, media = await asyncio.to_thread(store.read_source, bundle_hash, source_id)
    except SourceBundleError as exc:
        fail(exc)
    # Text is inert, including Markdown/JSON. Never render source HTML or URLs.
    return Response(content=data, media_type=media if media.startswith("image/") else "text/plain",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; sandbox", "X-Frame-Options": "DENY"})


@router.post("/source-bundles/{bundle_hash}/verify")
async def verify_bundle(bundle_hash: str, request: Request, store: SourceBundleStore = Depends(source_store)) -> dict:
    get_current_admin_user_from_request(request)
    try:
        return {"success": True, "data": await asyncio.to_thread(store.verify, bundle_hash)}
    except SourceBundleError as exc:
        fail(exc)


@router.get("/source-verifications/{report_hash}")
async def get_verification(report_hash: str, request: Request, response: Response, store: SourceBundleStore = Depends(source_store)) -> dict:
    get_current_admin_user_from_request(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"success": True, "data": await asyncio.to_thread(store.get_report, report_hash)}
    except SourceBundleError as exc:
        fail(exc)


@router.post("/script-packages/{version_id}/verify-sources")
async def verify_candidate(version_id: int, request: Request, store: SourceBundleStore = Depends(source_store),
                           service: PackageImportService = Depends(import_service)) -> dict:
    get_current_admin_user_from_request(request)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 4096:
            raise HTTPException(status_code=413, detail="来源核验请求过长")
        raw.extend(chunk)
    try:
        body = VerifyCandidateSourcesRequest.model_validate(parse_package_json(bytes(raw)))
    except (ValueError, ValidationError):
        raise HTTPException(status_code=422, detail="需要有效的来源快照标识") from None
    try:
        document = service.get_version(version_id)["package"]
        # No Session is passed to the filesystem worker.
        result = await asyncio.to_thread(store.verify, body.bundle_hash, document=document)
        return {"success": True, "data": result}
    except (SourceBundleError, PackageConflict, PackageNotFound) as exc:
        fail(exc)
