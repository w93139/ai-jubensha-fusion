# 固定版本文字试玩契约

本接口起于阶段 4B，4D 增加显式版本的共享行动点与条件调查，独立于开场 `package-runtime`、4A `package-flow` 和旧 Fusion 游戏。`runtime_ready=false` 仍表示商业本完整 MVP 未验收。M2增加实际公共讨论、1.3私密回忆和自然角色回应；整局投票评分仍待完成。

## HTTP

所有接口位于 `/api/fusion/package-plays`，使用现有有效用户认证与所有者检查。管理员不自动拥有他人的局。不存在、格式错误或别人的对象均为 404。业务响应使用 `Cache-Control: no-store`；统一认证中间件先拦截的响应沿用其认证行为。

| 请求 | 行为 |
|---|---|
| GET `?opening_session_id=package-…` | 查已有试玩，未创建为 `data:null`，不写入 |
| POST 根路径 | 严格 `opening_session_id`、`idempotency_key`；重新核验当前发布后创建独立 `play-…`，201 |
| GET `/{play_id}` | 校验固定绑定和完整历史后返回已授权视图 |
| POST `/{play_id}/actions` | `expected_revision`、`idempotency_key`、动作；手动推进、分享、结尾 |
| POST `/{play_id}/ask` | `expected_revision`、`idempotency_key`、`character_id`、`question`；预占后至多调用一次模型 |

请求原始 JSON 最多 8192 字节，拒绝重复字段、未声明字段、非法 Unicode 和隐式类型转换；问题输入 1–1000 字符且非空白，去除首尾空白后做幂等哈希。身份、材料正文、模型配置与私密知识不能由请求注入。严格 DTO 与静态 Schema 对应：

- [创建](package-play-create.v1.schema.json)
- [动作](package-play-action.v1.schema.json)
- [提问](package-play-ask.v1.schema.json)

`SHARE_MATERIAL` 必须有 `{collection:knowledge|evidence,id}` target；`PERFORM_ACTION` 必须有且只有 `{action_id}` target，并仅支持 `script-package/1.2`；`ADVANCE_PHASE` 与 `SETTLE` 必须完全省略 target，`null` 也拒绝。只有其他角色可作为 AI 提问目标。

## 1.2 共享行动点与调查

包 1.0/1.1 固定使用 `package-play-rules/1.0`，状态仍为 1.0；包 1.2 固定使用规则 1.1 与状态 1.1。读取时检查包/规则配对，历史会话不会自动升级。1.2 开场返回 `supports_rules_preview:false`，旧 4A 演练接口拒绝新包，避免跳过调查条件。

1.2 的 `mechanics.phase_budgets` 必须逐阶段声明 `points` 和 `advance_policy`（`ALLOW_REMAINING` 或 `REQUIRE_EXHAUSTED`）；`mechanics.actions` 声明动作 ID、显示名称、固定成本、阶段、允许角色、全部动作及公开证据前置。动作和预算各有来源引用、`SOURCE_EXPLICIT` 或 `EDITORIAL` 标记，编辑规则至少引用一份补充材料；来源种类验证不能代替原文语义审核。

服务端仅接受所选真人本人可执行的调查，每个动作全局最多执行一次，扣除当阶段点数并解锁满足全部 `release.required_action_ids` 的材料。原有阶段下限和公开证据前置继续共同生效；私人奖励仍按角色隔离，获得不等于公开。动作成本允许为零，零成本动作也只能一次。点数不跨阶段结转，完成记录和已得材料保留。

玩家视图只对 1.2 增加 `mechanics:{initial_points,remaining_points,spent_points,can_finish_phase,available_actions:[{id,label,cost}]}`。可用列表只含已满足权限、阶段、前置和当前点数的动作；不返回锁定动作标题、奖励或原始规则。`can_advance` 同时受阶段点数条件限制，末阶段 `SETTLE` 也需 `can_finish_phase`。没有自动花点、AI 代调查或自动推进。

