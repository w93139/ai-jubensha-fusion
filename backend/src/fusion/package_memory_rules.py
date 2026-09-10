"""Private memories granted only by observed, committed-play events."""
from copy import deepcopy

from src.fusion.package_investigation_rules import PackageInvestigationRules
from src.fusion.package_play_rules import PlayRulesError


RULES_CONTRACT = "package-play-rules/1.2"


class PackageMemoryRules(PackageInvestigationRules):
    _schema_versions = ("script-package/1.3",)

    def __init__(self, package, human_character_id):
        super().__init__(package, human_character_id)
        self._memories = {item["id"]: item for item in self._package["memories"]}
        self._ranks = {phase["id"]: index for index, phase in enumerate(self._phases)}
        self._memory_grants = {}
        self._seen_evidence = {actor: set() for actor in self._characters}
        self._observed_sequence = -1
        self.observe_event(0)

    def _held_evidence(self, actor):
        return self._public["evidence"] | {identifier for collection, identifier in self._unlocked
            if collection == "evidence" and self._items[collection][identifier]["character_id"] == actor}

    def observe_event(self, sequence: int, speech: dict | None = None):
        """Caller first applies an accepted event; this runs before its state hash.

        Only the authored speech text is heard. References, material replies and
        newly obtained memory bodies are not implicit speeches.
        """
        phase = self._phases[self._phase_index]["id"]
        if (type(sequence) is not int or sequence <= self._observed_sequence
                or (speech is not None and (type(speech) is not dict
                    or set(speech) != {"speaker", "text", "phase_id"}
                    or speech["speaker"] not in self._characters
                    or speech["phase_id"] != phase or type(speech["text"]) is not str
                    or not speech["text"].strip() or len(speech["text"]) > 4000))):
            raise PlayRulesError("PACKAGE_MEMORY_EVENT_INVALID")
        held = {actor: self._held_evidence(actor) for actor in self._characters}
        newly_held = {actor: items - self._seen_evidence[actor] for actor, items in held.items()}
        if not self._settled:
            for identifier, memory in self._memories.items():
                if identifier in self._memory_grants or self._ranks[memory["phase_id"]] > self._phase_index:
                    continue
                actor = memory["character_id"]
                for trigger in memory["triggers"]:
                    cause = None
                    if (trigger["kind"] == "OTHER_PUBLIC_SPEECH" and speech is not None
                            and speech["speaker"] != actor and trigger["keyword"] in speech["text"]):
                        cause = {"kind": "OTHER_PUBLIC_SPEECH", "speaker": speech["speaker"]}
                    elif trigger["kind"] == "ACQUIRED_EVIDENCE" and trigger["evidence_id"] in newly_held[actor]:
                        cause = {"kind": "ACQUIRED_EVIDENCE", "evidence_id": trigger["evidence_id"]}
                    if cause is not None:
                        self._memory_grants[identifier] = {"id": identifier, "sequence": sequence,
                            "phase_id": phase, "cause": cause}
                        break
        self._seen_evidence = held
        self._observed_sequence = sequence

    def state(self):
        return {**super().state(), "schema_version": "package-play-state/1.2",
            "memory_observed_sequence": self._observed_sequence,
            "memory_grants": [deepcopy(item) for item in self._memory_grants.values()],
            "memory_seen_evidence": {actor: sorted(items) for actor, items in self._seen_evidence.items()}}

    def view(self):
        result = super().view()
        entries = [{**deepcopy(grant), **{key: self._memories[identifier][key] for key in
            ("title", "text", "kind", "card_disclosure", "retelling")}, "character_id": self._human}
            for identifier, grant in self._memory_grants.items()
            if self._memories[identifier]["character_id"] == self._human]
        result["memories"] = {"schema_version": "package-memory-view/1.0", "entries": entries}
        return result

    def proposal_context(self, actor):
        context = super().proposal_context(actor)
        context["materials"].extend({"collection": "knowledge", "id": identifier, "text": memory["text"],
            "kind": memory["kind"], "public": False} for identifier, memory in self._memories.items()
            if memory["character_id"] == actor and identifier in self._memory_grants)
        return context

    def dialogue_context(self, actor):
        """Only speakable content; ordinary KEEP_PRIVATE goals never reach prose."""
        context = self.role_context(actor)
        context["materials"] = [{**item, "kind": item.get("kind", "FACT")} for item in context["materials"]]
        context["materials"].extend({"collection": "memory", "id": identifier, "text": memory["text"],
            "kind": memory["kind"], "retelling": memory["retelling"]} for identifier, memory in self._memories.items()
            if memory["character_id"] == actor and identifier in self._memory_grants)
        return context
