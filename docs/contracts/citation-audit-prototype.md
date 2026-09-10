# 引用目录审核原型（仅离线）

这是未接入调度的候选协议，不是现有 1.11 的扩展字段。当前没有 HTTP/worker/真实 SDK 入口。

`prepare_citation_contract(package)` 只接受结构有效的 script-package/1.2，返回 citation-audit-contract/1.0、package_hash、catalog_hash、catalog、local_schema 和 provider_schema_candidate。状态固定 UNREVIEWED、UNVERIFIED、dispatch_enabled=false、publication_ready=false。

目录由旧 audit_source_catalog 的目标和其全部 sources 按原顺序展平，每项包含 index、target 和 reference。最多 2048 项，超限报 CITATION_CATALOG_TOO_LARGE；不把“未进入目录”隐藏为审核完成。索引最大值在生成 schema 中固定为实际目录长度减一。

输出 citation-audit-draft/1.0：顶层 schema_version、status、summary、coverage、findings；发现只含 category、severity、message、citation_indexes。不接收 target、sources、source_indexes、id 或批准字段。摘要≤240 字符、说明≤160、发现≤10、引用数 1–100；索引必须是唯一整数，静态范围 0–2047，再按本目录收窄。

`assemble_citation_audit` 要求提供准备时的 expected_package_hash 和 expected_catalog_hash。任一依据变化拒绝，错误索引和一条发现内跨目标引用拒绝。只复制模型实际选择的目录引用，不补来源；同目标多来源顺序保留。发现 ID 按序生成 finding-1 等记录标签；仍装配原 script-audit/1.1，再执行完整 validate_model_audit。

错误只返回固定 code 与有限位置，不回显输入值或任意字段名。原型的引用来源尚未由这个纯函数访问文件核验，不能单独用它作为调用授权、文件可信或发布门禁。未来接线必须在外层重核源文件、预算和冻结任务依据；本轮离线样本复核已单独核验源文件。

provider_schema_candidate 去掉 pattern 以延续既有兼容探索，但本地仍执行完整 pattern。这只是标准 JSON Schema 候选，没有供应商兼容性或模型质量通过结论。实现及验证见[4M](../development/PHASE_4_CITATION_PROTOTYPE.md)。
