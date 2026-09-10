"""Offline deterministic checks; no file access, model calls or runtime writes."""
from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
import json
from typing import Any

from pydantic import ValidationError

from src.schemas.script_package import (
    CONTRACT_VERSION, CONTRACT_VERSION_V11, CONTRACT_VERSION_V12, CONTRACT_VERSION_V13, CONTRACT_VERSION_V14,
    ScriptPackage, ScriptPackageV11, ScriptPackageV12, ScriptPackageV13, ScriptPackageV14, SourceFileV11,
    parse_script_package,
)


VALIDATOR_VERSION = "script-package-validator/1.1"
VALIDATOR_VERSION_V12 = "script-package-validator/1.2"
CANONICAL_VERSION = "python-json-sort-utf8/1"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024


def validator_version_for(document: Any) -> str:
    if isinstance(document, dict) and document.get('schema_version') == CONTRACT_VERSION_V14:
        return 'script-package-validator/1.4'
    if isinstance(document, dict) and document.get("schema_version") == CONTRACT_VERSION_V13:
        return "script-package-validator/1.3"
    return (VALIDATOR_VERSION_V12 if isinstance(document, dict)
            and document.get("schema_version") == CONTRACT_VERSION_V12 else VALIDATOR_VERSION)


class PackageInputError(ValueError):
    """Safe error text: never include candidate content or JSON parser errors."""


