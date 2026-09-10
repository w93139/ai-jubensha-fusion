"""单真人 + AI 对局服务。

所有状态改变都先经过 ``fusion.rules``，再写入事件表。LLM 不持有数据库
会话，也不能直接改变阶段、证据或投票。
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import asyncio
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from uuid import uuid4

from sqlalchemy import and_, or_
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
from src.fusion.agents import AgentTurn, FusionAgentOrchestrator
from src.fusion.budget import (
    BudgetDecision,
    BudgetPolicy,
    UsageAmount,
    provider_usage_is_complete,
    usage_metadata_is_valid,
)
from src.fusion.knowledge import RoleKnowledgeView, RoleScope, event_is_visible


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
        self._validate_client_key(key)
        session = self._owned_session(session_id, user_id, for_update=True)
        duplicate = self._duplicate(session_id, key)
        if duplicate:
            self._require_same_request(duplicate, "select_character", {"character_id": character_id})
            state = self.get_state(session_id, user_id)
            self.db.commit()
            return state
        if session.is_read_only:
            raise FusionGameError("旧会话仅供查看")
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
        event = self._append_event(session, "CHARACTER_SELECTED", {"character_id": character_id}, key=key, actor_user_id=user_id)
        self._stamp_request(event, session, "select_character", {"character_id": character_id})
        self._append_event(session, "PHASE_CHANGED", {"phase": session.current_phase})
        self.db.commit()
        return self.get_state(session_id, user_id)

    def perform_action(self, session_id: str, user_id: int, action: str, payload: dict, key: str) -> dict:
        self._validate_client_key(key)
        with self._session_lock(session_id):
            session = self._owned_session(session_id, user_id, for_update=True)
            if session.is_read_only:
                raise FusionGameError("旧会话仅供查看")
            duplicate = self._duplicate(session_id, key)
            if duplicate:
                self._require_same_request(duplicate, action, payload)
                state = self.get_state(session_id, user_id)
                self.db.commit()
                return state
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
            self.db.flush()
            self._stamp_request(self._duplicate(session_id, key), session, action, payload)
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
            EvidenceDBModel.script_id == session.script_id,
        ).all()
        private_evidence = []
        if human:
            private_evidence = self.db.query(ParticipantEvidence, EvidenceDBModel).join(
                EvidenceDBModel, EvidenceDBModel.id == ParticipantEvidence.evidence_id
            ).filter(
                ParticipantEvidence.session_id == session_id,
                ParticipantEvidence.participant_id == human.id,
                ParticipantEvidence.visibility.in_([
                    EvidenceVisibility.PUBLIC.value, EvidenceVisibility.CHARACTER_PRIVATE.value,
                ]),
                EvidenceDBModel.script_id == session.script_id,
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
        return self._visible_events(session_id, human.character_id if human else None, after=after)

    def _visible_events(self, session_id: str, character_id: int | None, after: int = 0, limit: int | None = None) -> list[dict]:
        """先按接收角色过滤，再裁剪窗口，私密流量不能挤掉公共上下文。"""
        allowed = and_(
            GameEventDBModel.visibility == EvidenceVisibility.PUBLIC.value,
            GameEventDBModel.recipient_character_id.is_(None),
        )
        if character_id is not None:
            allowed = or_(allowed, and_(
                GameEventDBModel.visibility == EvidenceVisibility.CHARACTER_PRIVATE.value,
                GameEventDBModel.recipient_character_id == character_id,
            ))
        query = self.db.query(GameEventDBModel).filter(
            GameEventDBModel.session_id == session_id,
            GameEventDBModel.event_sequence > after,
            allowed,
        )
        if limit is not None:
            rows = list(reversed(query.order_by(GameEventDBModel.event_sequence.desc()).limit(limit).all()))
        else:
            rows = query.order_by(GameEventDBModel.event_sequence).all()
        return [dict(self.event_envelope(event), visibility=event.visibility) for event in rows
                if event_is_visible(event.visibility, event.recipient_character_id, character_id)]

    def role_knowledge(self, session_id: str, user_id: int, character_id: int) -> RoleKnowledgeView:
        """内部 Agent 入口，不作为全知角色资料 API 暴露。"""
        session = self._owned_session(session_id, user_id)
        participant = self.db.query(UserGameParticipant).filter_by(
            session_id=session_id, character_id=character_id, participant_type="AI",
        ).first()
        role = self.db.query(CharacterDBModel).filter_by(
            id=character_id, script_id=session.script_id, is_victim=False,
        ).first()
        if not participant or not role:
            raise FusionGameError("目标不是本局 AI 角色")
        evidence = self.db.query(ParticipantEvidence, EvidenceDBModel).join(
            EvidenceDBModel, ParticipantEvidence.evidence_id == EvidenceDBModel.id,
        ).filter(
            ParticipantEvidence.session_id == session_id,
            EvidenceDBModel.script_id == session.script_id,
            or_(ParticipantEvidence.visibility == EvidenceVisibility.PUBLIC.value, and_(
                ParticipantEvidence.visibility == EvidenceVisibility.CHARACTER_PRIVATE.value,
                ParticipantEvidence.participant_id == participant.id,
            )),
        ).order_by(EvidenceDBModel.id).all()
        return RoleKnowledgeView(
            scope=RoleScope(session_id, session.script_id, character_id, session.current_phase, session.state_version),
            # 旧平面字段暂只代表初始私本；分阶段记忆必须等导入契约，不能用 is_murderer 补全。
            identity={"name": role.name, "background": role.background, "secret": role.secret,
                      "objective": role.objective, "personality_traits": role.personality_traits or []},
            evidence=tuple(dict(self._evidence(item), visibility=owned.visibility) for owned, item in evidence),
            events=tuple(self._visible_events(session_id, character_id, limit=30)),
        )

    async def answer_question(self, session_id: str, user_id: int, action_key: str) -> dict | None:
        """只回答已经由规则服务接受并持久化的问题，不再相信调用方重传的内容。"""
        source = self._duplicate(session_id, action_key)
        self._owned_session(session_id, user_id)
        if not source or source.event_type != "QUESTION_ASKED" or source.actor_user_id != user_id:
            raise FusionGameError("缺少已接受的提问事件")
        target = (source.event_metadata or {}).get("to")
        return await self._reply_to_action(session_id, user_id, action_key, target, "QUESTION_ASKED")

    async def run_ai_phase(self, session_id: str, user_id: int, action_key: str) -> list[dict]:
        """阶段发言绑定一次已接受的推进动作；重放不会重新生成整轮发言。"""
        session = self._owned_session(session_id, user_id)
        if session.current_phase not in (FusionGamePhase.INTRODUCTION.value, FusionGamePhase.DISCUSSION.value):
            return []
        participants = self.db.query(UserGameParticipant).filter_by(session_id=session_id, participant_type="AI").all()
        orchestrator = FusionAgentOrchestrator()
        tasks = [asyncio.create_task(
            self._reply_to_action(
                session_id, user_id, action_key, item.character_id, "PLAYER_ADVANCED", orchestrator,
            ),
        ) for item in participants]
        try:
            replies = await asyncio.gather(*tasks)
        except BaseException:
            # ``gather`` does not cancel siblings when one raises.  Explicitly
            # drain every task so a request cannot return while another role is
            # still spending money or using the soon-to-close DB session.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return [reply for reply in replies if reply is not None]

    async def _reply_to_action(self, session_id: str, user_id: int, action_key: str, character_id: int,
                               source_type: str, orchestrator: FusionAgentOrchestrator | None = None) -> dict | None:
        session = self._owned_session(session_id, user_id, for_update=True)
        source = self._duplicate(session_id, action_key)
        if not source or source.event_type != source_type or source.actor_user_id != user_id:
            raise FusionGameError("AI 发言必须绑定本局已接受的动作")
        source_data = source.event_metadata or {}
        identity_key = sha256(f"{source.id}:{character_id}".encode()).hexdigest()
        result_key, receipt_key = f"ai-result:{identity_key}", f"ai-start:{identity_key}"
        completed = self._duplicate(session_id, result_key)
        if completed:
            result_data = completed.event_metadata or {}
            if (completed.event_type != "AI_MESSAGE" or completed.visibility != EvidenceVisibility.PUBLIC.value or
                    completed.actor_user_id is not None or result_data.get("character_id") != character_id or
                    result_data.get("source_event_id") != source.event_sequence):
                raise FusionGameError("AI 结果记录不一致")
            self.db.commit()
            return self.event_envelope(completed)
        if session.is_read_only or session.status != GameSessionStatus.STARTED:
            raise FusionGameError("当前会话不能生成角色发言")
        if source_data.get("_action_phase") != session.current_phase:
            raise FusionGameError("动作对应的游戏状态已经改变，请重新发起")
        if source_type == "QUESTION_ASKED":
            require_action_allowed(session.current_phase, "ask_question")
            if character_id != source_data.get("to"):
                raise FusionGameError("角色与已接受的问题不匹配")
            question = source_data["content"]
        else:
            prompts = {
                FusionGamePhase.INTRODUCTION.value: "请以角色身份做自我介绍。必须覆盖：姓名、身份/职业、以及 background 中所有标注为公开的基本信息。不要主动披露私有秘密，但不能以保密为由跳过公开内容。用肯定语气，不要用'可能''也许'修饰自己明确知道的事。",
                FusionGamePhase.DISCUSSION.value: "请围绕当前公开线索给出一个简短判断。对自己亲历的事用肯定语气陈述，对他人行为的推断才需要区分猜测与事实。不要重复你在 visible_events 中已经说过的内容。",
            }
            if session.current_phase not in prompts:
                raise FusionGameError("本阶段不允许自动角色发言")
            question = prompts[session.current_phase]
        knowledge = self.role_knowledge(session_id, user_id, character_id)
        runtime = orchestrator or FusionAgentOrchestrator()
        receipt = self._duplicate(session_id, receipt_key)
        if receipt and not isinstance(receipt.event_metadata, dict):
            raise FusionGameError("AI 运行记录损坏，本局已停止模型调用")
        receipt_metadata = receipt.event_metadata if receipt else {}
        if receipt and (receipt.event_type != "AI_TURN_RECEIPT" or
                        receipt.visibility != EvidenceVisibility.SYSTEM_TRUTH.value or receipt.actor_user_id is not None or
                        receipt_metadata.get("source_event_id") != source.event_sequence or
                        receipt_metadata.get("character_id") != character_id):
            raise FusionGameError("AI 运行记录不一致")
        policy, configuration_issue = self._model_budget_snapshot(session, runtime)
        if self._accounting_integrity_issue(session_id):
            raise FusionGameError("模型计费记录异常，本局已停止模型调用")
        now = datetime.now(timezone.utc).timestamp()
        self._expire_ai_reservations(session, policy, now)
        # Expiry reconciliation replaces ``event_metadata`` with a new object.
        # Re-read it here; the reference captured before expiry is intentionally
        # no longer authoritative and could otherwise erase the settled usage.
        if receipt and not isinstance(receipt.event_metadata, dict):
            raise FusionGameError("AI 运行记录损坏，本局已停止模型调用")
        receipt_data = dict(receipt.event_metadata) if receipt else {}
        generations = self._receipt_generations(receipt_data)
        if len(generations) >= 64:
            raise FusionGameError("AI 运行记录已达到安全上限，请新开测试局")
        current = self._find_generation(generations, receipt_data.get("current_generation_id") or
                                        receipt_data.get("generation_id"))
        if current and current.get("status") == "RUNNING" and self._lease_value(
            current.get("lease_until"),
        ) > now:
            self.db.commit()
            return None

        role_context, visible_events = knowledge.model_inputs()
        estimated = runtime.reservation_tokens(role_context, visible_events, question)
        reservation = policy.amount(estimated.prompt_tokens, estimated.completion_tokens)
        active = self._active_ai_reservations(session_id, policy, now)
        decision = policy.decide(self._ledger_usage(session), active, reservation)
        if configuration_issue:
            decision = BudgetDecision(False, configuration_issue)
        generation_id = uuid4().hex
        generation = {
            "generation_id": generation_id,
            "status": "RUNNING" if decision.allowed else "BUDGET_BLOCKED",
            "lease_until": now + 600 if decision.allowed else 0,
            "reservation": reservation.to_metadata(),
            "accounting": UsageAmount().to_metadata(),
            "accounting_status": "PENDING" if decision.allowed else "NOT_CALLED_BUDGET",
            "budget_reason": decision.reason,
            "model": runtime.model_metadata(),
        }
        generations.append(generation)
        if receipt:
            self._write_receipt(receipt, source.event_sequence, character_id, generations, generation_id)
        else:
            receipt = self._append_event(session, "AI_TURN_RECEIPT", {}, key=receipt_key,
                                         visibility=EvidenceVisibility.SYSTEM_TRUTH.value)
            self._write_receipt(receipt, source.event_sequence, character_id, generations, generation_id)
        self.db.commit()  # 不持有数据库行锁等待模型；续跑/重复请求先看到持久化的领取记录。
        if not decision.allowed:
            turn = AgentTurn("我需要一点时间整理线索，先听听其他人的看法。", degraded=True)
        else:
            try:
                turn = await runtime.player_reply(role_context, visible_events, question)
            except asyncio.CancelledError:
                session = self._owned_session(session_id, user_id, for_update=True)
                cancelled_receipt = self._duplicate(session_id, receipt_key)
                if not cancelled_receipt or not isinstance(cancelled_receipt.event_metadata, dict):
                    raise FusionGameError("AI 运行记录损坏，本局已停止模型调用")
                data = dict(cancelled_receipt.event_metadata)
                cancelled_generations = self._receipt_generations(data)
                cancelled_generation = self._find_generation(cancelled_generations, generation_id)
                if cancelled_generation:
                    usage_uncertain = runtime.client is not None
                    self._settle_generation(session, cancelled_generation, policy, None, usage_uncertain)
                    cancelled_generation["status"] = "CANCELLED_UNKNOWN" if usage_uncertain else "CANCELLED"
                    cancelled_generation["lease_until"] = 0
                    self._write_receipt(cancelled_receipt, source.event_sequence, character_id,
                                        cancelled_generations, data.get("current_generation_id") or generation_id)
                self.db.commit()
                raise
        session = self._owned_session(session_id, user_id, for_update=True)
        receipt = self._duplicate(session_id, receipt_key)
        if not receipt or not isinstance(receipt.event_metadata, dict):
            raise FusionGameError("AI 运行记录损坏，本局已停止模型调用")
        receipt_data = dict(receipt.event_metadata)
        generations = self._receipt_generations(receipt_data)
        finished_generation = self._find_generation(generations, generation_id)
        if not finished_generation:
            raise FusionGameError("AI 运行记录缺少本次 generation")
        self._settle_generation(
            session,
            finished_generation,
            policy,
            turn.usage,
            turn.usage_uncertain or (turn.model_attempted and turn.usage is None),
            unknown_attempts=turn.unknown_attempts,
            max_attempts=runtime.settings.retries + 1,
        )
        finished_generation["lease_until"] = 0
        finished_generation["model_attempted"] = turn.model_attempted
        finished_generation["attempt_count"] = turn.attempt_count
        finished_generation["unknown_attempts"] = turn.unknown_attempts
        finished_generation["response_model"] = turn.response_model
        finished_generation["provider_request_id"] = turn.provider_request_id
        finished_generation["finish_reason"] = turn.finish_reason
        current_generation_id = receipt_data.get("current_generation_id") or receipt_data.get("generation_id")
        is_current_generation = current_generation_id == generation_id
        still_current = (not session.is_read_only and session.status == GameSessionStatus.STARTED and
                         session.script_id == knowledge.scope.script_id and
                         session.current_phase == knowledge.scope.phase and is_current_generation)
        # 普通聊天/新增证据不会撤销原已知信息；不能因对话游标变化吞掉正在生成的回答。
        # 阶段、会话状态与固定角色资格才是当前旧规则下的有效性边界。
        still_current = still_current and self.db.query(UserGameParticipant).filter_by(
            session_id=session_id, character_id=character_id, participant_type="AI",
        ).first() is not None
        if finished_generation.get("budget_reason"):
            finished_generation["status"] = "BUDGET_BLOCKED"
        else:
            finished_generation["status"] = "COMPLETED" if still_current else (
                "LATE_RECONCILED" if not is_current_generation else "STALE"
            )
        self._write_receipt(receipt, source.event_sequence, character_id, generations, current_generation_id)
        if not still_current:
            self.db.commit()
            return None
        event = self._append_event(session, "AI_MESSAGE", {
            "character_id": character_id, "content": turn.message, "degraded": turn.degraded,
            "source_event_id": source.event_sequence,
        }, key=result_key, character_name=knowledge.identity["name"])
        self.db.commit()
        return self.event_envelope(event)

    @staticmethod
    def event_envelope(event: GameEventDBModel) -> dict:
        return {
            "event_id": event.event_sequence,
            "session_id": event.session_id,
            "type": event.event_type,
            "timestamp": event.timestamp.replace(tzinfo=timezone.utc).isoformat() if event.timestamp.tzinfo is None else event.timestamp.isoformat(),
            "payload": {key: value for key, value in (event.event_metadata or {"content": event.content}).items()
                        if not key.startswith("_")},
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
            raise FusionGameError("请在候选审核页面确认具体版本后登记发布，旧入口不能直接发布")
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
        content = payload.get("content", "")
        participant = self.db.query(UserGameParticipant).filter_by(
            session_id=session.session_id, character_id=target, participant_type="AI",
        ).first()
        role = self.db.query(CharacterDBModel).filter_by(id=target, script_id=session.script_id, is_victim=False).first()
        if not participant or not role or not isinstance(content, str) or not content.strip() or len(content) > 1000:
            raise FusionGameError("提问目标或内容无效")
        self._append_event(session, "QUESTION_ASKED", {"from": human.character_id, "to": target, "content": content.strip()}, key, human.user_id, human.character_name)

    def _searchable_evidence(self, session: GameSession):
        """真人和 AI 共用候选范围；隐藏/未知地点证据不会由旧搜证规则提前发放。"""
        claimed = self.db.query(ParticipantEvidence.evidence_id).filter_by(session_id=session.session_id)
        locations = self.db.query(LocationDBModel.name).filter_by(script_id=session.script_id)
        return self.db.query(EvidenceDBModel).filter(
            EvidenceDBModel.script_id == session.script_id,
            EvidenceDBModel.is_hidden.is_(False),
            EvidenceDBModel.location.in_(locations),
            ~EvidenceDBModel.id.in_(claimed),
        ).order_by(EvidenceDBModel.id)

    def _search(self, session, human, payload, key):
        location_id = int(payload.get("location_id", 0))
        location = self.db.query(LocationDBModel).filter_by(id=location_id, script_id=session.script_id).first()
        if not location:
            raise FusionGameError("搜证地点无效")
        if human.search_actions_remaining <= 0:
            raise FusionGameError("本局搜证次数已用完")
        evidence = self._searchable_evidence(session).filter(
            EvidenceDBModel.location == location.name,
        ).first()
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
        evidence = self.db.query(EvidenceDBModel).filter_by(id=evidence_id, script_id=session.script_id).first()
        if not evidence or owned.visibility not in (EvidenceVisibility.CHARACTER_PRIVATE.value, EvidenceVisibility.PUBLIC.value):
            raise FusionGameError("证据不属于本局可公开范围")
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
        """旧分配启发式；不是证据推理或完整的剧本信息披露规则。"""
        available = self._searchable_evidence(session).all()
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

    def _owned_session(self, session_id, user_id, for_update=False):
        query = self.db.query(GameSession).filter_by(session_id=session_id, host_user_id=user_id)
        if for_update:
            # PostgreSQL 在所有写入口锁定同一行；不依赖可选 Redis 才保证序列化。
            query = query.populate_existing().with_for_update()
        session = query.first()
        if not session:
            raise FusionGameError("会话不存在或无权访问")
        return session

    def _human(self, session_id, user_id, required=True):
        item = self.db.query(UserGameParticipant).filter_by(session_id=session_id, user_id=user_id, participant_type="HUMAN").first()
        if required and not item:
            raise FusionGameError("请先选择角色")
        return item

    def _duplicate(self, session_id, key):
        return self.db.query(GameEventDBModel).filter_by(
            session_id=session_id, idempotency_key=key,
        ).populate_existing().first()

    @staticmethod
    def _validate_client_key(key: str) -> None:
        if not isinstance(key, str) or not 8 <= len(key) <= 100 or key.startswith(("ai-start:", "ai-result:")):
            raise FusionGameError("无效的客户端幂等键，不能使用系统保留前缀")

    @staticmethod
    def _request_fingerprint(action: str, payload: dict) -> str:
        return sha256(json.dumps({"action": action, "payload": payload}, sort_keys=True,
                                 ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    def _stamp_request(self, event: GameEventDBModel, session: GameSession, action: str, payload: dict) -> None:
        event.event_metadata = dict(event.event_metadata or {},
                                    _request_fingerprint=self._request_fingerprint(action, payload),
                                    _action_phase=session.current_phase,
                                    _action_state_version=session.state_version)

    def _require_same_request(self, event: GameEventDBModel, action: str, payload: dict) -> None:
        if (event.event_metadata or {}).get("_request_fingerprint") != self._request_fingerprint(action, payload):
            raise FusionGameError("幂等键已用于其他操作，或属于旧版本请求；请使用新键")

    @staticmethod
    def _receipt_generations(receipt_data: dict) -> list[dict]:
        """Read v2 history while accepting the single-generation v1 receipt."""
        raw = receipt_data.get("generations")
        if isinstance(raw, list):
            return [dict(item) for item in raw if isinstance(item, dict) and item.get("generation_id")]
        generation_id = receipt_data.get("generation_id")
        if not generation_id:
            return []
        return [{
            "generation_id": generation_id,
            "status": receipt_data.get("status", "UNKNOWN"),
            "lease_until": receipt_data.get("lease_until", 0),
            "reservation": receipt_data.get("reservation") or {},
            "accounting": receipt_data.get("accounting") or {},
            "accounting_status": receipt_data.get("accounting_status", "LEGACY"),
            "model": receipt_data.get("model") or {},
            "legacy_missing_reservation": not usage_metadata_is_valid(receipt_data.get("reservation")),
        }]

    @staticmethod
    def _find_generation(generations: list[dict], generation_id: str | None) -> dict | None:
        return next((item for item in generations if item.get("generation_id") == generation_id), None)

    @classmethod
    def _write_receipt(cls, receipt: GameEventDBModel, source_event_id: int, character_id: int,
                       generations: list[dict], current_generation_id: str | None) -> None:
        current = cls._find_generation(generations, current_generation_id)
        receipt.event_metadata = {
            "schema_version": 2,
            "source_event_id": source_event_id,
            "character_id": character_id,
            "current_generation_id": current_generation_id,
            # Mirrors keep the operational fields easy to inspect and preserve
            # compatibility with the first receipt implementation.
            "generation_id": current_generation_id,
            "status": current.get("status", "UNKNOWN") if current else "UNKNOWN",
            "lease_until": current.get("lease_until", 0) if current else 0,
            "generations": generations,
        }

    @staticmethod
    def _session_usage(session: GameSession) -> UsageAmount:
        return UsageAmount.from_metadata({
            "prompt_tokens": session.prompt_tokens,
            "completion_tokens": session.completion_tokens,
            "cost_cny": session.estimated_cost,
        })

    def _receipt_accounting_total(self, session_id: str) -> UsageAmount:
        total = UsageAmount()
        receipts = self.db.query(GameEventDBModel).filter_by(
            session_id=session_id, event_type="AI_TURN_RECEIPT",
        ).all()
        for receipt in receipts:
            data = receipt.event_metadata
            if not isinstance(data, dict):
                continue
            for generation in self._receipt_generations(data):
                accounting = generation.get("accounting")
                if usage_metadata_is_valid(accounting):
                    total = total + UsageAmount.from_metadata(accounting)
        return total

    def _ledger_usage(self, session: GameSession) -> UsageAmount:
        """Use decimal receipt data as authority; the session Float is display-only."""
        state = session.state_data or {}
        snapshot = state.get("_llm_budget_v1") if isinstance(state, dict) else None
        baseline = snapshot.get("baseline") if isinstance(snapshot, dict) else None
        if not usage_metadata_is_valid(baseline):
            return self._session_usage(session)
        return UsageAmount.from_metadata(baseline) + self._receipt_accounting_total(session.session_id)

    @staticmethod
    def _apply_accounting(session: GameSession, previous: UsageAmount, replacement: UsageAmount) -> None:
        session.prompt_tokens = max(0, session.prompt_tokens - previous.prompt_tokens + replacement.prompt_tokens)
        session.completion_tokens = max(
            0, session.completion_tokens - previous.completion_tokens + replacement.completion_tokens,
        )
        previous_cost = Decimal(str(session.estimated_cost or 0))
        session.estimated_cost = float(max(Decimal("0"), previous_cost - previous.cost_cny + replacement.cost_cny))

    def _settle_generation(self, session: GameSession, generation: dict, policy: BudgetPolicy,
                           provider_usage: dict | None, usage_uncertain: bool,
                           unknown_attempts: int | None = None,
                           max_attempts: int | None = None) -> UsageAmount:
        reservation = UsageAmount.from_metadata(generation.get("reservation"))
        previous = UsageAmount.from_metadata(generation.get("accounting"))
        usage_uncertain = usage_uncertain or (
            provider_usage is not None and not provider_usage_is_complete(provider_usage)
        )
        replacement = policy.settle(
            reservation,
            provider_usage,
            usage_uncertain,
            unknown_attempts=unknown_attempts if unknown_attempts and unknown_attempts > 0 else None,
            max_attempts=max_attempts,
        )
        self._apply_accounting(session, previous, replacement)
        generation["accounting"] = replacement.to_metadata()
        if usage_uncertain:
            generation["accounting_status"] = "ESTIMATED_MAX"
        elif provider_usage is not None:
            generation["accounting_status"] = "PROVIDER_REPORTED"
        elif generation.get("budget_reason"):
            generation["accounting_status"] = "NOT_CALLED_BUDGET"
        else:
            generation["accounting_status"] = "NOT_CHARGED"
        if (replacement.prompt_tokens > reservation.prompt_tokens or
                replacement.completion_tokens > reservation.completion_tokens or
                replacement.cost_cny > reservation.cost_cny):
            generation["accounting_status"] = "ACCOUNTING_OVERRUN"
            generation["accounting_overrun"] = True
            state = dict(session.state_data or {})
            state["_llm_accounting_overrun"] = True
            session.state_data = state
        return replacement

    def _model_budget_snapshot(self, session: GameSession, runtime: FusionAgentOrchestrator
                               ) -> tuple[BudgetPolicy, str | None]:
        """Freeze model/pricing behavior for a game at its first model action."""
        current_policy = BudgetPolicy.from_env(runtime.settings.provider)
        current = {"policy": current_policy.to_snapshot(), "model": runtime.model_metadata()}
        if session.state_data is not None and not isinstance(session.state_data, dict):
            return current_policy, "ACCOUNTING_CORRUPT"
        state = dict(session.state_data or {})
        summary_metadata = {
            "prompt_tokens": session.prompt_tokens,
            "completion_tokens": session.completion_tokens,
            "cached_prompt_tokens": 0,
            "cost_cny": session.estimated_cost,
        }
        if not usage_metadata_is_valid(summary_metadata):
            return current_policy, "ACCOUNTING_CORRUPT"
        stored = state.get("_llm_budget_v1")
        if stored is None:
            summary = UsageAmount.from_metadata(summary_metadata)
            existing = self._receipt_accounting_total(session.session_id)
            has_legacy_activity = summary.total_tokens > 0 or existing.total_tokens > 0 or self.db.query(
                GameEventDBModel.id,
            ).filter_by(session_id=session.session_id, event_type="AI_TURN_RECEIPT").first() is not None
            baseline = UsageAmount(
                prompt_tokens=max(0, summary.prompt_tokens - existing.prompt_tokens),
                completion_tokens=max(0, summary.completion_tokens - existing.completion_tokens),
                cost_cny=max(Decimal("0"), summary.cost_cny - existing.cost_cny),
            )
            current["baseline"] = baseline.to_metadata()
            # A historical Float/token summary cannot prove which provider price
            # produced it.  Persist the migration doubt so a second request cannot
            # turn a one-time warning into a paid call.
            current["legacy_accounting_unverified"] = has_legacy_activity
            state["_llm_budget_v1"] = current
            session.state_data = state
            issue = "LEGACY_ACCOUNTING_UNVERIFIED" if current_policy.paid_calls_enabled and has_legacy_activity else None
            return current_policy, issue
        if not isinstance(stored, dict):
            return current_policy, "ACCOUNTING_CORRUPT"
        stored_policy = BudgetPolicy.from_snapshot(stored.get("policy"))
        if (stored_policy is None or not usage_metadata_is_valid(stored.get("baseline")) or
                not isinstance(stored.get("legacy_accounting_unverified"), bool)):
            return current_policy, "ACCOUNTING_CORRUPT"
        if state.get("_llm_accounting_overrun") is True:
            return stored_policy, "ACCOUNTING_OVERRUN"
        if stored["legacy_accounting_unverified"] and stored_policy.paid_calls_enabled:
            return stored_policy, "LEGACY_ACCOUNTING_UNVERIFIED"
        if stored.get("policy") != current["policy"] or stored.get("model") != current["model"]:
            return stored_policy, "MODEL_CONFIG_CHANGED"
        return stored_policy, None

    @staticmethod
    def _lease_value(value) -> float:
        try:
            lease = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return lease if math.isfinite(lease) and lease >= 0 else 0

    @staticmethod
    def _legacy_max_reservation(policy: BudgetPolicy) -> UsageAmount:
        amount = policy.amount(policy.token_limit, 0)
        cost = amount.cost_cny
        if policy.cost_limit_cny is not None:
            cost = max(cost, policy.cost_limit_cny)
        return UsageAmount(prompt_tokens=policy.token_limit, cost_cny=cost)

    def _active_ai_reservations(self, session_id: str, policy: BudgetPolicy, now: float) -> UsageAmount:
        total = UsageAmount()
        receipts = self.db.query(GameEventDBModel).filter_by(
            session_id=session_id, event_type="AI_TURN_RECEIPT",
        ).all()
        for receipt in receipts:
            if not isinstance(receipt.event_metadata, dict):
                continue
            for generation in self._receipt_generations(dict(receipt.event_metadata)):
                if (generation.get("status") == "RUNNING" and
                        self._lease_value(generation.get("lease_until")) > now):
                    if generation.get("legacy_missing_reservation"):
                        total = total + self._legacy_max_reservation(policy)
                    else:
                        total = total + UsageAmount.from_metadata(generation.get("reservation"))
        return total

    def _expire_ai_reservations(self, session: GameSession, policy: BudgetPolicy, now: float) -> None:
        """Turn expired in-flight work into conservative usage before reuse."""
        receipts = self.db.query(GameEventDBModel).filter_by(
            session_id=session.session_id, event_type="AI_TURN_RECEIPT",
        ).all()
        for receipt in receipts:
            if not isinstance(receipt.event_metadata, dict):
                continue
            data = dict(receipt.event_metadata)
            generations = self._receipt_generations(data)
            changed = False
            for generation in generations:
                if (generation.get("status") == "RUNNING" and
                        self._lease_value(generation.get("lease_until")) <= now):
                    if generation.get("legacy_missing_reservation"):
                        generation["reservation"] = self._legacy_max_reservation(policy).to_metadata()
                    self._settle_generation(session, generation, policy, None, True)
                    generation["status"] = "EXPIRED_UNKNOWN"
                    generation["lease_until"] = 0
                    changed = True
            if changed:
                self._write_receipt(
                    receipt,
                    data.get("source_event_id"),
                    data.get("character_id"),
                    generations,
                    data.get("current_generation_id") or data.get("generation_id"),
                )

    def _accounting_integrity_issue(self, session_id: str) -> bool:
        """Reject corrupted v2 ledgers instead of coercing missing values to zero."""
        allowed_statuses = {
            "RUNNING", "BUDGET_BLOCKED", "COMPLETED", "STALE", "CANCELLED",
            "CANCELLED_UNKNOWN", "EXPIRED_UNKNOWN", "LATE_RECONCILED",
        }
        receipts = self.db.query(GameEventDBModel).filter_by(
            session_id=session_id, event_type="AI_TURN_RECEIPT",
        ).all()
        for receipt in receipts:
            data = receipt.event_metadata
            if not isinstance(data, dict):
                return True
            if data.get("schema_version") != 2:
                # The single-generation v1 shape is handled conservatively.
                if not data.get("generation_id"):
                    return True
                # An in-flight legacy call is charged at the current game's full
                # allowance by the reservation/expiry paths.  Any other legacy
                # receipt without trustworthy usage cannot be reconciled safely.
                if data.get("status") != "RUNNING" and (
                    not usage_metadata_is_valid(data.get("reservation")) or
                    not usage_metadata_is_valid(data.get("accounting"))
                ):
                    return True
                continue
            raw = data.get("generations")
            if not isinstance(raw, list) or not raw or len(raw) > 64:
                return True
            ids = [item.get("generation_id") for item in raw if isinstance(item, dict)]
            if len(ids) != len(raw) or any(not isinstance(item, str) or not item for item in ids):
                return True
            if len(set(ids)) != len(ids) or data.get("current_generation_id") not in ids:
                return True
            for generation in raw:
                if generation.get("status") not in allowed_statuses:
                    return True
                if not usage_metadata_is_valid(generation.get("reservation")):
                    return True
                if not usage_metadata_is_valid(generation.get("accounting")):
                    return True
                if generation.get("status") == "RUNNING" and self._lease_value(
                    generation.get("lease_until"),
                ) <= 0:
                    return True
        return False

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