未知、越权、重复、未满足前置或点数不足的调查统一返回 `PACKAGE_PLAY_ACTION_NOT_AVAILABLE`；预算未用尽时推进或结尾返回 `PACKAGE_PLAY_PHASE_BUDGET_REMAINS`。动作沿既有事务、revision、幂等键和重放机制提交。同局 AI 等待期间可以合法调查；因此过时 AI 结果只入费用账，不增授权限。

结构校验能检查引用、单项可负担和跨角色乐观可达，不证明每个可选真人角色都能用尽预算、避免所有行动顺序死路或完成整局。新模型审核与人工审核必须检查这一可玩性；不能把尚未实现的 AI 调查算作可用动作。

## 规则、输入与输出

`PackagePlayRules` 维护同一个公开账本。阶段使用包的显式线性连接，release 是阶段下限加全部公开证据前置条件；原生公开证据可连续解锁。只解锁自己的私有材料不等于已公开。MUST_SHARE 仍需要显式分享，不推断截止时限或阶段退出门槛。

AI 输入只有目标角色 id/name、当前阶段 id/title、当前公共材料与该角色已解锁的 MAY_SHARE/MUST_SHARE 候选。KEEP_PRIVATE、其他角色未公开材料、未来材料、未达成条件材料、truth、settlement、来源与旧全本记忆均不进入输入；真人问题也是非可信数据。没有自由文字输出、工具执行或自动主持。

模型 wire 是严格 `{refs:[{collection,id}]}`，最多三项不重复引用，或空数组。服务端再次按请求时的候选范围校验整批，不能用同批第一项分享去授权第二项原本锁定材料。接受的私有引用正式公开并触发规则闭包，回答原样组装所选文本、保留 FACT/CLAIM/INFERENCE 标记；未选择材料不返回给真人。空引用只显示固定无信息句。格式正确不能证明模型选择的语义相关性，相关性仍待真实模型验收。

终末阶段不会自动返回真相。只有 `SETTLE` 且该阶段等于 `settlement.phase_id`，才显示结尾说明及 `truth_ids` 指定的真相，保持引用顺序，随后拒绝所有新规则动作及 AI 回答。没有自动凶手判定、计分、胜负或个人目标裁决。

## 固定绑定、幂等与费用

绑定冻结 opening/release/version/package/selected_character 及其哈希、规则版本、模型元数据和预算费率。每个 opening 最多一份文字试玩；不复制旧 4A 进度，也不自动升级旧页面。配置改变后旧局 AI 不继续调用，但历史和非模型规则仍可读写。发布失效时所有新知识授予冻结，旧 GET 与原请求的幂等读取保留已获内容。

追加事件类型为 ACTION、AI_REQUEST、AI_RESULT，revision 按每条事件增加。动作、问题、结果分别使用 kind 与外部 key 的组合哈希作为内部幂等键；同 kind 同 key 不接受不同正文。原请求重试返回当前完整视图并校验全历史，不回退 UI revision。AI_REQUEST 已存在时不会再次发送模型请求。

AI_REQUEST 在局锁内校验当前 revision、模型/价格冻结配置和单局剩余额度，预占完整输入与输出上界，提交事务后调用一次。SDK 与应用均不自动重试；等待模型时没有数据库事务或锁。同局最多一个未结请求，最多 30 次请求（包括失败与未知）；各角色共享同一预算。

AI_RESULT 再次核验当前发布并取局锁。仅原 revision 未变化、租约未过期、未结尾且配置仍匹配的合法结果能分享/发言；否则为 STALE，只入费用账。非法输出但已知用量照实入账；未知用量保留完整预占估算，不当作零；确认未尝试传输的失败按零用量入账。UNKNOWN/EXPIRED 不自动重发。已知费用是按冻结单价计算的估算，不替代供应商账单。

若其他请求已把预占结为 EXPIRED，之后到达的原调用结果不会再覆盖记录或分享资料；本版继续保留全额预占估算，没有独立的迟到实际用量追记/供应商账单对账接口。

GET 不写过期记录；过期请求的原 key 重试会记 EXPIRED 并返回结果。新 key 遇过期预占时先保存 EXPIRED，再返回 REVISION_CONFLICT，用户刷新后以同新 key、同问题和新 revision 显式重试。网络结果未知时必须保留原全部请求；不能把任意 409 都改写 revision。前端只在明确 REVISION_CONFLICT 且成功刷新后重新取得 revision。

