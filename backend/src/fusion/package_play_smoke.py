"""Synthetic-only, one-request acceptance of the actual package-play wire.

This is not a publication audit or a production database runner. Publication
authority is an explicitly simulated fixture seam; candidate import, source
verification, opening, play rules, durable accounting and the model adapter are
real. Every invocation creates a new private SQLite database outside the repo.
No arbitrary prompt, package, source path, database URL or resume is accepted.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from time import monotonic
from typing import Sequence

from dotenv import dotenv_values
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.db.models import ScriptImportJob, ScriptPackageVersion, User
from src.db.models.package_play import ScriptPackagePlay, ScriptPackagePlayEvent
from src.db.models.package_runtime import ScriptPackagePlaySession
from src.fusion.agents import PlayerModelSettings
from src.fusion.authoring_sources import locate_text
from src.fusion.budget import UsageAmount
from src.fusion.package_import import PackageImportService
from src.fusion.package_play import PackagePlayService
from src.fusion.package_play_rules import PackagePlayRules
from src.fusion.package_role_model import PackageRoleModel
from src.fusion.package_runtime import PackageRuntimeService
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.fusion.provider_smoke import (
    DEFAULT_ENV_PATH, SelectedSmokeConfig, SmokeConfigurationError, load_selected_config,
    parse_confirmed_cost,
)
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.fusion.source_bundles import SourceBundleStore, source_id
from src.schemas.script_package import SourceReference


REPOSITORY = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = REPOSITORY.parent / "private-data/import-jobs/package-play-smoke"
CONFIRM_PAID = "ONE_SYNTHETIC_PACKAGE_PLAY_CALL"
FIXTURE_ID = "synthetic-package-play-smoke-v1"
QUALITY_VERSION = "package-play-smoke-quality/1.0"
MAX_OUTPUT_TOKENS = 256
MAX_INPUT_BYTES = 8000
QUESTION = "请提供你独有的蓝色封条证据，只选择写有它编号和背面图案的那条证据。"
ALLOWED_EVIDENCE = "我保管的蓝色封条编号为七，背面画有三角形。"
FORBIDDEN_MARKERS = (
    "SYNTHETIC_KEEP_PRIVATE_CANARY", "SYNTHETIC_OTHER_PRIVATE_CANARY",
    "SYNTHETIC_FUTURE_CANARY", "SYNTHETIC_SYSTEM_TRUTH_CANARY",
    "SYNTHETIC_UNSELECTED_TRUTH_CANARY",
)


class PackagePlaySmokeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PackagePlaySmokeError(code)


def _write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _json(path: Path, value: dict) -> None:
    _write(path, (canonical_json(value) + "\n").encode("utf-8"))


def new_run_directory(parent: Path) -> Path:
    parent = Path(parent).absolute()
    _require(not any(path.is_symlink() for path in (parent, *parent.parents)), "SMOKE_OUTPUT_SYMLINK")
    _require(not parent.resolve().is_relative_to(REPOSITORY), "SMOKE_OUTPUT_INSIDE_REPOSITORY")
    # Create each missing component privately, without changing existing dirs.
    missing = []
    cursor = parent
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700)
    _require(parent.is_dir() and not parent.stat().st_mode & 0o077
             and parent.stat().st_uid == os.getuid(), "SMOKE_OUTPUT_NOT_PRIVATE")
    return Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=parent))


def synthetic_package() -> tuple[dict, str]:
    """New invented material, including explicit rules, with real source bytes."""
    reference_id = source_id("fixture.md")

    def refs(anchor):
        return [{"source_id": reference_id, "anchor": anchor}]

    def material(identifier, body, *, character=None, phase="entry", required=(), kind=None, keep=False):
        result = {"id": identifier, "text": body, "visibility": "CHARACTER_PRIVATE" if character else "PUBLIC",
                  "character_id": character, "release": {"phase_id": phase, "required_public_evidence_ids": list(required)},
                  "disclosure": "KEEP_PRIVATE" if keep else "MAY_SHARE" if character else "PUBLIC", "sources": refs(identifier)}
        if kind:
            result["kind"] = kind
        return result

    package = {
        "schema_version": "script-package/1.1", "script_key": FIXTURE_ID, "content_version": "synthetic-v1",
        "title": "虚构蓝封条核对", "player_count": 2, "sources": [],
        "introduction": {"text": "两名保管员在资料室核对一枚蓝色封条。", "sources": refs("introduction")},
        "characters": [{"id": key, "name": name, "sources": refs(key)} for key, name in (("a", "保管员甲"), ("b", "保管员乙"))],
        "initial_phase_id": "entry",
        "phases": [{"id": key, "title": title, "next_phase_id": following, "sources": refs(key)}
                   for key, title, following in (("entry", "登记", "compare"), ("compare", "核对", "ending"), ("ending", "结尾", None))],
        "knowledge": [
            material("a-private", FORBIDDEN_MARKERS[1] + "：甲记得私人物品的位置。", character="a", kind="FACT", keep=True),
            material("b-keep", FORBIDDEN_MARKERS[0] + "：乙的私人便条不可公开。", character="b", kind="FACT", keep=True),
            material("b-future", FORBIDDEN_MARKERS[2] + "：乙在核对阶段才想起换班时间。", character="b", phase="compare", kind="FACT"),
            material("public-notice", "核对地点是资料室。", kind="FACT"),
            material("public-claim", "保管员甲声称封条没有被替换。", kind="CLAIM", required=("shape-match",)),
        ],
        "evidence": [
            material("blue-seal", ALLOWED_EVIDENCE, character="b"),
            material("seal-catalog", "目录确认编号七对应蓝色封条。", required=("blue-seal",)),
            material("shape-match", "图案记录的三角形与封条相符。", required=("seal-catalog",)),
        ],
        "truth": [{"id": key, "text": body, "visibility": "SYSTEM_TRUTH", "sources": refs(key)}
                  for key, body in (("selected-truth", FORBIDDEN_MARKERS[3] + "：目录与封条属于同一次交接。"),
                                    ("unselected-truth", FORBIDDEN_MARKERS[4] + "：此额外系统记录不在本次结尾范围。"))],
        "settlement": {"phase_id": "ending", "truth_ids": ["selected-truth"],
                       "instructions": {"text": "对照已公开的封条与目录，揭晓本次交接记录。", "sources": refs("settlement")}},
    }
    sections = [("introduction", package["introduction"]["text"], {})]
    sections += [(item["id"], item["name"], {}) for item in package["characters"]]
    sections += [(item["id"], item["title"], {"next_phase_id": item["next_phase_id"]}) for item in package["phases"]]
    for collection in ("knowledge", "evidence", "truth"):
        sections += [(item["id"], item["text"], {key: value for key, value in item.items() if key not in {"id", "text", "sources"}})
                     for item in package[collection]]
    sections += [("settlement", package["settlement"]["instructions"]["text"],
                  {"phase_id": "ending", "truth_ids": ["selected-truth"]})]
    source = "# 合成资料范围\n独立虚构接口夹具，不属于商业剧本，不构成人工批准。规则使用下列显式字段：阶段为下限，前置证据必须全部公开；只有本人已解锁的 MAY_SHARE 材料可以分享。\n"
    source += "\n".join(f"# {anchor}\n{body}\n规则：{canonical_json(rules)}\n" for anchor, body, rules in sections)
    package["sources"] = [{"id": reference_id, "relative_path": "fixture.md", "sha256": sha256(source.encode()).hexdigest(),
                           "kind": "original", "media_type": "text/markdown", "original_source_ids": []}]
    return package, source


@dataclass
class SyntheticFixture:
    package: dict
    sources: SourceBundleStore
    bundle_hash: str
    report_hash: str


def make_fixture(root: Path) -> SyntheticFixture:
    package, source = synthetic_package()
    _require(validate_package(package)["valid"], "SMOKE_FIXTURE_INVALID")
    materials = {package["sources"][0]["id"]: source}
    for collection in ("characters", "phases", "knowledge", "evidence", "truth"):
        for item in package[collection]:
            body = item.get("text", item.get("name", item.get("title")))
            _require(body in locate_text(SourceReference.model_validate(item["sources"][0]), materials), "SMOKE_SOURCE_TEXT_MISMATCH")
    for item in (package["introduction"], package["settlement"]["instructions"]):
        _require(item["text"] in locate_text(SourceReference.model_validate(item["sources"][0]), materials), "SMOKE_SOURCE_TEXT_MISMATCH")
    inputs = root / "synthetic-inputs"
    inputs.mkdir(mode=0o700)
    _write(inputs / "fixture.md", source.encode())
    _json(root / "package.json", package)
    store = SourceBundleStore(root / "source-bundles")
    bundle = store.freeze(inputs, {"schema_version": "source-plan/1.0", "script_key": FIXTURE_ID,
                                 "edition": "synthetic-v1", "notes": ["全新虚构接口资料；发布权限仅由烟测夹具模拟。"],
                                 "sources": [{"relative_path": "fixture.md", "kind": "original", "material_type": "host"}]})
    report = store.verify(bundle["bundle_hash"], document=package)
    _require(report["valid"], "SMOKE_SOURCE_VERIFICATION_FAILED")
    return SyntheticFixture(package, store, bundle["bundle_hash"], report["report_hash"])


class FixturePublisher:
    """A visible simulation of approval, never a ScriptPublicationService substitute in production."""
    def __init__(self, fixture: SyntheticFixture, version_id: int):
        self.fixture = fixture
        package = fixture.package
        self.record = {"id": 1, "version_id": version_id, "package_hash": content_hash(package),
                       "title": package["title"], "content_version": package["content_version"], "player_count": 2,
                       "approval_mode": "SIMULATED_FIXTURE_ONLY", "synthetic": True}
        self.record["release_hash"] = content_hash(self.record)

    def get_release(self, identifier, *, require_current=True):
        _require(identifier == 1, "SMOKE_RELEASE_UNKNOWN")
        if require_current:
            _require(self.fixture.sources.verify(self.fixture.bundle_hash, document=self.fixture.package, persist=False)["valid"],
                     "SMOKE_SOURCE_VERIFICATION_FAILED")
        return deepcopy(self.record)

    def list_releases(self):
        return [self.get_release(1)]


def read_paid_authorization(env_path: Path = DEFAULT_ENV_PATH) -> bool:
    """Read just the existing flag, respecting an explicit process override."""
    override = os.environ.get("ENABLE_PAID_MODEL_CALLS")
    if override is not None:
        return override.strip().lower() in {"true", "1", "yes", "on"}
    try:
        _require(env_path.is_file() and env_path.stat().st_size <= 1_000_000, "SMOKE_AUTHORIZATION_UNAVAILABLE")
        selected = [line for line in env_path.read_text(encoding="utf-8").splitlines()
                    if re.match(r"^\s*(?:export\s+)?ENABLE_PAID_MODEL_CALLS\s*=", line)]
        _require(len(selected) <= 1, "SMOKE_AUTHORIZATION_AMBIGUOUS")
        if not selected:
            return False
        value = dotenv_values(stream=StringIO(selected[0]), interpolate=False).get("ENABLE_PAID_MODEL_CALLS")
        return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "on"}
    except (OSError, UnicodeError):
        raise PackagePlaySmokeError("SMOKE_AUTHORIZATION_UNAVAILABLE") from None


@contextmanager
def selected_environment(config: SelectedSmokeConfig):
    # No paid flag is set here. Only already-validated selected-provider fields
    # are briefly exposed to the adapter constructor, then restored exactly.
    values = {config.profile.api_key_env: config.api_key, config.profile.base_url_env: config.base_url}
    before = {name: os.environ.get(name) for name in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def make_model(config: SelectedSmokeConfig, paid_authorized: bool, client=None) -> PackageRoleModel:
    _require(config.profile == PLAYER_PROVIDER_PROFILES.get(config.profile.name), "SMOKE_PROFILE_INVALID")
    settings = PlayerModelSettings(provider=config.profile.name, model=config.model, timeout_seconds=30,
                                   retries=0, max_output_tokens=MAX_OUTPUT_TOKENS, max_input_bytes=MAX_INPUT_BYTES,
                                   thinking_mode="disabled", temperature=0, paid_calls_enabled=paid_authorized)
    with selected_environment(config):
        return PackageRoleModel(client=client, settings=settings)


def verify_prepared(package: dict, prepared: dict) -> dict:
    context = PackagePlayRules(package, "a").role_context("b")
    payload = json.loads(prepared["messages"][1]["content"])
    _require(payload == {"context": context, "question": QUESTION}, "SMOKE_ROLE_PROJECTION_CHANGED")
    _require({(item["collection"], item["id"]) for item in context["materials"]}
             == {("knowledge", "public-notice"), ("evidence", "blue-seal")}, "SMOKE_SCOPE_CHANGED")
    serialized = canonical_json(prepared)
    _require(ALLOWED_EVIDENCE in serialized and not any(marker in serialized for marker in FORBIDDEN_MARKERS),
             "SMOKE_MODEL_INPUT_LEAK")
    _require(not any(value in serialized for value in ("relative_path", "source_id", "required_public_evidence_ids")),
             "SMOKE_MODEL_METADATA_LEAK")
    prompt_bytes = len(canonical_json(prepared["messages"]).encode())
    _require(prepared["input_tokens"] == prompt_bytes + 4096, "SMOKE_INPUT_BUDGET_MISMATCH")
    return {"prompt_bytes": prompt_bytes, "prepared_hash": content_hash(prepared), "context_hash": content_hash(context),
            "input_token_upper_bound": prepared["input_tokens"], "output_token_upper_bound": prepared["output_tokens"],
            "forbidden_materials_excluded": True, "authorized_material_count": 2}


class ObservedModel:
    """One-shot audit guard around the unchanged real material selector."""
    def __init__(self, inner, expected, root, factory, reservation):
        self.inner, self.expected, self.root, self.factory = inner, deepcopy(expected), root, factory
        self.reservation = reservation
        self.calls = 0
        self.result = None

    @property
    def available(self):
        return self.inner.available

    @property
    def unavailable_reason(self):
        return self.inner.unavailable_reason

    def metadata(self):
        return self.inner.metadata()

    def prepare(self, context, question):
        prepared = self.inner.prepare(context, question)
        _require(prepared == self.expected, "SMOKE_PREPARED_CHANGED")
        return prepared

    async def call(self, prepared):
        _require(self.calls == 0 and prepared == self.expected, "SMOKE_DISPATCH_REFUSED")
        with self.factory() as fresh:
            events = fresh.query(ScriptPackagePlayEvent).all()
            _require(len(events) == 1 and events[0].kind == "AI_REQUEST", "SMOKE_RESERVATION_NOT_DURABLE")
        _json(self.root / "dispatch.json", {"maximum_sdk_requests": 1, "prepared_hash": content_hash(prepared),
                                           "reservation": self.reservation, "reservation_committed": True})
        self.calls += 1
        self.result = await self.inner.call(prepared)
        # The adapter returns only normalized usage, fixed errors and authorized
        # refs. Neither provider exception nor invalid output body is retained.
        _json(self.root / "model-result.json", self.result)
        return self.result


def _database(root: Path):
    database = root / "smoke.sqlite"
    _write(database, b"")
    engine = create_engine("sqlite:///" + str(database))

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for model in (User, ScriptPackageVersion, ScriptImportJob, ScriptPackagePlaySession, ScriptPackagePlay, ScriptPackagePlayEvent):
        model.__table__.to_metadata(metadata)
    Table("script_package_releases", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    return engine, sessionmaker(engine, autoflush=False)


def _seed(db, fixture, model, policy):
    for identifier in (1, 2):
        db.add(User(id=identifier, username=f"synthetic-smoke-{identifier}", email=f"smoke-{identifier}@example.invalid",
                    hashed_password="SYNTHETIC_NO_LOGIN", is_active=True, is_admin=identifier == 1))
    db.commit()
    imported = PackageImportService(db).submit(fixture.package, submitted_by=1, idempotency_key="synthetic-import")
    _require(imported["status"] == "SUCCEEDED", "SMOKE_IMPORT_FAILED")
    db.execute(text("INSERT INTO script_package_releases (id) VALUES (1)"))
    db.commit()
    publisher = FixturePublisher(fixture, imported["version_id"])
    opening = PackageRuntimeService(db, publisher).create({"release_id": 1, "character_id": "a", "idempotency_key": "synthetic-opening"}, 2)
    db.commit()
    service = PackagePlayService(db, publisher, model, policy)
    play = service.create({"opening_session_id": opening["session_id"], "idempotency_key": "synthetic-play"}, 2)
    db.commit()
    return service, play, publisher


def verify_answer(package: dict, view: dict) -> None:
    _require(view["last_ai_status"] == "OK" and len(view["dialogue"]) == 1, "SMOKE_MODEL_TURN_FAILED")
    reply = view["dialogue"][0]
    _require(reply["character_id"] == "b" and reply["character_name"] == "保管员乙"
             and reply["materials"] == [{"collection": "evidence", "id": "blue-seal", "text": ALLOWED_EVIDENCE, "shared_by_character_id": "b"}]
             and reply["text"] == "证据原文：" + ALLOWED_EVIDENCE, "SMOKE_MATERIAL_SELECTION_FAILED")
    _require({item["id"] for item in view["public_evidence"]} == {"blue-seal", "seal-catalog", "shape-match"},
             "SMOKE_PUBLIC_CLOSURE_FAILED")
    claim = next((item for item in view["public_knowledge"] if item["id"] == "public-claim"), {})
    _require(claim.get("kind") == "CLAIM" and claim.get("text") == package["knowledge"][-1]["text"], "SMOKE_CLAIM_CLASSIFICATION_FAILED")
    _require(view["settlement"] is None and not view["settled"], "SMOKE_EARLY_SETTLEMENT")
    _require(not any(marker in canonical_json(view) for marker in (FORBIDDEN_MARKERS[0], *FORBIDDEN_MARKERS[2:])), "SMOKE_PUBLIC_VIEW_LEAK")


def verify_settlement(package: dict, view: dict) -> None:
    _require(view["settled"] and not view["can_advance"] and view["settlement"] == {
        "text": package["settlement"]["instructions"]["text"],
        "truths": [{"id": "selected-truth", "text": package["truth"][0]["text"]}]}, "SMOKE_SETTLEMENT_FAILED")
    _require(FORBIDDEN_MARKERS[4] not in canonical_json(view), "SMOKE_UNSELECTED_TRUTH_LEAK")


async def run_smoke(config: SelectedSmokeConfig, confirmed_cost: Decimal, *, output_root: Path = DEFAULT_OUTPUT_ROOT,
                    execute: bool = False, paid_authorized: bool = False, client=None) -> tuple[int, dict]:
    confirmed_cost = parse_confirmed_cost(str(confirmed_cost))
    root = new_run_directory(output_root)
    receipt = {"schema_version": "package-play-smoke/1.0", "quality_version": QUALITY_VERSION,
               "synthetic": True, "publication_mode": "SIMULATED_FIXTURE_ONLY", "runtime_ready": False,
               "mode": "execute" if execute else "preview", "status": "FAILED", "result_code": "SMOKE_LOCAL_FAILURE",
               "maximum_model_requests": 1 if execute else 0, "model_requests": 0, "model_attempts": 0,
               "max_cost_cny": format(confirmed_cost, "f"), "usage_known": False,
               "charged_cost_cny": "0", "artifact_directory": str(root)}
    engine, observed = None, None
    started = monotonic()
    try:
        fixture = make_fixture(root)
        model = make_model(config, paid_authorized, client)
        prepared = model.prepare(PackagePlayRules(fixture.package, "a").role_context("b"), QUESTION)
        bounds = verify_prepared(fixture.package, prepared)
        policy = replace(config.pricing, token_limit=32000, cost_limit_cny=confirmed_cost, paid_calls_enabled=paid_authorized)
        reservation = policy.amount(prepared["input_tokens"], prepared["output_tokens"])
        receipt.update({"fixture_hash": content_hash({"package": fixture.package, "question": QUESTION}),
                        "package_hash": content_hash(fixture.package), "bundle_hash": fixture.bundle_hash,
                        "source_report_hash": fixture.report_hash, "model": model.metadata(), "budget": policy.to_snapshot(),
                        **bounds, "reservation": reservation.to_metadata(), "paid_authorized": paid_authorized,
                        "within_confirmed_budget": reservation.cost_cny <= confirmed_cost})
        _json(root / "preview.json", receipt | {"status": "PREVIEW", "result_code": "PREVIEW_READY"})
        _require(reservation.cost_cny <= confirmed_cost, "SMOKE_RESERVATION_EXCEEDS_LIMIT")
        if execute:
            _require(paid_authorized is True and model.available, "SMOKE_PAID_CALLS_DISABLED")
        engine, factory = _database(root)
        observed = ObservedModel(model, prepared, root, factory, reservation.to_metadata())
        with factory() as db:
            service, view, publisher = _seed(db, fixture, observed, policy)
            _json(root / "binding.json", {"opening_session_id": view["opening_session_id"], "play_id": view["play_id"],
                                          "release": publisher.record, "package_hash": receipt["package_hash"]})
            if not execute:
                _require(db.query(ScriptPackagePlayEvent).count() == 0, "SMOKE_PREVIEW_HAS_EVENTS")
                receipt.update({"status": "PREVIEW", "result_code": "PREVIEW_READY", "event_count": 0})
            else:
                view = await service.ask(view["play_id"], {"idempotency_key": "synthetic-only-question", "expected_revision": 0,
                                                         "character_id": "b", "question": QUESTION}, 2)
                events = db.query(ScriptPackagePlayEvent).order_by(ScriptPackagePlayEvent.revision).all()
                _require([row.kind for row in events] == ["AI_REQUEST", "AI_RESULT"], "SMOKE_EVENT_COUNT_FAILED")
                result_data = json.loads(events[-1].event_json)["data"]
                receipt.update({"last_ai_status": view["last_ai_status"], "usage_known": result_data["usage"] is not None,
                                "usage": result_data["usage"], "charged_cost_cny": result_data["accounted"]["cost_cny"],
                                "request_event_hash": events[0].event_hash, "result_event_hash": events[-1].event_hash,
                                "answer_view_hash": content_hash(view)})
                # The durable service result is authoritative even if writing
                # a supplementary browser artifact subsequently fails.
                _json(root / "after-answer.json", view)
                _require(Decimal(receipt["charged_cost_cny"]) <= confirmed_cost, "SMOKE_COST_OVERRUN")
                verify_answer(fixture.package, view)
                for index, action in enumerate(("ADVANCE_PHASE", "ADVANCE_PHASE", "SETTLE")):
                    view = service.act(view["play_id"], {"idempotency_key": f"synthetic-action-{index}",
                                                       "expected_revision": view["revision"], "action": action}, 2)
                    db.commit()
                verify_settlement(fixture.package, view)
                _json(root / "settled-view.json", view)
                with factory() as fresh:
                    restored = PackagePlayService(fresh, publisher, observed, policy).get(view["play_id"], 2)
                    _require(restored == view, "SMOKE_REPLAY_FAILED")
                receipt.update({"status": "PASSED", "result_code": "PASSED", "event_count": db.query(ScriptPackagePlayEvent).count(),
                                "final_view_hash": content_hash(view), "selection_verified": True, "public_closure_verified": True,
                                "settlement_verified": True, "replay_verified": True})
    except (PackagePlaySmokeError, SmokeConfigurationError) as exc:
        receipt["result_code"] = exc.code
    except Exception:
        # Provider bodies, SQL exceptions, secrets and fixture text never reach stdout.
        receipt["result_code"] = "SMOKE_LOCAL_FAILURE"
    finally:
        if observed:
            receipt["model_requests"] = observed.calls
            receipt["model_attempts"] = (int(observed.result["model_attempted"]) if observed.result is not None else observed.calls)
            if observed.calls and not receipt["usage_known"] and receipt["charged_cost_cny"] == "0":
                receipt["charged_cost_cny"] = receipt["reservation"]["cost_cny"]
        if engine:
            engine.dispose()
        receipt["duration_ms"] = max(0, int((monotonic() - started) * 1000))
        _json(root / "receipt.json", receipt)
    return (0 if receipt["status"] in {"PREVIEW", "PASSED"} else 3), receipt


def build_parser():
    parser = argparse.ArgumentParser(description="虚构包材料选择烟测；默认只预审，执行最多一次模型请求。")
    parser.add_argument("--provider", required=True, choices=tuple(sorted(PLAYER_PROVIDER_PROFILES)))
    parser.add_argument("--max-cost-cny", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-paid", choices=(CONFIRM_PAID,))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _require(not args.execute or args.confirm_paid == CONFIRM_PAID, "SMOKE_CONFIRMATION_REQUIRED")
        cost = parse_confirmed_cost(args.max_cost_cny)
        authorized = read_paid_authorization()
        config = load_selected_config(args.provider)
        code, receipt = asyncio.run(run_smoke(config, cost, output_root=args.output_root,
                                             execute=args.execute, paid_authorized=authorized))
    except (PackagePlaySmokeError, SmokeConfigurationError) as exc:
        code, receipt = 2, {"status": "REFUSED", "result_code": exc.code, "model_requests": 0}
    except Exception:
        code, receipt = 4, {"status": "FAILED", "result_code": "SMOKE_LOCAL_FAILURE_NO_RECEIPT"}
    print(canonical_json(receipt))
    return code
