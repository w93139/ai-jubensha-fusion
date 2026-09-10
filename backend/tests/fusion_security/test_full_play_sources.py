"""The full finale's nested source locations are verified, not only mechanics."""
from copy import deepcopy
from hashlib import sha256

import pytest

from src.fusion.source_bundles import SourceBundleStore
from tests.fusion_security.test_package_full_play import full_package


@pytest.mark.parametrize('target',['phase','vote','identity','question','fact','goal','part','ending'])
def test_full_play_nested_finale_sources_have_explicit_new_verifier(tmp_path,target):
    incoming=tmp_path/'source';incoming.mkdir();data=b'Fictional line one\nFictional line two\n'
    (incoming/'fiction.txt').write_bytes(data)
    store=SourceBundleStore(tmp_path/'bundles')
    bundle=store.freeze(incoming,{'schema_version':'source-plan/1.0','script_key':'fictional-full-game',
        'edition':'v1','sources':[{'relative_path':'fiction.txt','kind':'original','material_type':'rules'}]})
    package=full_package();package['sources'][0]['sha256']=sha256(data).hexdigest()
    valid=store.verify(bundle['bundle_hash'],document=package,persist=False)
    assert valid['valid'] and valid['verifier_version']=='source-verifier/1.4'
    f=package['full_play']['finale']
    node={'phase':package['full_play']['phases'][0], 'vote':f['votes'], 'identity':f['votes']['identities'][0],
        'question':f['questions'][0], 'fact':f['facts'][0], 'goal':f['goals'][0],
        'part':f['goals'][0]['parts'][0], 'ending':f['endings'][0]['branches'][0]}[target]
    node['sources']=[{'source_id':'fiction','anchor':'L999'}]
    report=store.verify(bundle['bundle_hash'],document=package,persist=False)
    assert not report['valid'] and {'code':'SOURCE_LOCATION_UNVERIFIED','source_id':'fiction'} in report['issues']
