"""Reviewed server-owned hints and required retellings for guided text play.

The catalogue never enters a player response. The service projects current
labels and requested hints, or allowlisted finale answers after settlement.
"""
from copy import deepcopy
from hashlib import sha256
import re
import unicodedata

from src.fusion.package_validation import content_hash, validate_package
from src.fusion.package_play_rules import PlayRulesError

GUIDED_POLICY = 'package-guided-play/1.0'


def require(value, code='GUIDED_CONTENT_INVALID'):
    if not value:
        raise ValueError(code)


def safe_text(value, maximum=4000):
    return (type(value) is str and bool(value.strip()) and len(value) <= maximum
            and not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') and c not in '\n\t' for c in value))


def stable_id(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,95}', value) is not None


def digest(value):
    return sha256(value.encode()).hexdigest()


def valid_hash(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


class GuidedFlowContent:
    def __init__(self, package: dict, document: dict, *, source_package: dict | None = None):
        source_package = source_package or package
        require(validate_package(package)['valid'] and validate_package(source_package)['valid'])
        require(document['schema_version'] == 'guided-flow-content/1.0'
                and document['package_hash'] == content_hash(package)
                and document.get('source_package_hash', document['package_hash']) == content_hash(source_package)
                and package['schema_version'] == 'script-package/1.4')
        self.package_hash = content_hash(package)
        self.character_id = document['selected_character_id']
        characters = {c['id'] for c in package['characters']}
        phases = {p['id'] for p in package['phases']}
        source_hashes = {s['id']: s['sha256'] for s in source_package['sources']}
        require(self.character_id in characters)
        self._topics = {}
        self._retells = {}
        def sources(refs):
            require(type(refs) is list and bool(refs))
            for ref in refs:
                require(type(ref) is dict and ref.get('source_id') in source_hashes
                        and ref.get('sha256', source_hashes.get(ref.get('source_id'))) == source_hashes.get(ref.get('source_id')))
        require(0 < len(document['host_topics']) <= 100 and len(document['required_retells']) <= 100)
        for topic in document['host_topics']:
            require(stable_id(topic['id']) and topic['id'] not in self._topics
                    and topic['phase_id'] in phases and safe_text(topic['title'], 120)
                    and [level['level'] for level in topic['levels']] == [1, 2, 3]
                    and all(type(level['level']) is int for level in topic['levels'])
                    and topic.get('selected_character_id', self.character_id) == self.character_id)
            sources(topic['sources'])
            for level in topic['levels']:
                require(safe_text(level['text']))
                sources(level['source_refs'])
            self._topics[topic['id']] = deepcopy(topic)
        for item in document['required_retells']:
            collection = item['collection']
            require(collection in ('knowledge', 'evidence', 'memory') and item['id'] not in self._retells
                    and stable_id(item['id']) and safe_text(item['speech'], 1500)
                    and item.get('speech_sha256', digest(item['speech'])) == digest(item['speech'])
                    and item['owner_character_id'] in characters and item['kind'] == 'CLAIM')
            material = next((m for m in package['memories' if collection == 'memory' else collection]
                             if m['id'] == item['material_id']), None)
            require(material is not None and material['character_id'] == item['owner_character_id']
                    and digest(material['text']) == item['text_sha256']
                    and (material.get('retelling') == 'MUST_RETELL' or material.get('disclosure') == 'MUST_SHARE'))
            sources(item['sources'])
            self._retells[item['id']] = deepcopy(item)
        self.revision = content_hash(document)

    def topics(self, phase):
        return [{'id': t['id'], 'title': t['title'], 'phase_id': phase, 'max_level': 3}
                for t in self._topics.values() if t['phase_id'] == phase]

    def post_game_questions(self, phase):
        """Return only reviewed finale-answer text, without authoring metadata.

        Settlement and player ownership are enforced by the service before this
        read-only projection is attached to a player view. Unknown scopes fail
        closed; ordinary hints do not become post-game answers by default.
        """
        return deepcopy([
            {'id': topic['id'], 'title': topic['title'], 'text': level['text']}
            for topic in self._topics.values() if topic['phase_id'] == phase
            for level in topic['levels'] if level['level'] == 3
            and level.get('reveal_scope') == 'CURRENT_FINALE_QUESTION_ANSWER'
        ])

    def hint(self, phase, topic_id, level):
        topic = self._topics.get(topic_id)
        if not topic or topic['phase_id'] != phase or type(level) is not int or level not in (1, 2, 3):
            raise PlayRulesError('GUIDED_HINT_NOT_AVAILABLE')
        return {'topic_id': topic_id, 'title': topic['title'], 'phase_id': phase,
                'level': level, 'text': topic['levels'][level - 1]['text']}

    def pending(self, engine, already):
        view = engine.view()
        if view['settled'] or view['full_game']['phase_kind'] != 'INVESTIGATION' or view['full_game']['phone_busy']:
            return []
        found = []
        for entry in self._retells.values():
            key = (entry['collection'], entry['material_id'], entry['owner_character_id'])
            if key in already or entry['owner_character_id'] == self.character_id:
                continue
            try:
                material = engine.guided_material(entry['collection'], entry['material_id'], entry['owner_character_id'])
            except PlayRulesError:
                continue
            require(digest(material['text']) == entry['text_sha256'], 'GUIDED_MATERIAL_CHANGED')
            found.append(deepcopy(entry))
        return found
