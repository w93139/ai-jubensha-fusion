"""Private, sealed structured answers; no model judgement or live-game authority.

This pure component does not enable a package version, publish a script, or
decide a commercial script's scoring policy. The caller must authenticate the
actor and bind the reviewed rubric to its immutable game/source snapshot.
"""
from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.script_package import PackageModel, StableId, References, Digest
from src.fusion.package_validation import content_hash


class AnswerOption(PackageModel):
    id: StableId
    label: str = Field(min_length=1, max_length=300, pattern=r'\S')


class FinaleQuestion(PackageModel):
    id: StableId
    character_id: StableId
    prompt: str = Field(min_length=1, max_length=1000, pattern=r'\S')
    options: list[AnswerOption] = Field(min_length=2, max_length=50)
    sources: References


class AnswerScorePart(PackageModel):
    id: StableId
    question_id: StableId
    points: int = Field(ge=1, le=100)
    # None means there is no approved finite answer key, not every answer wrong.
    accepted_option_ids: list[StableId] | None = Field(max_length=50)
    origin: Literal['SOURCE_EXPLICIT', 'EDITORIAL']
    sources: References


class FinaleAnswerPlan(PackageModel):
    schema_version: Literal['finale-answer-plan/1.0']
    character_ids: list[StableId] = Field(min_length=2, max_length=8)
    questions: list[FinaleQuestion] = Field(min_length=1, max_length=100)
    score_parts: list[AnswerScorePart] = Field(min_length=1, max_length=300)

    @model_validator(mode='after')
    def linked_plan(self):
        characters = set(self.character_ids)
        questions = {q.id:q for q in self.questions}
        if (len(characters) != len(self.character_ids) or len(questions) != len(self.questions)
                or len({p.id for p in self.score_parts}) != len(self.score_parts)
                or {q.character_id for q in self.questions} != characters):
            raise ValueError('FINALE_PLAN_IDENTITIES_INVALID')
        for question in self.questions:
            if len({o.id for o in question.options}) != len(question.options):
                raise ValueError('FINALE_PLAN_OPTIONS_INVALID')
        for part in self.score_parts:
            if part.question_id not in questions:
                raise ValueError('FINALE_PLAN_QUESTION_INVALID')
            if part.accepted_option_ids is not None:
                valid = {o.id for o in questions[part.question_id].options}
                if (not part.accepted_option_ids or len(set(part.accepted_option_ids)) != len(part.accepted_option_ids)
                        or not set(part.accepted_option_ids) <= valid):
                    raise ValueError('FINALE_PLAN_ANSWER_KEY_INVALID')
        if {questions[p.question_id].character_id for p in self.score_parts} != characters:
            raise ValueError('FINALE_PLAN_CHARACTER_SCORE_MISSING')
        return self


class SealedAnswer(PackageModel):
    question_id: StableId
    option_id: StableId | None
    explanation: str = Field(default='', max_length=2000)


class AnswerSheet(PackageModel):
    schema_version: Literal['finale-answer-sheet/1.0']
    answers: list[SealedAnswer] = Field(min_length=1, max_length=100)


class AnswerState(PackageModel):
    schema_version: Literal['finale-answer-state/1.0']
    plan_hash: Digest
    sheets: dict[StableId, AnswerSheet] = Field(max_length=8)


class FinaleAnswerError(ValueError):
    pass


