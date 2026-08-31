from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.base import SQLAlchemyBase
from src.db.models import BackgroundStoryDBModel, CharacterDBModel, EvidenceDBModel, LocationDBModel, ScriptDBModel, User
from src.db.models.script_model import ScriptStatus
from src.fusion.service import FusionGameService


def make_service():
    engine = create_engine("sqlite:///:memory:")
    SQLAlchemyBase.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(username="detective", email="d@example.com", hashed_password="x")
    script = ScriptDBModel(
        title="钟楼疑云", description="测试案件", player_count=4, duration_minutes=40,
        status=ScriptStatus.PUBLISHED, is_public=True,
    )
    db.add_all([user, script]); db.flush()
    roles = [
        CharacterDBModel(script_id=script.id, name=f"角色{i}", profession="调查者", background=f"经历{i}", secret=f"秘密{i}", objective="找出真相", is_murderer=i == 0)
        for i in range(4)
    ]
    db.add_all(roles)
    db.add(LocationDBModel(script_id=script.id, name="书房", description="布满灰尘"))
    db.add_all([EvidenceDBModel(script_id=script.id, name=f"线索{i}", location="书房", description=f"证据{i}") for i in range(4)])
    db.add(BackgroundStoryDBModel(script_id=script.id, title="午夜钟声", setting_description="旧宅", incident_description="主人遇害", murder_method="毒杀", murder_location="书房"))
    db.commit()
    return FusionGameService(db), db, user, script, roles


def test_private_information_filtering_and_idempotent_action():
    games, db, user, script, roles = make_service()
    state = games.create_session(script.id, user.id)
    session_id = state["session"]["session_id"]
    state = games.select_character(session_id, user.id, roles[1].id, "select-key-0001")
    assert state["my_role"]["secret"] == "秘密1"
    assert all("secret" not in item and "is_murderer" not in item for item in state["characters"])

    first = games.perform_action(session_id, user.id, "ready", {}, "ready-key-00001")
    duplicate = games.perform_action(session_id, user.id, "ready", {}, "ready-key-00001")
    assert first["last_event_id"] == duplicate["last_event_id"]


def test_search_is_private_until_owner_reveals_it():
    games, db, user, script, roles = make_service()
    session_id = games.create_session(script.id, user.id)["session"]["session_id"]
    games.select_character(session_id, user.id, roles[1].id, "select-key-0002")
    session = games._owned_session(session_id, user.id)
    session.current_phase = "EVIDENCE_ROUND_1"
    db.commit()
    location_id = db.query(LocationDBModel).filter_by(script_id=script.id).one().id
    state = games.perform_action(session_id, user.id, "search_location", {"location_id": location_id}, "search-key-0001")
    assert len(state["private_evidence"]) == 1
    assert state["public_evidence"] == []
    evidence_id = state["private_evidence"][0]["id"]
    state = games.perform_action(session_id, user.id, "reveal_evidence", {"evidence_id": evidence_id}, "reveal-key-0001")
    assert state["public_evidence"][0]["id"] == evidence_id

