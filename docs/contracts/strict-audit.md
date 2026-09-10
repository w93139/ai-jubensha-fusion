# 显式严格审核契约（1.7–1.10）

2026-09-06。继承[1.6 简短审核](bounded-audit.md)的全部本地输出约束；同一管理员 `/authoring-jobs/with-rule-plan` 入口、1 MiB 限额、完整待审 rule_plan、`package_contract=script-package/1.2`、`compiler_mode=CONFIRM_FROZEN_TEXT`，幂等和人审边界不变。

| 请求 schema | audit_mode | 模型/请求快照后缀 |
| --- | --- | --- |
| authoring-request.v1.7.schema.json | DIRECT_BOUNDED_SOURCE_INDEXES | 1.7 |
| authoring-request.v1.8.schema.json | STRICT_BOUNDED_SOURCE_INDEXES | 1.8 |
| authoring-request.v1.9.schema.json | PORTABLE_STRICT_SOURCE_INDEXES | 1.9 |
| authoring-request.v1.10.schema.json | TYPED_STRICT_SOURCE_INDEXES | 1.10 |

快照分别为 `authoring-model/<版本>` 与 `bailian-authoring-json/<版本>`。未知模式、缺失依赖或与任务快照不同的 worker 拒绝执行；版本不能恢复重发旧失败任务。

1.7 的 Audit schema 为 BoundedAuditDraft 本身，去掉无效的 output 包装，response_format 仍为 json_object。1.8 起只有 Audit 使用 `json_schema`，`json_schema.strict=true`。1.9 的 prompt/供应商 schema 将说明和摘要的非空白正则改为等价的整串表达。1.10 的 prompt schema 继承 1.9，但发送给供应商的 schema 递归移除 pattern 字段；保留其余类型、枚举、必填、禁止额外字段及上下界。

**本地 parse_bounded_audit 没有移除正则或放宽规则。** 非空白文本、合法 ID、长度、完整性、五维覆盖及目标自身非空有效来源编号继续严格核验，最终装配为原 `script-audit/1.1`。供应商接受格式不能直接成为合法报告，更不能成为人工批准。

完整 response_format 加入准备请求契约，按版本重算并核对；其 JSON UTF-8 字节与消息一起纳入保守 token 预占。调用只使用已冻结契约的副本，不另行猜测生成格式。Compiler 保持 json_object 和原固定正文确认逻辑。

worker 的完整 1.10 模式参数：

```text
--package-contract script-package/1.2 --frozen-rule-plan --confirm-frozen-text
--indexed-audit --bounded-audit --direct-audit-schema --strict-audit-schema
--portable-audit-patterns --typed-audit-schema
```

仍须提供原有任务 ID 和显式付费参数，排队或读取不会调用模型。1.7 终止于 direct 标记，1.8 终止于 strict，1.9 终止于 portable；所有依赖标记必须同时存在。1.10 不兼容单独 typed 标记。

`real_frozen_rule_plan_smoke.py` 同样提供这些模式标记；默认是零调用预览，真实运行仍要求 `--execute --confirm-paid TWO_SYNTHETIC_FROZEN_RULE_PLAN_CALLS` 及既有授权/配置。四版本真实实验已经完成，本轮不得追加付费请求；详见[4J 结果及限制](../development/PHASE_4_STRICT_AUDIT.md)。
