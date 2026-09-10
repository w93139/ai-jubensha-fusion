"""Global, deterministic package play state; models only select material IDs."""
from collections import defaultdict, deque
from copy import deepcopy

from src.fusion.package_validation import validate_package


RULES_CONTRACT = "package-play-rules/1.0"
STATE_CONTRACT = "package-play-state/1.0"
COLLECTIONS = ("knowledge", "evidence")


class PlayRulesError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PackagePlayRules:
    _schema_versions = ("script-package/1.0", "script-package/1.1")

    def __init__(self, package: dict, human_character_id: str):
        try:
            if (type(package) is not dict or package.get("schema_version") not in self._schema_versions
                    or not validate_package(package)["valid"]):
                raise ValueError
            self._package = deepcopy(package)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise PlayRulesError("PACKAGE_PLAY_PACKAGE_INVALID") from None
        self._characters = {item["id"]: item for item in self._package["characters"]}
        self._character(human_character_id)
        self._human = human_character_id
        self._settled = False
        self._phases = []
        phases = {item["id"]: item for item in self._package["phases"]}
        identifier = self._package["initial_phase_id"]
        while identifier is not None:
            phase = phases[identifier]
            self._phases.append(phase)
            identifier = phase["next_phase_id"]
        ranks = {item["id"]: index for index, item in enumerate(self._phases)}
        self._phase_index = 0
        self._items = {name: {item["id"]: item for item in self._package[name]} for name in COLLECTIONS}
        self._public = {name: set() for name in COLLECTIONS}
        self._shared: dict[tuple[str, str], str] = {}
        self._unlocked: set[tuple[str, str]] = set()
        self._active: set[tuple[str, str]] = set()
        self._buckets = defaultdict(list)
        self._remaining = {}
        self._dependents = defaultdict(list)
        for name in COLLECTIONS:
            for item in self._package[name]:
                key = (name, item["id"])
                self._buckets[ranks[item["release"]["phase_id"]]].append(key)
                required = set(item["release"].get("required_public_evidence_ids", []))
                self._remaining[key] = required
                for evidence_id in required:
                    self._dependents[evidence_id].append(key)
        self._activate()

    def _character(self, actor: str) -> dict:
        if type(actor) is not str or actor not in self._characters:
            raise PlayRulesError("PACKAGE_PLAY_CHARACTER_NOT_FOUND")
        return self._characters[actor]

    def _activate(self):
        ready = deque()
        for key in self._buckets[self._phase_index]:
            self._active.add(key)
            if not self._remaining[key]:
                ready.append(key)
        self._drain(ready)

    def _publish_evidence(self, identifier, ready):
        if identifier in self._public["evidence"]:
            return
        self._public["evidence"].add(identifier)
        for key in self._dependents[identifier]:
            self._remaining[key].discard(identifier)
            if not self._remaining[key] and key in self._active:
                ready.append(key)

    def _drain(self, ready):
        while ready:
            key = ready.popleft()
            if key in self._unlocked:
                continue
            self._unlocked.add(key)
            name, identifier = key
            if self._items[name][identifier]["visibility"] == "PUBLIC":
                if name == "evidence":
                    self._publish_evidence(identifier, ready)
                else:
                    self._public[name].add(identifier)

    @staticmethod
    def _target(target):
        if (type(target) is not dict or set(target) != {"collection", "id"}
                or type(target["collection"]) is not str or target["collection"] not in COLLECTIONS
                or type(target["id"]) is not str):
            raise PlayRulesError("PACKAGE_PLAY_ACTION_INVALID")
        return target["collection"], target["id"]

    def _shareable(self, actor, key):
        name, identifier = key
        item = self._items[name].get(identifier)
        return bool(item and key in self._unlocked and item["visibility"] == "CHARACTER_PRIVATE"
                    and item["character_id"] == actor and item["disclosure"] in {"MAY_SHARE", "MUST_SHARE"}
                    and key not in self._shared)

    def _share(self, actor, key):
        name, identifier = key
        self._shared[key] = actor
        if name == "evidence":
            ready = deque()
            self._publish_evidence(identifier, ready)
            self._drain(ready)
        else:
            self._public[name].add(identifier)

    def apply(self, action: str, target: dict | None = None, actor_character_id: str | None = None) -> None:
        if self._settled:
            raise PlayRulesError("PACKAGE_PLAY_ALREADY_SETTLED")
        actor = self._human if actor_character_id is None else actor_character_id
        self._character(actor)
        if type(action) is not str or action not in {"ADVANCE_PHASE", "SHARE_MATERIAL", "SETTLE"}:
            raise PlayRulesError("PACKAGE_PLAY_ACTION_INVALID")
        if action != "SHARE_MATERIAL":
            if target is not None:
                raise PlayRulesError("PACKAGE_PLAY_ACTION_INVALID")
            if actor != self._human:
                raise PlayRulesError("PACKAGE_PLAY_ACTION_FORBIDDEN")
            final = self._phase_index == len(self._phases) - 1
            if action == "ADVANCE_PHASE":
                if final:
                    raise PlayRulesError("PACKAGE_PLAY_PHASE_COMPLETE")
                self._phase_index += 1
                self._activate()
            else:
                if not final or self._phases[self._phase_index]["id"] != self._package["settlement"]["phase_id"]:
                    raise PlayRulesError("PACKAGE_PLAY_SETTLEMENT_NOT_READY")
                self._settled = True
            return
        key = self._target(target)
        if not self._shareable(actor, key):
            raise PlayRulesError("PACKAGE_PLAY_MATERIAL_NOT_SHAREABLE")
        self._share(actor, key)

    def _reply_keys(self, actor, refs):
        self._character(actor)
        if actor == self._human or type(refs) is not list or len(refs) > 3:
            raise PlayRulesError("PACKAGE_PLAY_REPLY_INVALID")
        try:
            keys = [self._target(ref) for ref in refs]
        except PlayRulesError:
            raise PlayRulesError("PACKAGE_PLAY_REPLY_INVALID") from None
        if len(set(keys)) != len(keys) or any(
                identifier not in self._public[name] and not self._shareable(actor, (name, identifier))
                for name, identifier in keys):
            raise PlayRulesError("PACKAGE_PLAY_REPLY_INVALID")
        return keys

    def apply_reply(self, actor: str, refs: list) -> None:
        if self._settled:
            raise PlayRulesError("PACKAGE_PLAY_ALREADY_SETTLED")
        # Validate the whole selection against the pre-turn scope before any
        # share. A first selection cannot authorize a second locked selection.
        keys = self._reply_keys(actor, refs)
        for key in keys:
            if key[1] not in self._public[key[0]]:
                self._share(actor, key)

    def role_context(self, actor: str) -> dict:
        character = self._character(actor)
        materials = []
        for name in COLLECTIONS:
            for item in self._package[name]:
                key = (name, item["id"])
                if item["id"] not in self._public[name] and not self._shareable(actor, key):
                    continue
                safe = {"collection": name, "id": item["id"], "text": item["text"]}
                if name == "knowledge":
                    safe["kind"] = item["kind"]
                materials.append(safe)
        phase = self._phases[self._phase_index]
        return {"character": {key: character[key] for key in ("id", "name")},
                "current_phase": {key: phase[key] for key in ("id", "title")}, "materials": materials}

    def render_reply(self, actor: str, refs: list) -> dict:
        keys = self._reply_keys(actor, refs)
        materials, lines = [], []
        labels = {"FACT": "事实材料原文", "CLAIM": "角色说法原文", "INFERENCE": "推测原文"}
        for name, identifier in keys:
            item = self._items[name][identifier]
            safe = {"collection": name, "id": identifier, "text": item["text"]}
            if name == "knowledge":
                safe["kind"] = item["kind"]
            if (name, identifier) in self._shared:
                safe["shared_by_character_id"] = self._shared[(name, identifier)]
            materials.append(safe)
            label = labels[item["kind"]] if name == "knowledge" else "证据原文"
            lines.append(f"{label}：{item['text']}")
        return {"character_id": actor, "character_name": self._characters[actor]["name"],
                "text": "\n".join(lines) if lines else "我目前没有可以补充的公开材料。", "materials": materials}

    def state(self) -> dict:
        return {"schema_version": STATE_CONTRACT, "current_phase_id": self._phases[self._phase_index]["id"],
                "settled": self._settled,
                **{"public_" + name + "_ids": sorted(self._public[name]) for name in COLLECTIONS},
                "shared_materials": [{"collection": name, "id": identifier, "character_id": actor}
                                     for (name, identifier), actor in sorted(self._shared.items())]}

    def view(self) -> dict:
        phase = self._phases[self._phase_index]
        final = self._phase_index == len(self._phases) - 1
        result = {"current_phase": {key: phase[key] for key in ("id", "title")},
                  "can_advance": not final and not self._settled, "phase_complete": final,
                  "settled": self._settled, "settlement": None}
        for name in COLLECTIONS:
            public, private = [], []
            for item in self._package[name]:
                key = (name, item["id"])
                is_public = item["id"] in self._public[name]
                if not is_public and (key not in self._unlocked or item["character_id"] != self._human):
                    continue
                safe = {key: item[key] for key in ("id", "text", "disclosure")}
                if name == "knowledge":
                    safe["kind"] = item["kind"]
                safe["can_share"] = not self._settled and self._shareable(self._human, key)
                if key in self._shared:
                    safe["shared_by_character_id"] = self._shared[key]
                (public if is_public else private).append(safe)
            result["public_" + name] = public
            result["private_" + name] = private
        if self._settled:
            truth = {item["id"]: item for item in self._package["truth"]}
            result["settlement"] = {"text": self._package["settlement"]["instructions"]["text"],
                "truths": [{"id": identifier, "text": truth[identifier]["text"]}
                           for identifier in self._package["settlement"]["truth_ids"]]}
        return result
