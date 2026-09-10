"""Server-authored, source-bound interaction plan; never a player truth bundle."""
from copy import deepcopy

from src.fusion.package_guided_flow import require, safe_text, stable_id, digest
from src.fusion.package_validation import content_hash, validate_package
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.topic_answer_checks import validate_contract, freeze_contract

SINGLE_POLICY = 'package-single-player/1.0'


class SinglePlayerContent:
    def __init__(self, package, document, *, source_package=None):
        source_package = source_package or package
        require(validate_package(package)['valid'] and validate_package(source_package)['valid'])
        require(document['schema_version'] == 'single-player-content/1.0'
                and document['package_hash'] == content_hash(package)
                and document.get('source_package_hash', document['package_hash']) == content_hash(source_package))
        self.package_hash = content_hash(package)
        self.character_id = document['selected_character_id']
        characters = {c['id'] for c in package['characters']}
        phases = {p['id'] for p in package['phases']}
        sources = {s['id']: s['sha256'] for s in source_package['sources']}
        materials = {(c, m['id']): m for c, key in [('knowledge','knowledge'), ('evidence','evidence'), ('memory','memories')]
                     for m in package.get(key, [])}
        require(self.character_id in characters and safe_text(document['operation_rules'], 8000))
        def refs(values):
            require(type(values) is list and bool(values))
            for r in values:
                require(r['source_id'] in sources and r.get('sha256', sources[r['source_id']]) == sources[r['source_id']])
        def material(r):
            require((r['collection'], r['id']) in materials)
            return materials[(r['collection'], r['id'])]
        self.rules = document['operation_rules']
        self.replaces = set()
        for r in document['replaces_materials']:
            m = material(r)
            require(r['collection'] == 'knowledge' and m['character_id'] in (None, self.character_id))
            self.replaces.add((r['collection'], r['id']))
        self.stages = {}
        for stage in document['phase_guides']:
            require(stage['phase_id'] in phases and stage['phase_id'] not in self.stages
                    and safe_text(stage['goal'], 300) and safe_text(stage['completion'], 1000)
                    and 1 <= len(stage['instructions']) <= 8 and all(safe_text(x, 1000) for x in stage['instructions']))
            refs(stage['source_refs'])
            self.stages[stage['phase_id']] = {k: deepcopy(stage[k]) for k in ('phase_id','goal','instructions','completion')}
        require(set(self.stages) == phases)
        self.topics = {}
        for topic in document['topics']:
            require(stable_id(topic['id']) and topic['id'] not in self.topics and topic['phase_id'] in phases
                    and safe_text(topic['title'], 120) and 1 <= len(topic['intents']) <= 3
                    and topic['intents'][0]['id'] == 'initial')
            refs(topic['source_refs'])
            ids = set()
            for intent in topic['intents']:
                require(stable_id(intent['id']) and intent['id'] not in ids
                        and safe_text(intent['label'], 100) and safe_text(intent['question'], 800))
                ids.add(intent['id'])
            require(type(topic['display_gate']) is list)
            for group in topic['display_gate']:
                require(type(group) is list and bool(group))
                for r in group: material(r)
            actors = set()
            for responder in topic['responders']:
                actor = responder['character_id']
                require(actor in characters - {self.character_id} and actor not in actors
                        and responder['channels'] and set(responder['channels']) <= {'PUBLIC','PRIVATE'})
                actors.add(actor)
                require(responder['basis'] and len(responder['basis']) <= 20)
                for r in responder['basis']:
                    m = material(r)
                    require(m['character_id'] in (None, actor) and digest(m['text']) == r['text_sha256'])
                    refs(r['source_refs'])
                require({a['intent_id'] for a in responder['answers']} == ids
                        and len(responder['answers']) == len(ids)
                        and all(safe_text(a['fixed_fallback'], 1000) for a in responder['answers']))
                require(type(responder['disclosure']) in (str, dict))
                if 'answer_contract' in responder:
                    validate_contract(responder['answer_contract'],
                        {(r['collection'],r['id']):material(r) for r in responder['basis']}, ids)
            require(bool(actors))
            self.topics[topic['id']] = deepcopy(topic)
        self.revision = content_hash(document)

    def options(self, engine, turns):
        projection = engine.view()
        if projection['settled'] or projection['full_game']['phase_kind'] != 'INVESTIGATION':
            return []
        seen = {('knowledge', m['id']) for m in projection['public_knowledge'] + projection['private_knowledge']}
        seen |= {('evidence', m['id']) for m in projection['public_evidence'] + projection['private_evidence']}
        seen |= {('memory', m['id']) for m in projection.get('memories', {}).get('entries', [])}
        result = []
        for topic in self.topics.values():
            if topic['phase_id'] != projection['current_phase']['id']:
                continue
            if topic['display_gate'] and not any(all((r['collection'],r['id']) in seen for r in group) for group in topic['display_gate']):
                continue
            responders = []
            for responder in topic['responders']:
                known = {(m['collection'],m['id']) for m in engine.dialogue_context(responder['character_id'])['materials']}
                if not all((r['collection'],r['id']) in known for r in responder['basis']):
                    continue
                done = {t['intent_id'] for t in turns if t['topic_id'] == topic['id'] and t['character_id'] == responder['character_id']}
                answered = any(t['topic_id'] == topic['id'] and t['character_id'] == responder['character_id']
                               and t['intent_id'] == 'initial' and t.get('status') in ('OK','FALLBACK') for t in turns)
                responders.append({'character_id': responder['character_id'], 'channels': responder['channels'],
                    'intents': [{**i, 'available': i['id'] not in done and (i['id'] == 'initial' or answered)} for i in topic['intents']]})
            if responders:
                result.append({'id': topic['id'], 'title': topic['title'], 'responders': responders})
        return result

    def select(self, engine, turns, payload):
        options = self.options(engine, turns)
        option = next((t for t in options if t['id'] == payload['topic_id']), None)
        responder = next((r for r in (option or {}).get('responders', []) if r['character_id'] == payload['character_id']), None)
        intent = next((i for i in (responder or {}).get('intents', []) if i['id'] == payload['intent_id'] and i['available']), None)
        if intent is None or payload['channel'] not in responder['channels']:
            raise PlayRulesError('SINGLE_TOPIC_NOT_AVAILABLE')
        topic = self.topics[payload['topic_id']]
        private = next(r for r in topic['responders'] if r['character_id'] == payload['character_id'])
        fallback = next(a['fixed_fallback'] for a in private['answers'] if a['intent_id'] == payload['intent_id'])
        result = {'title': topic['title'], 'question': intent['question'], 'basis': private['basis'],
                'disclosure': private['disclosure'], 'fallback': fallback, 'catalog_hash': self.revision}
        if 'answer_contract' in private:
            view = engine.view()
            public = {(c,m['id']) for c in ('knowledge','evidence') for m in view['public_'+c]}
            refs = [{'collection':r['collection'],'id':r['id']} for r in private['basis'] if (r['collection'],r['id']) in public]
            result['answer_contract'] = freeze_contract(private['answer_contract'],payload['intent_id'],refs)
        return result

    def replace_rules(self, view):
        inserted = False
        for key in ('public_knowledge','private_knowledge'):
            retained = []
            for item in view[key]:
                if ('knowledge', item['id']) in self.replaces:
                    if inserted:
                        continue
                    item['text'] = self.rules
                    item.pop('original_text', None)
                    item.pop('original', None)
                    inserted = True
                retained.append(item)
            view[key] = retained
        return view


def topic_response_request(turn):
    private = turn['channel'] == 'PRIVATE'
    return {'schema_version': 'package-private-dialogue-command/1.0' if private else 'package-dialogue-command/1.0',
            'expected_revision': turn['sequence'], 'idempotency_key': content_hash({'topic_turn':turn['id']}),
            'character_id': turn['character_id'], 'action': 'RESPOND_PRIVATE' if private else 'RESPOND', 'reply_to':turn['reply_to']}


def topic_status(turn, state):
    if turn.get('answer') is not None:
        return 'FALLBACK'
    request = topic_response_request(turn)
    key = request['idempotency_key']
    if key in state.pending:
        return 'PENDING'
    receipt = next((r for r in state.response_requests + state.private_response_requests if r['request_id'] == key), None)
    return ('OK' if receipt['status'] == 'OK' else 'FAILED') if receipt else 'READY'