事件哈希链与确定性重放能检测记录损坏及非法状态迁移；没有独立外部锚，不能证明原始 SQL 删除完整尾部或整库回滚未发生。日志不输出模型完整上下文、私本或供应商错误正文；事件保存真人提问和引用/用量元数据，不保存完整候选提示词。

## 玩家视图

基本身份/版本、当前阶段、公共与本人私有材料沿用 4A 的授权形状。增加 `play_id`、`status:TEXT_PLAY|SETTLED`、`settled`、`settlement:null|{text,truths:[{id,text}]}`、`dialogue`（仅已接受的原文回答）、`model:{available,reason}`、单局 `budget` 汇总、`pending_ai` 和 `last_ai_status`。后者为 `null|OK|INVALID|UNKNOWN|STALE|EXPIRED`。不返回内部事件、用户问题收据、模型完整输入或未选角色材料。

2026-09-08 HTTP 入口启用可选 `ai_interactions:{schema_version:"package-ai-interactions/1.0",initiated,limit}` 只读投影。`initiated` 取真实 AI_REQUEST 数（原文问答、建议、公开/私人回应、电话、正式 AI 行动共用；失败也计数；同一请求重试不增加），完整引擎上限400、旧文字引擎30。不是剩余可用次数保证，内部预算与调度限制仍生效；玩家页面只显示轮数和输入字符数，不渲染 Token/费用。服务默认关闭扩展以保持旧冻结视图，HTTP及当前验收服务显式开启；不改 binding、模型元数据或回放摘要。

同轮 HTTP 启用 `package-request-scope/1.0` 准入：新问题及当前获准回应目标的明确外部任务/元信息索取返回422 `PACKAGE_PLAY_OUT_OF_SCOPE`，在模型准备、预算预占和AI_REQUEST之前拒绝。已存请求先按原幂等规则恢复；该拒绝才可告知不消耗轮次，未知网络错误不能推断未请求。此策略是有限确定性匹配，不代表任意跑题语义识别；模型仍受已有获准材料和无依据拒答约束。

UI 只在用户点击时创建/提问/分享/推进/结尾。刷新恢复固定局，旧异步响应不能覆盖新局或更新进度；401/403/404 清除材料与草稿。当前模型不可用不阻止手动阶段和结尾揭晓。

## 显式公共讨论扩展（2026-09-07）

`POST /api/fusion/package-plays/{play_id}/discussion` 使用同一登录/对象权限、8 KiB 请求边界、no-store 和会话事务：

```json
{
  "schema_version": "package-discussion-command/1.0",
  "action": "SPEAK",
  "expected_revision": 0,
  "idempotency_key": "fictional-statement-1",
  "text": "我认为应先核对这个说法。"
}
```

正文为 1–1000 字符的非空字符串，先校验原始长度，再去首尾空白保存/hash；拒绝额外字段。一局最多 100 条。使用既有 ACTION 事件类型，但由显式 schema_version 分发新的严格命令；旧 `/actions` 不接受 SPEAK。说话者固定为本局真人角色，phase_id 和 sequence 由服务端生成，kind 固定 CLAIM；不接受客户端事实认定，不公开材料，不改变点数或模型费率。

响应仍为完整试玩视图，新增 `discussion:{schema_version:"package-discussion-view/1.0",limit:100,entries:[{id,sequence,phase_id,kind:"CLAIM",speaker,text}]}`。未使用讨论的历史视图 entries 为空。前端兼容旧后端缺少 discussion 的响应，但不显示发言入口。

首次显式 SPEAK 后，新的事件包使用 `package-text-play-event/1.1`，后续事件状态包含完整讨论；此前 binding/事件/摘要不改写，无新表或迁移。没有 SPEAK 的会话继续使用原 1.0 事件格式。旧代码无法读取含新协议的历史，回滚不得通过删除事件解决。

幂等沿用 ACTION＋key：相同原请求返回当前存档，不同正文/版本前提复用 key 返回冲突。新发言须当前 release 有效、未结尾和 revision 一致；失败不产生事件。发言占用统一 revision，因此旧在途 AI 结果只能记为 STALE 并记账，不再分享材料。既有 AI 材料模型提示与绑定不变，尚不读取 discussion。

