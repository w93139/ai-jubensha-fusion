# 按审核目标选择来源编号

仅用于显式新 authoring 1.5 任务。持久化领域报告仍为 `script-audit/1.1`，候选仍为 `script-package/1.2`。

## 创建与版本绑定

`POST /api/fusion/authoring-jobs/with-rule-plan` 接收已有 1.4 全部字段，并显式增加 `audit_mode: "TARGET_SOURCE_INDEXES"`。必须同时包含完整 `rule_plan`、`package_contract: "script-package/1.2"` 和 `compiler_mode: "CONFIRM_FROZEN_TEXT"`。请求 schema 见 `authoring-request.v1.5.schema.json`。管理员权限、1 MiB 专用限制、幂等键及完整输入 hash 绑定沿用原行为，普通旧接口仍为 16 KiB。

显式 worker 参数为：

```sh
backend/.venv/bin/python backend/scripts/authoring_worker.py --package-contract script-package/1.2 --frozen-rule-plan --confirm-frozen-text --indexed-audit --help
```

上例仅展示参数并输出帮助；实际运行还需原 worker 要求的任务和付费参数。`--indexed-audit` 必须伴随前三项，不通过现有任务自动推断升级。模型/请求快照为 `authoring-model/1.5`、`bailian-authoring-json/1.5`。旧 1.4 及更早请求和提示不变。

## 模型输出与解析

`indexed-audit-draft.v1.0.schema.json` 只用于模型传输。每条发现仍包含 id、category、severity、target、message，但使用 `source_indexes` 替代完整 sources。编号零基、严格整数、唯一、非空且最多 100 项，每个编号必须实际存在于该 target 自身的 sources 中。编号不引用全局来源目录，不代表其他对象的同位置来源。

模型输入新增 `audit_source_catalog`，按 target 列出 `{index, reference}`。支持 introduction、settlement、characters、phases、knowledge、evidence、truth、mechanics.actions、mechanics.phase_budgets；开场/结算 id 为 null，阶段预算以 phase_id 标识。

服务器用选中的编号拷贝冻结引用，不猜测、不补全或自动选择。之后继续验证领域报告全部约束与候选绑定，包括完整五维覆盖。非法传输或编号报 `AUDIT_INDEXED_OUTPUT_INVALID`；最终领域校验沿用原错误码。非正常完成在解析前拒绝，保持原 `AUTHORING_FINISH_INVALID`，不会拿半份报告保存为成功。

成功装配不等于语义正确或人审批准。BLOCKER、未知用量、模型失败和手动批准仍遵守原持久任务/预算/人审发布门禁；不新增自动重试、自动回退或自动发布。

## 验收工具

零调用预览：

```sh
backend/.venv/bin/python backend/scripts/real_frozen_rule_plan_smoke.py --confirm-frozen-text --indexed-audit
```

真实调用需要显式 `--execute --confirm-paid TWO_SYNTHETIC_FROZEN_RULE_PLAN_CALLS`、已授权付费开关及有效配置；每个新任务最多一次 Compiler 和一次 Audit，使用新私有目录。不得通过重新运行旧失败任务消除失败记录。当前真实结果及限制见[4H 验收](../development/PHASE_4_INDEXED_AUDIT.md)。
