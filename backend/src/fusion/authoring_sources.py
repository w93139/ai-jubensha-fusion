"""Select verified text explicitly; no retrieval, OCR, inference, or model I/O."""
from __future__ import annotations

import re
from typing import Any

from src.fusion.source_bundles import SourceBundleError, SourceBundleStore
from src.schemas.authoring import AuthoringRequest, AuthoringRequestV12
from src.schemas.script_package import SourceFileV11, SourceReference


MAX_SOURCE_TEXT_BYTES = 16 * 1024
MAX_MATERIAL_TEXT_BYTES = 48 * 1024
TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown"})


class AuthoringSourceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def locate_text(reference: SourceReference, materials: dict[str, str]) -> str:
    """Return the actual cited lines/heading section, rejecting unseen assets."""
    text = materials.get(reference.source_id)
    if text is None or reference.page is not None or reference.anchor is None:
        raise AuthoringSourceError("SOURCE_LOCATION_NOT_AUTHORIZED")
    lines = text.splitlines()
    raw_lines = text.splitlines(keepends=True)
    match = re.fullmatch(r"L([1-9][0-9]*)(?:-L([1-9][0-9]*))?", reference.anchor)
    if match:
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= len(lines):
            raise AuthoringSourceError("SOURCE_LOCATION_NOT_AUTHORIZED")
        return "".join(raw_lines[start - 1:end])
    headings = [(i, len(line) - len(line.lstrip("#"))) for i, line in enumerate(lines)
                if re.match(r"^#{1,6}\s", line) and line.lstrip("#").strip() == reference.anchor]
    # A repeated heading is ambiguous. Require line locators instead.
    if len(headings) != 1:
        raise AuthoringSourceError("SOURCE_LOCATION_NOT_AUTHORIZED")
    start, level = headings[0]
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^#{1,6}\s", lines[i])
                and len(lines[i]) - len(lines[i].lstrip("#")) <= level), len(lines))
    return "".join(raw_lines[start:end])


def prepare_authoring_sources(store: SourceBundleStore, request: AuthoringRequest | AuthoringRequestV12) -> dict[str, Any]:
    """Read selected text only; attached originals contribute metadata only."""
    try:
        manifest = store.manifest(request.bundle_hash)
        by_id = {source.id: source for source in manifest.sources}
        by_path = {source.relative_path: source for source in manifest.sources}
        selected = [by_id.get(source_id) for source_id in request.source_ids]
        if any(source is None for source in selected):
            raise AuthoringSourceError("SOURCE_NOT_IN_BUNDLE")
        declarations: dict[str, dict] = {}
        materials = []
        total = 0

        def declare(source: Any) -> None:
            originals = []
            if source.kind in ("ocr", "revised"):
                if not source.original_paths:
                    raise AuthoringSourceError("ORIGINAL_RELATION_MISSING")
                for path in source.original_paths:
                    original = by_path.get(path)
                    if original is None or original.kind != "original":
                        raise AuthoringSourceError("ORIGINAL_RELATION_INVALID")
                    declare(original)
                    originals.append(original.id)
            item = {"id": source.id, "relative_path": source.relative_path, "sha256": source.sha256,
                    "media_type": source.media_type, "kind": "normalized" if originals else source.kind,
                    "original_source_ids": originals}
            if source.kind == "supplement":
                note = "编辑补充；仅声明冻结来源，不代表原件恢复或人工批准。\n" + "\n".join(manifest.notes)
                if not manifest.notes or len(note) > 2000:
                    raise AuthoringSourceError("SUPPLEMENT_PROVENANCE_UNAVAILABLE")
                item["provenance_note"] = note
            if source.media_type.startswith("image/"):
                item["page_count"] = 1
            try:
                declarations[source.id] = SourceFileV11.model_validate(item).model_dump(exclude_none=True)
            except ValueError:
                raise AuthoringSourceError("SOURCE_TYPE_UNSUPPORTED") from None

        for source in selected:
            if source.media_type not in TEXT_MEDIA_TYPES or source.kind not in {"ocr", "revised", "original", "supplement"}:
                raise AuthoringSourceError("SOURCE_TYPE_UNSUPPORTED")
            if source.size_bytes > MAX_SOURCE_TEXT_BYTES:
                raise AuthoringSourceError("SOURCE_TEXT_TOO_LARGE")
            declare(source)
            data, media_type = store.read_source(request.bundle_hash, source.id)
            if media_type != source.media_type or len(data) > MAX_SOURCE_TEXT_BYTES:
                raise AuthoringSourceError("SOURCE_TEXT_TOO_LARGE")
            total += len(data)
            if total > MAX_MATERIAL_TEXT_BYTES:
                raise AuthoringSourceError("MATERIAL_TEXT_TOO_LARGE")
            text = data.decode("utf-8-sig")
            if not text.strip():
                raise AuthoringSourceError("SOURCE_TEXT_EMPTY")
            materials.append({"source_id": source.id, "text": text})
        context = {"script_key": manifest.script_key, "bundle_hash": request.bundle_hash,
                "source_ids": list(request.source_ids), "title": request.title,
                "content_version": request.content_version, "player_count": request.player_count,
                "sources": list(declarations.values()), "materials": materials,
                "notes": list(manifest.notes)}
        if isinstance(request, AuthoringRequestV12):
            context["package_contract"] = request.package_contract
        if "rule_plan" in request.model_fields_set:
            context["rule_plan"] = request.model_dump()["rule_plan"]
        if "compiler_mode" in request.model_fields_set:
            context["compiler_mode"] = request.compiler_mode
        if "audit_mode" in request.model_fields_set:
            context["audit_mode"] = request.audit_mode
        return context
    except (SourceBundleError, OSError, UnicodeError):
        raise AuthoringSourceError("SOURCE_INTEGRITY_FAILED") from None
