# 简短审核与完整性契约

显式新请求 1.6，沿用 `/api/admin/fusion/authoring-jobs/with-rule-plan` 管理员入口、1 MiB 限制、完整冻结输入和幂等键。`authoring-request.v1.6.schema.json` 要求完整 rule_plan、`package_contract=script-package/1.2`、`compiler_mode=CONFIRM_FROZEN_TEXT`、`audit_mode=BOUNDED_TARGET_SOURCE_INDEXES`。缺失/未知标记不自动猜测升级。

模型/请求快照为 `authoring-model/1.6`、`bailian-authoring-json/1.6`；新提示 `audit_bounded_system_v1.txt`，旧提示不变。worker 需同时显式使用 `--package-contract script-package/1.2 --frozen-rule-plan --confirm-frozen-text --indexed-audit --bounded-audit`，以及原先必要的任务 ID 和付费参数。HTTP 排队本身不调用模型。

`bounded-audit-draft.v1.0.schema.json` 在编号审核上增加必填 `status=COMPLETE|INCOMPLETE`，摘要 ≤240 字符、发现说明 ≤160 字符、发现 ≤10 条。保留完整五维覆盖和唯一 ID、严格目标、目标自身的非空唯一整数引用编号。只接受 COMPLETE 经全部原领域校验后形成 `script-audit/1.1`；不持久化 INCOMPLETE 为成功报告，不截掉多余发现，不补引用或状态。

错误码：`AUDIT_INCOMPLETE` 表示模型自报未完成；`AUDIT_BOUNDED_OUTPUT_INVALID` 表示新传输格式不合格；实际目标引用错误继续使用编号或领域错误码；非正常结束仍先于解析，以 `AUTHORING_FINISH_INVALID` 拒绝。`AUDIT_BOUNDED_SCHEMA_INVALID` 是收据中的安全字段诊断，不是新的通过条件。

新收据可包含 `response_finish`，严格白名单值 stop、length、content_filter、tool_calls、function_call、OTHER、null。未识别供应商值统一 OTHER；不保存原始字符串。旧收据省略该字段时仍按原结构读取，不能补字段改变历史 hash；带字段的成功收据仅允许 stop。

COMPLETE 是待核查的模型完整性声明，不是语义质量证明或人审批准。超过容量须 INCOMPLETE；系统不自动分批、重试、切换供应商或发布。现有 4096 token 上限保持，字符限制不能保证每份完整 JSON 都能放入该预算。

零调用预览：

```sh
backend/.venv/bin/python backend/scripts/real_frozen_rule_plan_smoke.py --confirm-frozen-text --indexed-audit --bounded-audit
```

真实验收另需明确 `--execute --confirm-paid TWO_SYNTHETIC_FROZEN_RULE_PLAN_CALLS` 和既有付费授权/配置。只运行新私有任务，每步骤最多一次；不以新参数恢复旧失败任务。本轮结果见[4I 记录](../development/PHASE_4_BOUNDED_AUDIT.md)。
