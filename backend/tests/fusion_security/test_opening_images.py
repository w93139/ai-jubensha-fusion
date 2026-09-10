"""Opening image reads cannot acquire future, other-role or memory material."""
from unittest.mock import Mock

import pytest

from src.fusion.package_runtime import PackageRuntimeError
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.source_bundles import SourceBundleStore
from tests.fusion_security.test_full_play_images import PNG, image_package
from tests.fusion_security.test_package_runtime import runtime, publish, request, client


def opening_images(runtime, tmp_path):
    incoming = tmp_path / 'opening-images'
    incoming.mkdir()
    (incoming / 'pixel.png').write_bytes(PNG)
    store = SourceBundleStore(tmp_path / 'frozen-opening-images')
    bundle = store.freeze(incoming, {'schema_version': 'source-plan/1.0',
        'script_key': 'fictional-full-game', 'edition': 'test', 'sources': [
            {'relative_path': 'pixel.png', 'kind': 'original', 'material_type': 'clue', 'original_paths': []}]})
    release = publish(runtime, image_package())
    release['bundle_hash'] = bundle['bundle_hash']
    release['release_hash'] = content_hash(release)
    runtime.publisher.records[release['id']] = release
    runtime.publisher.source_store = store
    view = runtime.service.create(request(), 1)
    runtime.db.commit()
    return view, store, bundle


def test_opening_visuals_only_project_initial_owned_material(runtime, tmp_path):
    view, store, bundle = opening_images(runtime, tmp_path)
    assert view['visuals'] == [{'id': 'image-initial-a', 'collection': 'knowledge',
                              'material_id': 'initial-a', 'label': '虚构像素原图'}]
    assert 'pixel.png' not in canonical_json(view)
    assert 'image-initial-b' not in canonical_json(view)
    assert runtime.service.image(view['session_id'], 'image-initial-a', 1) == (PNG, 'image/png')
    runtime.publisher.current.clear()
    assert runtime.service.image(view['session_id'], 'image-initial-a', 1) == (PNG, 'image/png')
    assert runtime.service.get(view['session_id'], 1) == view


@pytest.mark.parametrize('owner, visual_id', [
    (2, 'image-initial-a'), (1, 'image-initial-b'), (1, 'image-evidence-find-key'),
    (1, 'image-memory-a'), (1, 'picture'), (1, '../pixel.png')])
def test_opening_denied_images_never_read_source_files(runtime, tmp_path, owner, visual_id):
    view, _, _ = opening_images(runtime, tmp_path)
    store = Mock()
    with pytest.raises(PackageRuntimeError, match='NOT_FOUND'):
        runtime.service.image(view['session_id'], visual_id, owner, source_store=store)
    assert not store.mock_calls


def test_opening_corrupt_image_is_sanitized(runtime, tmp_path):
    view, store, bundle = opening_images(runtime, tmp_path)
    (store.root / bundle['bundle_hash'] / 'files/pixel.png').write_bytes(b'PRIVATE_BAD_IMAGE')
    with pytest.raises(PackageRuntimeError, match='PACKAGE_SESSION_IMAGE_UNAVAILABLE'):
        runtime.service.image(view['session_id'], 'image-initial-a', 1)


@pytest.mark.parametrize('token, status', [(None, 401), ('disabled', 403), ('player-2', 404), ('player-1', 200)])
def test_opening_image_http_owner_mime_cache_and_no_mutation(client, runtime, tmp_path, token, status):
    view, _, _ = opening_images(runtime, tmp_path)
    response = client.get(f"/api/fusion/package-sessions/{view['session_id']}/images/image-initial-a",
                          headers={'Authorization': token} if token else {})
    assert response.status_code == status
    assert response.headers['Cache-Control'] == 'no-store'
    if status == 200:
        assert response.content == PNG
        assert response.headers['Content-Type'] == 'image/png'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert runtime.service.get(view['session_id'], 1) == view


def test_opening_response_and_image_share_exact_action_prerequisites(runtime, tmp_path):
    # The ordinary full-game fixture already binds the key image to an action;
    # having the image grant in the package is insufficient to show it early.
    view, _, _ = opening_images(runtime, tmp_path)
    assert all(v['collection'] == 'knowledge' for v in view['visuals'])
    with pytest.raises(PackageRuntimeError, match='NOT_FOUND'):
        runtime.service.image(view['session_id'], 'image-evidence-find-key', 1)
