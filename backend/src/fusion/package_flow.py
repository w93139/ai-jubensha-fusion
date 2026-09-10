"""Replay-verified deterministic previews, separate from opening bindings.

The caller owns commit/rollback. New grants acquire the publication fence
before a flow fence; historical reads and exact retries never reauthorize or
upgrade a release. Source verification happens before either write fence.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from src.db.models.package_flow import ScriptPackageFlow, ScriptPackageFlowAction
from src.fusion.package_flow_rules import RULES_CONTRACT, RulesEngine, RulesError
from src.fusion.package_runtime import PackageRuntimeError, PackageRuntimeService, PublicationReader
from src.fusion.package_validation import canonical_json, content_hash
from src.schemas.package_flow import CreatePackageFlowRequest, PackageFlowActionRequest


FLOW_CONTRACT = "package-flow-binding/1.0"
EVENT_CONTRACT = "package-flow-event/1.0"
FLOW_PATTERN = r"flow-[0-9a-f]{32}"


class PackageFlowError(ValueError):
    def __init__(self, code: str, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class PackageFlowService:
    def __init__(self, db: Session, publisher: PublicationReader | None = None) -> None:
        self.db = db
        self.runtime = PackageRuntimeService(db, publisher)
        self.publisher = self.runtime.publisher

    @staticmethod
    def _actor(owner: int) -> None:
        if type(owner) is not int or owner <= 0:
            raise PackageFlowError("PACKAGE_FLOW_IDENTITY_REQUIRED", 401)

    @staticmethod
    def _request(body: object, schema: type) -> dict:
        try:
            # Preserve explicit target:null so ADVANCE validation rejects it.
            raw = body.model_dump(exclude_unset=True) if isinstance(body, schema) else body
            return schema.model_validate(raw).model_dump(exclude_none=True)
        except (ValidationError, TypeError, ValueError):
            raise PackageFlowError("PACKAGE_FLOW_REQUEST_INVALID", 422) from None

    def _opening(self, identifier: str, owner: int) -> tuple:
        try:
            opening, package = self.runtime.resolve_binding(identifier, owner)
            if package["schema_version"] not in {"script-package/1.0", "script-package/1.1"}:
                raise PackageFlowError("PACKAGE_FLOW_RULES_UNSUPPORTED")
            return opening, package
        except PackageRuntimeError as exc:
            code = "PACKAGE_FLOW_OPENING_NOT_FOUND" if exc.status_code == 404 else "PACKAGE_FLOW_SNAPSHOT_INVALID"
            raise PackageFlowError(code, exc.status_code) from None

    def _row(self, flow_id: str, owner: int) -> ScriptPackageFlow:
        self._actor(owner)
        if type(flow_id) is not str or re.fullmatch(FLOW_PATTERN, flow_id) is None:
            raise PackageFlowError("PACKAGE_FLOW_NOT_FOUND", 404)
        row = self.db.query(ScriptPackageFlow).filter_by(flow_id=flow_id, owner_user_id=owner).populate_existing().one_or_none()
        if row is None:
            raise PackageFlowError("PACKAGE_FLOW_NOT_FOUND", 404)
        return row

    @staticmethod
    def _json(raw: str, digest: str) -> dict:
        value = json.loads(raw)
        if type(value) is not dict or canonical_json(value) != raw or content_hash(value) != digest:
            raise ValueError
        return value

    def _resolve(self, row: ScriptPackageFlow) -> dict:
        try:
            binding = self._json(row.binding_json, row.binding_hash)
            request = self._request(binding["request"], CreatePackageFlowRequest)
            opening, package = self._opening(row.opening_session_id, row.owner_user_id)
            expected = {
                "schema_version": FLOW_CONTRACT, "rules_contract": RULES_CONTRACT,
                "flow_id": row.flow_id, "owner_user_id": row.owner_user_id,
                "opening_session_id": row.opening_session_id, "opening_binding_hash": opening.binding_hash,
                "release_id": row.release_id, "release_hash": json.loads(opening.binding_json)["release_hash"],
                "version_id": row.version_id, "package_hash": row.package_hash,
                "selected_character_id": row.selected_character_id,
                "request": request, "request_hash": row.request_hash,
                "initial_state_hash": binding["initial_state_hash"],
            }
            if (binding != expected or re.fullmatch(FLOW_PATTERN, row.flow_id) is None
                    or content_hash(request) != row.request_hash
                    or request != {"opening_session_id": row.opening_session_id, "idempotency_key": row.idempotency_key}
                    or any(getattr(row, key) != getattr(opening, key) for key in
                           ("owner_user_id", "release_id", "version_id", "package_hash", "selected_character_id"))):
                raise ValueError
            return package
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise PackageFlowError("PACKAGE_FLOW_SNAPSHOT_INVALID") from None

    def _current(self, row: object) -> None:
        try:
            release = self.publisher.get_release(row.release_id, require_current=True)
            if (release["id"] != row.release_id or release["version_id"] != row.version_id
                    or release["package_hash"] != row.package_hash
                    or release["release_hash"] != json.loads(row.binding_json)["release_hash"]):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise PackageFlowError("PACKAGE_FLOW_RELEASE_UNAVAILABLE") from None
        except OperationalError:
            raise PackageFlowError("PACKAGE_FLOW_WRITE_CONFLICT") from None

    def _lock(self, table: str, column: str, value: str) -> None:
        # Only internal constant table/column names reach this method. The DML
        # also starts a real transaction before SAVEPOINT on native sqlite3.
        if self.db.get_bind().dialect.name == "sqlite":
            result = self.db.execute(text(f"UPDATE {table} SET id = id WHERE {column} = :identifier"),
                                     {"identifier": value})
            exists = result.rowcount == 1
        else:
            from src.db.models.package_runtime import ScriptPackagePlaySession
            model = ScriptPackageFlow if table == ScriptPackageFlow.__tablename__ else ScriptPackagePlaySession
            exists = self.db.execute(select(model.id).where(getattr(model, column) == value).with_for_update()).scalar_one_or_none() is not None
        if not exists:
            raise PackageFlowError("PACKAGE_FLOW_WRITE_CONFLICT")

    def _existing(self, owner: int, request: dict) -> ScriptPackageFlow | None:
        row = self.db.query(ScriptPackageFlow).filter_by(owner_user_id=owner,
              idempotency_key=request["idempotency_key"]).populate_existing().one_or_none()
        if row is not None and row.request_hash != content_hash(request):
            raise PackageFlowError("PACKAGE_FLOW_KEY_CONFLICT")
        return row

    def create(self, body: CreatePackageFlowRequest | dict, owner: int) -> dict:
        self._actor(owner)
        request = self._request(body, CreatePackageFlowRequest)
        opening, package = self._opening(request["opening_session_id"], owner)
        existing = self._existing(owner, request)
        if existing is not None:
            return self.get(existing.flow_id, owner)
        if self.db.query(ScriptPackageFlow.id).filter_by(opening_session_id=opening.session_id).first() is not None:
            raise PackageFlowError("PACKAGE_FLOW_OPENING_CONFLICT")
        self._current(opening)
        try:
            self._lock("script_package_play_sessions", "session_id", opening.session_id)
            # Re-read after the write fence, including the opening binding.
            opening, package = self._opening(request["opening_session_id"], owner)
            existing = self._existing(owner, request)
            if existing is not None:
                return self.get(existing.flow_id, owner)
            engine = RulesEngine(package, opening.selected_character_id)
            binding = {"schema_version": FLOW_CONTRACT, "rules_contract": RULES_CONTRACT,
                       "flow_id": "flow-" + uuid4().hex, "owner_user_id": owner,
                       "opening_session_id": opening.session_id, "opening_binding_hash": opening.binding_hash,
                       "release_id": opening.release_id, "release_hash": json.loads(opening.binding_json)["release_hash"],
                       "version_id": opening.version_id, "package_hash": opening.package_hash,
                       "selected_character_id": opening.selected_character_id,
                       "request": request, "request_hash": content_hash(request),
                       "initial_state_hash": content_hash(engine.state())}
            row = ScriptPackageFlow(**{key: binding[key] for key in (
                "flow_id", "owner_user_id", "opening_session_id", "release_id", "version_id", "package_hash",
                "selected_character_id", "request_hash")}, idempotency_key=request["idempotency_key"],
                binding_json=canonical_json(binding), binding_hash=content_hash(binding))
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
                return self._view(row, package, engine, 0)
        except IntegrityError:
            existing = self._existing(owner, request)
            if existing is not None:
                return self.get(existing.flow_id, owner)
            raise PackageFlowError("PACKAGE_FLOW_OPENING_CONFLICT") from None
        except OperationalError:
            raise PackageFlowError("PACKAGE_FLOW_WRITE_CONFLICT") from None
        except RulesError as exc:
            raise PackageFlowError(exc.code) from None

    def find_for_opening(self, opening_session_id: str, owner: int) -> dict | None:
        self._actor(owner)
        self._opening(opening_session_id, owner)
        row = self.db.query(ScriptPackageFlow).filter_by(opening_session_id=opening_session_id,
              owner_user_id=owner).populate_existing().one_or_none()
        return self.get(row.flow_id, owner) if row is not None else None

    @staticmethod
    def _view(row: ScriptPackageFlow, package: dict, engine: RulesEngine, revision: int) -> dict:
        return {"flow_id": row.flow_id, "opening_session_id": row.opening_session_id,
                "release_id": row.release_id, "version_id": row.version_id, "package_hash": row.package_hash,
                "selected_character_id": row.selected_character_id, "revision": revision,
                "runtime_ready": False, "status": "RULES_PREVIEW",
                "script": {key: package[key] for key in ("title", "content_version", "player_count")},
                "characters": [{"id": item["id"], "name": item["name"]} for item in package["characters"]],
                "introduction": {"text": package["introduction"]["text"]}, **engine.view()}

    def _replay(self, row: ScriptPackageFlow, package: dict, at_revision: int | None = None) -> tuple:
        try:
            engine = RulesEngine(package, row.selected_character_id)
            if content_hash(engine.state()) != json.loads(row.binding_json)["initial_state_hash"]:
                raise ValueError
            previous, revision, historical = row.binding_hash, 0, None
            # Successful commands can only advance once per edge or share an
            # item once. This bound rejects impossible histories, never truncates.
            maximum = len(package["phases"]) - 1 + len(package["knowledge"]) + len(package["evidence"])
            events = self.db.query(ScriptPackageFlowAction).filter_by(flow_id=row.flow_id).order_by(
                ScriptPackageFlowAction.revision).populate_existing().yield_per(100)
            for event in events:
                request = self._json(event.request_json, event.request_hash)
                if self._request(request, PackageFlowActionRequest) != request:
                    raise ValueError
                if (event.revision != revision + 1 or event.revision > maximum
                        or request["expected_revision"] != revision
                        or request["idempotency_key"] != event.idempotency_key
                        or event.previous_event_hash != previous):
                    raise ValueError
                engine.apply(request["action"], request.get("target"))
                state_hash = content_hash(engine.state())
                expected = {"schema_version": EVENT_CONTRACT, "flow_id": row.flow_id,
                            "revision": event.revision, "request_hash": event.request_hash,
                            "previous_event_hash": previous, "state_hash": state_hash}
                if (event.state_hash != state_hash or self._json(event.event_json, event.event_hash) != expected):
                    raise ValueError
                revision, previous = event.revision, event.event_hash
                if revision == at_revision:
                    historical = self._view(row, package, engine, revision)
            if at_revision is not None and historical is None:
                raise ValueError
            return engine, revision, previous, historical
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise PackageFlowError("PACKAGE_FLOW_HISTORY_INVALID") from None

    def get(self, flow_id: str, owner: int) -> dict:
        row = self._row(flow_id, owner)
        package = self._resolve(row)
        engine, revision, _, _ = self._replay(row, package)
        return self._view(row, package, engine, revision)

    def _repeat(self, row: ScriptPackageFlow, package: dict, request: dict) -> dict | None:
        event = self.db.query(ScriptPackageFlowAction).filter_by(flow_id=row.flow_id,
                idempotency_key=request["idempotency_key"]).populate_existing().one_or_none()
        if event is None:
            return None
        if event.request_hash != content_hash(request):
            raise PackageFlowError("PACKAGE_FLOW_KEY_CONFLICT")
        return self._replay(row, package, event.revision)[3]

    def act(self, flow_id: str, body: PackageFlowActionRequest | dict, owner: int) -> dict:
        self._actor(owner)
        request = self._request(body, PackageFlowActionRequest)
        row = self._row(flow_id, owner)
        package = self._resolve(row)
        repeated = self._repeat(row, package, request)
        if repeated is not None:
            return repeated
        # Source I/O and publication validity precede the per-flow lock. The
        # publisher acquires its candidate fence before returning successfully.
        self._current(row)
        try:
            self._lock("script_package_flows", "flow_id", row.flow_id)
            row = self._row(flow_id, owner)
            package = self._resolve(row)
            repeated = self._repeat(row, package, request)
            if repeated is not None:
                return repeated
            engine, revision, previous, _ = self._replay(row, package)
            if request["expected_revision"] != revision:
                raise PackageFlowError("PACKAGE_FLOW_REVISION_CONFLICT")
            engine.apply(request["action"], request.get("target"))
            state_hash = content_hash(engine.state())
            event = {"schema_version": EVENT_CONTRACT, "flow_id": row.flow_id, "revision": revision + 1,
                     "request_hash": content_hash(request), "previous_event_hash": previous, "state_hash": state_hash}
            action = ScriptPackageFlowAction(flow_id=row.flow_id, revision=revision + 1,
                     idempotency_key=request["idempotency_key"], request_json=canonical_json(request),
                     request_hash=content_hash(request), previous_event_hash=previous, state_hash=state_hash,
                     event_json=canonical_json(event), event_hash=content_hash(event))
            with self.db.begin_nested():
                self.db.add(action)
                self.db.flush()
                return self._view(row, package, engine, revision + 1)
        except IntegrityError:
            repeated = self._repeat(row, package, request)
            if repeated is not None:
                return repeated
            raise PackageFlowError("PACKAGE_FLOW_REVISION_CONFLICT") from None
        except OperationalError:
            raise PackageFlowError("PACKAGE_FLOW_WRITE_CONFLICT") from None
        except RulesError as exc:
            raise PackageFlowError(exc.code) from None
