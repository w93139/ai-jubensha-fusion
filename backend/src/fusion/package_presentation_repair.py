"""Explicit, source-reviewed reading corrections for immutable historical games.

Never used by the event engine, model context, scoring, or saved dialogue.
Registration is server-owned and binds two validated packages plus an exact
reviewed change list; the API cannot submit or select a repair registry.
"""
from copy import deepcopy
from hashlib import sha256

from src.fusion.package_validation import content_hash, validate_package


def text_hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class PackagePresentationRepair:
    def __init__(self, original: dict, revised: dict, records: list[dict], *,
                 expected_original_hash: str, supplement_ids: tuple[str, ...] = (),
                 extra_visual_ids: tuple[str, ...] = ()):
        if (len(set(supplement_ids)) != len(supplement_ids) or len(set(extra_visual_ids)) != len(extra_visual_ids)
                or content_hash(original) != expected_original_hash or not validate_package(original)['valid']
                or not validate_package(revised)['valid']
                or original['initial_phase_id'] != revised['initial_phase_id']
                or original['characters'] != revised['characters']):
            raise ValueError('PRESENTATION_REPAIR_PACKAGE_INVALID')
        self.package_hash = expected_original_hash
        self._changes = {}
        for record in records:
            collection, identifier = record['collection'], record['material_id']
            if collection not in ('knowledge', 'evidence', 'memories', 'truth'):
                raise ValueError('PRESENTATION_REPAIR_COLLECTION_INVALID')
            before = next((x for x in original[collection] if x['id'] == identifier), None)
            after = next((x for x in revised[collection] if x['id'] == identifier), None)
            if (not before or not after or (collection, identifier) in self._changes
                    or record['old_text_sha256'] != text_hash(before['text'])
                    or record['new_text_sha256'] != text_hash(after['text'])
                    or {k: v for k, v in before.items() if k not in ('text', 'sources')}
                    != {k: v for k, v in after.items() if k not in ('text', 'sources')}):
                raise ValueError('PRESENTATION_REPAIR_ITEM_INVALID')
            self._changes[collection, identifier] = (before['text'], after['text'])
        self._supplements = []
        for identifier in supplement_ids:
            item = next((x for x in revised['knowledge'] if x['id'] == identifier), None)
            if (not item or any(x['id'] == identifier for x in original['knowledge'])
                    or item['visibility'] != 'CHARACTER_PRIVATE' or not item['character_id']
                    or item['release']['phase_id'] != original['initial_phase_id']
                    or item['release'].get('required_public_evidence_ids') or item['release'].get('required_action_ids')):
                raise ValueError('PRESENTATION_REPAIR_SUPPLEMENT_INVALID')
            self._supplements.append(deepcopy(item))
        self._visuals = []
        for identifier in extra_visual_ids:
            visual = next((x for x in revised.get('visuals', []) if x['id'] == identifier), None)
            if not visual or any(x['id'] == identifier for x in original.get('visuals', [])):
                raise ValueError('PRESENTATION_REPAIR_VISUAL_INVALID')
            collection = 'memories' if visual['collection'] == 'memory' else visual['collection']
            item = next((x for x in original[collection] if x['id'] == visual['material_id']), None)
            revised_item = next((x for x in revised[collection] if x['id'] == visual['material_id']), None)
            source = next((x for x in original['sources'] if x['id'] == visual['source_id']), None)
            revised_source = next((x for x in revised['sources'] if x['id'] == visual['source_id']), None)
            source_index = {x['id']: x for x in revised['sources']}
            allowed_sources = {ref['source_id'] for ref in (revised_item or {}).get('sources', [])}
            allowed_sources |= {original_id for source_id in tuple(allowed_sources)
                                for original_id in source_index[source_id].get('original_source_ids', [])}
            if (not item or not revised_item or source is None or source != revised_source or source['kind'] != 'original'
                    or source['media_type'] not in ('image/png', 'image/jpeg')
                    or visual['exposure'] != 'WHOLE_ORIGINAL_IMAGE'
                    or {k: v for k, v in item.items() if k not in ('text', 'sources')}
                    != {k: v for k, v in revised_item.items() if k not in ('text', 'sources')}
                    or source['id'] not in allowed_sources):
                raise ValueError('PRESENTATION_REPAIR_VISUAL_SOURCE_INVALID')
            self._visuals.append(deepcopy(visual))
        self.revision = content_hash({'base': self.package_hash, 'revised': content_hash(revised),
            'records': records, 'supplements': list(supplement_ids), 'visuals': list(extra_visual_ids)})

    def visual(self, package_hash: str, visual_id: str) -> dict | None:
        if package_hash != self.package_hash:
            return None
        return next((deepcopy(v) for v in self._visuals if v['id'] == visual_id), None)

    def apply(self, view: dict) -> dict:
        if view.get('package_hash') != self.package_hash:
            return view
        result = deepcopy(view)
        visible = set()
        def correct(collection, item):
            pair = self._changes.get((collection, item.get('id')))
            if pair and item.get('text') == pair[0]:
                item['text'] = pair[1]
        for collection in ('knowledge', 'evidence'):
            for prefix in ('public_', 'private_'):
                for item in result.get(prefix + collection, []):
                    correct(collection, item); visible.add((collection, item['id']))
        for item in (result.get('memories') or {}).get('entries', []):
            correct('memories', item); visible.add(('memory', item['id']))
        for item in (result.get('settlement') or {}).get('truths', []):
            correct('truth', item)
        for ending in ((result.get('full_game') or {}).get('result') or {}).get('endings', []):
            if len(ending.get('truth_ids', [])) != len(ending.get('texts', [])):
                continue
            for index, identifier in enumerate(ending['truth_ids']):
                pair = self._changes.get(('truth', identifier))
                if pair and ending['texts'][index] == pair[0]:
                    ending['texts'][index] = pair[1]
        notes = [{'id': x['id'], 'text': x['text']} for x in self._supplements
                 if x['character_id'] == result.get('selected_character_id')]
        if notes:
            result['reading_supplements'] = notes
        extras = [{k: v[k] for k in ('id', 'collection', 'material_id', 'label')}
                  for v in self._visuals if (v['collection'], v['material_id']) in visible]
        if extras:
            existing = result.setdefault('visuals', [])
            existing_ids = {x['id'] for x in existing}
            existing.extend(v for v in extras if v['id'] not in existing_ids)
        result['transcription_revision'] = self.revision
        return result
