"""Only generated files and fictional materials; no commercial source reads."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from src.api.routes import source_bundle_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.fusion.package_validation import content_hash
from src.fusion.source_bundles import VERIFIER_VERSION, SourceBundleError, SourceBundleStore, media_type, read_beneath
from tests.fusion_security.test_script_packages import fictional_package


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "original.md").write_text("# fixture-opening\nSynthetic original\n")
    (root / "normalized.txt").write_text("# fixture-opening\nPRIVATE_SOURCE_SENTINEL\n")
    image = io.BytesIO()
    Image.new("RGB", (4, 4), (12, 30, 90)).save(image, format="JPEG")
    (root / "original.jpg").write_bytes(image.getvalue())
    plan = {"schema_version": "source-plan/1.0", "script_key": "fictional-gallery", "edition": "fixture-1",
            "notes": ["Synthetic fixture; image and OCR semantics unreviewed"], "sources": [
                {"relative_path": "original.md", "kind": "original", "material_type": "host"},
                {"relative_path": "normalized.txt", "kind": "ocr", "material_type": "host", "original_paths": ["original.md"]},
                {"relative_path": "original.jpg", "kind": "original", "material_type": "clue"},
            ]}
    store = SourceBundleStore(tmp_path / "private-store")
    frozen = store.freeze(root, plan)
    return root, plan, store, frozen


def candidate_for(bundle):
    _, _, _, frozen = bundle
    entries = {item["relative_path"]: item for item in frozen["sources"]}
    document = fictional_package()
    document["sources"] = [
        {"id": "original", "relative_path": "original.md", "sha256": entries["original.md"]["sha256"],
         "kind": "original", "media_type": "text/markdown"},
        {"id": "normalized", "relative_path": "normalized.txt", "sha256": entries["normalized.txt"]["sha256"],
         "kind": "normalized", "media_type": "text/plain", "original_source_id": "original"},
    ]
    return document


def candidate_v11_for(bundle):
    document = candidate_for(bundle)
    document["schema_version"] = "script-package/1.1"
    for item in document["sources"]:
        original = item.pop("original_source_id", None)
        item["original_source_ids"] = [original] if original is not None else []
    return document


def test_freeze_is_private_deduplicated_and_restart_readable(bundle):
    root, plan, store, frozen = bundle
    assert store.freeze(root, plan) == frozen
    assert len(store.list_bundles()) == 1
    assert frozen["file_count"] == 3 and not frozen["publication_ready"]
    assert frozen["audience"] == "AUTHORING_ONLY"
    assert store.root.stat().st_mode & 0o777 == 0o700
    directory = store.root / frozen["bundle_hash"]
    for path in directory.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o777 == 0o600
    restored = SourceBundleStore(store.root)
    assert restored.describe(frozen["bundle_hash"]) == frozen
    report = restored.verify(frozen["bundle_hash"])
    assert report["valid"] and report["checked_files"] == 3
    assert restored.get_report(report["report_hash"]) == report
    assert "PRIVATE_SOURCE_SENTINEL" not in json.dumps(report)


def test_changed_input_produces_new_snapshot_without_changing_old(bundle):
    root, plan, store, original = bundle
    (root / "normalized.txt").write_text("# fixture-opening\nUpdated\n")
    changed = store.freeze(root, plan)
    assert changed["bundle_hash"] != original["bundle_hash"]
    entry = next(item for item in original["sources"] if item["relative_path"] == "normalized.txt")
    assert b"PRIVATE_SOURCE_SENTINEL" in store.read_source(original["bundle_hash"], entry["id"])[0]


@pytest.mark.parametrize("mutation", ["missing", "changed", "symlink"])
def test_invalid_snapshot_is_blocked_and_never_overwritten(bundle, mutation, tmp_path):
    root, plan, store, frozen = bundle
    target = store.root / frozen["bundle_hash"] / "files/normalized.txt"
    if mutation == "changed":
        target.write_text("CORRUPTED_PRIVATE_CONTENT")
    else:
        target.unlink()
        if mutation == "symlink":
            external = tmp_path / "external.txt"
            external.write_text("EXTERNAL_SECRET")
            target.symlink_to(external)
    report = store.verify(frozen["bundle_hash"])
    assert not report["valid"]
    assert {item["code"] for item in report["issues"]} == {"SOURCE_FILE_INVALID"}
    assert "PRIVATE_CONTENT" not in json.dumps(report) and "EXTERNAL_SECRET" not in json.dumps(report)
    with pytest.raises(SourceBundleError):
        store.freeze(root, plan)
    assert not list(store.root.glob(".intake-*"))


def test_corrupt_manifest_and_report_are_detected(bundle):
    _, _, store, frozen = bundle
    report = store.verify(frozen["bundle_hash"])
    (store.root / "verification-reports" / (report["report_hash"] + ".json")).write_text("{}")
    with pytest.raises(SourceBundleError):
        store.get_report(report["report_hash"])
    (store.root / frozen["bundle_hash"] / "manifest.json").write_text("{}")
    with pytest.raises(SourceBundleError):
        store.describe(frozen["bundle_hash"])
    assert store.list_bundles() == [{"bundle_hash": frozen["bundle_hash"], "status": "CORRUPT"}]


def test_source_listing_handles_missing_and_non_directory_store(tmp_path):
    root = tmp_path / "source-store"
    store = SourceBundleStore(root)
    assert store.list_bundles() == []
    root.write_text("PRIVATE_STORAGE_SENTINEL")
    with pytest.raises(SourceBundleError, match="^来源存储目录无法读取$"):
        store.list_bundles()


def test_source_listing_permission_failure_is_sanitized(bundle, monkeypatch):
    _, _, store, _ = bundle
    monkeypatch.setattr(Path, "iterdir", Mock(side_effect=PermissionError("PRIVATE_STORAGE_PATH")))
    with pytest.raises(SourceBundleError, match="^来源存储目录无法读取$"):
        store.list_bundles()


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../secret", "a//secret", "a\\secret", "a%2fsecret"])
def test_no_path_traversal_reads(tmp_path, path):
    with pytest.raises(SourceBundleError):
        read_beneath(tmp_path, path)


def test_nested_directory_symlink_and_fifo_are_rejected(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret").write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "linked").symlink_to(external, target_is_directory=True)
    with pytest.raises(SourceBundleError):
        read_beneath(root, "linked/secret")
    import os
    os.mkfifo(root / "pipe.txt")
    with pytest.raises(SourceBundleError):
        read_beneath(root, "pipe.txt")


@pytest.mark.parametrize("path,data", [("bad.jpg", b"not an image"), ("bad.png", b"not an image"),
                                      ("bad.py", b"print('never run')"), ("bad.txt", b"abc\x00def"),
                                      ("bad.json", b'{"x":1,"x":2}')])
def test_real_file_type_validation_rejects_invalid_content(path, data):
    with pytest.raises(SourceBundleError):
        media_type(path, data)


def test_missing_source_relation_and_invalid_plan_leave_no_snapshot(bundle):
    root, plan, store, _ = bundle
    invalid = deepcopy(plan)
    invalid["sources"][1]["original_paths"] = ["missing.jpg"]
    with pytest.raises(SourceBundleError):
        store.freeze(root, invalid)
    invalid = deepcopy(plan)
    invalid["sources"].append(invalid["sources"][0])
    with pytest.raises(SourceBundleError):
        store.freeze(root, invalid)
    assert len(store.list_bundles()) == 1


def test_failed_freeze_cleans_only_its_own_staging_directory(bundle):
    root, plan, store, _ = bundle
    preserved = store.root / ".intake-from-interrupted-other-process"
    preserved.mkdir()
    (preserved / "unique").write_text("keep")
    (root / "normalized.txt").write_bytes(b"bad\x00text")
    with pytest.raises(SourceBundleError):
        store.freeze(root, plan)
    assert (preserved / "unique").read_text() == "keep"
    assert list(store.root.glob(".intake-*")) == [preserved]


def test_verification_never_writes_through_a_symlink_directory(bundle, tmp_path):
    _, _, store, frozen = bundle
    external = tmp_path / "unrelated-records"
    external.mkdir()
    (store.root / "verification-reports").symlink_to(external, target_is_directory=True)
    with pytest.raises(SourceBundleError):
        store.verify(frozen["bundle_hash"])
    assert list(external.iterdir()) == []


def test_oversized_source_and_repo_storage_are_rejected(tmp_path, monkeypatch):
    import src.fusion.source_bundles as module
    target = tmp_path / "large.txt"
    target.write_text("12345")
    with pytest.raises(SourceBundleError):
        read_beneath(tmp_path, "large.txt", limit=4)
    with pytest.raises(SourceBundleError):
        SourceBundleStore(module.REPOSITORY / "static/private-sources")


def test_source_read_by_id_cannot_escape_bundle(bundle):
    _, _, store, frozen = bundle
    with pytest.raises(SourceBundleError):
        store.read_source(frozen["bundle_hash"], "../secret")
    with pytest.raises(SourceBundleError):
        store.describe("../secret")


def test_candidate_sources_bind_package_bundle_and_report_hashes(bundle):
    _, _, store, frozen = bundle
    document = candidate_for(bundle)
    report = store.verify(frozen["bundle_hash"], document=document)
    assert report["valid"] and report["package_hash"] == content_hash(document)
    assert report["bundle_hash"] == frozen["bundle_hash"] and not report["publication_ready"]


@pytest.mark.parametrize("original_paths,code", [
    ([], "ORIGINAL_LINK_MISMATCH"),
    (["original.md", "original.jpg"], "MULTI_ORIGINAL_PROVENANCE_UNSUPPORTED"),
])
def test_v1_candidate_requires_exactly_one_original_per_normalized_source(bundle, original_paths, code):
    root, plan, store, _ = bundle
    plan["sources"][1]["kind"] = "revised"
    plan["sources"][1]["original_paths"] = original_paths
    frozen = store.freeze(root, plan)
    document = candidate_for((root, plan, store, frozen))
    report = store.verify(frozen["bundle_hash"], document=document)
    assert not report["valid"]
    assert {issue["code"] for issue in report["issues"]} == {code}
    assert report["issues"][0]["source_id"] == "normalized"


@pytest.mark.parametrize("original_ids", [["original", "extra-original"], ["extra-original", "original"]])
def test_v11_candidate_preserves_all_originals_in_either_declaration_order(bundle, original_ids):
    root, plan, store, _ = bundle
    plan["sources"][1]["original_paths"] = ["original.md", "original.jpg"]
    frozen = store.freeze(root, plan)
    document = candidate_v11_for((root, plan, store, frozen))
    extra = next(item for item in frozen["sources"] if item["relative_path"] == "original.jpg")
    document["sources"].append({"id": "extra-original", "relative_path": "original.jpg",
                                "sha256": extra["sha256"], "media_type": "image/jpeg",
                                "kind": "original", "original_source_ids": []})
    document["sources"][1]["original_source_ids"] = original_ids
    report = store.verify(frozen["bundle_hash"], document=document)
    assert report["valid"] and report["package_hash"] == content_hash(document)
    assert report["verifier_version"] == VERIFIER_VERSION == "source-verifier/1.1"
    assert not report["publication_ready"]


@pytest.mark.parametrize("case", ["missing", "extra", "unlinked"])
def test_v11_candidate_original_set_must_exactly_match_frozen_manifest(bundle, case):
    root, plan, store, _ = bundle
    if case == "missing":
        plan["sources"][1]["original_paths"] = ["original.md", "original.jpg"]
    elif case == "unlinked":
        plan["sources"][1].update(kind="revised", original_paths=[])
    frozen = store.freeze(root, plan)
    document = candidate_v11_for((root, plan, store, frozen))
    if case == "extra":
        extra = next(item for item in frozen["sources"] if item["relative_path"] == "original.jpg")
        document["sources"].append({"id": "extra-original", "relative_path": "original.jpg",
                                    "sha256": extra["sha256"], "media_type": "image/jpeg",
                                    "kind": "original", "original_source_ids": []})
        document["sources"][1]["original_source_ids"].append("extra-original")
    report = store.verify(frozen["bundle_hash"], document=document)
    assert not report["valid"]
    assert {item["code"] for item in report["issues"]} == {"ORIGINAL_LINK_MISMATCH"}


def test_v11_editorial_supplement_is_verifiable_but_cannot_masquerade_as_original(bundle):
    root, plan, store, _ = bundle
    (root / "editorial.md").write_text("# Editorial note\nSynthetic supplement.\n")
    plan["sources"].append({"relative_path": "editorial.md", "kind": "supplement", "material_type": "clue"})
    frozen = store.freeze(root, plan)
    document = candidate_v11_for((root, plan, store, frozen))
    extra = next(item for item in frozen["sources"] if item["relative_path"] == "editorial.md")
    document["sources"].append({"id": "editorial", "relative_path": "editorial.md", "sha256": extra["sha256"],
                                "media_type": "text/markdown", "kind": "supplement", "original_source_ids": [],
                                "provenance_note": "Synthetic editorial supplement, not recovered original."})
    document["evidence"][0]["sources"] = [{"source_id": "editorial", "anchor": "L1-L2"}]
    report = store.verify(frozen["bundle_hash"], document=document)
    assert report["valid"] and not report["publication_ready"]
    document["sources"][-1]["kind"] = "original"
    document["sources"][-1].pop("provenance_note")
    report = store.verify(frozen["bundle_hash"], document=document)
    assert not report["valid"]
    assert {item["code"] for item in report["issues"]} == {"ORIGINAL_KIND_MISMATCH"}


@pytest.mark.parametrize("frozen_kind", ["original", "ocr", "revised", "reference"])
def test_v11_supplement_kind_must_match_the_frozen_editorial_classification(bundle, frozen_kind):
    root, plan, store, _ = bundle
    (root / "editorial.md").write_text("# Editorial note\nSynthetic supplement.\n")
    plan["sources"].append({"relative_path": "editorial.md", "kind": frozen_kind, "material_type": "clue",
                            "original_paths": ["original.md"] if frozen_kind == "ocr" else []})
    frozen = store.freeze(root, plan)
    document = candidate_v11_for((root, plan, store, frozen))
    extra = next(item for item in frozen["sources"] if item["relative_path"] == "editorial.md")
    document["sources"].append({"id": "editorial", "relative_path": "editorial.md", "sha256": extra["sha256"],
                                "media_type": "text/markdown", "kind": "supplement", "original_source_ids": [],
                                "provenance_note": "Synthetic editorial supplement."})
    report = store.verify(frozen["bundle_hash"], document=document)
    assert not report["valid"]
    assert {item["code"] for item in report["issues"]} == {"SUPPLEMENT_KIND_MISMATCH"}


def test_historical_v1_source_verification_receipt_remains_readable(bundle):
    _, _, store, frozen = bundle
    report = store.verify(frozen["bundle_hash"], persist=False)
    report.pop("report_hash")
    report["verifier_version"] = "source-verifier/1.0"
    digest = content_hash(report)
    store._save_report(frozen["bundle_hash"], digest, report)
    assert store.get_report(digest) == {**report, "report_hash": digest}


@pytest.mark.parametrize("case,code", [("script", "SCRIPT_SCOPE_MISMATCH"), ("hash", "SOURCE_MANIFEST_MISMATCH"),
                                      ("path", "SOURCE_MANIFEST_MISMATCH"), ("kind", "ORIGINAL_KIND_MISMATCH"),
                                      ("location", "SOURCE_LOCATION_UNVERIFIED"), ("schema", "PACKAGE_INVALID")])
def test_candidate_source_failures(bundle, case, code):
    _, _, store, frozen = bundle
    document = candidate_for(bundle)
    if case == "script":
        document["script_key"] = "other-script"
    elif case == "hash":
        document["sources"][0]["sha256"] = "0" * 64
    elif case == "path":
        document["sources"][0]["relative_path"] = "other.txt"
    elif case == "kind":
        document["sources"][1]["kind"] = "original"
        document["sources"][1].pop("original_source_id")
    elif case == "location":
        document["introduction"]["sources"][0]["anchor"] = "L99999"
    else:
        document["approved"] = True
    report = store.verify(frozen["bundle_hash"], document=document)
    assert not report["valid"] and code in {issue["code"] for issue in report["issues"]}


def test_editorial_supplement_cannot_masquerade_as_original_in_v1(bundle):
    root, plan, store, _ = bundle
    plan["sources"][0]["kind"] = "supplement"
    plan["sources"][1]["kind"] = "reference"
    plan["sources"][1]["original_paths"] = []
    frozen = store.freeze(root, plan)
    document = candidate_for((root, plan, store, frozen))
    result = store.verify(frozen["bundle_hash"], document=document)
    assert not result["valid"]
    assert "EDITORIAL_PROVENANCE_UNSUPPORTED" in {item["code"] for item in result["issues"]}


def test_valid_line_anchor_and_unknown_source_page_handling(bundle):
    _, _, store, frozen = bundle
    document = candidate_for(bundle)
    document["introduction"]["sources"][0]["anchor"] = "L1-L2"
    assert store.verify(frozen["bundle_hash"], document=document)["valid"]
    document["introduction"]["sources"][0] = {"source_id": "normalized", "page": 1}
    assert not store.verify(frozen["bundle_hash"], document=document)["valid"]


@pytest.fixture
def source_client(bundle):
    _, _, store, frozen = bundle
    class OfflineAuth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return {"admin": SimpleNamespace(id=1, is_active=True, is_admin=True),
                    "player": SimpleNamespace(id=2, is_active=True, is_admin=False)}.get(token)

    app = FastAPI()
    app.include_router(source_bundle_routes.router)
    app.add_middleware(OfflineAuth)
    app.dependency_overrides[source_bundle_routes.source_store] = lambda: store
    service = Mock()
    service.get_version.return_value = {"package": candidate_for(bundle)}
    app.dependency_overrides[source_bundle_routes.import_service] = lambda: service
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("token,status", [(None, 401), ("player", 403)])
@pytest.mark.parametrize("method,suffix", [("GET", ""), ("GET", "/HASH"), ("POST", "/HASH/verify"),
                                         ("GET", "/HASH/sources/ID")])
def test_source_routes_require_admin(source_client, token, status, method, suffix):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    result = source_client.request(method, "/api/admin/fusion/source-bundles" + suffix, headers=headers)
    assert result.status_code == status


def test_http_preview_verify_and_candidate_verification(source_client, bundle):
    _, _, _, frozen = bundle
    headers = {"Authorization": "Bearer admin"}
    base = "/api/admin/fusion/source-bundles"
    assert source_client.get(base, headers=headers).json()["data"][0]["file_count"] == 3
    listing = source_client.get(f"{base}/{frozen['bundle_hash']}", headers=headers)
    assert listing.headers["cache-control"] == "no-store"
    source = next(item for item in frozen["sources"] if item["relative_path"] == "normalized.txt")
    preview = source_client.get(f"{base}/{frozen['bundle_hash']}/sources/{source['id']}", headers=headers)
    assert preview.headers["content-type"].startswith("text/plain")
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert "PRIVATE_SOURCE_SENTINEL" in preview.text
    result = source_client.post(f"{base}/{frozen['bundle_hash']}/verify", headers=headers).json()["data"]
    assert result["valid"]
    assert source_client.get(f"/api/admin/fusion/source-verifications/{result['report_hash']}", headers=headers).json()["data"] == result
    result = source_client.post("/api/admin/fusion/script-packages/1/verify-sources", headers=headers,
                                json={"bundle_hash": frozen["bundle_hash"]})
    assert result.status_code == 200 and result.json()["data"]["valid"]


def test_http_source_storage_error_is_sanitized(source_client, bundle):
    root, _, store, _ = bundle
    store.root = root / "original.md"
    result = source_client.get("/api/admin/fusion/source-bundles", headers={"Authorization": "Bearer admin"})
    assert result.status_code == 409
    assert result.json() == {"detail": "来源存储目录无法读取"}


def test_no_middleware_still_requires_admin_and_malformed_body_is_safe(bundle):
    _, _, store, _ = bundle
    app = FastAPI()
    app.include_router(source_bundle_routes.router)
    app.dependency_overrides[source_bundle_routes.source_store] = lambda: store
    with TestClient(app) as client:
        assert client.get("/api/admin/fusion/source-bundles").status_code == 401


def test_candidate_source_http_errors_do_not_echo_input(source_client, caplog):
    result = source_client.post("/api/admin/fusion/script-packages/1/verify-sources", headers={"Authorization": "Bearer admin"},
                                json={"bundle_hash": "PRIVATE_BAD_DIGEST", "payload": "PRIVATE_BODY"})
    assert result.status_code == 422
    assert "PRIVATE_" not in result.text + caplog.text
