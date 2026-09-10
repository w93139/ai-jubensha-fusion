"""Bounded, read-only rule/source comparison. No semantic approval or repair."""
from __future__ import annotations

from copy import deepcopy

from src.fusion.authoring_sources import AuthoringSourceError, locate_text
from src.fusion.audit_runtime import investigation_runtime_facts
from src.fusion.package_validation import content_hash
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.script_package import SourceReference, parse_script_package


MAX_SOURCE_BYTES = 512 * 1024
MAX_TOTAL_TEXT_BYTES = 2 * 1024 * 1024
MAX_EXCERPT_CHARS = 2000
MAX_ROW_SOURCES = 3
MAX_ROW_NOTICES = 100


class RuleReviewError(ValueError):
    """Fixed, public-safe errors only."""


def build_rule_review(document: dict, bundle_hash: str, store: SourceBundleStore,
                      *, offset: int = 0, limit: int = 20) -> dict:
    if (type(offset) is not int or not 0 <= offset <= 15100
            or type(limit) is not int or not 1 <= limit <= 50):
        raise RuleReviewError("RULE_REVIEW_PAGE_INVALID")
    if document.get("schema_version") != "script-package/1.2":
        raise RuleReviewError("RULE_REVIEW_CONTRACT_UNSUPPORTED")
    # Validate all frozen files and the candidate binding, without writing a
    # verification report. Never trust a caller-supplied 'verified' flag.
    verification = store.verify(bundle_hash, document=document, persist=False)
    if not verification["valid"] or verification["issues"] or verification["issues_truncated"]:
        raise RuleReviewError("RULE_REVIEW_SOURCE_INVALID")
    manifest = store.manifest(bundle_hash)
    sources = {source.id: source for source in manifest.sources}
    # Display defaults already defined by the contract (e.g. absent []), while
    # retaining the exact original package hash and never altering its bytes.
    parsed = parse_script_package(document).model_dump()
    actions = {action["id"]: action for action in parsed["mechanics"]["actions"]}
    entities = [("mechanics.phase_budgets", item["phase_id"], item)
                for item in parsed["mechanics"]["phase_budgets"]]
    entities.extend(("mechanics.actions", item["id"], item) for item in actions.values())
    entities.extend((collection, item["id"], item)
                    for collection in ("knowledge", "evidence") for item in parsed[collection])
    entities.append(("settlement", None, parsed["settlement"]["instructions"]))
    materials: dict[str, str] = {}
    total_bytes = 0

    def excerpt(reference: dict) -> dict:
        nonlocal total_bytes
        source = sources[reference["source_id"]]
        result = {"source_id": source.id, "anchor": reference.get("anchor"), "page": reference.get("page"),
                  "kind": source.kind, "text": None, "status": "NON_TEXT", "truncated": False}
        if source.media_type not in {"text/plain", "text/markdown"}:
            return result
        if source.id not in materials:
            if source.size_bytes > MAX_SOURCE_BYTES or total_bytes + source.size_bytes > MAX_TOTAL_TEXT_BYTES:
                return result | {"status": "TEXT_LIMIT"}
            # read_source rechecks the digest: no stale bytes after verify().
            data, media_type = store.read_source(bundle_hash, source.id)
            if media_type != source.media_type or len(data) != source.size_bytes:
                raise RuleReviewError("RULE_REVIEW_SOURCE_INVALID")
            total_bytes += len(data)
            try:
                materials[source.id] = data.decode("utf-8-sig")
            except UnicodeError:
                return result | {"status": "TEXT_UNAVAILABLE"}
        try:
            text = locate_text(SourceReference.model_validate(reference), materials)
        except (AuthoringSourceError, ValueError):
            return result | {"status": "LOCATION_UNAVAILABLE"}
        return result | {"status": "AVAILABLE", "text": text[:MAX_EXCERPT_CHARS],
                         "truncated": len(text) > MAX_EXCERPT_CHARS}

    rows = []
    for collection, identifier, item in entities[offset:offset + limit]:
        notices = []
        if collection == "settlement":
            rules = {key: deepcopy(parsed["settlement"][key]) for key in ("phase_id", "truth_ids")}
        elif collection in {"knowledge", "evidence"}:
            release = item["release"]
            for action_id in release.get("required_action_ids", []):
                already_required = set(actions[action_id]["required_public_evidence_ids"])
                for evidence_id in release["required_public_evidence_ids"]:
                    if evidence_id in already_required:
                        notices.append({"code": "PUBLIC_PREREQUISITE_ALREADY_REQUIRED_BY_ACTION",
                                        "action_id": action_id, "evidence_id": evidence_id})
            rules = {key: deepcopy(item[key]) for key in ("visibility", "character_id", "disclosure", "release")}
            if "kind" in item:
                rules["kind"] = item["kind"]
        else:
            rules = {key: deepcopy(value) for key, value in item.items() if key not in {"sources", "label", "id"}}
        rows.append({"target": {"collection": collection, "id": identifier},
                     "label": "本版指定结尾" if collection == "settlement" else item.get("label", identifier), "rules": rules,
                     "sources": [excerpt(ref) for ref in item["sources"][:MAX_ROW_SOURCES]],
                     "source_count": len(item["sources"]),
                     "sources_truncated": len(item["sources"]) > MAX_ROW_SOURCES,
                     "notices": notices[:MAX_ROW_NOTICES], "notice_count": len(notices),
                     "notices_truncated": len(notices) > MAX_ROW_NOTICES})
    return {"schema_version": "rule-review/1.1",
            "runtime_facts": investigation_runtime_facts(), "package_hash": content_hash(document),
            "bundle_hash": bundle_hash, "semantic_status": "UNREVIEWED", "publication_ready": False,
            "row_count": len(entities), "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < len(entities) else None, "rows": rows}
