"""Version-bound, fixed-role opening previews, never a gameplay engine.

The publisher and this service share the caller's transaction. The publisher
must lock its current release/approval fence while resolving a new binding.
Existing previews resolve their exact historical release, without upgrading
to a newer release or gaining any additional knowledge.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models.package_runtime import ScriptPackagePlaySession
from src.fusion.package_import import PackageImportService
from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.schemas.package_runtime import CreatePackageSessionRequest


RUNTIME_CONTRACT = "package-opening-preview/1.0"
SESSION_PATTERN = r"package-[0-9a-f]{32}"


class PackageRuntimeError(ValueError):
    def __init__(self, code: str, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class PublicationReader(Protocol):
    def list_releases(self) -> list[dict]: ...

    def get_release(self, release_id: int, *, require_current: bool = True) -> dict: ...


class PackageRuntimeService:
    def __init__(self, db: Session, publisher: PublicationReader | None = None, *, presentation_repair=None,
                 single_player_content: dict[str, SinglePlayerContent] | None = None,
                 single_player_required: dict[str, str] | None = None) -> None:
        self.db = db
        self.presentation_repair = presentation_repair
        self.single_player_content = dict(single_player_content or {})
        self.single_player_required = dict(single_player_required or {})
        if publisher is None:
            from src.fusion.script_publication import ScriptPublicationService
            publisher = ScriptPublicationService(db)
        self.publisher = publisher

    @staticmethod
    def _actor(owner: int) -> None:
        if type(owner) is not int or owner <= 0:
            raise PackageRuntimeError("PACKAGE_RUNTIME_IDENTITY_REQUIRED", 401)

    def list_releases(self, owner: int) -> list[dict]:
        self._actor(owner)
        try:
            rows = self.publisher.list_releases()
            return [self._catalog(self._package(row), row) for row in rows[:100]]
        except (ValueError, TypeError, KeyError):
            raise PackageRuntimeError("PACKAGE_RELEASE_UNAVAILABLE") from None

    def create(self, body: CreatePackageSessionRequest | dict, owner: int) -> dict:
        self._actor(owner)
        try:
            raw = body.model_dump() if isinstance(body, CreatePackageSessionRequest) else body
            request = CreatePackageSessionRequest.model_validate(raw).model_dump()
        except ValidationError:
            raise PackageRuntimeError("PACKAGE_SESSION_REQUEST_INVALID", 422) from None
        digest = content_hash(request)
        existing = self._existing(owner, request["idempotency_key"], digest)
        if existing is not None:
            return self._view(existing)
        # The publisher's current-release fence stays locked through the
        # caller's commit. This service performs no model calls or network I/O.
        try:
            release = self.publisher.get_release(request["release_id"], require_current=True)
        except ValueError:
            raise PackageRuntimeError("PACKAGE_RELEASE_UNAVAILABLE", 404) from None
        package = self._package(release)
        if release["id"] != request["release_id"]:
            raise PackageRuntimeError("PACKAGE_RELEASE_UNAVAILABLE")
        if request["character_id"] not in {item["id"] for item in package["characters"]}:
            raise PackageRuntimeError("PACKAGE_CHARACTER_NOT_FOUND", 404)
        identifier = "package-" + uuid4().hex
        binding = {"schema_version": RUNTIME_CONTRACT, "session_id": identifier,
                   "owner_user_id": owner, "release_id": release["id"],
                   "release_hash": release["release_hash"], "version_id": release["version_id"],
                   "package_hash": release["package_hash"], "selected_character_id": request["character_id"],
                   "request": request, "request_hash": digest}
        row = ScriptPackagePlaySession(
            session_id=identifier, owner_user_id=owner, release_id=release["id"],
            version_id=release["version_id"], package_hash=release["package_hash"],
            selected_character_id=request["character_id"], idempotency_key=request["idempotency_key"],
            request_hash=digest, binding_json=canonical_json(binding), binding_hash=content_hash(binding),
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
                result = self._view(row)
        except IntegrityError:
            existing = self._existing(owner, request["idempotency_key"], digest)
            if existing is None:
                raise PackageRuntimeError("PACKAGE_SESSION_CREATE_CONFLICT") from None
            return self._view(existing)
        return result

    def get(self, session_id: str, owner: int) -> dict:
        row, package = self.resolve_binding(session_id, owner)
        return self._opening_view(row, package)

    def image(self, session_id: str, visual_id: str, owner: int, *, source_store=None) -> tuple[bytes, str]:
        """Read only an original image explicitly granted by this owned opening."""
        from src.fusion.source_bundles import SourceBundleStore, SourceBundleError
        row, package = self.resolve_binding(session_id, owner)
        view = self._opening_view(row, package)
        if visual_id not in {item['id'] for item in view.get('visuals', [])}:
            raise PackageRuntimeError('PACKAGE_SESSION_IMAGE_NOT_FOUND', 404)
        visual = next((item for item in package.get('visuals', []) if item['id'] == visual_id), None)
        if visual is None and self.presentation_repair:
            visual = self.presentation_repair.visual(row.package_hash, visual_id)
        if visual is None:
            raise PackageRuntimeError('PACKAGE_SESSION_IMAGE_NOT_FOUND', 404)
        source = next(item for item in package['sources'] if item['id'] == visual['source_id'])
        try:
            release = self.publisher.get_release(row.release_id, require_current=False)
            binding = json.loads(row.binding_json)
            if (release['id'] != row.release_id or release['version_id'] != row.version_id
                    or release['release_hash'] != binding['release_hash'] or release['package_hash'] != row.package_hash):
                raise ValueError
            store = source_store or getattr(self.publisher, 'source_store', None) or SourceBundleStore()
            manifest = store.manifest(release['bundle_hash'])
            frozen = next((item for item in manifest.sources if item.relative_path == source['relative_path']), None)
            if (visual['exposure'] != 'WHOLE_ORIGINAL_IMAGE' or frozen is None or frozen.kind != 'original'
                    or frozen.sha256 != source['sha256'] or frozen.media_type != source['media_type']):
                raise ValueError
            data, media = store.read_source(release['bundle_hash'], frozen.id)
            if (source['kind'] != 'original' or media not in ('image/png', 'image/jpeg')
                    or media != source['media_type'] or sha256(data).hexdigest() != source['sha256']):
                raise ValueError
            return data, media
        except (SourceBundleError, ValueError, TypeError, KeyError, OSError):
            raise PackageRuntimeError('PACKAGE_SESSION_IMAGE_UNAVAILABLE', 409) from None

    def resolve_binding(self, session_id: str, owner: int) -> tuple[ScriptPackagePlaySession, dict]:
        """Resolve an owned historical binding for internal version-bound services."""
        self._actor(owner)
        if type(session_id) is not str or re.fullmatch(SESSION_PATTERN, session_id) is None:
            raise PackageRuntimeError("PACKAGE_SESSION_NOT_FOUND", 404)
        row = self.db.query(ScriptPackagePlaySession).filter_by(
            session_id=session_id, owner_user_id=owner,
        ).populate_existing().one_or_none()
        if row is None:
            raise PackageRuntimeError("PACKAGE_SESSION_NOT_FOUND", 404)
        return row, self._resolve(row)

    def _existing(self, owner: int, key: str, digest: str) -> ScriptPackagePlaySession | None:
        row = self.db.query(ScriptPackagePlaySession).filter_by(
            owner_user_id=owner, idempotency_key=key,
        ).populate_existing().one_or_none()
        if row is not None and row.request_hash != digest:
            raise PackageRuntimeError("PACKAGE_SESSION_KEY_CONFLICT")
        return row

    def _package(self, release: dict) -> dict:
        try:
            for key in ("id", "version_id"):
                if type(release[key]) is not int or release[key] <= 0:
                    raise ValueError
            for key in ("package_hash", "release_hash"):
                if type(release[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", release[key]) is None:
                    raise ValueError
            version = PackageImportService(self.db).get_version(release["version_id"])
            package = version["package"]
            if version["package_hash"] != release["package_hash"] or not validate_package(package)["valid"]:
                raise ValueError
            return package
        except (ValueError, TypeError, KeyError):
            raise PackageRuntimeError("PACKAGE_RUNTIME_SNAPSHOT_INVALID") from None

    @staticmethod
    def _catalog(package: dict, release: dict) -> dict:
        return {"id": release["id"], "version_id": release["version_id"], "title": package["title"],
                "content_version": package["content_version"], "player_count": package["player_count"],
                "characters": [{"id": item["id"], "name": item["name"]} for item in package["characters"]],
                "runtime_ready": False, "status": "READING_PREVIEW"}

    def _view(self, row: ScriptPackagePlaySession) -> dict:
        return self._opening_view(row, self._resolve(row))

    def _resolve(self, row: ScriptPackagePlaySession) -> dict:
        try:
            binding = json.loads(row.binding_json)
            if content_hash(binding) != row.binding_hash or binding["schema_version"] != RUNTIME_CONTRACT:
                raise ValueError
            request = CreatePackageSessionRequest.model_validate(binding["request"]).model_dump()
            expected = {"schema_version": RUNTIME_CONTRACT, "session_id": row.session_id,
                        "owner_user_id": row.owner_user_id, "release_id": row.release_id,
                        "release_hash": binding["release_hash"], "version_id": row.version_id,
                        "package_hash": row.package_hash, "selected_character_id": row.selected_character_id,
                        "request": request, "request_hash": row.request_hash}
            if (binding != expected or content_hash(request) != row.request_hash
                    or request != {"release_id": row.release_id, "character_id": row.selected_character_id,
                                   "idempotency_key": row.idempotency_key}
                    or re.fullmatch(SESSION_PATTERN, row.session_id) is None):
                raise ValueError
            release = self.publisher.get_release(row.release_id, require_current=False)
            if any(release[key] != binding[key] for key in ("version_id", "package_hash", "release_hash")):
                raise ValueError
            if release["id"] != row.release_id:
                raise ValueError
            package = self._package(release)
            if row.selected_character_id not in {item["id"] for item in package["characters"]}:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise PackageRuntimeError("PACKAGE_RUNTIME_SNAPSHOT_INVALID") from None
        return package

    def _opening_view(self, row: ScriptPackagePlaySession, package: dict) -> dict:
        single = self.single_player_content.get(row.package_hash)
        if (single is not None and (single.package_hash != row.package_hash
                                   or single.character_id != row.selected_character_id)):
            single = None
        if single is None and self.single_player_required.get(row.package_hash) == row.selected_character_id:
            raise PackageRuntimeError("PACKAGE_OPENING_SINGLE_PLAYER_UNAVAILABLE")
        initial = next(item for item in package["phases"] if item["id"] == package["initial_phase_id"])
        result = {"session_id": row.session_id, "release_id": row.release_id, "version_id": row.version_id,
                  "package_hash": row.package_hash, "selected_character_id": row.selected_character_id,
                  "runtime_ready": False, "status": "READING_PREVIEW", "available_actions": [],
                  "script": {key: package[key] for key in ("title", "content_version", "player_count")},
                  "characters": [{"id": item["id"], "name": item["name"]} for item in package["characters"]],
                  "introduction": {"text": package["introduction"]["text"]},
                  "initial_phase": {"id": initial["id"], "title": initial["title"]}}
        if package["schema_version"] in ("script-package/1.2", "script-package/1.3", 'script-package/1.4'):
            result["supports_rules_preview"] = False
        for collection in ("knowledge", "evidence"):
            public, private = [], []
            for item in package[collection]:
                if (item["release"]["phase_id"] != initial["id"]
                        or item["release"].get("required_public_evidence_ids")
                        or item["release"].get("required_action_ids")):
                    continue
                safe = {key: item[key] for key in ("id", "text", "disclosure")}
                if collection == "knowledge":
                    safe["kind"] = item["kind"]
                if item["visibility"] == "PUBLIC" and item["character_id"] is None:
                    public.append(safe)
                elif item["visibility"] == "CHARACTER_PRIVATE" and item["character_id"] == row.selected_character_id:
                    private.append(safe)
            result["public_" + collection] = public
            result["private_" + collection] = private
        if 'visuals' in package:
            visible = {(collection, item['id']) for collection in ('knowledge', 'evidence')
                       for visibility in ('public_', 'private_') for item in result[visibility + collection]}
            result['visuals'] = [{key: item[key] for key in ('id', 'collection', 'material_id', 'label')}
                                 for item in package['visuals']
                                 if (item['collection'], item['material_id']) in visible]
        result = self.presentation_repair.apply(result) if self.presentation_repair else result
        if single is not None:
            result = single.replace_rules(result)
            if 'visuals' in result:
                # Replaced operation instructions must not link back to an old
                # multiplayer rule image. Other authorized images stay intact.
                result['visuals'] = [visual for visual in result['visuals']
                                     if (visual['collection'], visual['material_id']) not in single.replaces]
        return result
