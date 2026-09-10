"""Whole-image grants never imply permission to see other material or pages."""
from io import BytesIO
from copy import deepcopy
from hashlib import sha256
from unittest.mock import Mock

import pytest
from PIL import Image

from src.fusion.package_play import PackagePlayError
from src.fusion.package_play_engine import play_engine
from src.fusion.package_validation import validate_package, content_hash, canonical_json
from src.fusion.source_bundles import SourceBundleStore, SourceBundleError
from tests.fusion_security.test_package_full_play import full_package, advance, investigate
from tests.fusion_security.test_package_play_store import play, events
from tests.fusion_security.test_package_runtime import runtime, publish, request
from tests.fusion_security.test_package_play_http import play_http

_pixel = BytesIO()
Image.new('RGB', (1, 1), 'white').save(_pixel, format='PNG')
PNG = _pixel.getvalue()


def image_package():
    package = full_package()
    package['sources'].append({'id':'picture', 'relative_path':'pixel.png', 'sha256':sha256(PNG).hexdigest(),
        'kind':'original', 'media_type':'image/png', 'original_source_ids':[]})
    package['visuals']=[]
    for collection, identifier in [('knowledge','initial-a'),('knowledge','initial-b'),('evidence','evidence-find-key'),('memory','memory-a')]:
        rows=package['memories' if collection=='memory' else collection]
        next(r for r in rows if r['id']==identifier)['sources'].append({'source_id':'picture','page':1})
        package['visuals'].append({'id':'image-'+identifier,'collection':collection,'material_id':identifier,
            'source_id':'picture','label':'虚构像素原图','exposure':'WHOLE_ORIGINAL_IMAGE'})
    return package


def test_full_play_visual_projection_only_follows_acquired_human_material():
    package=image_package();assert validate_package(package)['valid']
    game=play_engine(package,'a')
    assert [v['id'] for v in game.view()['visuals']]==['image-initial-a']
    assert 'picture' not in canonical_json(game.view()['visuals'])
    advance(game); investigate(game,'find-key')
    assert {v['id'] for v in game.view()['visuals']}=={'image-initial-a','image-evidence-find-key'}
    game.observe_event(game.state()['memory_observed_sequence']+1,{'speaker':'b','text':'三角木片','phase_id':'investigate-one'})
    assert 'image-memory-a' in {v['id'] for v in game.view()['visuals']}


@pytest.mark.parametrize('bad',['foreign-source','missing-item','duplicate','not-image','not-original','implicit-crop'])
def test_full_play_visual_authoring_requires_explicit_whole_original_binding(bad):
    package=image_package(); visual=package['visuals'][0]
    if bad=='foreign-source': package['knowledge'][0]['sources'].pop()
    elif bad=='missing-item': visual['material_id']='missing'
    elif bad=='duplicate': package['visuals'].append(deepcopy(visual))
    elif bad=='not-image': package['sources'][-1]['media_type']='text/plain'
    elif bad=='not-original': package['sources'][-1].update(kind='supplement',provenance_note='编辑图')
    else: visual['exposure']='IMPLIED_CROP'
    assert not validate_package(package)['valid']


def image_play(play,tmp_path):
    incoming=tmp_path/'images';incoming.mkdir();(incoming/'pixel.png').write_bytes(PNG)
    store=SourceBundleStore(tmp_path/'frozen-images')
    bundle=store.freeze(incoming,{'schema_version':'source-plan/1.0','script_key':'fictional-full-game','edition':'test',
        'sources':[{'relative_path':'pixel.png','kind':'original','material_type':'clue','original_paths':[]}]})
    release=publish(play,image_package());release['bundle_hash']=bundle['bundle_hash'];release['release_hash']=content_hash(release)
    play.publisher.records[release['id']]=release
    opening=play.service.create(request(),1);play.db.commit()
    view=play.play.create({'opening_session_id':opening['session_id'],'idempotency_key':'create-images'},1);play.db.commit()
    return view['play_id'],store,bundle


def test_full_play_image_uses_frozen_path_identity_not_candidate_source_id(play,tmp_path):
    identifier,store,bundle=image_play(play,tmp_path)
    assert store.manifest(bundle['bundle_hash']).sources[0].id!='picture'
    before=len(events(play)); assert play.play.image(identifier,'image-initial-a',1,source_store=store)==(PNG,'image/png')
    play.publisher.current.clear()
    assert play.play.image(identifier,'image-initial-a',1,source_store=store)==(PNG,'image/png')
    assert len(events(play))==before and play.sdk.chat_completion.await_count==0


@pytest.mark.parametrize('owner,visual',[(2,'image-initial-a'),(1,'image-initial-b'),(1,'image-evidence-find-key'),
    (1,'image-memory-a'),(1,'picture'),(1,'../pixel.png')])
def test_full_play_image_denied_before_reading_files(play,tmp_path,owner,visual):
    identifier,_,_=image_play(play,tmp_path);store=Mock()
    with pytest.raises(PackagePlayError,match='NOT_FOUND'): play.play.image(identifier,visual,owner,source_store=store)
    assert not store.mock_calls


def test_full_play_image_corruption_is_sanitized(play,tmp_path):
    identifier,store,bundle=image_play(play,tmp_path)
    (store.root/bundle['bundle_hash']/'files/pixel.png').write_bytes(b'PRIVATE_CORRUPTION_SENTINEL')
    with pytest.raises(PackagePlayError,match='IMAGE_UNAVAILABLE'): play.play.image(identifier,'image-initial-a',1,source_store=store)


@pytest.mark.parametrize('token,status',[(None,401),('disabled',403),('player',200)])
def test_full_play_image_http_requires_active_owner_and_no_cache(play_http,token,status):
    client,service=play_http;service.image=Mock(return_value=(PNG,'image/png'))
    result=client.get('/api/fusion/package-plays/play-x/images/visual-x',headers={'Authorization':'Bearer '+token} if token else {})
    assert result.status_code==status
    if status==200:
        service.image.assert_called_once_with('play-x','visual-x',2)
        assert result.content==PNG and result.headers['content-type']=='image/png'
        assert result.headers['Cache-Control']=='no-store' and result.headers['X-Content-Type-Options']=='nosniff'
    else: service.image.assert_not_called()
