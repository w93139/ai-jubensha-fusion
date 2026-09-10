"""Explicit table 1.1 dispatch, pending recovery and complete settlement."""
import asyncio
from unittest.mock import patch

from src.fusion.package_play import PackagePlayService
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play
from tests.fusion_security import test_full_play_decisions as existing


def modern(play, db=None):
    return PackagePlayService(db if db is not None else play.db, play.publisher, play.model,
        play.policy, lambda: play.clock[0], speech_policy='role-speech/1.8', table_policy='package-table-model/1.1')


def test_bound_table_policy_complete_five_seat_settlement_and_restored_result(play):
    play.play = modern(play)
    with patch.object(existing, 'service', modern):
        existing.test_full_play_complete_persistent_five_seat_model_submissions_and_settlement(play)
    assert play.play.table_model.metadata()['schema_version'] == 'package-table-model/1.1'


def test_bound_table_pending_request_replays_before_sdk_and_expiry_never_redispatches(play):
    play.play = modern(play); view = existing.open_ballot(play); pid = view['play_id']
    req = existing.decision(view['revision']); _, prepared = play.play._begin(pid, req, 1)
    assert prepared is not None and play.sdk.chat_completion.await_count == 0
    with play.factory() as db:
        assert modern(play, db).get(pid, 1)['pending_ai']
    play.clock[0] += 100
    view = asyncio.run(play.play.decide(pid, req, 1))
    assert view['last_ai_status'] == 'EXPIRED' and play.sdk.chat_completion.await_count == 0
    with play.factory() as db: assert modern(play, db).get(pid, 1) == view
