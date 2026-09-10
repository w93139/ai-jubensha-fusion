# 四样本 Audit 语义验收工具

独立 CLI，不是玩家或管理员 HTTP API；无数据库迁移和新表。默认只预览，只生成内置虚构资料，不接受自定义正文、候选或提示文件。模型固定沿用现有百炼配置和 1.11 审核契约；旧流程与 4K 原件不修改。

```text
backend/.venv/bin/python backend/scripts/real_audit_semantic_smoke.py
```

预览返回 suite_hash 和预占总额，不读取付费授权、不开 SDK。显式执行还需：

```text
--execute --confirm-paid FOUR_SYNTHETIC_AUDITS --expected-suite-hash <预览摘要>
```

使用同一受保护配置和私有 output-root；不得通过更换 output-root 绕过已执行批次的 claim。程序没有 resume/retry 参数。全部输入重新生成且必须与预览 hash 相同。默认每次最多 ¥0.015，四次合计最多 ¥0.05；预占不等于实际账单，结果不明仍占用额度并停止后续请求。

suite.json 包含 model_snapshot、四份 packet、独立 criteria 和 oracles。只有 packet 内实际 messages 发送给模型，case 标识、评价标准和引擎预期不进入消息。每份 packet 包含原上下文、候选、消息、request_contract、contract_hash、input_tokens 和 reservation，输出仍按原 script-audit/1.1 装配。

输出目录之外的 executed-<suite_hash>.json 通过 O_EXCL 创建，作为整个 output-root 下不可重复请求的 claim。每次先保存 dispatch-N.json，再请求一次；result-N.json 保存成功报告或安全错误、用量收据及估算费用。receipt.json 汇总请求数、预占、已知收费/未知预占、各项结果与 UNREVIEWED 状态。未知异常不回显供应商或磁盘原文；进程崩溃留下的 IN_FLIGHT 不能被推断为未收费。

PREVIEW/COLLECTED 返回 0；拒绝执行或未知结果停止返回非零。COLLECTED 仅表示四个请求均返回可记录的已知用量结果，可能有格式失败、INCOMPLETE 或 BLOCKER，不能用于发布批准或宣布语义通过。逐项质量结论需要独立只读复核。

本工具不保存任何真实候选的人审处置，不修改报告内容，不删除 BLOCKER。结果范围和评价标准见[4L](../development/PHASE_4_SEMANTIC_ACCEPTANCE.md)。

4L 真实运行后补充了来源编号错误位置：AUDIT_SOURCE_INDEX_UNAVAILABLE 只允许 `/findings/N/source_indexes/M`，N 为 0–199、M 为 0–99，拒绝负数、前导零、额外字段或任意文本；目标不存在继续使用 AUDIT_TARGET_NOT_FOUND。诊断最多十项，不携带模型提供的目标 ID 或索引数值，不改变输出接收条件。旧收据不回填，新错误路径本轮仅离线/模拟验证。
