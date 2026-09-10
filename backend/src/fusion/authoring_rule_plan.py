"""Frozen-rule assembly. No semantic inference, file access, or approval here."""
from copy import deepcopy


def text_entities(package: dict):
    yield ("introduction", None), package["introduction"]
    for collection in ("knowledge", "evidence", "truth"):
        for item in package[collection]:
            yield (collection, item["id"]), item
    yield ("settlement.instructions", None), package["settlement"]["instructions"]


def locked_projection(package: dict) -> dict:
    """Only these text leaves are editable; all other raw fields stay frozen."""
    result = deepcopy(package)
    for _, entity in text_entities(result):
        entity.pop("text")
    return result


def slot_catalog(package: dict, *, include_text: bool = False) -> list[dict]:
    return [{"collection": collection, "id": identifier, "sources": deepcopy(entity["sources"]),
             **({"text": entity["text"]} if include_text else {})}
            for (collection, identifier), entity in text_entities(package)]


def assemble_text_slots(plan: dict, slots: list[dict], *, preserve_text: bool = False) -> dict:
    result = deepcopy(plan)
    targets = dict(text_entities(result))
    seen = set()
    for slot in slots:
        key = (slot["collection"], slot["id"])
        if key not in targets or key in seen:
            raise ValueError("COMPILER_TEXT_SLOTS_INVALID")
        seen.add(key)
        if not preserve_text:
            targets[key]["text"] = slot["text"]
    if seen != set(targets):
        raise ValueError("COMPILER_TEXT_SLOTS_INVALID")
    return result
