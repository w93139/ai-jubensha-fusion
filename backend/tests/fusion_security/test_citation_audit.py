"""Citation-only feasibility, not a real provider or semantic pass."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from src.fusion import citation_audit
from src.fusion.citation_audit import prepare_citation_contract, assemble_citation_audit, CitationAuditError
from src.fusion.audit_references import audit_source_catalog
from src.fusion.authoring_model import validate_model_audit
from src.schemas.script_package import SourceReference
from tests.fusion_security.test_authoring_v12 import synthetic_v12_context_and_package


def case():
    _, package = synthetic_v12_context_and_package()
    contract = prepare_citation_contract(package)
    draft = {'schema_version':'citation-audit-draft/1.0', 'status':'COMPLETE', 'summary':'五维已查；仅为格式测试。',
        'coverage':['PROVENANCE','TIMELINE','EVIDENCE','KNOWLEDGE_BOUNDARY','PLAYABILITY'],
        'findings':[{'category':'PLAYABILITY','severity':'BLOCKER','message':'用于测试的阻断意见。','citation_indexes':[0]}]}
    return package, contract, draft


def assemble(draft, package, contract):
    return assemble_citation_audit(draft, package, expected_package_hash=contract['package_hash'],
                                   expected_catalog_hash=contract['catalog_hash'])


def test_catalog_preserves_every_existing_target_reference_and_order():
    package, contract, draft = case()
    before = deepcopy((package, contract, draft))
    expected = [(row['target'],entry['reference']) for row in audit_source_catalog(package) for entry in row['sources']]
    assert [(r['target'],r['reference']) for r in contract['catalog']] == expected
    assert [r['index'] for r in contract['catalog']] == list(range(len(expected)))
    result = assemble(draft,package,contract)
    assert result == validate_model_audit(result,package)
    assert result['findings'][0]['severity'] == 'BLOCKER'
    assert result['findings'][0]['target'] == contract['catalog'][0]['target']
    assert result['findings'][0]['sources'] == [SourceReference.model_validate(contract['catalog'][0]['reference']).model_dump()]
    assert (package,contract,draft) == before
    assert not contract['dispatch_enabled'] and not contract['publication_ready']


def test_multiple_selected_sources_are_kept_without_adding_unselected_ones():
    package, _, draft = case()
    # Valid alternate locator, distinct from the initial anchor.
    source = deepcopy(package['introduction']['sources'][0]); source['anchor'] = 'L1-L2'
    package['introduction']['sources'].append(source)
    contract = prepare_citation_contract(package)
    draft['findings'][0]['citation_indexes'] = [1,0]
    report = assemble(draft,package,contract)
    assert report['findings'][0]['sources'] == [SourceReference.model_validate(contract['catalog'][i]['reference']).model_dump() for i in [1,0]]
    draft['findings'][0]['citation_indexes'] = [1]
    assert assemble(draft,package,contract)['findings'][0]['sources'] == [SourceReference.model_validate(contract['catalog'][1]['reference']).model_dump()]


def test_mixed_targets_rejected_even_if_their_source_references_match():
    package, contract, draft = case()
    # Existing synthetic fixture reuses a source anchor across several targets.
    first=contract['catalog'][0]
    other=next(r for r in contract['catalog'] if r['target'] != first['target'] and r['reference'] == first['reference'])
    draft['findings'][0]['citation_indexes']=[0,other['index']]
    assert Draft202012Validator(contract['local_schema']).is_valid(draft)
    with pytest.raises(CitationAuditError, match='CITATION_TARGET_MIXED') as error:
        assemble(draft,package,contract)
    assert error.value.path == '/findings/0/citation_indexes'


@pytest.mark.parametrize('mutation', ['negative','outside','boolean','string','empty','duplicate','target','sources','id','incomplete','coverage','whitespace','long','too-many','extra'])
def test_invalid_wire_output_is_rejected_without_repair(mutation):
    package,contract,draft=case(); finding=draft['findings'][0]
    if mutation=='negative': finding['citation_indexes']=[-1]
    elif mutation=='outside': finding['citation_indexes']=[len(contract['catalog'])]
    elif mutation=='boolean': finding['citation_indexes']=[True]
    elif mutation=='string': finding['citation_indexes']=['0']
    elif mutation=='empty': finding['citation_indexes']=[]
    elif mutation=='duplicate': finding['citation_indexes']=[0,0]
    elif mutation in ('target','sources','id'): finding[mutation]='PRIVATE_UNTRUSTED_VALUE'
    elif mutation=='incomplete': draft['status']='INCOMPLETE'
    elif mutation=='coverage': draft['coverage']=['PLAYABILITY']*5
    elif mutation=='whitespace': finding['message']=' \n '
    elif mutation=='long': finding['message']='字'*161
    elif mutation=='too-many': draft['findings']=[deepcopy(finding) for _ in range(11)]
    else: draft['approved']=True
    before=deepcopy(draft)
    with pytest.raises(CitationAuditError) as error: assemble(draft,package,contract)
    assert draft==before and 'PRIVATE_UNTRUSTED_VALUE' not in str(error.value)


@pytest.mark.parametrize('change', ['order','reference','package-hash','catalog-hash'])
def test_catalog_cannot_be_reinterpreted_after_binding(change):
    package,contract,draft=case()
    if change=='order': package['characters'].reverse()
    elif change=='reference': package['introduction']['sources'][0]['anchor']='L1-L2'
    elif change=='package-hash': contract['package_hash']='0'*64
    else: contract['catalog_hash']='0'*64
    with pytest.raises(CitationAuditError,match='CITATION_BINDING_CHANGED'): assemble(draft,package,contract)


def test_dynamic_schema_bounds_indices_but_does_not_replace_local_validation():
    package,contract,draft=case()
    provider=Draft202012Validator(contract['provider_schema_candidate'])
    assert provider.is_valid(draft)
    draft['findings'][0]['citation_indexes']=[len(contract['catalog'])]
    assert not provider.is_valid(draft)
    draft['findings'][0].update(citation_indexes=[0],message='   ')
    assert provider.is_valid(draft)
    with pytest.raises(CitationAuditError): assemble(draft,package,contract)
    assert contract['provider_compatibility']=='UNVERIFIED'


def test_catalog_limit_refuses_instead_of_truncating(monkeypatch):
    package,contract,_=case()
    monkeypatch.setattr(citation_audit,'MAX_CITATIONS',len(contract['catalog'])-1)
    with pytest.raises(CitationAuditError,match='CITATION_CATALOG_TOO_LARGE'): prepare_citation_contract(package)


def test_empty_findings_are_valid_but_not_an_approval():
    package,contract,draft=case();draft['findings']=[]
    result=assemble(draft,package,contract)
    assert result['findings']==[] and result['coverage']==draft['coverage']
    assert 'approved' not in result and not contract['publication_ready']
