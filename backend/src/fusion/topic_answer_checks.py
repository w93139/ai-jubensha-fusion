"""Opt-in, authored acceptance checks for finite topics, not a semantic oracle.

Only new topic events containing this version are checked. A conservative
rejection leaves the paid receipt intact and permits the reviewed fixed answer.
"""
from copy import deepcopy
import re
import unicodedata

from src.fusion.package_guided_flow import require, safe_text
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.speech_passages import source_passages

VERSION = 'topic-answer-check/1.0'
BASIS_VERSION = 'topic-answer-check/1.1'
DISCLOSURE_VERSION = 'topic-answer-check/1.2'
DISCLOSURE_GUIDANCE = (
    'forbidden_terms是本题明确禁止披露的细节线索，公开和私聊同样适用。'
    '不要提及这些细节，也不要改用代称、否定句、反问或动作暗示来透露同一秘密。'
    '只回答本题允许的亲历节点，未问到的私人经过不主动补充；允许明确保留私事。'
)
BASIS_GUIDANCE = (
    'conditional_basis列出本题已审核的表达与来源要求：某段出现when_any中的表达时，'
    '该段basis必须包括requires指定的资料和全部passage_ids，其他段的引用不能代替。'
    '先选真正支撑本段各项事实的片段，再用自己的话表达；不能只引用相邻段落，'
    '也不能用未引用的新事实解释原因或结果。未提到的事实不用为满足引用而补讲。'
    '仍保留来源中的人物归属、否定和不确定程度，不照抄原卡。'
)


def normalized(text):
    return re.sub(r'\W+', '', unicodedata.normalize('NFKC', text)).lower()


def validate_contract(contract, materials, intent_ids):
    require(type(contract) is dict)
    version = contract.get('schema_version')
    require(version in (VERSION, BASIS_VERSION, DISCLOSURE_VERSION)
            and set(contract) <= {'schema_version','required_terms','reported_passages','conditional_terms',
                                 'reject_public_personal_observation','reject_unsupported_certainty',
                                 'public_document_basis'}
            | ({'conditional_basis'} if version in (BASIS_VERSION, DISCLOSURE_VERSION) else set())
            | ({'forbidden_terms'} if version == DISCLOSURE_VERSION else set()))
    if version == DISCLOSURE_VERSION:
        terms = contract.get('forbidden_terms', [])
        require(type(terms) is list and len(terms) <= 32
                and all(safe_text(term, 80) and normalized(term) for term in terms))
        require(len({normalized(term) for term in terms}) == len(terms))
    for flag in ('reject_public_personal_observation','reject_unsupported_certainty'):
        require(type(contract.get(flag, False)) is bool)
    all_text = normalized('\n'.join(m['text'] for m in materials.values()))
    require(type(contract.get('required_terms', {})) is dict)
    for intent, groups in contract.get('required_terms', {}).items():
        require(intent in intent_ids and type(groups) is list and len(groups) <= 8)
        for group in groups:
            require(type(group) is list and 1 <= len(group) <= 8 and all(safe_text(t,80)
                    and normalized(t) in all_text for t in group))
    for ref in contract.get('reported_passages', []):
        material = materials.get((ref['collection'],ref['id']))
        require(material is not None)
        known = {p['id'] for p in source_passages(material['text'])}
        require(ref['passage_ids'] and set(ref['passage_ids']) <= known)
    documents = contract.get('public_document_basis', [])
    require(type(documents) is list and len(documents) <= 16)
    for ref in documents:
        require(type(ref) is dict and set(ref) == {'collection','id'})
        material = materials.get((ref['collection'],ref['id']))
        require(material is not None and re.search(r'报纸|報紙|报刊|剪报|头条|頭條|日记|档案|记录', material['text']))
    for condition in contract.get('conditional_terms', []):
        require(set(condition) == {'when_any','requires_any'})
        for key in ('when_any','requires_any'):
            require(type(condition[key]) is list and 1 <= len(condition[key]) <= 16
                    and all(safe_text(t,80) for t in condition[key]))
    if version in (BASIS_VERSION, DISCLOSURE_VERSION):
        validate_conditional_basis(contract.get('conditional_basis', []), materials)


def validate_conditional_basis(rules, materials):
    """Validate authored source dependencies; aliases need separate source review."""
    require(type(rules) is list and len(rules) <= 16)
    for rule in rules:
        require(type(rule) is dict and set(rule) == {'when_any', 'requires'})
        terms, refs = rule['when_any'], rule['requires']
        require(type(terms) is list and 1 <= len(terms) <= 16
                and all(safe_text(term, 80) and normalized(term) for term in terms))
        require(len({normalized(term) for term in terms}) == len(terms))
        require(type(refs) is list and 1 <= len(refs) <= 3)
        keys = set()
        for ref in refs:
            require(type(ref) is dict and set(ref) == {'collection', 'id', 'passage_ids'})
            require(ref['collection'] in ('knowledge', 'evidence', 'memory') and type(ref['id']) is str)
            key = (ref['collection'], ref['id'])
            require(key in materials and key not in keys)
            keys.add(key)
            selected = ref['passage_ids']
            require(type(selected) is list and 1 <= len(selected) <= 6
                    and all(type(p) is str for p in selected))
            allowed = {p['id'] for p in source_passages(materials[key]['text'])}
            require(len(set(selected)) == len(selected) and set(selected) <= allowed)


