"""Whitelist package/rules pairs without upgrading historical play bindings."""
from src.fusion.package_play_rules import PackagePlayRules, PlayRulesError, RULES_CONTRACT
from src.fusion.package_investigation_rules import (
    PackageInvestigationRules, RULES_CONTRACT as INVESTIGATION_RULES_CONTRACT,
)
from src.fusion.package_memory_rules import PackageMemoryRules, RULES_CONTRACT as MEMORY_RULES_CONTRACT
from src.fusion.package_full_play_rules import PackageFullPlayRules, RULES_CONTRACT as FULL_RULES_CONTRACT


def rules_contract(package: dict) -> str:
    version = package.get("schema_version")
    if version in {"script-package/1.0", "script-package/1.1"}:
        return RULES_CONTRACT
    if version == "script-package/1.2":
        return INVESTIGATION_RULES_CONTRACT
    if version == "script-package/1.3":
        return MEMORY_RULES_CONTRACT
    if version == 'script-package/1.4':
        return FULL_RULES_CONTRACT
    raise PlayRulesError("PACKAGE_PLAY_PACKAGE_INVALID")


def play_engine(package: dict, character_id: str, frozen_contract: str | None = None):
    contract = rules_contract(package)
    if frozen_contract is not None and frozen_contract != contract:
        raise PlayRulesError("PACKAGE_PLAY_SNAPSHOT_INVALID")
    engine = {INVESTIGATION_RULES_CONTRACT: PackageInvestigationRules,
              MEMORY_RULES_CONTRACT: PackageMemoryRules, FULL_RULES_CONTRACT: PackageFullPlayRules}.get(contract, PackagePlayRules)
    return engine(package, character_id)
