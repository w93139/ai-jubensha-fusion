"""Reading repairs are exact, optional and never rewrite engine or dialogue."""
from copy import deepcopy

import pytest

from src.fusion.package_presentation_repair import PackagePresentationRepair, text_hash
from src.fusion.package_validation import content_hash
from src.fusion.package_play_engine import play_engine
from tests.fusion_security.test_full_play_images import image_package
from tests.fusion_security.test_package_full_play import advance, investigate


def repair_fixture():
    original = image_package()
    extra = original['visuals'].pop(2)
    revised = deepcopy(original)
    revised['content_version'] += '-corrected'
    before = next(x for x in original['knowledge'] if x['id'] == 'initial-a')
    after = next(x for x in revised['knowledge'] if x['id'] == 'initial-a')
    after['text'] += '\n7、虚构来源校勘。'
    note = deepcopy(after)
    note.update(id='opening-note', text='虚构开场情境补充。')
    revised['knowledge'].append(note)
    revised['visuals'].append(extra)
    records = [{'collection': 'knowledge', 'material_id': before['id'],
                'old_text_sha256': text_hash(before['text']), 'new_text_sha256': text_hash(after['text'])}]
    options = {'expected_original_hash': content_hash(original), 'supplement_ids': ('opening-note',),
               'extra_visual_ids': (extra['id'],)}
    return original, revised, records, options


def test_repair_exact_bound_material_does_not_touch_input_engine_or_saved_dialogue():
    old, new, records, options = repair_fixture()
    repair = PackagePresentationRepair(old, new, records, **options)
    game = play_engine(old, 'a')
    view = {**game.view(), 'package_hash': content_hash(old), 'selected_character_id': 'a'}
    text = view['private_knowledge'][0]['text']
    view['discussion'] = {'entries': [{'text': text}]}
    snapshot, state = deepcopy(view), deepcopy(game.state())
    result = repair.apply(view)
    assert result['private_knowledge'][0]['text'] != text
    assert result['discussion'] == view['discussion']
    assert result['reading_supplements'] == [{'id': 'opening-note', 'text': '虚构开场情境补充。'}]
    assert view == snapshot and game.state() == state
    assert result['transcription_revision'] == repair.revision


def test_repair_requires_both_exact_package_and_exact_original_text():
    old, new, records, options = repair_fixture()
    repair = PackagePresentationRepair(old, new, records, **options)
    view = {**play_engine(old, 'a').view(), 'package_hash': content_hash(new), 'selected_character_id': 'a'}
    assert repair.apply(view) is view
    view['package_hash'] = content_hash(old)
    view['private_knowledge'][0]['text'] = 'Unrelated same-ID text'
    assert repair.apply(view)['private_knowledge'][0]['text'] == 'Unrelated same-ID text'


def test_repair_visuals_follow_acquisition_and_supplements_follow_role():
    old, new, records, options = repair_fixture()
    repair = PackagePresentationRepair(old, new, records, **options)
    game = play_engine(old, 'a')
    project = lambda: repair.apply({**game.view(), 'package_hash': content_hash(old), 'selected_character_id': 'a'})
    assert 'image-evidence-find-key' not in {x['id'] for x in project()['visuals']}
    advance(game); investigate(game, 'find-key')
    assert 'image-evidence-find-key' in {x['id'] for x in project()['visuals']}
    other = repair.apply({**play_engine(old, 'b').view(), 'package_hash': content_hash(old), 'selected_character_id': 'b'})
    assert 'reading_supplements' not in other
    assert not any(x['id'] == 'initial-a' for x in other['private_knowledge'])
    assert repair.visual('wrong-hash', 'image-evidence-find-key') is None


def test_ending_repair_uses_truth_id_even_when_two_truths_have_identical_text():
    old, new, records, options = repair_fixture()
    first = old['truth'][0]
    second = deepcopy(first); second['id'] = 'unmodified-truth'
    old['truth'].append(second); new['truth'].append(deepcopy(second))
    corrected = next(item for item in new['truth'] if item['id'] == first['id'])
    corrected['text'] += '（已校勘）'
    records.append({'collection': 'truth', 'material_id': first['id'],
                    'old_text_sha256': text_hash(first['text']), 'new_text_sha256': text_hash(corrected['text'])})
    options['expected_original_hash'] = content_hash(old)
    repair = PackagePresentationRepair(old, new, records, **options)
    view = {'package_hash': content_hash(old), 'selected_character_id': 'b', 'full_game': {'result': {'endings': [
        {'truth_ids': [first['id'], second['id']], 'texts': [first['text'], second['text']]}]}}}
    result = repair.apply(view)['full_game']['result']['endings'][0]
    assert result['texts'] == [corrected['text'], second['text']]


@pytest.mark.parametrize('bad', ['base', 'old-text', 'new-text', 'permission', 'future-note', 'unknown-visual', 'duplicate-visual'])
def test_registration_rejects_mismatched_or_permission_changing_repairs(bad):
    old, new, records, options = repair_fixture()
    if bad == 'base': options['expected_original_hash'] = '0' * 64
    elif bad == 'old-text': records[0]['old_text_sha256'] = '0' * 64
    elif bad == 'new-text': records[0]['new_text_sha256'] = '0' * 64
    elif bad == 'permission': next(x for x in new['knowledge'] if x['id'] == 'initial-a')['disclosure'] = 'MAY_SHARE'
    elif bad == 'future-note': next(x for x in new['knowledge'] if x['id'] == 'opening-note')['release']['phase_id'] = 'investigate-one'
    elif bad == 'unknown-visual': options['extra_visual_ids'] = ('missing',)
    else: options['extra_visual_ids'] *= 2
    with pytest.raises(ValueError, match='PRESENTATION_REPAIR'):
        PackagePresentationRepair(old, new, records, **options)
