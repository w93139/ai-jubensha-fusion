"""Resolve only model-selected reference indices on the exact finding target."""
from copy import deepcopy

from src.schemas.indexed_audit import IndexedAuditDraft
from src.schemas.script_review import finding_entity


class IndexedReferenceError(ValueError):
    """Only fixed codes and bounded positions; no raw target IDs or values."""

    def __init__(self, diagnostics: list[dict]):
        super().__init__('target source index unavailable')
        self.diagnostics = diagnostics


def audit_source_catalog(package: dict) -> list[dict]:
    targets = [{"collection": collection, "id": None} for collection in ("introduction", "settlement")]
    for collection in ("characters", "phases", "knowledge", "evidence", "truth"):
        targets.extend({"collection": collection, "id": item["id"]} for item in package[collection])
    for collection, key in (("actions", "id"), ("phase_budgets", "phase_id")):
        targets.extend({"collection": "mechanics." + collection, "id": item[key]}
                       for item in package["mechanics"][collection])
    return [{"target": target, "sources": [{"index": index, "reference": deepcopy(reference)}
             for index, reference in enumerate(finding_entity(package, target)["sources"])]} for target in targets]


def assemble_indexed_audit(document: dict, package: dict) -> dict:
    draft = IndexedAuditDraft.model_validate(document)
    if package.get("schema_version") != "script-package/1.2":
        raise ValueError("unsupported indexed audit package")
    findings, diagnostics = [], []
    for position, finding in enumerate(draft.findings):
        entity = finding_entity(package, finding.target)
        if entity is None:
            diagnostics.append({'code':'AUDIT_TARGET_NOT_FOUND', 'entity_path':f'/findings/{position}/target'})
        else:
            for reference_position, index in enumerate(finding.source_indexes):
                if index >= len(entity['sources']):
                    diagnostics.append({'code':'AUDIT_SOURCE_INDEX_UNAVAILABLE',
                                        'entity_path':f'/findings/{position}/source_indexes/{reference_position}'})
                if len(diagnostics) == 10:
                    break
        if len(diagnostics) == 10:
            break
        if diagnostics:
            continue
        item = finding.model_dump(exclude={"source_indexes"})
        item["sources"] = [deepcopy(entity["sources"][index]) for index in finding.source_indexes]
        findings.append(item)
    if diagnostics:
        raise IndexedReferenceError(diagnostics)
    return {"schema_version": "script-audit/1.1", "summary": draft.summary,
            "coverage": list(draft.coverage), "findings": findings}
