"""Exact source spans generated only after the caller filters role permissions."""
from copy import deepcopy
import re


PASSAGE_POLICY = 'speech-source-passages/1.0'
_BOUNDARY = re.compile(r'\n[ \t]*\n|(?m:^[ \t]*(?=[（(][0-9?？]{1,2}[:：][0-9?？]{1,2}[）)]))')


def source_passages(text: str) -> list[dict]:
    cuts = sorted({0, len(text), *(m.start() for m in _BOUNDARY.finditer(text))})
    spans = []
    for first, last in zip(cuts, cuts[1:]):
        while first < last:
            end = min(last, first + 640)
            if end < last:
                # Prefer a sentence/line edge without rewriting or reordering text.
                edges = [m.end() for m in re.finditer(r'[。！？\n]', text[first:end])]
                if edges and edges[-1] >= 160: end = first + edges[-1]
            start = first
            while start < end and text[start].isspace(): start += 1
            stop = end
            while stop > start and text[stop - 1].isspace(): stop -= 1
            if start < stop:
                spans.append({'id': f'p{len(spans) + 1:04d}', 'start': start, 'end': stop, 'text': text[start:stop]})
            first = end
    return spans


def passage_catalog(context):
    result = {(m['collection'], m['id']): source_passages(m['text']) for m in context['materials']}
    result.update({('discussion', d['id']): source_passages(d['text']) for d in context['discussion']})
    return result


def passage_wire_context(context):
    wire = deepcopy(context)
    for material in wire['materials']:
        material['passages'] = [{'id': p['id'], 'text': p['text']} for p in source_passages(material.pop('text'))]
    # Discussion remains the actual heard message; its passage IDs are explicit.
    for message in wire['discussion']:
        message['passages'] = [{'id': p['id'], 'text': p['text']} for p in source_passages(message.pop('text'))]
    return wire


class PassagePreparedMixin:
    def _wire_context(self, context): return passage_wire_context(context)
    def _extra_prepared_context(self, context): return {'source_context': deepcopy(context)}
    def _prepared_payload(self, frozen):
        payload = super()._prepared_payload(frozen)
        return {**payload, 'context': deepcopy(frozen['source_context'])}
