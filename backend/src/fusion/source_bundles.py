"""Bounded local source snapshots and verifiable, private intake receipts.

The API receives hashes/IDs only. Source roots and selection plans are supplied
to the local CLI by the operator, never by a game character or HTTP caller.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any
from uuid import uuid4

from PIL import Image
from pydantic import ValidationError

from src.fusion.package_validation import canonical_json, content_hash, parse_package_json, validate_package
from src.schemas.script_package import CONTRACT_VERSION_V12, CONTRACT_VERSION_V13, CONTRACT_VERSION_V14, ScriptPackageV12, ScriptPackageV13, ScriptPackageV14, SourceFile, SourceFileV11, SourceReference, parse_script_package
from src.schemas.source_bundle import FrozenSource, SourceManifest, SourcePlan, SourceVerificationReport


MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
VERIFIER_VERSION = "source-verifier/1.1"
VERIFIER_VERSION_V12 = "source-verifier/1.2"
REPOSITORY = Path(__file__).resolve().parents[3]
DEFAULT_STORE = REPOSITORY.parent / "private-data/import-jobs/source-bundles"
SUFFIX_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json", ".csv": "text/csv"}


def verifier_version_for(document: Any) -> str:
    if isinstance(document, dict) and document.get('schema_version') == CONTRACT_VERSION_V14:
        return 'source-verifier/1.4'
    if isinstance(document, dict) and document.get("schema_version") == CONTRACT_VERSION_V13:
        return "source-verifier/1.3"
    return (VERIFIER_VERSION_V12 if isinstance(document, dict)
            and document.get("schema_version") == CONTRACT_VERSION_V12 else VERIFIER_VERSION)


class SourceBundleError(ValueError):
    """Only fixed safe messages; no file paths, bytes or underlying exception."""


class SourceBundleNotFound(SourceBundleError):
    pass


def source_id(relative_path: str) -> str:
    return "src-" + sha256(relative_path.encode("utf-8")).hexdigest()[:24]


def _digest_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SourceBundleError("无效的来源记录标识")
    return value


def read_beneath(root: Path, relative_path: str, limit: int = MAX_FILE_BYTES) -> bytes:
    """Walk directory FDs with O_NOFOLLOW, then read a bounded regular file."""
    descriptors = []
    try:
        SourceFile.safe_relative_path(relative_path)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        parts = relative_path.split("/")
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise SourceBundleError("来源文件类型或大小不符合限制")
        chunks = []
        size = 0
        while chunk := os.read(fd, min(1024 * 1024, limit + 1 - size)):
            size += len(chunk)
            if size > limit:
                raise SourceBundleError("来源文件超出大小限制")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SourceBundleError("读取时来源文件发生变化")
        return b"".join(chunks)
    except (OSError, ValueError) as exc:
        if isinstance(exc, SourceBundleError):
            raise
        raise SourceBundleError("来源文件缺失或不允许访问") from None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def media_type(relative_path: str, data: bytes) -> str:
    expected = SUFFIX_TYPES.get(Path(relative_path).suffix.lower())
    try:
        if expected in ("image/jpeg", "image/png"):
            with Image.open(io.BytesIO(data)) as image:
                if image.format != ("JPEG" if expected == "image/jpeg" else "PNG") or image.width * image.height > 40000000:
                    raise ValueError
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        elif expected:
            decoded = data.decode("utf-8-sig")
            if not decoded.strip() or any(ord(char) < 32 and char not in "\t\n\r" for char in decoded):
                raise ValueError
            if expected == "application/json":
                parse_package_json(data)
        else:
            raise ValueError
    except Exception:
        raise SourceBundleError("来源文件扩展名与真实内容类型不匹配或格式不支持") from None
    return expected


def _validate_relations(sources: list) -> None:
    by_path = {source.relative_path: source for source in sources}
    if len(by_path) != len(sources):
        raise SourceBundleError("来源路径重复")
    for source in sources:
        if (len(set(source.original_paths)) != len(source.original_paths)
                or any(path not in by_path or by_path[path].kind != "original" for path in source.original_paths)
                or source.kind == "original" and source.original_paths
                or source.kind == "ocr" and not source.original_paths):
            raise SourceBundleError("原件关联缺失、重复或无效")


def _private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SourceBundleStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(os.environ.get("FUSION_SOURCE_BUNDLE_DIR", str(DEFAULT_STORE)))
        # Serving source bodies from repo/static would violate the asset boundary.
        resolved = self.root.resolve()
        if resolved.is_relative_to(REPOSITORY) or self.root.is_symlink():
            raise SourceBundleError("来源存储必须位于仓库外的独立私有目录")

    def freeze(self, input_root: Path, raw_plan: Any) -> dict:
        try:
            plan = SourcePlan.model_validate(raw_plan)
        except ValidationError:
            raise SourceBundleError("来源选取计划格式不符合契约") from None
        _validate_relations(plan.sources)
        if input_root.is_symlink() or self.root.resolve().is_relative_to(input_root.resolve()):
            raise SourceBundleError("快照存储不能位于输入目录内或使用符号链接输入根")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        stage = Path(tempfile.mkdtemp(prefix=".intake-", dir=self.root))
        try:
            frozen = []
            total = 0
            for selection in sorted(plan.sources, key=lambda item: item.relative_path):
                data = read_beneath(input_root, selection.relative_path)
                total += len(data)
                if total > MAX_BUNDLE_BYTES:
                    raise SourceBundleError("来源快照超过 256 MiB 限制")
                entry = FrozenSource(**selection.model_dump(), id=source_id(selection.relative_path),
                                     sha256=sha256(data).hexdigest(), size_bytes=len(data),
                                     media_type=media_type(selection.relative_path, data))
                frozen.append(entry)
                _private_write(stage / "files" / selection.relative_path, data)
            manifest = SourceManifest(schema_version="source-bundle/1.0", script_key=plan.script_key,
                                      edition=plan.edition, notes=plan.notes, audience="AUTHORING_ONLY", sources=frozen)
            document = manifest.model_dump()
            bundle_hash = content_hash(document)
            _private_write(stage / "manifest.json", canonical_json(document).encode("utf-8"))
            # Verify the copied bytes before making a snapshot discoverable.
            for entry in frozen:
                copied = read_beneath(stage, "files/" + entry.relative_path)
                current = read_beneath(input_root, entry.relative_path)
                if sha256(copied).hexdigest() != entry.sha256 or sha256(current).hexdigest() != entry.sha256:
                    raise SourceBundleError("冻结过程中来源发生变化")
            # Directory rename is atomic; complete immutable snapshots arbitrate
            # concurrent identical submissions. Never replace an existing bundle.
            destination = self.root / bundle_hash
            if destination.exists() or destination.is_symlink():
                if not self.verify(bundle_hash, persist=False)["valid"]:
                    raise SourceBundleError("同标识快照已损坏，不覆盖原记录")
            else:
                for directory in sorted((path for path in stage.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
                    _fsync_directory(directory)
                _fsync_directory(stage)
                try:
                    os.rename(stage, destination)
                except OSError:
                    if not destination.is_dir() or not self.verify(bundle_hash, persist=False)["valid"]:
                        raise SourceBundleError("快照保存冲突") from None
                _fsync_directory(self.root)
            return self.describe(bundle_hash)
        finally:
            if stage.exists():
                # Only this function's unpublished temporary directory.
                shutil.rmtree(stage)

    def manifest(self, bundle_hash: str) -> SourceManifest:
        _digest_id(bundle_hash)
        try:
            data = parse_package_json(read_beneath(self.root, bundle_hash + "/manifest.json", 2 * 1024 * 1024))
            manifest = SourceManifest.model_validate(data)
            _validate_relations(manifest.sources)
            if (content_hash(data) != bundle_hash
                    or any(entry.id != source_id(entry.relative_path) for entry in manifest.sources)
                    or sum(entry.size_bytes for entry in manifest.sources) > MAX_BUNDLE_BYTES):
                raise ValueError
            return manifest
        except (ValueError, OSError):
            if not (self.root / bundle_hash).exists():
                raise SourceBundleNotFound("来源快照不存在") from None
            raise SourceBundleError("来源清单完整性检查失败") from None

    def list_bundles(self) -> list[dict]:
        try:
            directories = sorted(self.root.iterdir())
        except FileNotFoundError:
            return []
        except OSError:
            raise SourceBundleError("来源存储目录无法读取") from None
        result = []
        for directory in directories:
            if re.fullmatch(r"[0-9a-f]{64}", directory.name):
                try:
                    result.append(self.describe(directory.name, include_sources=False))
                except SourceBundleError:
                    result.append({"bundle_hash": directory.name, "status": "CORRUPT"})
        return result

    def describe(self, bundle_hash: str, *, include_sources: bool = True) -> dict:
        manifest = self.manifest(bundle_hash)
        result = {"bundle_hash": bundle_hash, "status": "FROZEN", "script_key": manifest.script_key,
                  "edition": manifest.edition, "notes": manifest.notes, "file_count": len(manifest.sources),
                  "total_bytes": sum(source.size_bytes for source in manifest.sources),
                  "audience": "AUTHORING_ONLY", "publication_ready": False}
        if include_sources:
            result["sources"] = [source.model_dump() for source in manifest.sources]
        return result

    def _source_bytes(self, bundle_hash: str, source: FrozenSource) -> bytes:
        data = read_beneath(self.root, bundle_hash + "/files/" + source.relative_path)
        if (len(data) != source.size_bytes or sha256(data).hexdigest() != source.sha256
                or media_type(source.relative_path, data) != source.media_type):
            raise SourceBundleError("来源文件完整性检查失败")
        return data

    def read_source(self, bundle_hash: str, requested_id: str) -> tuple[bytes, str]:
        manifest = self.manifest(bundle_hash)
        source = next((item for item in manifest.sources if item.id == requested_id), None)
        if source is None:
            raise SourceBundleNotFound("来源文件不在此快照中")
        return self._source_bytes(bundle_hash, source), source.media_type

    def verify(self, bundle_hash: str, *, document: dict | None = None, persist: bool = True) -> dict:
        manifest = self.manifest(bundle_hash)
        issues = []
        checked = 0
        blobs: dict[str, bytes] = {}
        declared = document.get("sources", []) if document else []
        required_paths = {item.get("relative_path") for item in declared if isinstance(item, dict) and isinstance(item.get("relative_path"), str)} if isinstance(declared, list) else set()
        for entry in manifest.sources:
            try:
                data = self._source_bytes(bundle_hash, entry)
                checked += 1
                if entry.relative_path in required_paths:
                    blobs[entry.relative_path] = data
            except SourceBundleError:
                issues.append({"code": "SOURCE_FILE_INVALID", "source_id": entry.id})
        if document is not None:
            self._verify_candidate(manifest, document, blobs, issues)
        report = SourceVerificationReport(
            schema_version="source-verification/1.0", verifier_version=verifier_version_for(document),
            bundle_hash=bundle_hash, package_hash=content_hash(document) if document is not None else None,
            verified_at=datetime.now(timezone.utc).isoformat(), valid=not issues,
            publication_ready=False, checked_files=checked, issues=issues[:1000], issues_truncated=len(issues) > 1000,
        ).model_dump()
        digest = content_hash(report)
        if persist:
            try:
                self._save_report(bundle_hash, digest, report)
            except OSError:
                raise SourceBundleError("核验记录未能完整保存") from None
        return {**report, "report_hash": digest}

    @staticmethod
    def _verify_candidate(manifest: SourceManifest, document: dict, blobs: dict[str, bytes], issues: list) -> None:
        if not validate_package(document)["valid"]:
            issues.append({"code": "PACKAGE_INVALID"})
            return
        package = parse_script_package(document)
        if package.script_key != manifest.script_key:
            issues.append({"code": "SCRIPT_SCOPE_MISMATCH"})
            return
        entries = {item.relative_path: item for item in manifest.sources}
        by_id = {item.id: item for item in package.sources}
        for item in package.sources:
            entry = entries.get(item.relative_path)
            if entry is None or item.relative_path not in blobs or entry.sha256 != item.sha256 or entry.media_type != item.media_type:
                issues.append({"code": "SOURCE_MANIFEST_MISMATCH", "source_id": item.id})
                continue
            if item.kind == "original" and entry.kind != "original":
                issues.append({"code": "ORIGINAL_KIND_MISMATCH", "source_id": item.id})
            if isinstance(item, SourceFileV11):
                if item.kind == "supplement" and entry.kind != "supplement":
                    issues.append({"code": "SUPPLEMENT_KIND_MISMATCH", "source_id": item.id})
                if item.kind == "normalized":
                    original_paths = {by_id[key].relative_path for key in item.original_source_ids}
                    if entry.kind not in ("ocr", "revised") or original_paths != set(entry.original_paths):
                        issues.append({"code": "ORIGINAL_LINK_MISMATCH", "source_id": item.id})
                continue
            if entry.kind == "supplement":
                # v1 cannot distinguish editorial material from original canon.
                issues.append({"code": "EDITORIAL_PROVENANCE_UNSUPPORTED", "source_id": item.id})
            if item.kind == "normalized":
                if len(entry.original_paths) > 1:
                    # v1 has one original_source_id and cannot preserve a
                    # normalized file's complete multi-original provenance.
                    issues.append({"code": "MULTI_ORIGINAL_PROVENANCE_UNSUPPORTED", "source_id": item.id})
                original = by_id.get(item.original_source_id)
                if entry.kind not in ("ocr", "revised") or original is None or original.relative_path not in entry.original_paths:
                    issues.append({"code": "ORIGINAL_LINK_MISMATCH", "source_id": item.id})
        refs = list(package.introduction.sources) + list(package.settlement.instructions.sources)
        for collection in (package.characters, package.phases, package.knowledge, package.evidence, package.truth):
            refs.extend(ref for item in collection for ref in item.sources)
        if isinstance(package, ScriptPackageV12):
            for collection in (package.mechanics.phase_budgets, package.mechanics.actions):
                refs.extend(ref for item in collection for ref in item.sources)
        if isinstance(package, ScriptPackageV13):
            refs.extend(ref for item in package.memories for ref in item.sources)
        if isinstance(package, ScriptPackageV14):
            pending = [package.full_play.model_dump()]
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    if 'sources' in node:
                        refs.extend(SourceReference.model_validate(ref) for ref in node['sources'])
                    pending.extend(value for key, value in node.items() if key != 'sources')
                elif isinstance(node, list):
                    pending.extend(node)
        for ref in refs:
            item = by_id[ref.source_id]
            data = blobs.get(item.relative_path)
            if data is None:
                continue
            if item.media_type.startswith("image/"):
                located = ref.page == 1 and ref.anchor is None
            elif item.media_type in ("text/plain", "text/markdown") and ref.anchor is not None and ref.page is None:
                lines = data.decode("utf-8-sig").splitlines()
                match = re.fullmatch(r"L([1-9][0-9]*)(?:-L([1-9][0-9]*))?", ref.anchor)
                if match:
                    start = int(match[1])
                    end = int(match[2] or match[1])
                    located = 1 <= start <= end <= len(lines)
                else:
                    headings = {line.lstrip("#").strip() for line in lines if re.match(r"^#{1,6}\s", line)}
                    located = ref.anchor in headings
            else:
                located = False
            if not located:
                issues.append({"code": "SOURCE_LOCATION_UNVERIFIED", "source_id": ref.source_id})

    def _save_report(self, bundle_hash: str, digest: str, report: dict) -> None:
        # Anchor every write to directory FDs, including the root and report
        # directory: swapping a path for a symlink must not redirect the write.
        payload = canonical_json(report).encode("utf-8")
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        report_fd = None
        temporary = ".report-" + uuid4().hex
        created = False
        try:
            try:
                os.mkdir("verification-reports", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            report_fd = os.open("verification-reports", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=report_fd)
            created = True
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, digest + ".json", src_dir_fd=report_fd, dst_dir_fd=report_fd, follow_symlinks=False)
            except FileExistsError:
                raise SourceBundleError("核验记录冲突，请重新核验") from None
            os.fsync(report_fd)
            os.fsync(root_fd)
        finally:
            if report_fd is not None:
                if created:
                    os.unlink(temporary, dir_fd=report_fd)
                os.close(report_fd)
            os.close(root_fd)

    def get_report(self, report_hash: str) -> dict:
        _digest_id(report_hash)
        try:
            raw = parse_package_json(read_beneath(self.root, "verification-reports/" + report_hash + ".json", 2 * 1024 * 1024))
            SourceVerificationReport.model_validate(raw)
            if content_hash(raw) != report_hash:
                raise ValueError
        except ValueError:
            raise SourceBundleError("核验记录缺失或完整性检查失败") from None
        return {**raw, "report_hash": report_hash}
