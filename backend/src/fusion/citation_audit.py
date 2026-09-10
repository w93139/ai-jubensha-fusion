"""Pure citation-only contract builder. No SDK, queue or approval.

Every selected entry binds a target and one of its declared sources. All
entries within one finding must belong to the same target. No repair/fallback.
"""
from copy import deepcopy

from pydantic import ValidationError

from src.fusion.audit_references import audit_source_catalog
from src.fusion.package_validation import content_hash
from src.schemas.citation_audit import CitationAuditDraft
from src.schemas.script_package import parse_script_package


MAX_CITATIONS = 2048


class CitationAuditError(ValueError):
    def __init__(self, code: str, path: str = '/'):
        self.code, self.path = code, path
        super().__init__(code)


def _catalog(package: dict) -> list[dict]:
    try:
        if package.get('schema_version') != 'script-package/1.2':
            raise ValueError
        parse_script_package(package)
    except (ValueError, TypeError, AttributeError):
        raise CitationAuditError('CITATION_CANDIDATE_INVALID') from None
    # Count before flattening: no silent truncation or large reference copies.
    count = len(package['introduction']['sources']) + len(package['settlement']['instructions']['sources'])
    count += sum(len(item['sources']) for collection in ('characters', 'phases', 'knowledge', 'evidence', 'truth')
                 for item in package[collection])
    count += sum(len(item['sources']) for collection in ('phase_budgets', 'actions')
                 for item in package['mechanics'][collection])
    if not 1 <= count <= MAX_CITATIONS:
        raise CitationAuditError('CITATION_CATALOG_TOO_LARGE')
    catalog = []
    for row in audit_source_catalog(package):
        for entry in row['sources']:
            catalog.append({'index':len(catalog), 'target':deepcopy(row['target']),
                            'reference':deepcopy(entry['reference'])})
    return catalog


def prepare_citation_contract(package: dict) -> dict:
    """Bounded schema and catalog, without provider calls.

    File/source verification and budget/ledger binding belong to the explicit
    Authoring 1.12 integration before dispatch. This helper is not a dispatch gate.
    """
    catalog = _catalog(package)
    local_schema = CitationAuditDraft.model_json_schema()
    local_schema['$defs']['CitationFinding']['properties']['citation_indexes']['items']['maximum'] = len(catalog) - 1

    def without_patterns(value):
        if isinstance(value, dict):
            return {k:without_patterns(v) for k,v in value.items() if k != 'pattern'}
        if isinstance(value, list):
            return [without_patterns(v) for v in value]
        return value

    return {'schema_version':'citation-audit-contract/1.0', 'package_hash':content_hash(package),
            'catalog_hash':content_hash(catalog), 'catalog':catalog, 'local_schema':local_schema,
            'provider_schema_candidate':without_patterns(local_schema),
            'semantic_status':'UNREVIEWED', 'provider_compatibility':'UNVERIFIED',
            'dispatch_enabled':False, 'publication_ready':False}


def assemble_citation_audit(raw: dict, package: dict, *, expected_package_hash: str,
                            expected_catalog_hash: str) -> dict:
    from src.fusion.authoring_model import validate_model_audit
    catalog = _catalog(package)
    if content_hash(package) != expected_package_hash or content_hash(catalog) != expected_catalog_hash:
        raise CitationAuditError('CITATION_BINDING_CHANGED')
    try:
        draft = CitationAuditDraft.model_validate(raw)
    except (ValidationError, ValueError, TypeError):
        # No raw values, user field names or Pydantic details escape this helper.
        raise CitationAuditError('CITATION_DRAFT_INVALID') from None
    if draft.status != 'COMPLETE':
        raise CitationAuditError('CITATION_INCOMPLETE', '/status')
    findings = []
    for position, finding in enumerate(draft.findings):
        selected = []
        for reference_position, index in enumerate(finding.citation_indexes):
            if index >= len(catalog):
                raise CitationAuditError('CITATION_INDEX_UNAVAILABLE',
                    f'/findings/{position}/citation_indexes/{reference_position}')
            selected.append(catalog[index])
        target = selected[0]['target']
        if any(row['target'] != target for row in selected):
            raise CitationAuditError('CITATION_TARGET_MIXED', f'/findings/{position}/citation_indexes')
        findings.append({'id':f'finding-{position + 1}', 'category':finding.category, 'severity':finding.severity,
            'message':finding.message, 'target':deepcopy(target),
            'sources':[deepcopy(row['reference']) for row in selected]})
    # Generated finding IDs are local record labels, not invented target/source
    # IDs. The domain report still undergoes the full existing validation.
    return validate_model_audit({'schema_version':'script-audit/1.1', 'summary':draft.summary,
                                'coverage':draft.coverage, 'findings':findings}, package)