内部只读 `discussion_context()` 将鉴权后的公共记录投影为 RoleEvent（所有本局角色可听的 CLAIM），返回绑定摘要、当前角色/阶段/revision；不是 HTTP 公共管理入口，也不是完整私密知识或角色目标视图。完整 AI 讨论和调查提议仍待显式新协议接入。

## 显式调查建议扩展（2026-09-07）

上述公共讨论之后已新增 `POST /api/fusion/package-plays/{play_id}/proposals`：

```json
{"schema_version":"package-investigation-command/1.0","action":"PROPOSE","expected_revision":0,"idempotency_key":"fictional-proposal-1","character_id":"b"}
```

仅接受其他本局角色；必须是 1.2 调查包，存在双方当前均可执行的选项。客户端不能传目标、材料、费用、调查动作选择或模型配置。使用同一鉴权、8 KiB、no-store 与错误封装。旧 `/ask` 不升级。

`PackageInvestigationRules.proposal_context()` 先筛选公开及角色本人已解锁材料，包括私密目标原文；仅作为内部模型输入，不直接输出 HTTP。`package-proposal-model/1.0` 输出 `{action_id,public_basis:[{collection,id}]}`；只允许共同可见且合法动作和公开引用，不接受自由文本。服务器生成公开建议短句，不执行动作。角色目标尚无独立结构化/评分契约。

首次 PROPOSE 冻结新模型元数据到事件状态；公共与私人输入只保存可重建的哈希，公开结果和请求回执进入原事件链；后续追加事件版本为 `package-text-play-event/1.2`，旧绑定及历史保留。新旧模型共用原供应商、预算、30 次请求限制和一次在途请求。完整动态 schema 计入输入上界，单次无重试，未知费用保留预占，任何中间新事件使旧结果 STALE。

试玩视图新增 `investigation_proposals:{schema_version:"package-proposal-view/1.0",available,reason,character_ids,entries,requests}`。entries 包含 `{id,sequence,phase_id,speaker,kind:"CLAIM",action:{id,label,cost},text,basis}`，basis 仅由服务器还原公开文字及类别；requests 包含 `{request_id,character_id,revision,status}`，revision 为发起前的版本，status 为 `PENDING|OK|INVALID|UNKNOWN|STALE|EXPIRED`。所有字段仅本局拥有者可读。

页面通过独立请求回执核对结果；未结请求在重进后恢复原 key/角色/revision，只有再次显式点击才检查原请求。不会因 GET、未知结果或切换页面产生新费用。当前能力与验证范围见[调查建议接线](../development/M2_INVESTIGATION_PROPOSALS.md)。

## 显式私密回忆扩展（2026-09-07）

`script-package/1.3` 继承 1.2 的调查规则，增加 `memories`，配对 `package-play-rules/1.2`。回忆声明包含稳定 id、接收角色、阶段下限、标题/正文、FACT/CLAIM/INFERENCE、`card_disclosure:KEEP_PRIVATE`、`retelling:MAY_RETELL|MUST_RETELL`、原件/编辑来源与非空触发列表。列表为 OR，按声明顺序保存首个匹配原因；内层触发只接受 `OTHER_PUBLIC_SPEECH + keyword` 或 `ACQUIRED_EVIDENCE + evidence_id`。完整结构见 [1.3 schema](script-package.v1.3.schema.json)。

关键词是严格非空、无两端空白/控制字符的字面子串，大小写及字符原样匹配；“提到”包括否定句，系统不判断其事实真假。保存成功的其他角色 SPEAK、有效调查建议或下述RESPOND自然短句才被听见；不扫描提问、引用文本或回忆正文，也不补放阶段之前的发言。调查建议仍为程序短句，自然对白使用独立RESPOND协议。

证据触发针对角色初始或本事件新增的实际持有集合，包括公共线索及本人已解锁私有线索；查看页面/读取上下文不产生事件。规则事件、正常 AI 资料分享可能新增证据；发放给另一角色的私有奖励只更新接收者。无动作条件、注定更早获得而不能再次取得的证据不能作为更晚回忆的有效获取分支；这项检查不代替完整规则可达性审核。

