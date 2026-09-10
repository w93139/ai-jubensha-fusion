"""Versioned engine facts for reviewers; never a playability verdict."""
from copy import deepcopy

from src.fusion.package_validation import content_hash


def investigation_runtime_facts() -> dict:
    # Keep this published contract stable. Engine changes require a new version.
    return {"package_contract": "script-package/1.2", "human_players": 1,
            "investigation_actor": "SELECTED_HUMAN_ONLY", "action_success_limit": "ONCE_PER_SESSION",
            "phase_budget": "SHARED_NO_CARRY", "material_recipient": "DECLARED_OWNER"}


def audit_runtime_contract() -> dict:
    return {"schema_version": "audit-runtime/1.0", "rules_contract": "package-play-rules/1.1",
            "facts": investigation_runtime_facts(),
            "action_prerequisites": "ALL_DECLARED_ACTIONS_AND_PUBLIC_EVIDENCE",
            "zero_cost_actions": "ALSO_ONCE_PER_SESSION",
            "require_exhausted": "CURRENT_PHASE_REMAINING_POINTS_MUST_BE_ZERO",
            "ai_investigation": "UNSUPPORTED",
            "ai_material_reply": "SEPARATE_AUTHORIZED_SHARING_NO_INVESTIGATION_POINTS",
            "settlement": "EXPLICIT_TERMINAL_ACTION_REVEALS_ONLY_DECLARED_TRUTH_IDS",
            "semantic_status": "UNREVIEWED", "publication_ready": False,
            "limitations": ["NO_GENERAL_REACHABILITY_PROOF", "NO_FULL_VOTING_OR_PERSONAL_SCORING",
                            "NO_KEYWORD_MEMORY_OR_COMPLEX_BRANCHES", "NO_PACING_OR_BALANCE_VERIFICATION"]}


def candidate_observations(package: dict) -> dict:
    """Project already validated candidate fields, never infer source correctness.

    Called only after parse_compiler_output; no source/client facts can replace
    this projection. No body text, truth text, or synthetic expected answer.
    """
    return {"schema_version": "audit-candidate-observations/1.0", "package_hash": content_hash(package),
            "settlement": deepcopy({key: package["settlement"][key] for key in ("phase_id", "truth_ids")}),
            "semantic_status": "UNREVIEWED"}