class SealedAnswers:
    """Accept one final sheet per authenticated seat; reveal only when all seal."""
    def __init__(self, plan: dict, source_ids: set[str]):
        parsed = FinaleAnswerPlan.model_validate(plan)
        for item in [*parsed.questions, *parsed.score_parts]:
            if any(ref.source_id not in source_ids for ref in item.sources):
                raise FinaleAnswerError('FINALE_SOURCE_UNKNOWN')
        self._plan = parsed.model_dump()
        self._plan_hash = content_hash(self._plan)
        self._sheets = {}

    @property
    def plan_hash(self) -> str:
        return self._plan_hash

    @classmethod
    def restore(cls, plan: dict, source_ids: set[str], snapshot: dict):
        """Validate a server-owned snapshot against its frozen plan.

        This is not a client import or proof of authenticity. The caller must
        separately validate the event history and its game/owner binding.
        """
        parsed = AnswerState.model_validate(snapshot).model_dump()
        restored = cls(plan, source_ids)
        if parsed['plan_hash'] != restored.plan_hash:
            raise FinaleAnswerError('FINALE_SNAPSHOT_PLAN_MISMATCH')
        for actor, sheet in parsed['sheets'].items():
            restored.seal(actor, sheet)
        return restored

    def _questions(self, actor: str):
        if type(actor) is not str or actor not in self._plan['character_ids']:
            raise FinaleAnswerError('FINALE_CHARACTER_INVALID')
        return [q for q in self._plan['questions'] if q['character_id'] == actor]

    def seal(self, actor: str, sheet: dict) -> bool:
        questions = self._questions(actor)
        parsed = AnswerSheet.model_validate(sheet).model_dump()
        answers = {item['question_id']:item for item in parsed['answers']}
        if (len(answers) != len(parsed['answers'])
                or set(answers) != {q['id'] for q in questions}
                or any(answers[q['id']]['option_id'] is not None
                       and answers[q['id']]['option_id'] not in {o['id'] for o in q['options']} for q in questions)):
            raise FinaleAnswerError('FINALE_SHEET_INVALID')
        # Canonical question order makes harmless JSON list reordering equivalent.
        parsed['answers'] = [answers[q['id']] for q in questions]
        if actor in self._sheets:
            if self._sheets[actor] != parsed:
                raise FinaleAnswerError('FINALE_ALREADY_SEALED')
            return False
        self._sheets[actor] = deepcopy(parsed)
        return True

    def view(self, actor: str) -> dict:
        questions = self._questions(actor)
        return {'schema_version':'finale-answer-view/1.0', 'plan_hash':self.plan_hash,
                'character_id':actor, 'sealed':actor in self._sheets,
                'sealed_count':len(self._sheets), 'required_count':len(self._plan['character_ids']),
                'questions':[{k:deepcopy(q[k]) for k in ('id','prompt','options')} for q in questions],
                'sheet':deepcopy(self._sheets.get(actor))}

    def state(self) -> dict:
        # Server-only state. Never use this as a player's response projection.
        return {'schema_version':'finale-answer-state/1.0', 'plan_hash':self.plan_hash,
                'sheets':deepcopy(self._sheets)}

    def score(self) -> dict:
        if len(self._sheets) != len(self._plan['character_ids']):
            raise FinaleAnswerError('FINALE_NOT_ALL_SEALED')
        answers = {a['question_id']:a for s in self._sheets.values() for a in s['answers']}
        questions = {q['id']:q for q in self._plan['questions']}
        rows = []
        for part in self._plan['score_parts']:
            answer = answers[part['question_id']]['option_id']
            key = part['accepted_option_ids']
            unknown = answer is None or key is None
            rows.append({'id':part['id'], 'character_id':questions[part['question_id']]['character_id'],
                         'question_id':part['question_id'], 'max_points':part['points'],
                         'points':None if unknown else part['points'] if answer in key else 0,
                         'status':'UNASSESSED' if unknown else 'ASSESSED',
                         'reason':'NO_APPROVED_KEY' if key is None else 'ANSWER_UNKNOWN' if answer is None
                         else 'FINITE_KEY_MATCH' if answer in key else 'FINITE_KEY_MISMATCH'})
        totals = []
        for actor in self._plan['character_ids']:
            parts = [r for r in rows if r['character_id'] == actor]
            complete = all(r['status'] == 'ASSESSED' for r in parts)
            earned = sum(r['points'] for r in parts if r['points'] is not None)
            totals.append({'character_id':actor,'known_points':earned,
                           'total_points':earned if complete else None,
                           'max_points':sum(r['max_points'] for r in parts),
                           'unassessed_parts':sum(r['status']=='UNASSESSED' for r in parts)})
        return {'schema_version':'finale-answer-scores/1.0','plan_hash':self.plan_hash,
                'complete':all(r['status']=='ASSESSED' for r in rows), 'parts':rows,'totals':totals}
