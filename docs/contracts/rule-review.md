# 规则与来源对照（管理员只读）

2026-09-06。实现入口为 `GET /api/admin/fusion/script-packages/{version_id}/rule-review`，仅支持 `script-package/1.2`。沿用管理员鉴权，成功响应 `Cache-Control: no-store`；玩家 403、匿名 401。结果不是人工审核记录或模型 Audit，不授予发布资格。

查询参数：

| 字段 | 约束 |
|---|---|
| `expected_package_hash` | 必填，当前候选的 64 位小写 SHA-256；错配 409 |
| `bundle_hash` | 必填，选中的冻结来源快照，64 位小写 SHA-256 |
| `offset` | 默认 0，范围 0–15100 |
| `limit` | 默认 20，范围 1–50 |

服务端重新读取候选，验证其内容哈希，再核验来源快照全部文件和候选声明；核验不保存报告。错配、损坏或来源丢失拒绝返回；不存在候选 404，参数无效 422。文件核验在线程中执行，不把数据库 Session 交给文件线程。

`data` 当前为 `rule-review/1.1`，带 `package_hash`、`bundle_hash`、`row_count`、`offset`、`limit`、`next_offset`、`runtime_facts` 及 `rows`。语义状态固定 `UNREVIEWED`，`publication_ready=false`。前端仍接受不带 runtime_facts 的旧 1.0。

每行包含 `target`、`label`、`rules`、来源段落和结构提示。顺序为阶段预算、动作、知识、证据，保留包中各集合顺序；1.1 最后追加结尾一行，target 为 `{collection: "settlement", id: null}`，rules 显示实际 `phase_id` 和 `truth_ids`，来源取结尾说明自身的引用。总行数最多 15101，分页范围不变。缺省空列表按既有契约显示；绑定仍取未修改的原始候选哈希。对照内容只返回给管理员，不进入玩家视图或角色模型输入。

`runtime_facts` 只包含六个固定字段：package_contract=script-package/1.2、human_players=1、investigation_actor=SELECTED_HUMAN_ONLY、action_success_limit=ONCE_PER_SESSION、phase_budget=SHARED_NO_CARRY、material_recipient=DECLARED_OWNER。前端逐个验证固定值，错误或未知事实不显示为可信结果。这些来自当前程序，不是模型输出；同一动作最多成功一次包括免费动作。它们不能证明候选符合原材料、每种选角和任意行动路径都可完成。

每行最多展示三处来源，每段最多 2000 字符，完整数量与截断标记明确返回。单份来源文字上限 512 KiB，本页解码文字合计 2 MiB；图片/非文本 `NON_TEXT`、超限 `TEXT_LIMIT`、无法解码 `TEXT_UNAVAILABLE`、不能唯一定位 `LOCATION_UNAVAILABLE`。实际可读段落为 `AVAILABLE`；超限或不可定位时正文为 null，不捏造摘要。来源按标题或行号精确截取，重复标题不任选一处。首次读取正文再核验文件摘要，防止核验后文件被换掉。

唯一结构提示 `PUBLIC_PREREQUISITE_ALREADY_REQUIRED_BY_ACTION`：某材料的公开证据前置，同时已列在该材料某个直接前置动作的公开证据前置中。每行最多 100 条，带完整数量及截断标记。本版不递归推断祖先条件。它表示需要查原文，**不证明条件错误，也不授权删除条件**。无提示同样不证明规则语义正确。

页面绑定候选和快照；切换时重建视图并取消旧请求，晚返回的旧结果不能显示到新版本。文本仅按普通文字渲染，来源中的 HTML、Markdown 链接或指令不会被执行。该接口不创建任务、调用模型、修补候选、保存人审报告、批准或发布。

原实现和验收见[阶段 4E](../development/PHASE_4_RULE_REVIEW.md)，1.1 及真实候选只读核对见[阶段 4J](../development/PHASE_4_STRICT_AUDIT.md)。