def canonical_json(value: Any) -> str:
    """Exact JSON values; key order ignored, array order/content preserved."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 40 or count > 200000:
            raise PackageInputError("候选包层级或节点数量超出限制")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise PackageInputError("候选包必须使用 JSON 字符串键")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise PackageInputError("候选包包含非 JSON 值")
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        size = len(serialized.encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise PackageInputError("候选包不是有效的 UTF-8 JSON") from exc
    if size > MAX_PACKAGE_BYTES:
        raise PackageInputError("候选包超过 2 MiB 限制")
    return serialized


def content_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_package_json(raw: bytes) -> Any:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise PackageInputError("请求超过 2 MiB 限制")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise PackageInputError("JSON 包含重复字段")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise PackageInputError("JSON 包含非法数字")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PackageInputError("请求必须是有效且无重复字段的 UTF-8 JSON") from exc
    canonical_json(value)
    return value


def validate_package(document: Any) -> dict:
    """Return a hash-bound report. valid means structural validation only."""
    digest = content_hash(document)
    issues: list[dict] = []
    report = {
        "package_hash": digest,
        "contract_version": (document["schema_version"] if isinstance(document, dict)
                             and document.get("schema_version") in (CONTRACT_VERSION_V11, CONTRACT_VERSION_V12, CONTRACT_VERSION_V13, CONTRACT_VERSION_V14)
                             else CONTRACT_VERSION),
        "validator_version": validator_version_for(document),
        "canonical_version": CANONICAL_VERSION,
        "valid": False,
        "publication_ready": False,
        "pending_gates": ["SOURCE_VERIFICATION", "RUNTIME_COMPATIBILITY", "AUDIT", "HUMAN_REVIEW"],
        "issues": issues,
        "issues_truncated": False,
    }

    def issue(code: str, path: str, message: str, sources: list | None = None) -> None:
        if len(issues) >= 200:
            report["issues_truncated"] = True
            return
        issues.append({"code": code, "severity": "ERROR", "path": path,
                       "message": message, "sources": (sources or [])[:3]})

    try:
        package = parse_script_package(document)
    except ValidationError as exc:
        # Unknown field names themselves can contain secrets. Report known
        # schema names/array indices only; do not echo input, msg, ctx or URLs.
        schema_text = [ScriptPackage.model_json_schema(), ScriptPackageV11.model_json_schema()]
        if isinstance(document, dict) and document.get("schema_version") == CONTRACT_VERSION_V12:
            schema_text.append(ScriptPackageV12.model_json_schema())
        if isinstance(document, dict) and document.get("schema_version") == CONTRACT_VERSION_V13:
            schema_text.append(ScriptPackageV13.model_json_schema())
        if isinstance(document, dict) and document.get('schema_version') == CONTRACT_VERSION_V14:
            schema_text.append(ScriptPackageV14.model_json_schema())
        known_fields: set[str] = set()

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                known_fields.update(node.get("properties", {}))
                for child in node.values():
                    collect(child)
            elif isinstance(node, list):
                for child in node:
                    collect(child)

        collect(schema_text)
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            path = "/" + "/".join(str(part) if isinstance(part, int) or part in known_fields else "<field>"
                                   for part in error["loc"])
            issue("SCHEMA_INVALID", path, "字段缺失、类型错误或契约尚不支持此字段/值")
        return report

    collections = {name: getattr(package, name) for name in ("sources", "characters", "phases", "knowledge", "evidence", "truth")}
    indexes = {}
    for name, items in collections.items():
        seen = {}
        for index, item in enumerate(items):
            if item.id in seen:
                issue("DUPLICATE_ID", f"/{name}/{index}/id", "同类实体 ID 必须唯一")
            seen[item.id] = item
        indexes[name] = seen
    sources = indexes["sources"]
    phases = indexes["phases"]
    evidence = indexes["evidence"]
    characters = indexes["characters"]

    def references(refs: list, path: str) -> None:
        for index, ref in enumerate(refs):
            source = sources.get(ref.source_id)
            location = f"{path}/sources/{index}"
            if source is None:
                issue("SOURCE_NOT_FOUND", location, "来源 ID 不在本包清单中")
            elif ref.page is not None and source.page_count is not None and ref.page > source.page_count:
                issue("SOURCE_PAGE_OUT_OF_RANGE", location, "来源页码超出清单页数")
            if ref.page is None and ref.anchor is None:
                issue("SOURCE_LOCATION_MISSING", location, "来源必须提供页码或锚点")

    paths: set[str] = set()
    for index, source in enumerate(package.sources):
        path = f"/sources/{index}"
        if source.relative_path in paths:
            issue("DUPLICATE_SOURCE_PATH", path, "同一路径不能声明为多份来源")
        paths.add(source.relative_path)
        if isinstance(source, SourceFileV11):
            if source.kind == "normalized" and any(
                    original_id not in sources or sources[original_id].kind != "original"
                    for original_id in source.original_source_ids):
                issue("ORIGINAL_SOURCE_MISSING", path, "规范化文件必须关联本包中的全部原件")
        else:
            original = sources.get(source.original_source_id)
            if source.kind == "normalized" and (original is None or original.kind != "original"):
                issue("ORIGINAL_SOURCE_MISSING", path, "规范化文件必须关联本包中的原件")
            if source.kind == "original" and source.original_source_id is not None:
                issue("INVALID_SOURCE_LINK", path, "原件不能再声明上游原件")

    references(package.introduction.sources, "/introduction")
    references(package.settlement.instructions.sources, "/settlement/instructions")
    for name in ("characters", "phases", "knowledge", "evidence", "truth"):
        for index, item in enumerate(collections[name]):
            references(item.sources, f"/{name}/{index}")

    if package.player_count != len(package.characters):
        issue("CHARACTER_COUNT_MISMATCH", "/player_count", "玩家数量与可选角色数量不一致")
    for index, phase in enumerate(package.phases):
        if phase.next_phase_id is not None and phase.next_phase_id not in phases:
            issue("PHASE_NOT_FOUND", f"/phases/{index}/next_phase_id", "下一阶段不在本包中")

    phase_order: dict[str, int] = {}
    current = package.initial_phase_id
    while current in phases and current not in phase_order:
        phase_order[current] = len(phase_order)
        current = phases[current].next_phase_id
    if current in phase_order:
        issue("PHASE_CYCLE", "/phases", "当前契约只支持无环的线性阶段")
    if package.initial_phase_id not in phases:
        issue("PHASE_NOT_FOUND", "/initial_phase_id", "初始阶段不在本包中")
    if len(phase_order) != len(phases):
        issue("UNREACHABLE_PHASE", "/phases", "存在无法从初始阶段到达的阶段")
    final_phase = package.settlement.phase_id
    if final_phase not in phases or phases[final_phase].next_phase_id is not None:
        issue("INVALID_SETTLEMENT_PHASE", "/settlement/phase_id", "结算必须绑定本包的最后阶段")
    if len(set(package.settlement.truth_ids)) != len(package.settlement.truth_ids):
        issue("DUPLICATE_REFERENCE", "/settlement/truth_ids", "结算真相引用不能重复")
    if any(key not in indexes["truth"] for key in package.settlement.truth_ids):
        issue("TRUTH_NOT_FOUND", "/settlement/truth_ids", "结算引用了不存在的后台真相")

    for name in ("knowledge", "evidence"):
        for index, item in enumerate(collections[name]):
            path = f"/{name}/{index}"
            refs = [ref.model_dump(exclude_none=True) for ref in item.sources]
            if item.visibility == "PUBLIC":
                if item.character_id is not None or item.disclosure != "PUBLIC":
                    issue("INVALID_PUBLIC_SCOPE", path, "公共材料不得绑定私有角色或私有披露规则", refs)
            elif item.character_id not in characters or item.disclosure == "PUBLIC":
                issue("INVALID_PRIVATE_SCOPE", path, "私有材料必须绑定本包角色和明确披露规则", refs)
            if item.release.phase_id not in phases:
                issue("PHASE_NOT_FOUND", path + "/release/phase_id", "解锁阶段不在本包中", refs)
            required = item.release.required_public_evidence_ids
            if len(set(required)) != len(required):
                issue("DUPLICATE_REFERENCE", path + "/release", "解锁依赖不能重复", refs)
            for prerequisite in required:
                target = evidence.get(prerequisite)
                if target is None:
                    issue("EVIDENCE_NOT_FOUND", path + "/release", "解锁依赖的线索不在本包中", refs)
                elif target.disclosure == "KEEP_PRIVATE":
                    issue("UNSATISFIABLE_RELEASE", path + "/release", "禁止公开的线索不能作为公开解锁条件", refs)
                # A phase is a floor, not an exact acquisition time. Requiring
                # later evidence is allowed; cycles are checked below.

    # Topological reachability is linear in nodes + edges, without recursion.
    waiting = {item.id: set(item.release.required_public_evidence_ids) for item in package.evidence}
    dependents: dict[str, set[str]] = defaultdict(set)
    for key, required in waiting.items():
        for prerequisite in required:
            dependents[prerequisite].add(key)
    ready = deque(key for key, required in waiting.items() if not required)
    available: set[str] = set()
    while ready:
        key = ready.popleft()
        available.add(key)
        for dependent in dependents[key]:
            waiting[dependent].discard(key)
            if not waiting[dependent]:
                ready.append(dependent)
    if len(available) != len(evidence):
        issue("UNREACHABLE_EVIDENCE", "/evidence", "线索依赖存在循环或缺失引用，无法全部解锁")

    for index, character in enumerate(package.characters):
        if not any(item.character_id == character.id and item.visibility == "CHARACTER_PRIVATE"
                   and item.release.phase_id == package.initial_phase_id
                   and not item.release.required_public_evidence_ids
                   and not getattr(item.release, "required_action_ids", []) for item in package.knowledge):
            issue("INITIAL_KNOWLEDGE_MISSING", f"/characters/{index}", "每个角色必须有无前置线索条件的初始私有材料")
    if isinstance(package, ScriptPackageV12):
        _validate_investigation(package, indexes, phase_order, references, issue)
    if isinstance(package, ScriptPackageV13):
        _validate_memories(package, indexes, phase_order, references, issue)
    if isinstance(package, ScriptPackageV14):
        _validate_full_play(package, indexes, phase_order, references, issue)
    report["valid"] = not issues
    return report


def _validate_full_play(package, indexes, phase_order, references, issue):
    from src.fusion.structured_finale import StructuredFinale

    full = package.full_play
    phases = {p.phase_id: p for p in full.phases}
    actors = set(indexes['characters'])
    orders = {x.action_id: x.order for x in full.action_order}
    if (len(phases) != len(full.phases) or set(phases) != set(indexes['phases'])
            or [p.phase_id for p in full.phases] != list(phase_order)
            or [p.kind for p in full.phases].count('FINALE') != 1
            or full.phases[-1].kind != 'FINALE' or full.phases[0].kind != 'READING'):
        issue('FULL_PLAY_PHASES_INVALID', '/full_play/phases', '整局阶段必须覆盖有序流程，开场私读、末阶段唯一封卷')
    if (len(orders) != len(full.action_order) or len(set(orders.values())) != len(orders)
            or set(orders) != {a.id for a in package.mechanics.actions}):
        issue('FULL_PLAY_ACTION_ORDER_INVALID', '/full_play/action_order', '每项调查须有唯一明确的决议顺序')
    if set(full.finale.votes.character_ids) != actors or package.player_count != 5:
        issue('FULL_PLAY_SEATS_INVALID', '/full_play/finale', '五席结算与本包角色必须一致')
    for action in package.mechanics.actions:
        if (set(action.allowed_character_ids) != actors
                or any(phases.get(p) is None or phases[p].kind != 'INVESTIGATION' for p in action.phase_ids)):
            issue('FULL_PLAY_COLLECTIVE_ACTION_INVALID', '/full_play', '共同调查仅在调查阶段开放，五席共同参与')
    for budget in package.mechanics.phase_budgets:
        if budget.phase_id in phases and phases[budget.phase_id].kind != 'INVESTIGATION' and budget.points != 0:
            issue('FULL_PLAY_NON_INVESTIGATION_BUDGET', '/full_play', '私读和封卷不配置调查额度')
    catalogs = {name: set(indexes[name]) for name in ('knowledge', 'evidence')}
    catalogs['memory'] = {m.id for m in package.memories}
    visual_ids = set()
    visual_pairs = set()
    materials = {**{(name, item.id): item for name in ('knowledge', 'evidence') for item in indexes[name].values()},
                 **{('memory', m.id): m for m in package.memories}}
    for visual in package.visuals:
        source = indexes['sources'].get(visual.source_id)
        material = materials.get((visual.collection, visual.material_id))
        allowed_sources = set()
        for ref in material.sources if material is not None else []:
            allowed_sources.add(ref.source_id)
            cited = indexes['sources'].get(ref.source_id)
            if cited is not None:
                allowed_sources.update(cited.original_source_ids)
        pair = (visual.collection, visual.material_id, visual.source_id)
        if (visual.id in visual_ids or pair in visual_pairs or material is None or source is None
                or source.kind != 'original' or source.media_type not in ('image/png', 'image/jpeg')
                or visual.source_id not in allowed_sources):
            issue('FULL_PLAY_VISUAL_INVALID', '/visuals', '整张原图须明确绑定已有材料及其原件来源，不按相邻页推定权限')
        visual_ids.add(visual.id)
        visual_pairs.add(pair)
    for question in full.finale.questions:
        for option in question.options:
            if any(r.id not in catalogs[r.collection] for clause in option.available_when for r in clause):
                issue('FULL_PLAY_FINALE_OPTION_REFERENCE_INVALID', '/full_play/finale/questions', '答卷可见条件仅能引用本包已有资料')
    for identity in full.finale.votes.identities:
        if any(r.id not in catalogs[r.collection] for clause in identity.available_when for r in clause):
            issue('FULL_PLAY_FINALE_OPTION_REFERENCE_INVALID', '/full_play/finale/votes/identities', '指认选项可见条件仅能引用本包已有资料')
    # All nested citations use the same page/source checks as existing fields.
    pending = [(full.model_dump(), '/full_play')]
    while pending:
        node, path = pending.pop()
        if isinstance(node, dict):
            if 'sources' in node:
                from src.schemas.package_primitives import SourceReference
                references([SourceReference.model_validate(r) for r in node['sources']], path)
                kinds = {indexes['sources'][r['source_id']].kind for r in node['sources'] if r['source_id'] in indexes['sources']}
                if ((node.get('origin') == 'SOURCE_EXPLICIT' and 'supplement' in kinds)
                        or (node.get('origin') == 'EDITORIAL' and 'supplement' not in kinds)):
                    issue('RULE_ORIGIN_MISMATCH', path + '/origin', '终局规则须区分原件与编辑补充来源')
            pending.extend((v, path + '/' + k) for k, v in node.items() if k != 'sources')
        elif isinstance(node, list):
            pending.extend((v, path + '/' + str(i)) for i, v in enumerate(node))
    try:
        StructuredFinale(full.finale.model_dump(), set(indexes['sources']),
                         {m.id: m.character_id for m in package.memories}, set(indexes['truth']), set())
    except ValueError:
        issue('FULL_PLAY_FINALE_BINDING_INVALID', '/full_play/finale', '终局题目、来源、回忆及结局引用必须完整属于本包')


def _validate_memories(package, indexes, phase_order, references, issue):
    identifiers = set()
    ranks = {phase_id: index for index, phase_id in enumerate(phase_order)}
    # Native evidence without actions/shares is forced at its phase floor.
    # Do not accept a trigger whose only acquisition already happened before
    # its memory becomes eligible. Non-forced investigation timing stays open.
    forced = {}
    pending = list(indexes["evidence"].values())
    while pending:
        later = []
        for evidence in pending:
            release = evidence.release
            if getattr(release, "required_action_ids", []):
                continue
            deps = release.required_public_evidence_ids
            if any(key not in forced or indexes["evidence"][key].visibility != "PUBLIC" for key in deps):
                later.append(evidence)
            elif release.phase_id in ranks:
                forced[evidence.id] = max([ranks[release.phase_id], *(forced[key] for key in deps)])
        if len(later) == len(pending):
            break
        pending = later
    for index, memory in enumerate(package.memories):
        path = f"/memories/{index}"
        if memory.id in identifiers or memory.id in indexes["knowledge"]:
            issue("DUPLICATE_ID", path + "/id", "回忆 ID 必须唯一，且不能与知识材料冲突")
        identifiers.add(memory.id)
        references(memory.sources, path)
        kinds = {indexes["sources"][ref.source_id].kind for ref in memory.sources if ref.source_id in indexes["sources"]}
        if ((memory.origin == "SOURCE_EXPLICIT" and "supplement" in kinds)
                or (memory.origin == "EDITORIAL" and "supplement" not in kinds)):
            issue("RULE_ORIGIN_MISMATCH", path + "/origin", "回忆规则与原件或编辑补充来源类别不一致")
        if memory.character_id not in indexes["characters"]:
            issue("CHARACTER_NOT_FOUND", path + "/character_id", "回忆接收角色不在本包中")
        if memory.phase_id not in indexes["phases"]:
            issue("PHASE_NOT_FOUND", path + "/phase_id", "回忆阶段不在本包中")
        seen = set()
        for offset, trigger in enumerate(memory.triggers):
            key = canonical_json(trigger.model_dump())
            if key in seen:
                issue("DUPLICATE_REFERENCE", path + f"/triggers/{offset}", "同一回忆不能重复声明触发方式")
            seen.add(key)
            if trigger.kind == "ACQUIRED_EVIDENCE":
                evidence = indexes["evidence"].get(trigger.evidence_id)
                if evidence is None:
                    issue("EVIDENCE_NOT_FOUND", path + f"/triggers/{offset}", "触发线索不在本包中")
                elif (evidence.visibility == "CHARACTER_PRIVATE" and evidence.disclosure == "KEEP_PRIVATE"
                      and evidence.character_id != memory.character_id):
                    issue("MEMORY_EVIDENCE_INACCESSIBLE", path + f"/triggers/{offset}", "他人不可出示的私密线索不能作为本人取得线索的触发")
                elif (memory.phase_id in ranks and evidence.id in forced
                      and forced[evidence.id] < ranks[memory.phase_id]
                      and (evidence.visibility == "PUBLIC" or evidence.character_id == memory.character_id)):
                    issue("MEMORY_TRIGGER_PRECEDES_PHASE", path + f"/triggers/{offset}", "线索必在回忆开放前取得，不能作为此后新增线索触发；请核对阶段或触发来源")


def _validate_investigation(package, indexes, phase_order, references, issue) -> None:
    """Check references and optimistic temporal reachability, not choice solvability.

    Each action must fit one phase's whole budget. We intentionally do not
    spend the aggregate here: mutually exclusive investigation choices are
    valid. A human review must still assess playability and exhaustion policy.
    """
    actions, budgets = {}, {}
    phases, evidence, characters = (indexes[key] for key in ("phases", "evidence", "characters"))

    def sourced(rule, path):
        references(rule.sources, path)
        kinds = [indexes["sources"][ref.source_id].kind for ref in rule.sources if ref.source_id in indexes["sources"]]
        if ((rule.origin == "SOURCE_EXPLICIT" and "supplement" in kinds)
                or (rule.origin == "EDITORIAL" and "supplement" not in kinds)):
            issue("RULE_ORIGIN_MISMATCH", path + "/origin", "规则标记必须与原件或编辑补充来源类别一致")

    for index, budget in enumerate(package.mechanics.phase_budgets):
        path = f"/mechanics/phase_budgets/{index}"
        sourced(budget, path)
        if budget.phase_id not in phases:
            issue("PHASE_NOT_FOUND", path + "/phase_id", "行动预算阶段不在本包中")
        if budget.phase_id in budgets:
            issue("DUPLICATE_REFERENCE", path + "/phase_id", "每阶段只能声明一份行动预算")
        budgets[budget.phase_id] = budget
    if set(budgets) != set(phases):
        issue("PHASE_BUDGET_COVERAGE", "/mechanics/phase_budgets", "必须为全部阶段逐一声明独立预算")
    for index, action in enumerate(package.mechanics.actions):
        path = f"/mechanics/actions/{index}"
        sourced(action, path)
        if action.id in actions:
            issue("DUPLICATE_ID", path + "/id", "调查动作 ID 必须唯一")
        actions[action.id] = action
        for field, collection, code in (("phase_ids", phases, "PHASE_NOT_FOUND"),
                                        ("allowed_character_ids", characters, "CHARACTER_NOT_FOUND")):
            values = getattr(action, field)
            if len(set(values)) != len(values):
                issue("DUPLICATE_REFERENCE", path + "/" + field, "规则引用不能重复")
            if any(value not in collection for value in values):
                issue(code, path + "/" + field, "调查动作引用了本包中不存在的实体")

    def requirements(rule, path):
        for field, targets, code in (("required_action_ids", actions, "ACTION_NOT_FOUND"),
                                     ("required_public_evidence_ids", evidence, "EVIDENCE_NOT_FOUND")):
            values = getattr(rule, field)
            if len(set(values)) != len(values):
                issue("DUPLICATE_REFERENCE", path + "/" + field, "规则前置引用不能重复")
            if any(value not in targets for value in values):
                issue(code, path + "/" + field, "规则前置引用不在本包中")
        if any(evidence[key].disclosure == "KEEP_PRIVATE"
               for key in rule.required_public_evidence_ids if key in evidence):
            issue("UNSATISFIABLE_RELEASE", path, "禁止公开的线索不能作为公开前置条件")

    for index, action in enumerate(package.mechanics.actions):
        requirements(action, f"/mechanics/actions/{index}")
    for name in ("knowledge", "evidence"):
        for index, material in enumerate(getattr(package, name)):
            requirements(material.release, f"/{name}/{index}/release")

    def dependencies(rule):
        return {("action", key) for key in rule.required_action_ids} | {
            ("evidence", key) for key in rule.required_public_evidence_ids}

    waiting = {("action", key): dependencies(action) for key, action in actions.items()}
    waiting.update({("evidence", key): dependencies(item.release) for key, item in evidence.items()})
    dependents = defaultdict(set)
    for node, required in waiting.items():
        for prerequisite in required:
            dependents[prerequisite].add(node)
    obtained, active_evidence = set(), set()
    evidence_by_phase = defaultdict(set)
    for key, item in evidence.items():
        evidence_by_phase[item.release.phase_id].add(("evidence", key))
    for phase_id in phase_order:
        active_evidence.update(evidence_by_phase[phase_id])
        budget = budgets.get(phase_id)
        eligible_actions = {("action", key) for key, action in actions.items()
                            if phase_id in action.phase_ids and budget is not None and action.cost <= budget.points}
        eligible = active_evidence | eligible_actions
        ready = deque(node for node in eligible if node not in obtained and not waiting[node])
        while ready:
            node = ready.popleft()
            if node in obtained:
                continue
            obtained.add(node)
            for dependent in dependents[node]:
                waiting[dependent].discard(node)
                if dependent in eligible and not waiting[dependent]:
                    ready.append(dependent)
    if any(("action", key) not in obtained for key in actions):
        issue("UNREACHABLE_ACTION", "/mechanics/actions", "调查动作存在循环、跨阶段阻断或单次成本超出可用预算")
    if any(("evidence", key) not in obtained for key in evidence):
        issue("UNREACHABLE_EVIDENCE", "/evidence", "线索和行动的前置条件无法按阶段解锁")
