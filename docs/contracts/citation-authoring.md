# Citation Authoring 1.12

这是 4M `citation-audit-contract/1.0` 的显式集成，纯目录 helper 本身仍不调用 SDK；其 `dispatch_enabled=false` 不是外层任务的授权开关。供应商兼容性保持 UNVERIFIED，模型意见不授予发布权限。

## 请求与版本

`POST /api/admin/fusion/authoring-jobs/with-rule-plan` 接受 [authoring-request.v1.12.schema.json](authoring-request.v1.12.schema.json)。必须包含完整 rule_plan、`package_contract=script-package/1.2`、`compiler_mode=CONFIRM_FROZEN_TEXT`、`audit_mode=CITATION_CATALOG`。客户端不能提供 citation_catalog、citation_binding 或 response_format。鉴权、幂等及 HTTP 仅排队规则沿用旧流程。

模型版本固定 `authoring-model/1.12`，请求契约 `bailian-authoring-json/1.12`。适配器 `citation_audit=True` 依赖 `runtime_audit_context=True` 及其原有全部依赖；worker 新参数 `--citation-audit` 必须与 `--runtime-audit-context` 及原有配套参数一起显式提供。模式与任务快照不匹配即拒绝，不能升级旧任务或重发未知调用。

`real_frozen_rule_plan_smoke.py` 同样新增 `--citation-audit`，新烟测收据版本为 `frozen-rule-plan-smoke/1.9`；默认仍 preview，执行仍需原付费门禁。本轮只使用离线或模拟 SDK。原 `real_audit_semantic_smoke.py` 四样本工具仍为 1.11，没有修改其冻结输入或执行凭据。

## 动态请求绑定

静态模型快照固定基础 `CitationAuditDraft` schema 哈希，动态 Audit 请求的 schema_hash 则绑定当前目录的本地 schema；两者有意不同，不能直接拿静态哈希替代动态哈希。

Audit 系统消息含新独立提示、原 runtime_contract 和动态本地 schema；用户消息包含当前候选、candidate_observations、完整 citation_catalog 以及来源输入，不发送旧 audit_source_catalog。每个目录项为 `{index,target,reference}`，保持旧目标及其来源的顺序。目录总量超过 2048 拒绝，不截断。

request_contract 新增 citation_binding：`schema_version=citation-audit-contract/1.0`、package_hash、catalog_hash。response_format 为 strict json_schema，名称 citation_audit；索引最大值为目录长度减一，仅供应商 schema 去除 pattern，本地全部约束保留。契约哈希仍覆盖整个 request_contract 和配置快照。

预占输入上界为全部消息 UTF-8 字节数加 512，再加完整 response_format 的 UTF-8 字节数；保持原 32768 输入上限、4096 输出上限及单次 ¥0.05 上限。账本依据原任务 context.rule_plan 重建动态绑定和 response_format，不能只验证外层哈希；模型调用前还会重新 prepare 并比较完整对象。

## 输出与失败

输出详见 [引用原型契约](citation-audit-prototype.md)，wire 为 citation-audit-draft/1.0，服务器装配后仍经过原完整领域校验，保存 script-audit/1.1。BLOCKER 保留，COMPLETE 与任务 COMPLETED 都不代表人审批准。

新增输出错误 AUDIT_CITATION_OUTPUT_INVALID，安全诊断 AUDIT_CITATION_SCHEMA_INVALID 仅允许 `/`；AUDIT_CITATION_REFERENCE_INVALID 仅允许 `/findings/N/citation_indexes` 或 `/findings/N/citation_indexes/M`，N=0..9、M=0..99。不完整仍使用 AUDIT_INCOMPLETE；非正常结束、未知用量及单次调用规则不变。失败不保存伪造领域报告，也不修复或重试。
