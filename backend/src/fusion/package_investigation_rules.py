"""Sourced, shared investigation points on the existing package play engine.

The historical engine and its state contract stay unchanged. Only explicitly
versioned v1.2 packages can obtain the additional action-gated permissions.
"""
from collections import defaultdict, deque

from src.fusion.package_play_rules import PackagePlayRules, PlayRulesError


RULES_CONTRACT = "package-play-rules/1.1"
STATE_CONTRACT = "package-play-state/1.1"


class PackageInvestigationRules(PackagePlayRules):
    _schema_versions = ("script-package/1.2",)

    def _activate(self) -> None:
        # The base constructor validates and copies the complete package before
        # it first activates a phase. Initialize these indexes at that boundary.
        if not hasattr(self, "_actions"):
            mechanics = self._package["mechanics"]
            self._actions = {item["id"]: item for item in mechanics["actions"]}
            self._budgets = {item["phase_id"]: item for item in mechanics["phase_budgets"]}
            self._completed_actions: set[str] = set()
            self._spent_by_phase = {identifier: 0 for identifier in self._budgets}
            self._remaining_actions = {}
            self._action_dependents = defaultdict(list)
            for name, items in self._items.items():
                for identifier, item in items.items():
                    key = (name, identifier)
                    required = set(item["release"].get("required_action_ids", []))
                    self._remaining_actions[key] = required
                    for action_id in required:
                        self._action_dependents[action_id].append(key)
        super()._activate()

    def _drain(self, ready: deque) -> None:
        while ready:
            key = ready.popleft()
            if key in self._unlocked or self._remaining_actions[key]:
                continue
            self._unlocked.add(key)
            name, identifier = key
            if self._items[name][identifier]["visibility"] == "PUBLIC":
                if name == "evidence":
                    self._publish_evidence(identifier, ready)
                else:
                    self._public[name].add(identifier)

    def _budget(self) -> dict:
        return self._budgets[self._phases[self._phase_index]["id"]]

    def _remaining_points(self) -> int:
        budget = self._budget()
        return budget["points"] - self._spent_by_phase[budget["phase_id"]]

    def _can_finish(self) -> bool:
        return (not self._settled and (self._budget()["advance_policy"] == "ALLOW_REMAINING"
                                      or self._remaining_points() == 0))

    def _available(self, action: dict, actor: str) -> bool:
        return bool(not self._settled and action["id"] not in self._completed_actions
                    and self._phases[self._phase_index]["id"] in action["phase_ids"]
                    and actor in action["allowed_character_ids"]
                    and action["cost"] <= self._remaining_points()
                    and set(action.get("required_action_ids", [])) <= self._completed_actions
                    and set(action.get("required_public_evidence_ids", [])) <= self._public["evidence"])

    def apply(self, action: str, target: dict | None = None, actor_character_id: str | None = None) -> None:
        if self._settled:
            raise PlayRulesError("PACKAGE_PLAY_ALREADY_SETTLED")
        actor = self._human if actor_character_id is None else actor_character_id
        if action != "PERFORM_ACTION":
            if action in ("ADVANCE_PHASE", "SETTLE") and not self._can_finish():
                raise PlayRulesError("PACKAGE_PLAY_PHASE_BUDGET_REMAINS")
            return super().apply(action, target, actor_character_id)
        # Only the selected human may initiate a resource action. AI material
        # selection remains a separate, bounded operation with no point spend.
        if (actor != self._human or type(target) is not dict or set(target) != {"action_id"}
                or type(target["action_id"]) is not str):
            raise PlayRulesError("PACKAGE_PLAY_ACTION_NOT_AVAILABLE")
        rule = self._actions.get(target["action_id"])
        if rule is None or not self._available(rule, actor):
            raise PlayRulesError("PACKAGE_PLAY_ACTION_NOT_AVAILABLE")
        # All rejection paths precede mutation; there is no caller-provided
        # price, grant list, phase or actor in this transition.
        self._spent_by_phase[self._budget()["phase_id"]] += rule["cost"]
        self._completed_actions.add(rule["id"])
        ready = deque()
        for key in self._action_dependents[rule["id"]]:
            self._remaining_actions[key].discard(rule["id"])
            if not self._remaining_actions[key] and not self._remaining[key] and key in self._active:
                ready.append(key)
        self._drain(ready)

    def state(self) -> dict:
        return {**super().state(), "schema_version": STATE_CONTRACT,
                "completed_action_ids": sorted(self._completed_actions),
                "spent_points_by_phase": [{"phase_id": key, "spent_points": value}
                                          for key, value in sorted(self._spent_by_phase.items())]}

    def view(self) -> dict:
        result = super().view()
        result["can_advance"] = result["can_advance"] and self._can_finish()
        budget = self._budget()
        result["mechanics"] = {"initial_points": budget["points"], "remaining_points": self._remaining_points(),
            "spent_points": self._spent_by_phase[budget["phase_id"]], "can_finish_phase": self._can_finish(),
            "available_actions": [{key: action[key] for key in ("id", "label", "cost")}
                                  for action in self._actions.values() if self._available(action, self._human)]}
        return result

    def proposal_context(self, actor: str) -> dict:
        """Private reasoning input; never return it in the player's HTTP view.

        Read only already unlocked, sourced role materials. Goals remain their
        original authored text; no objective is inferred from a character name.
        Only choices visible and available to both actor and human are proposed.
        """
        character = self._character(actor)
        materials = []
        for collection, items in self._items.items():
            for identifier, item in items.items():
                public = identifier in self._public[collection]
                if not public and ((collection, identifier) not in self._unlocked or item["character_id"] != actor):
                    continue
                materials.append({"collection": collection, "id": identifier, "text": item["text"],
                                  "kind": item.get("kind", "FACT"), "public": public})
        return {"character": {key: character[key] for key in ("id", "name")},
                "current_phase": {key: self._phases[self._phase_index][key] for key in ("id", "title")},
                "materials": materials,
                "options": [{key: action[key] for key in ("id", "label", "cost")}
                            for action in self._actions.values()
                            if self._available(action, actor) and self._available(action, self._human)]}