def validate_segment_basis(segment, rules):
    plain = normalized(segment.get('text') or '')
    selected = {(ref['collection'], ref['id']): set(ref.get('passage_ids', []))
                for ref in segment['basis']}
    for rule in rules:
        if any(normalized(term) in plain for term in rule['when_any']):
            if any(not set(ref['passage_ids']) <= selected.get((ref['collection'], ref['id']), set())
                   for ref in rule['requires']):
                raise PlayRulesError('SINGLE_TOPIC_BASIS_INCOMPLETE')


def freeze_contract(contract, intent, public_basis):
    result = deepcopy(contract)
    result['required_terms'] = result.get('required_terms', {}).get(intent, [])
    result['public_basis'] = deepcopy(public_basis)
    return result


def document_reading(prefix, suffix):
    document = r'(?:报纸|報紙|报刊|剪报|日记|档案|记录)'
    if re.search(document + r'(?:上|中|里|里面)(?:面|的内容)?$', prefix):
        return True
    # A reading discovery may have a location/date before the document. Do not
    # exempt an unrelated observed event merely followed by a newspaper quote.
    era_date = r'(?:清[\u4e00-\u9fff]{1,4}|民国|民國)[元零〇一二三四五六七八九十百]{1,6}年'
    lead = r'(?:(?:抽屉里|桌子上|桌上|这里|那里|有一张|有张|有一份|有份|一张|一份|那张|这张|放着|旧的|旧|的|\d+(?:年|月|日)|' + era_date + r'|《[^》]{1,24}》|\s)){0,12}'
    attribution = r'(?:[^。！？；\n]{0,32}?(?:头条|頭條|版面|上面|里面|内容|上|中)[^。！？；\n]{0,8}?)?(?:说|写|記載|记载|报道|提到)'
    return bool(re.match(r'了?' + lead + document + attribution, suffix))


def personal_observation(text, *, allow_document_reading=False):
    for match in re.finditer(r'我(?:们)?([^。！？；\n]{0,18}?)(?:看见|看到|见到|见过|发现|目睹)', text):
        if re.search(r'听说|据说|听.{0,8}说|转述|告诉', match.group(1)):
            continue
        if allow_document_reading and document_reading(match.group(1),text[match.end():]):
            continue
        return True
    return False


def asserted_certainty(text):
    # Preserve explicit uncertainty; inspect every later assertion separately.
    negatives = ('不','不能','无法','难以','未能','未必','并非','不是','不能说','不能说这',
                 '不能说这就','无法说','不能认为','不能认定','没有证据说明','没有依据说')
    for match in re.finditer(r'肯定|一定|准是|必然|百分之百|绝对', text):
        prefix = re.split(r'[。！？；，\n]', text[:match.start()])[-1].rstrip()
        if prefix.endswith(('不能不','不得不','并非不','不是不')):
            return True
        if not prefix.endswith(negatives):
            return True
    return False


def validate_topic_answer(speech, turn):
    contract = turn.get('answer_contract') if turn else None
    if contract is None:
        return
    if contract.get('schema_version') not in (VERSION, BASIS_VERSION, DISCLOSURE_VERSION):
        raise PlayRulesError('SINGLE_TOPIC_ANSWER_CONTRACT_INVALID')
    text = '\n'.join(s.get('text') or '' for s in speech['segments'])
    plain = normalized(text)
    if contract['schema_version'] == DISCLOSURE_VERSION:
        if any(normalized(term) in plain for term in contract.get('forbidden_terms', [])):
            raise PlayRulesError('SINGLE_TOPIC_DISCLOSURE_FORBIDDEN')
    if any(not any(normalized(term) in plain for term in group) for group in contract.get('required_terms', [])):
        raise PlayRulesError('SINGLE_TOPIC_ANSWER_INCOMPLETE')
    public = {(r['collection'],r['id']) for r in contract.get('public_basis', [])}
    documents = {(r['collection'],r['id']) for r in contract.get('public_document_basis', [])}
    reported = {(r['collection'],r['id']):set(r['passage_ids']) for r in contract.get('reported_passages', [])}
    for segment in speech['segments']:
        value = segment.get('text') or ''
        if contract['schema_version'] in (BASIS_VERSION, DISCLOSURE_VERSION):
            validate_segment_basis(segment, contract.get('conditional_basis', []))
        if contract.get('reject_unsupported_certainty') and asserted_certainty(value):
            raise PlayRulesError('SINGLE_TOPIC_CERTAINTY_UNSUPPORTED')
        for rule in contract.get('conditional_terms', []):
            if any(t in value for t in rule['when_any']) and not any(t in value for t in rule['requires_any']):
                raise PlayRulesError('SINGLE_TOPIC_QUALIFIER_REQUIRED')
        if personal_observation(value):
            for basis in segment['basis']:
                key = (basis['collection'],basis['id'])
                if (contract.get('reject_public_personal_observation') and key in public
                        and personal_observation(value, allow_document_reading=key in documents)):
                    raise PlayRulesError('SINGLE_TOPIC_PUBLIC_IS_NOT_PERSONAL')
                if key in reported and (not basis.get('passage_ids') or reported[key] & set(basis['passage_ids'])):
                    raise PlayRulesError('SINGLE_TOPIC_REPORT_IS_NOT_PERSONAL')