新 1.3 会话的首条起使用 `package-text-play-event/1.3`，授予在既有事件事务内、状态摘要前计算；初始已获证据可在 sequence=0 授予。内部 `package-play-state/1.2` 保存观察序号、每角色已见证据和一次性授予结果。重放使用原事件，不重新调用模型或追补历史授权。旧包/绑定/历史摘要不升级，无新表。新代码不能用旧规则版本读取新包；回滚必须保留事件。

HTTP 视图仅新增可选：

```json
{"memories":{"schema_version":"package-memory-view/1.0","entries":[{"id":"fictional-memory","character_id":"a","title":"一段回忆","text":"仅本人已获的回忆内容。","kind":"CLAIM","card_disclosure":"KEEP_PRIVATE","retelling":"MAY_RETELL","sequence":4,"phase_id":"opening","cause":{"kind":"OTHER_PUBLIC_SPEECH","speaker":"b"}}]}}
```

证据型 cause 改为 `{kind:"ACQUIRED_EVIDENCE",evidence_id}`。不返回其他角色的回忆存在、数量、关键词或正文。页面核对接收角色、序号、实际发言绑定/已获证据，异常响应不显示；刷新不增发、不请求模型。单卡“阅读这段回忆”只展开本地已授权内容，没有原卡公开控件。

内部 proposal 输入只扩充该角色自己的已获回忆，编码为 `collection:knowledge, public:false`；回忆 id 不得与原 knowledge id 冲突。它们不成为公开依据、可分享材料或旧 `/ask` 的候选。MUST_RETELL 仅为剧本义务说明，本版不标记履行、自动代述或增加阶段门槛，任意真人输入也没有语义审核。

来源核验 `source-verifier/1.3` 覆盖 memories 的实际引用定位；人审 `script-audit/1.2` 可定位 memory 项。Compiler/模型 Audit 尚未接入该包版本，完整模型审核发布门禁继续生效，开发夹具不构成批准。范围与证据见[回忆触发开发记录](../development/M2_MEMORY_TRIGGERS.md)。

## 自然角色回应扩展（M2）

`POST /api/fusion/package-plays/{play_id}/responses` 沿用认证、所有者、8 KiB、no-store和单次费用账本，仅支持显式1.3回忆引擎：

```json
{"schema_version":"package-dialogue-command/1.0","action":"RESPOND","expected_revision":1,"idempotency_key":"role-response-1","character_id":"b","reply_to":"statement-1"}
```

目标必须是本阶段已保存的真人发言，回应者须是其他角色；不接受草稿、旧阶段或客户端材料。表达输入只读当前获准转述内容和按序公共说法，普通KEEP_PRIVATE目标不进入表达模型。按段输出授权依据，严格校验后才保存为公共CLAIM；内部依据、回忆原卡与AI目标不返回真人。实际保存台词才触发回忆，不执行搜证或计分。

视图新增 `role_responses:{schema_version:"package-dialogue-view/1.0",available,reason,character_ids,reply_target_ids,entries,requests}`。每个公开entry严格8字段：`id,sequence,phase_id,speaker,reply_to,kind,text,modes`。requests保存原请求键、角色、目标、原revision和状态，供断线后用原命令检查。初次RESPOND后追加1.4事件，未启用的旧局哈希不变。

表达模型1.0、1.1提示和元数据保留，按记录版本验历史；默认1.2额外冻结`refusal_policy_hash`。版本变化使旧局新调用CONFIG_CHANGED，但GET及原请求检查仍可用，不隐式升级或改写失败。1.2对明确元信息索取核验完整请求后返回固定UNCERTAIN、零SDK和零用量；其他问题仍单次模型和严格输出核验。固定未知的状态为OK，表示回应已保存，不能当作已知事实。拒答仍计入本局30次请求上限，既有费用预占、迟到、过期和可用性门槛仍适用。

