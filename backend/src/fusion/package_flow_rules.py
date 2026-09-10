"""Pure, fixed-role rules previews over an immutable candidate package.

Only a phase floor and an AND of actually public evidence grant access. The
engine never acts for another role, guesses a disclosure deadline, or reveals
truth/settlement. Phase activation and evidence propagation are incremental;
event replay need only project the final view once.
"""
from collections import defaultdict, deque
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from src.fusion.package_validation import validate_package
from src.schemas.package_flow import PackageFlowTarget


RULES_CONTRACT = "package-flow-rules/1.0"
STATE_CONTRACT = "package-flow-state/1.0"
COLLECTIONS = ("knowledge", "evidence")


class RulesError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RulesEngine:
    def __init__(self, package: dict, character_id: str):
        try:
            if (type(package) is not dict
                    or package.get("schema_version") not in {"script-package/1.0", "script-package/1.1"}
                    or not validate_package(package)["valid"]):
                raise ValueError
            self._package = deepcopy(package)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise RulesError("PACKAGE_FLOW_PACKAGE_INVALID") from None
        if type(character_id) is not str or character_id not in {item["id"] for item in self._package["characters"]}:
            raise RulesError("PACKAGE_FLOW_CHARACTER_NOT_FOUND")
        self._character_id = character_id
        phase_by_id = {item["id"]: item for item in self._package["phases"]}
        self._phases = []
        phase_id = self._package["initial_phase_id"]
        while phase_id is not None:
            phase = phase_by_id[phase_id]
            self._phases.append(phase)
            phase_id = phase["next_phase_id"]
        rank = {item["id"]: index for index, item in enumerate(self._phases)}
        self._phase_index = 0
        self._items = {name: {item["id"]: item for item in self._package[name]} for name in COLLECTIONS}
        self._public = {name: set() for name in COLLECTIONS}
        self._shared = {name: set() for name in COLLECTIONS}
        self._active: set[tuple[str, str]] = set()
        self._unlocked: set[tuple[str, str]] = set()
        self._phase_buckets: dict[int, list[tuple[str, str]]] = defaultdict(list)
        self._remaining: dict[tuple[str, str], set[str]] = {}
        self._dependents: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for name in COLLECTIONS:
            for item in self._package[name]:
                # Another role's private material is never an unlock target
                # or an automatic public prerequisite in this fixed-role mode.
                if item["visibility"] != "PUBLIC" and item["character_id"] != character_id:
                    continue
                key = (name, item["id"])
                self._phase_buckets[rank[item["release"]["phase_id"]]].append(key)
                required = set(item["release"].get("required_public_evidence_ids", []))
                self._remaining[key] = required
                for evidence_id in required:
                    self._dependents[evidence_id].append(key)
        self._activate_phase()

    def _activate_phase(self) -> None:
        ready = deque()
        for key in self._phase_buckets[self._phase_index]:
            self._active.add(key)
            if not self._remaining[key]:
                ready.append(key)
        self._drain(ready)

    def _public_evidence(self, evidence_id: str, ready: deque) -> None:
        if evidence_id in self._public["evidence"]:
            return
        self._public["evidence"].add(evidence_id)
        # Each dependency edge is processed only when its evidence first
        # becomes public, including edges to later phase floors.
        for key in self._dependents[evidence_id]:
            self._remaining[key].discard(evidence_id)
            if not self._remaining[key] and key in self._active:
                ready.append(key)

    def _drain(self, ready: deque) -> None:
        while ready:
            key = ready.popleft()
            if key in self._unlocked:
                continue
            self._unlocked.add(key)
            name, identifier = key
            item = self._items[name][identifier]
            if item["visibility"] == "PUBLIC":
                if name == "evidence":
                    self._public_evidence(identifier, ready)
                else:
                    self._public[name].add(identifier)

    def apply(self, action: str, target: dict | None = None) -> None:
        """Apply one authorized command or fail without changing state."""
        if type(action) is not str or action not in {"ADVANCE_PHASE", "SHARE_MATERIAL"}:
            raise RulesError("PACKAGE_FLOW_ACTION_INVALID")
        if action == "ADVANCE_PHASE":
            if target is not None:
                raise RulesError("PACKAGE_FLOW_ACTION_INVALID")
            if self._phase_index == len(self._phases) - 1:
                raise RulesError("PACKAGE_FLOW_PHASE_COMPLETE")
            self._phase_index += 1
            self._activate_phase()
            return
        try:
            if type(target) is not dict:
                raise ValueError
            parsed = PackageFlowTarget.model_validate(target)
        except (ValidationError, ValueError, TypeError):
            raise RulesError("PACKAGE_FLOW_ACTION_INVALID") from None
        name, identifier = parsed.collection, parsed.id
        key = (name, identifier)
        item = self._items[name].get(identifier)
        if (item is None or key not in self._unlocked or item["visibility"] != "CHARACTER_PRIVATE"
                or item["character_id"] != self._character_id or item["disclosure"] not in {"MAY_SHARE", "MUST_SHARE"}
                or identifier in self._shared[name]):
            raise RulesError("PACKAGE_FLOW_MATERIAL_NOT_SHAREABLE")
        self._shared[name].add(identifier)
        if name == "evidence":
            ready = deque()
            self._public_evidence(identifier, ready)
            self._drain(ready)
        else:
            self._public[name].add(identifier)

    def state(self) -> dict:
        """Canonical machine state for internal replay/hash checks, not HTTP."""
        return {"schema_version": STATE_CONTRACT, "current_phase_id": self._phases[self._phase_index]["id"],
                **{"public_" + name + "_ids": sorted(self._public[name]) for name in COLLECTIONS},
                **{"shared_" + name + "_ids": sorted(self._shared[name]) for name in COLLECTIONS}}

    def view(self) -> dict:
        """Project authorized material only; no blocked IDs or future phases."""
        phase = self._phases[self._phase_index]
        can_advance = self._phase_index < len(self._phases) - 1
        result: dict[str, Any] = {"current_phase": {key: phase[key] for key in ("id", "title")},
                                  "can_advance": can_advance, "phase_complete": not can_advance}
        for name in COLLECTIONS:
            public, private = [], []
            for item in self._package[name]:
                identifier = item["id"]
                if (name, identifier) not in self._unlocked:
                    continue
                shared = identifier in self._shared[name]
                is_public = identifier in self._public[name]
                safe = {key: item[key] for key in ("id", "text", "disclosure")}
                if name == "knowledge":
                    safe["kind"] = item["kind"]
                safe["can_share"] = not is_public and item["disclosure"] in {"MAY_SHARE", "MUST_SHARE"}
                if shared:
                    safe["shared_by_character_id"] = self._character_id
                (public if is_public else private).append(safe)
            result["public_" + name] = public
            result["private_" + name] = private
        return result
