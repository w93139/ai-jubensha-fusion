"""Source-bound finite finale rules; no executable expressions or model scores."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.schemas.package_primitives import PackageModel, StableId, References, FinaleAvailability, FinaleMaterialRef
from src.schemas.table_decisions import FinaleVotePlan, FinaleVote


class FinaleOption(FinaleAvailability):
    id: StableId
    label: str = Field(min_length=1, max_length=300, pattern=r'\S')

class StructuredQuestion(PackageModel):
    id: StableId
    character_id: StableId
    prompt: str = Field(min_length=1, max_length=1000, pattern=r'\S')
    options: list[FinaleOption] = Field(min_length=1, max_length=100)
    max_choices: int = Field(ge=1, le=10)
    sources: References

    @model_validator(mode='after')
    def choices_unique(self):
        if len({x.id for x in self.options}) != len(self.options) or self.max_choices > len(self.options):
            raise ValueError('FINALE_QUESTION_OPTIONS_INVALID')
        return self


class AnswerAtom(PackageModel):
    kind: Literal['ANSWER_MATCH']
    question_id: StableId
    option_ids: list[StableId] = Field(max_length=10)
    mode: Literal['EXACT', 'CONTAINS']

    @model_validator(mode='after')
    def nonempty_containment(self):
        if self.mode == 'CONTAINS' and not self.option_ids:
            raise ValueError('FINALE_EMPTY_CONTAINMENT_INVALID')
        return self


class SupportAtom(PackageModel):
    kind: Literal['ANSWER_SUPPORT']
    question_id: StableId
    # Each group represents one distinct supporting fact, possibly several
    # equivalent answer choices. Two copies of a fact never count as two facts.
    fact_option_groups: list[list[StableId]] = Field(min_length=1, max_length=100)
    minimum_facts: int = Field(ge=1, le=10)
    reject_other_options: bool


class CountAtom(PackageModel):
    kind: Literal['GROUP_VOTES', 'IDENTITY_VOTES', 'EXTERNAL_GROUP_VOTES', 'TRUST_COUNT']
    target_id: StableId
    excluded_actor: StableId | None = None
    op: Literal['EQ', 'GE', 'GT']
    value: int = Field(ge=0, le=5)

    @model_validator(mode='after')
    def excluded_shape(self):
        if (self.kind == 'EXTERNAL_GROUP_VOTES') != (self.excluded_actor is not None):
            raise ValueError('FINALE_EXCLUDED_VOTER_INVALID')
        return self


class MoreAtom(PackageModel):
    kind: Literal['TRUST_MORE', 'IDENTITY_MORE']
    left_id: StableId
    right_id: StableId


class TrustAtom(PackageModel):
    kind: Literal['TRUST_TO']
    actor_id: StableId
    target_id: StableId | None


class MemoryAtom(PackageModel):
    kind: Literal['ALL_MEMORIES']
    character_id: StableId
    memory_ids: list[StableId] = Field(min_length=1, max_length=100)


FinaleAtom = Annotated[AnswerAtom | SupportAtom | CountAtom | MoreAtom | TrustAtom | MemoryAtom, Field(discriminator='kind')]
AtomClause = Annotated[list[FinaleAtom], Field(max_length=50)]
AtomCondition = Annotated[list[AtomClause], Field(min_length=1, max_length=50)]


class FinaleFact(PackageModel):
    id: StableId
    # None is an unassessed authoring rule, never a false player answer.
    when: AtomCondition | None
    origin: Literal['SOURCE_EXPLICIT', 'EDITORIAL']
    sources: References


class FactTerm(PackageModel):
    fact_id: StableId
    expected: bool


FactClause = Annotated[list[FactTerm], Field(max_length=50)]
FactCondition = Annotated[list[FactClause], Field(min_length=1, max_length=50)]


class FinaleScorePart(PackageModel):
    id: StableId
    points: int = Field(ge=1, le=100)
    explanation: str | None = Field(default=None, min_length=1, max_length=1000)
    when: FactCondition | None
    # An ending-dependent goal uses exactly one of when / ending_ids.
    ending_ids: list[StableId] | None = Field(default=None, min_length=1, max_length=30)
    sources: References

    @model_validator(mode='after')
    def condition_shape(self):
        if self.when is not None and self.ending_ids is not None:
            raise ValueError('FINALE_SCORE_CONDITION_INVALID')
        return self


class FinaleGoal(PackageModel):
    id: StableId
    character_id: StableId
    title: str = Field(min_length=1, max_length=300, pattern=r'\S')
    parts: list[FinaleScorePart] = Field(min_length=1, max_length=20)
    sources: References


class FinaleEnding(PackageModel):
    id: StableId
    when: FactCondition
    truth_ids: list[StableId] = Field(max_length=30)
    sources: References


class FinaleEndingGroup(PackageModel):
    id: StableId
    character_ids: list[StableId] = Field(min_length=1, max_length=5)
    branches: list[FinaleEnding] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def final_fallback(self):
        if self.branches[-1].when != [[]]:
            raise ValueError('FINALE_ENDING_FALLBACK_REQUIRED')
        return self


class StructuredFinalePlan(PackageModel):
    schema_version: Literal['structured-finale-plan/1.0']
    votes: FinaleVotePlan
    questions: list[StructuredQuestion] = Field(min_length=5, max_length=200)
    facts: list[FinaleFact] = Field(min_length=1, max_length=500)
    goals: list[FinaleGoal] = Field(min_length=5, max_length=100)
    endings: list[FinaleEndingGroup] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def references_valid(self):
        seats = set(self.votes.character_ids)
        questions = {q.id: q for q in self.questions}
        facts = {f.id for f in self.facts}
        identities = {i.id for i in self.votes.identities}
        groups = {i.group_id for i in self.votes.identities}
        ends = {e.id for g in self.endings for e in g.branches}
        for values in [self.questions, self.facts, self.goals, self.endings,
                       [p for g in self.goals for p in g.parts], [e for g in self.endings for e in g.branches]]:
            if len({x.id for x in values}) != len(values):
                raise ValueError('FINALE_DUPLICATE_ID')
        if ({q.character_id for q in self.questions} != seats or {g.character_id for g in self.goals} != seats
                or set(c for g in self.endings for c in g.character_ids) != seats
                or any(len(set(g.character_ids)) != len(g.character_ids) for g in self.endings)):
            raise ValueError('FINALE_SEAT_COVERAGE_INVALID')
        for fact in self.facts:
            for clause in fact.when or []:
                for atom in clause:
                    valid = True
                    if isinstance(atom, AnswerAtom):
                        q = questions.get(atom.question_id)
                        valid = bool(q and len(set(atom.option_ids)) == len(atom.option_ids)
                                     and set(atom.option_ids) <= {o.id for o in q.options}
                                     and len(atom.option_ids) <= q.max_choices)
                    elif isinstance(atom, SupportAtom):
                        q = questions.get(atom.question_id)
                        ids = [o for group in atom.fact_option_groups for o in group]
                        valid = bool(q and all(atom.fact_option_groups) and len(ids) == len(set(ids))
                                     and set(ids) <= {o.id for o in q.options}
                                     and atom.minimum_facts <= min(q.max_choices, len(atom.fact_option_groups)))
                    elif isinstance(atom, CountAtom):
                        catalog = seats if atom.kind == 'TRUST_COUNT' else identities if atom.kind == 'IDENTITY_VOTES' else groups
                        valid = atom.target_id in catalog and (atom.excluded_actor is None or atom.excluded_actor in seats)
                    elif isinstance(atom, MoreAtom):
                        catalog = seats if atom.kind == 'TRUST_MORE' else identities
                        valid = atom.left_id in catalog and atom.right_id in catalog
                    elif isinstance(atom, TrustAtom):
                        valid = atom.actor_id in seats and (atom.target_id is None or atom.target_id in seats - {atom.actor_id})
                    elif isinstance(atom, MemoryAtom):
                        valid = atom.character_id in seats and len(set(atom.memory_ids)) == len(atom.memory_ids)
                    if not valid:
                        raise ValueError('FINALE_ATOM_REFERENCE_INVALID')
        conditions = [p.when for g in self.goals for p in g.parts] + [e.when for g in self.endings for e in g.branches]
        if any(t.fact_id not in facts for c in conditions if c is not None for clause in c for t in clause):
            raise ValueError('FINALE_FACT_REFERENCE_INVALID')
        if any(p.ending_ids is not None and (not set(p.ending_ids) <= ends or len(set(p.ending_ids)) != len(p.ending_ids))
               for g in self.goals for p in g.parts):
            raise ValueError('FINALE_ENDING_REFERENCE_INVALID')
        return self


class StructuredAnswer(PackageModel):
    question_id: StableId
    # Empty explicitly means the player is uncertain, not a missing receipt.
    option_ids: list[StableId] = Field(max_length=10)


class StructuredSubmission(PackageModel):
    schema_version: Literal['structured-finale-submission/1.0']
    answers: list[StructuredAnswer] = Field(min_length=1, max_length=200)
    vote: FinaleVote
    reflection: str = Field(default='', max_length=2000)
