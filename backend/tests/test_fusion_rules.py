import pytest

from src.fusion.rules import FusionGamePhase, next_phase, require_action_allowed, resolve_vote


def test_phase_order_and_permissions():
    assert next_phase(FusionGamePhase.CHARACTER_SELECTION) == FusionGamePhase.SCRIPT_READING
    assert next_phase(FusionGamePhase.RUNOFF_VOTING) == FusionGamePhase.REVELATION
    require_action_allowed(FusionGamePhase.EVIDENCE_ROUND_1, "search_location")
    with pytest.raises(ValueError):
        require_action_allowed(FusionGamePhase.EVIDENCE_ROUND_1, "cast_vote")


def test_vote_resolution_supports_runoff():
    assert resolve_vote([1, 1, 2])["leaders"] == [1]
    tied = resolve_vote([1, 2, 1, 2])
    assert tied["status"] == "TIED"
    assert tied["leaders"] == [1, 2]

