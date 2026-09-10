# 固定角色阶段演练协议 1.0

实现入口：`PackageFlowService`、`RulesEngine`、`/play/package-flow`。本协议是[阶段 4A](../development/PHASE_4_RULES_PREVIEW.md)的确定性演练，不提供 AI、主持、投票或结算。旧开场接口保持 `READING_PREVIEW` 和无动作，演练需另行显式创建。

## HTTP

所有路径以下表为准，统一以 `success/data` 返回成功结果。请求须为已登录且启用的用户，owner 从认证依赖取得；请求中不允许 actor、角色切换、目标阶段、自由文本或私本正文。玩家读取仅包含本次固定角色的授权投影，成功和服务端业务错误响应标记 `Cache-Control: no-store`。

| 方法与路径 | 输入 | 结果 |
| --- | --- | --- |
| GET `/api/fusion/package-flows` | 查询 `opening_session_id` | 本人的已有演练；没有则为 null；他人的开场 404 |
| POST `/api/fusion/package-flows` | [创建请求](package-flow-create.v1.schema.json) | 201，新演练初始 revision 0；显式重核当前发布 |
| GET `/api/fusion/package-flows/{flow_id}` | 本人的 flow ID | 最新已保存投影，完整历史校验失败则拒绝 |
| POST `/api/fusion/package-flows/{flow_id}/actions` | [动作请求](package-flow-action.v1.schema.json) | 200，本次动作后的投影；同键原请求返回原动作版本 |

创建请求只有 `opening_session_id`（`package-` 后 32 位十六进制）和 `idempotency_key`。每份开场只创建一份演练；同一用户同键不同输入、同一开场不同创建请求返回 409，前端可主动重新查询已有演练。

推进请求示例：

```json
{"idempotency_key":"advance-1","expected_revision":0,"action":"ADVANCE_PHASE"}
```

公开请求示例（仅为格式，无实际素材）：

```json
{"idempotency_key":"share-1","expected_revision":1,"action":"SHARE_MATERIAL","target":{"collection":"evidence","id":"example-card"}}
```

`ADVANCE_PHASE` 不允许 target，显式 null 也拒绝；`SHARE_MATERIAL` 必须有 target，只允许 knowledge/evidence。revision 为非负整数，布尔或字符串不做隐式转换。请求最多 4096 字节，重复 JSON 键、额外字段和未知动作均拒绝；错误不回显正文或输入。

401 表示未登录，403 表示账号不可用，404 表示无权访问或对象不存在，409 表示发布、阶段、材料、版本或幂等冲突，422 表示输入非法，413 表示请求过大。冲突不能自动换键重发，前端需要明确刷新后再操作。

## 投影与权限

返回 `flow_id`、`opening_session_id`、`release_id`、`version_id`、`package_hash`、`selected_character_id`、`revision`，以及固定的 `status=RULES_PREVIEW`、`runtime_ready=false`。故事元数据为 `script`、`characters` 和 `introduction.text`；当前阶段为 `current_phase{id,title}`，`can_advance` 表示还有下一阶段，`phase_complete` 仅表示阶段链到末端，不表示故事结算或整局完成。

四组材料为 `public_knowledge`、`private_knowledge`、`public_evidence`、`private_evidence`；条目只含 `id/text/disclosure/can_share`，知识另含 `kind`，本人已公开材料另含 `shared_by_character_id`。内部状态、动作历史、来源路径、未解锁 ID、其他角色私密、真相与结算不进入玩家响应。私密材料公开后在公开集合展示，不能再次公开。

阶段顺序沿包的显式连接取得；解锁用阶段下限与全部已公开证据条件。原生公开证据计算连续解锁；知识公开不能冒充证据公开。本人已知的私密证据须显式分享，其他角色私密永远不代为分享。`MUST_SHARE` 保留提示但没有隐含截止时点或自动动作。

## 持久化与失效

演练绑定 `package-flow-binding/1.0` 和 `package-flow-rules/1.0`，事件使用 `package-flow-event/1.0`，机器状态哈希使用 `package-flow-state/1.0`。独立新表只追加，不修改旧开场或旧游戏；顺序及幂等唯一约束与候选/演练锁保护并发写入，调用方统一提交或回滚。

新建和新动作均要求当前发布仍有效，历史读取和原动作幂等重放仅读取已获授权，不扩大权限。发布失效后保留现有演练材料，拒绝新增解锁。前端写成功后可再 GET 最新投影，但不能把重试当作下一次操作。

读取重新验证绑定、原请求、连续序号、事件链和每步结果状态；ORM 禁止修改或删除记录。这不是外部签名或数据库备份：具备原始 SQL 写权限者删除完整末尾历史、连同可信数据整体回滚，不能仅靠同库哈希检测，仍依赖数据库权限及备份恢复机制。
