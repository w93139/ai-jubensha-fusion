# 运行规则审核契约（1.11）

继承[1.10 严格审核](strict-audit.md)全部本地校验。显式请求 schema 为 `authoring-request.v1.11.schema.json`，`audit_mode=RUNTIME_CONTEXT_SOURCE_INDEXES`；仍需包 1.2、完整 rule_plan 和 `compiler_mode=CONFIRM_FROZEN_TEXT`，沿用管理员专用 `/authoring-jobs/with-rule-plan`、1 MiB 限制及幂等键。接口只排队，不请求模型。

模型/请求快照为 `authoring-model/1.11`、`bailian-authoring-json/1.11`。新审核提示 `audit_runtime_system_v1.txt` 连同 `audit-runtime/1.0` JSON 形成完整系统提示，整体计算 prompt hash。规则事实来自服务器版本化模块，不能由请求、来源备注或候选字段覆盖；模块后续变动必须另开版本，不静默改变历史快照。

Audit 用户消息新增 `candidate_observations`：schema_version=audit-candidate-observations/1.0、package_hash、settlement.phase_id、settlement.truth_ids、semantic_status=UNREVIEWED。数据仅从已通过原候选校验的当前包复制，不参考测试答案或推断正文。原 candidate 与目标来源目录仍完整提供。客户端额外传入 runtime_contract/candidate_observations 被拒绝。

`audit-runtime/1.0` 包含与管理员规则页一致的六个 facts 字段及明确的动作前置、免费动作、用尽点数、AI 权限、材料回复、结尾行为和能力局限。facts 并不是语义审核结果；`publication_ready=false`。响应格式仍为 1.10 的严格 typed schema，本地仍执行完整正则、长度、完整性、引用和领域校验，装配产物仍为 `script-audit/1.1`。

所有新消息字节计入输入预算；prompt hash、模型快照和完整请求契约继续绑定步骤账本。旧模式与 1.11 模型不能混用，准备内容变化拒绝调用；Compiler 不添加运行规则或观察值，沿用原固定正文确认。新上下文不改候选、不填审核字段、不删除 BLOCKER、不生成批准记录。

worker 在完整 1.10 参数后新增 `--runtime-audit-context`：

```text
--package-contract script-package/1.2 --frozen-rule-plan --confirm-frozen-text
--indexed-audit --bounded-audit --direct-audit-schema --strict-audit-schema
--portable-audit-patterns --typed-audit-schema --runtime-audit-context
```

仍须提供原有任务 ID、预算及显式付费参数。单独 runtime 标记不足以执行；旧标记仍保留。烟测 CLI 同样提供新标记，默认零调用预览，1.3/1.10/1.11 预览均有不读付费授权、不创建 SDK 的测试。1.11 烟测收据版本为 frozen-rule-plan-smoke/1.8，原固定 fixture 和精确质量标准不变。

本轮只进行了离线及模拟 SDK 验证，真实语义效果待验，见[4K](../development/PHASE_4_RUNTIME_AUDIT.md)。