授权引用不证明自然句语义必然正确；原卡复制保护为规范化连续片段检查，不是完整语义泄露检测。转述仅记录ATTEMPTED_UNVERIFIED，不把MUST_RETELL判成已履行或用于评分。真实结果和限制见[M2验收](../development/M2_ROLE_DIALOGUE.md)。

## 完整文字整局扩展（M3）

显式 1.4 包的共同调查、五席封卷、个人结算、鉴权原图与匿名 AI 电话见 [完整文字整局接口](full-text-play.md)。旧接口、绑定和历史摘要保持原契约；采用开发候选不构成商业发布。

### 持续开发协议增量（2026-09-08）

新局默认 `role-speech/1.10`，对应公开对白 `package-dialogue-model/1.13`、电话 `package-call-model/1.8` 和单独指定的桌面决定 `package-table-model/1.1`；历史协议与事件按原版本重放。AI 不直接推进规则或计分。

对白 context1.4 明确 `material_scope`。服务端先根据完整当前授权状态、实际公开任务尝试和元请求拒绝规则选定至多一条 MUST_RETELL；有任务时为 `TURN_TASK_ONLY`，本次只发送该目标完整正文、原玩家问题和角色身份，策略材料为空。完整先前尝试保留审计并绑定 source/context hash；SDK 的先前尝试只含序号、结果与未核验状态。没有任务时为 `ALL_AUTHORIZED`，正常公开与私聊保留全部当前授权材料，再按既有窗口规则截取近期发言。长对局先选择历史窗口，再校验模型输入的历史数量上限。任务每条最多安排两次，失败也占一次；引用合法、回复 OK 或次数耗尽都不等于语义完成。

电话1.8从原 speaker、当前角色和唯一通话对方派生 CURRENT/PEER/THIRD_PARTY；原身份和正文不变。标注只说明发言者，不能证明 CLAIM 内容为真；代词归属仍需独立内容核对。

桌面1.1将每道封卷题与其合法选项集合、最大选择数绑定进 schema，仅相同完整选项集合与上限可合并枚举；本地仍检查所有题齐全、无重复和选项权限。规范化 wire、schema 和附加字段全部计入字节上限，旧协议 schema/hash 保持。


## 2026-09-08 阅读图片与有源勘误

开场 GET 可包含 `visuals:[{id,collection,material_id,label}]`，仅按同一开场已经返回的本人/公共知识和证据过滤；不返回后续回忆、未获得调查卡或来源路径。
`GET /api/fusion/package-sessions/{session_id}/images/{visual_id}` 与试玩的图片 GET 一样，先检查当前用户拥有的绑定和当前材料授权，再按历史 release/source bundle 校验原图来源、SHA 和 JPEG/PNG 类型。成功响应为图片字节，`Cache-Control:no-store`、`X-Content-Type-Options:nosniff`；拒绝未知/他人/未获授权图片，错误不返回文件路径。读取不创建游戏、不追加事件、不调用模型。

服务器可显式注入 `PackagePresentationRepair`，用于已经逐源复核的旧版转录修复；HTTP 不接受该配置。注册绑定旧包哈希、新包和确切材料 ID/原文 SHA，权限与规则字段必须相同。它只在已获材料的阅读投影上替换精确旧文、按 truth ID 修正已揭晓结局；原始包、事件、玩家/AI 台词、模型上下文与评分不变。初始私人缺项用 `reading_supplements:[{id,text}]` 返回给对应角色，只读，不作为分享/动作目标。新补图必须来自旧包相同的原件并在修订包中有显式关联，只在材料已经可见时增加安全 visual 元数据。

`transcription_revision` 标识这份服务器勘误绑定；未注册实例和不同包不应用。新建游戏使用正常导入/发布链中的修订候选；独立本机 M3 fixture 的候选登记不等于正式人工发布通过。前端图片使用当前身份认证读取到本地 blob，视口附近自动加载，支持显式重试、旋转和放大，换号/卸载清理图片与请求。

“我收藏的线索”是按账号/局/角色隔离的浏览器本地收藏，玩家主动保存/取消。展示时重新与当前授权材料正文和图片 ID 比对，不信任旧 localStorage 自动授予权限；换版内容需明确重新收藏。它不产生公开分享，不替代自由手记，不提供跨设备云同步。
