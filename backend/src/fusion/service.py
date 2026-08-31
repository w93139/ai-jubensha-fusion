"""单真人 + AI 对局服务。

所有状态改变都先经过 ``fusion.rules``，再写入事件表。LLM 不持有数据库
会话，也不能直接改变阶段、证据或投票。
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import asyncio
import os
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models.background_story import BackgroundStoryDBModel
from src.db.models.character import CharacterDBModel
from src.db.models.evidence import EvidenceDBModel
from src.db.models.fusion_game import FusionVote, ParticipantEvidence
from src.db.models.game_event import GameEventDBModel
from src.db.models.game_session import GameSession, GameSessionStatus
from src.db.models.location import LocationDBModel
from src.db.models.script_model import ScriptDBModel, ScriptStatus
from src.db.models.user_game_participant import UserGameParticipant
from src.fusion.rules import EvidenceVisibility, FusionGamePhase, next_phase, require_action_allowed, resolve_vote
from src.fusion.agents import FusionAgentOrchestrator


class FusionGameError(ValueError):
    pass


class FusionGameService:
    def __init__(self, db: Session):
        self.db = db

    def list_published_scripts(self) -> list[dict]:
        scripts = self.db.query(ScriptDBModel).filter(
            ScriptDBModel.status == ScriptStatus.PUBLISHED,
            ScriptDBModel.is_public.is_(True),
            ScriptDBModel.player_count.between(4, 8),
        ).order_by(ScriptDBModel.id.desc()).all()
        return [self._public_script(script) for script in scripts]

    def create_session(self, script_id: int, user_id: int) -> dict:
        script = self.db.query(ScriptDBModel).filter(ScriptDBModel.id == script_id).first()
        if not script or script.status != ScriptStatus.PUBLISHED or not script.is_public:
            raise FusionGameError("剧本不存在或尚未发布")
        playable = self._playable_characters(script_id)
        if not 4 <= len(playable) <= 8:
            raise FusionGameError("剧本必须包含 4–8 个非受害者角色")
        session = GameSession(
            session_id=f"fusion-{uuid4().hex}",
            script_id=script_id,
            host_user_id=user_id,
            status=GameSessionStatus.PENDING,
            current_phase=FusionGamePhase.CHARACTER_SELECTION.value,
            state_data={"runoff_candidates": [], "human_vote": None, "verdict": None},
        )
        self.db.add(session)
        self.db.flush()
        self._append_event(session, "SESSION_CREATED", {"script_id": script_id})
        self.db.commit()
        return self.get_state(session.session_id, user_id)

    def select_character(self, session_id: str, user_id: int, character_id: int, key: str) -> dict:
        session = self._owned_session(session_id, user_id)
        duplicate = self._duplicate(session_id, key)
        if duplicate:
            return self.get_state(session_id, user_id)
        require_action_allowed(session.current_phase, "select_character")
        characters = self._playable_characters(session.script_id)
        selected = next((item for item in characters if item.id == character_id), None)
        if not selected:
            raise FusionGameError("角色不属于当前剧本或是不可选的受害者")
        if self.db.query(UserGameParticipant).filter_by(session_id=session_id).count():
            raise FusionGameError("本局已经选择过角色")
        for character in characters:
            human = character.id == character_id
            self.db.add(UserGameParticipant(
                session_id=session_id,
                user_id=user_id if human else None,
                character_id=character.id,
                character_name=character.name,
                participant_type="HUMAN" if human else "AI",
                search_actions_remaining=2,
            ))
        session.current_phase = FusionGamePhase.SCRIPT_READING.value
        session.status = GameSessionStatus.STARTED
        session.started_at = datetime.now(timezone.utc)
        self._append_event(session, "CHARACTER_SELECTED", {"character_id": character_id}, key=key, actor_user_id=user_id)
        self._append_event(session, "PHASE_CHANGED", {"phase": session.current_phase})
        self.db.commit()
        return self.get_state(session_id, user_id)

    def perform_action(self, session_id: str, user_id: int, action: str, payload: dict, key: str) -> dict:
        with self._session_lock(session_id):
            session = self._owned_session(session_id, user_id)
            if session.is_read_only:
                raise FusionGameError("旧会话仅供查看")
            if self._duplicate(session_id, key):
                return self.get_state(session_id, user_id)
            require_action_allowed(session.current_phase, action)
            human = self._human(session_id, user_id)
            handlers = {
                "ready": self._ready,
                "advance_phase": self._advance,
                "send_message": self._message,
                "ask_question": self._question,
                "search_location": self._search,
                "reveal_evidence": self._reveal,
                "cast_vote": self._vote,
            }
            handlers[action](session, human, payload, key)
            session.state_version += 1
            self.db.commit()
            return self.get_state(session_id, user_id)

    def get_state(self, session_id: str, user_id: int) -> dict:
        session = self._owned_session(session_id, user_id)
        script = self.db.get(ScriptDBModel, session.script_id)
        characters = self._playable_characters(session.script_id)
        human = self.db.query(UserGameParticipant).filter_by(
            session_id=session_id, user_id=user_id, participant_type="HUMAN"
        ).first()
        role = self.db.get(CharacterDBModel, human.character_id) if human else None
        participants = self.db.query(UserGameParticipant).filter_by(session_id=session_id).all()
        locations = self.db.query(LocationDBModel).filter_by(script_id=session.script_id).all()
        public_evidence = self.db.query(ParticipantEvidence, EvidenceDBModel).join(
            EvidenceDBModel, EvidenceDBModel.id == ParticipantEvidence.evidence_id
        ).filter(
            ParticipantEvidence.session_id == session_id,
            ParticipantEvidence.visibility == EvidenceVisibility.PUBLIC.value,
        ).all()
        private_evidence = []
        if human:
            private_evidence = self.db.query(ParticipantEvidence, EvidenceDBModel).join(
                EvidenceDBModel, EvidenceDBModel.id == ParticipantEvidence.evidence_id
            ).filter(
                ParticipantEvidence.session_id == session_id,
                ParticipantEvidence.participant_id == human.id,
            ).all()
        background = self.db.query(BackgroundStoryDBModel).filter_by(script_id=session.script_id).first()
        result = {
            "session": session.to_dict(),
            "script": self._public_script(script),
            "phase": session.current_phase,
            "characters": [self._public_character(item) for item in characters],
            "participants": [{
                "character_id": item.character_id,
                "character_name": item.character_name,
                "type": item.participant_type,
                "ready": item.is_ready,
                "online": item.is_online,
            } for item in participants],
            "locations": [{"id": item.id, "name": item.name, "description": item.description} for item in locations],
            "public_evidence": [self._evidence(item) for _, item in public_evidence],
            "private_evidence": [dict(self._evidence(item), visibility=owned.visibility) for owned, item in private_evidence],
            "background": ({
                "title": background.title,
                "setting": background.setting_description,
                "incident": background.incident_description,
                "rules": background.rules_reminder,
            } if background else None),
            "my_role": ({
                **self._public_character(role),
                "background": role.background,
                "secret": role.secret,
                "objective": role.objective,
                "is_murderer": role.is_murderer,
            } if role else None),
            "last_event_id": session.last_event_id,
        }
        if session.current_phase in (FusionGamePhase.REVELATION.value, FusionGamePhase.ENDED.value):
            murderer = next((item for item in characters if item.is_murderer), None)
            result["revelation"] = {
                "murderer": self._public_character(murderer) if murderer else None,
                "verdict": (session.state_data or {}).get("verdict"),
                "human_vote": (session.state_data or {}).get("human_vote"),
                "truth": self._truth(background),
            }
        return result

    def get_events(self, session_id: str, user_id: int, after: int = 0) -> list[dict]:
        self._owned_session(session_id, user_id)
        human = self._human(session_id, user_id, required=False)
        query = self.db.query(GameEventDBModel).filter(
            GameEventDBModel.session_id == session_id,
            GameEventDBModel.event_sequence > after,
        ).order_by(GameEventDBModel.event_sequence)
        events = []
        for event in query.all():
            if event.visibility == EvidenceVisibility.SYSTEM_TRUTH.value:
                continue
            if event.visibility == EvidenceVisibility.CHARACTER_PRIVATE.value and (
                not human or event.recipient_character_id != human.character_id
            ):
                continue
            events.append(self.event_envelope(event))
        return events

    async def answer_question(self, session_id: str, user_id: int, target_character_id: int, question: str) -> dict | None:
        """让目标 AI 角色回答。模型只拿到该角色私有资料和公共事件。"""
        session = self._owned_session(session_id, user_id)
        token_budget = int(__import__("os").getenv("GAME_TOKEN_BUDGET", "30000"))
        if session.prompt_tokens + session.completion_tokens >= token_budget:
            message = "我需要一点时间整理线索，先听听其他人的看法。"
            degraded = True
            usage = None
        else:
            participant = self.db.query(UserGameParticipant).filter_by(
                session_id=session_id, character_id=target_character_id, participant_type="AI"
            ).first()
            if not participant:
                return None
            role = self.db.get(CharacterDBModel, target_character_id)
            public_events = self.get_events(session_id, user_id, max(0, session.last_event_id - 30))
            turn = await FusionAgentOrchestrator().player_reply(
                {"name": role.name, "background": role.background, "secret": role.secret,
                 "objective": role.objective, "is_murderer": role.is_murderer},
                public_events,
                question,
            )
            message, degraded, usage = turn.message, turn.degraded, turn.usage
        if usage:
            self._record_usage(session, usage)
        event = self._append_event(
            session, "AI_MESSAGE", {"character_id": target_character_id, "content": message, "degraded": degraded},
            character_name=self.db.get(CharacterDBModel, target_character_id).name,
        )
        self.db.commit()
        return self.event_envelope(event)

    async def run_ai_phase(self, session_id: str, user_id: int) -> list[dict]:
        """进入演绎阶段时并发生成 AI 发言；失败时逐角色使用安全降级文案。"""
        session = self._owned_session(session_id, user_id)
        prompts = {
            FusionGamePhase.INTRODUCTION.value: "请以角色身份做简短自我介绍，不要泄露秘密。",
            FusionGamePhase.DISCUSSION.value: "请结合公开线索给出简短判断，可以质疑他人，但不得剧透。",
        }
        prompt = prompts.get(session.current_phase)
        if not prompt:
            return []
        participants = self.db.query(UserGameParticipant).filter_by(session_id=session_id, participant_type="AI").all()
        public_events = self.get_events(session_id, user_id, max(0, session.last_event_id - 30))
        orchestrator = FusionAgentOrchestrator()
        roles = [self.db.get(CharacterDBModel, item.character_id) for item in participants]
        turns = await asyncio.gather(*[
            orchestrator.player_reply(
                {"name": role.name, "background": role.background, "secret": role.secret,
                 "objective": role.objective, "is_murderer": role.is_murderer},
                public_events,
                prompt,
            ) for role in roles
        ])
        output = []
        for role, turn in zip(roles, turns):
            if turn.usage:
                self._record_usage(session, turn.usage)
            event = self._append_event(
                session, "AI_MESSAGE", {"character_id": role.id, "content": turn.message, "degraded": turn.degraded},
                character_name=role.name,
            )
            output.append(event)
        self.db.commit()
        return [self.event_envelope(item) for item in output]

    @staticmethod
    def event_envelope(event: GameEventDBModel) -> dict:
        return {
            "event_id": event.event_sequence,
            "session_id": event.session_id,
            "type": event.event_type,
            "timestamp": event.timestamp.replace(tzinfo=timezone.utc).isoformat() if event.timestamp.tzinfo is None else event.timestamp.isoformat(),
            "payload": event.event_metadata or {"content": event.content},
        }

    def validate_script(self, script_id: int) -> dict:
        script = self.db.get(ScriptDBModel, script_id)
        if not script:
            raise FusionGameError("剧本不存在")
        characters = self._playable_characters(script_id)
        evidence = self.db.query(EvidenceDBModel).filter_by(script_id=script_id).all()
        locations = self.db.query(LocationDBModel).filter_by(script_id=script_id).all()
        background = self.db.query(BackgroundStoryDBModel).filter_by(script_id=script_id).first()
        checks = {
            "player_count_4_to_8": 4 <= len(characters) <= 8 and script.player_count == len(characters),
            "exactly_one_murderer": sum(bool(item.is_murderer) for item in characters) == 1,
            "has_background_and_truth": bool(background and background.incident_description and background.murder_method),
            "has_searchable_locations": bool(locations),
            "evidence_chain_present": len(evidence) >= max(4, len(characters)),
            "all_roles_have_private_content": all(item.background and item.secret and item.objective for item in characters),
        }
        return {"valid": all(checks.values()), "checks": checks}

    def set_script_status(self, script_id: int, status: ScriptStatus) -> dict:
        script = self.db.get(ScriptDBModel, script_id)
        if not script:
            raise FusionGameError("剧本不存在")
        if status == ScriptStatus.PUBLISHED:
            report = self.validate_script(script_id)
            if not report["valid"]:
                raise FusionGameError("质检未通过，不能发布")
            script.is_public = True
        elif status != ScriptStatus.PUBLISHED:
            script.is_public = False
        script.status = status
        self.db.commit()
        return {"id": script.id, "status": script.status.value, "is_public": script.is_public}

    def _ready(self, session, human, payload, key):
        human.is_ready = True
        self._append_event(session, "PLAYER_READY", {"character_id": human.character_id}, key, human.user_id)
        self._change_phase(session, FusionGamePhase.BACKGROUND)

    def _advance(self, session, human, payload, key):
        target = next_phase(session.current_phase)
        self._append_event(session, "PLAYER_ADVANCED", {}, key, human.user_id)
        self._change_phase(session, target)
        if target == FusionGamePhase.ENDED:
            session.status = GameSessionStatus.ENDED
            session.finished_at = datetime.now(timezone.utc)

    def _message(self, session, human, payload, key):
        content = str(payload.get("content", "")).strip()[:2000]
        if not content:
            raise FusionGameError("发言不能为空")
        self._append_event(session, "PUBLIC_MESSAGE", {"character_id": human.character_id, "content": content}, key, human.user_id, human.character_name)

    def _question(self, session, human, payload, key):
        target = int(payload.get("target_character_id", 0))
        content = str(payload.get("content", "")).strip()[:1000]
        valid_ids = {item.id for item in self._playable_characters(session.script_id)}
        if target not in valid_ids or not content:
            raise FusionGameError("提问目标或内容无效")
        self._append_event(session, "QUESTION_ASKED", {"from": human.character_id, "to": target, "content": content}, key, human.user_id, human.character_name)

    def _search(self, session, human, payload, key):
        location_id = int(payload.get("location_id", 0))
        location = self.db.query(LocationDBModel).filter_by(id=location_id, script_id=session.script_id).first()
        if not location:
            raise FusionGameError("搜证地点无效")
        if human.search_actions_remaining <= 0:
            raise FusionGameError("本局搜证次数已用完")
        claimed = self.db.query(ParticipantEvidence.evidence_id).filter_by(session_id=session.session_id)
        evidence = self.db.query(EvidenceDBModel).filter(
            EvidenceDBModel.script_id == session.script_id,
            EvidenceDBModel.location == location.name,
            ~EvidenceDBModel.id.in_(claimed),
        ).order_by(EvidenceDBModel.id).first()
        if not evidence:
            raise FusionGameError("该地点暂时没有可发现的新线索")
        owned = ParticipantEvidence(
            session_id=session.session_id,
            participant_id=human.id,
            evidence_id=evidence.id,
            visibility=EvidenceVisibility.CHARACTER_PRIVATE.value,
        )
        self.db.add(owned)
        human.search_actions_remaining -= 1
        self._append_event(
            session, "PRIVATE_EVIDENCE", {"evidence": self._evidence(evidence)}, key, human.user_id,
            human.character_name, EvidenceVisibility.CHARACTER_PRIVATE.value, human.character_id,
        )

    def _reveal(self, session, human, payload, key):
        evidence_id = int(payload.get("evidence_id", 0))
        owned = self.db.query(ParticipantEvidence).filter_by(
            session_id=session.session_id, participant_id=human.id, evidence_id=evidence_id
        ).first()
        if not owned:
            raise FusionGameError("只能公开自己发现的证据")
        evidence = self.db.get(EvidenceDBModel, evidence_id)
        owned.visibility = EvidenceVisibility.PUBLIC.value
        owned.revealed_at = datetime.now(timezone.utc)
        self._append_event(session, "EVIDENCE_REVEALED", {"evidence": self._evidence(evidence), "by": human.character_id}, key, human.user_id, human.character_name)

    def _vote(self, session, human, payload, key):
        suspect = int(payload.get("suspect_character_id", 0))
        valid = {item.id for item in self._playable_characters(session.script_id)}
        if suspect not in valid or suspect == human.character_id:
            raise FusionGameError("投票目标无效")
        state = dict(session.state_data or {})
        if session.current_phase == FusionGamePhase.RUNOFF_VOTING.value and suspect not in state.get("runoff_candidates", []):
            raise FusionGameError("加赛只能投给最高票候选人")
        round_number = 2 if session.current_phase == FusionGamePhase.RUNOFF_VOTING.value else 1
        if self.db.query(FusionVote).filter_by(session_id=session.session_id, round_number=round_number, voter_participant_id=human.id).first():
            raise FusionGameError("本轮已经投过票")
        self.db.add(FusionVote(session_id=session.session_id, round_number=round_number, voter_participant_id=human.id, suspect_character_id=suspect))
        state["human_vote"] = suspect
        ai_participants = self.db.query(UserGameParticipant).filter_by(session_id=session.session_id, participant_type="AI").all()
        candidates = state.get("runoff_candidates") if round_number == 2 else sorted(valid)
        for index, participant in enumerate(ai_participants):
            choices = [item for item in candidates if item != participant.character_id]
            chosen = choices[(participant.id + index) % len(choices)] if choices else suspect
            self.db.add(FusionVote(session_id=session.session_id, round_number=round_number, voter_participant_id=participant.id, suspect_character_id=chosen))
        self.db.flush()
        votes = [item[0] for item in self.db.query(FusionVote.suspect_character_id).filter_by(session_id=session.session_id, round_number=round_number).all()]
        result = resolve_vote(votes)
        self._append_event(session, "VOTE_RECORDED", {"round": round_number, "result": result}, key, human.user_id)
        if result["status"] == "TIED" and round_number == 1:
            state["runoff_candidates"] = result["leaders"]
            session.state_data = state
            self._change_phase(session, FusionGamePhase.RUNOFF_VOTING)
        else:
            state["verdict"] = result["leaders"][0] if result["status"] == "DECIDED" else None
            state["vote_result"] = result
            session.state_data = state
            self._change_phase(session, FusionGamePhase.REVELATION)

    def _change_phase(self, session: GameSession, phase: FusionGamePhase):
        session.current_phase = phase.value
        if phase in (FusionGamePhase.EVIDENCE_ROUND_1, FusionGamePhase.EVIDENCE_ROUND_2):
            session.current_round = 1 if phase == FusionGamePhase.EVIDENCE_ROUND_1 else 2
        self._append_event(session, "PHASE_CHANGED", {"phase": phase.value})
        if phase in (FusionGamePhase.EVIDENCE_ROUND_1, FusionGamePhase.EVIDENCE_ROUND_2):
            self._run_ai_searches(session)

    def _run_ai_searches(self, session: GameSession):
        """AI 角色按与真人相同的配额搜证，并各自决定是否公开。"""
        claimed = {item[0] for item in self.db.query(ParticipantEvidence.evidence_id).filter_by(session_id=session.session_id).all()}
        available = self.db.query(EvidenceDBModel).filter(
            EvidenceDBModel.script_id == session.script_id,
            ~EvidenceDBModel.id.in_(claimed),
        ).order_by(EvidenceDBModel.id).all()
        participants = self.db.query(UserGameParticipant).filter_by(session_id=session.session_id, participant_type="AI").order_by(UserGameParticipant.id).all()
        for index, participant in enumerate(participants):
            # 始终为真人至少保留一条当前轮可搜线索。
            if len(available) <= 1 or participant.search_actions_remaining <= 0:
                break
            evidence = available.pop((participant.id + index) % len(available))
            reveal = (participant.id + session.current_round) % 2 == 0
            owned = ParticipantEvidence(
                session_id=session.session_id,
                participant_id=participant.id,
                evidence_id=evidence.id,
                visibility=EvidenceVisibility.PUBLIC.value if reveal else EvidenceVisibility.CHARACTER_PRIVATE.value,
                revealed_at=datetime.now(timezone.utc) if reveal else None,
            )
            self.db.add(owned)
            participant.search_actions_remaining -= 1
            self._append_event(
                session, "PRIVATE_EVIDENCE", {"evidence_id": evidence.id},
                character_name=participant.character_name,
                visibility=EvidenceVisibility.CHARACTER_PRIVATE.value,
                recipient=participant.character_id,
            )
            if reveal:
                self._append_event(
                    session, "EVIDENCE_REVEALED", {"evidence": self._evidence(evidence), "by": participant.character_id},
                    character_name=participant.character_name,
                )

    def _append_event(self, session, event_type, payload, key=None, actor_user_id=None, character_name=None, visibility="PUBLIC", recipient=None):
        session.last_event_id += 1
        event = GameEventDBModel(
            session_id=session.session_id,
            event_type=event_type,
            event_sequence=session.last_event_id,
            visibility=visibility,
            recipient_character_id=recipient,
            actor_user_id=actor_user_id,
            idempotency_key=key,
            character_name=character_name,
            content=str(payload.get("content") or event_type),
            event_metadata=payload,
            is_public=visibility == EvidenceVisibility.PUBLIC.value,
        )
        self.db.add(event)
        return event

    def _owned_session(self, session_id, user_id):
        session = self.db.query(GameSession).filter_by(session_id=session_id, host_user_id=user_id).first()
        if not session:
            raise FusionGameError("会话不存在或无权访问")
        return session

    def _human(self, session_id, user_id, required=True):
        item = self.db.query(UserGameParticipant).filter_by(session_id=session_id, user_id=user_id, participant_type="HUMAN").first()
        if required and not item:
            raise FusionGameError("请先选择角色")
        return item

    def _duplicate(self, session_id, key):
        return self.db.query(GameEventDBModel).filter_by(session_id=session_id, idempotency_key=key).first()

    @staticmethod
    def _record_usage(session: GameSession, usage: dict):
        prompt = int(usage.get("prompt_tokens", 0))
        completion = int(usage.get("completion_tokens", 0))
        session.prompt_tokens += prompt
        session.completion_tokens += completion
        input_rate = float(os.getenv("DEEPSEEK_INPUT_COST_PER_MILLION", "0"))
        output_rate = float(os.getenv("DEEPSEEK_OUTPUT_COST_PER_MILLION", "0"))
        session.estimated_cost += (prompt * input_rate + completion * output_rate) / 1_000_000

    @contextmanager
    def _session_lock(self, session_id: str):
        """生产环境用 Redis 串行化会话操作；数据库唯一约束仍是最终防线。"""
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            yield
            return
        try:
            from redis import Redis
            lock = Redis.from_url(redis_url).lock(f"fusion:session:{session_id}", timeout=15, blocking_timeout=5)
            acquired = lock.acquire(blocking=True)
            if not acquired:
                raise FusionGameError("会话正忙，请稍后重试")
            try:
                yield
            finally:
                lock.release()
        except FusionGameError:
            raise
        except Exception as exc:
            raise FusionGameError("会话锁服务暂不可用") from exc

    def _playable_characters(self, script_id):
        return self.db.query(CharacterDBModel).filter_by(script_id=script_id, is_victim=False).order_by(CharacterDBModel.id).all()

    @staticmethod
    def _public_script(script):
        return {"id": script.id, "title": script.title, "description": script.description, "player_count": script.player_count, "duration_minutes": script.duration_minutes, "difficulty": script.difficulty, "category": script.category, "cover_image_url": script.cover_image_url}

    @staticmethod
    def _public_character(character):
        return {"id": character.id, "name": character.name, "age": character.age, "profession": character.profession, "gender": character.gender, "avatar_url": character.avatar_url}

    @staticmethod
    def _evidence(evidence):
        return {"id": evidence.id, "name": evidence.name, "description": evidence.description, "location": evidence.location, "importance": evidence.importance}

    @staticmethod
    def _truth(background):
        if not background:
            return None
        return {"murder_method": background.murder_method, "murder_location": background.murder_location, "victim_background": background.victim_background, "victory_conditions": background.victory_conditions}
