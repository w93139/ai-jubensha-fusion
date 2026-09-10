# 剧本导入与发布契约

**状态：截至 2026-09-06，4D 已接入共享行动点、条件调查及对应 Compiler/Audit 契约；4B 的受限材料问答与指定结尾仍保留。新 1.2 三次真实 Compiler 均未通过固定合成验收的精确规则映射检查，Audit 未调用；商业本完整结算、自然对白、主持与整局尚未验收。** 新版执行边界见[阶段 4D](../development/PHASE_4_BUDGETED_INVESTIGATION.md)与[文字试玩接口](package-play.md)，其下阶段记录为历史实现说明。本文不包含商业剧本正文，不授权自动发布。第一阶段素材范围只限已确认的《孽岛疑云》，不处理《办公室谋杀案》。

第三阶段 B 已增加兼容旧包的 [候选来源 v1.1](script-package.v1.1.schema.json)、[人工审核报告提交](script-audit-submit.v1.schema.json)和[问题处理](script-finding-disposition.v1.schema.json)，实现记录见[第三阶段 B](../development/PHASE_3_REVIEW_RECORDS.md)。这些记录不会生成自动语义结论、替代最终人审或清除发布门禁。第三阶段 C 已加入受控 Compiler/Audit 持久任务和服务端固定字段装配；2026-09-06 已通过双角色、双阶段合成资料的真实 Compiler v4 → 来源核验 → 候选 → Audit v3 链路，Audit 五维完整、2 项发现、0 BLOCKER。此前失败记录保留，3C 证据见[规则契约与真实验收](../development/PHASE_3_RULE_CONTRACT_REVIEW.md)。该小样本成功不代表商业剧本或完整产品验收。随后实现的 3D 门禁仍要求管理员独立判断，模型不会替人批准；本轮未处理商业正文、调用付费模型或批准真实候选。

## 4D 的显式版本选择

旧请求完全不变；新任务通过 [authoring-request.v1.2](authoring-request.v1.2.schema.json) 明确提交 `package_contract:"script-package/1.2"`，工作台可选择“含行动点与条件搜证”。worker 使用相同的 `--package-contract script-package/1.2`；默认仍是旧包 1.1。新模型/请求配对为 `authoring-model/1.2`、`bailian-authoring-json/1.2`，当前 Compiler v7 / Audit v4；Compiler v5/v6 文件和历史任务保持，不能用不同配对恢复已有任务。v7 将通用条件字段对照放在 schema 后，完整提示文件哈希和实际消息字节仍受原预算与派发校验约束。

新版模型只输出 [compiler-draft/1.1](compiler-draft.v1.1.schema.json)，内容新增 `mechanics` 和材料动作前置。服务器仍装配冻结元数据，得到 [script-package/1.2](script-package.v1.2.schema.json)；模型不得改标题、人数、版本或来源清单。仅新版编译输入额外列出已选来源的声明种类，帮助区分补充材料；`normalized` 不证明其规则属于原文明确。公开动作名称也必须摘自授权引用。旧请求、上下文、提示词、schema 和任务哈希不变。

1.2 候选的模型与人工审核均使用 [script-audit/1.1](script-audit.v1.1.schema.json)，人工提交见[独立新请求](script-audit-submit.v1.1.schema.json)。新增问题目标为 `mechanics.actions`（动作 `id`）与 `mechanics.phase_budgets`（使用 `phase_id` 作为目标 `id`），问题只能引用该实体已声明的来源。新旧报告与候选错配时拒绝；报告版本不能作为绕过来源、五维覆盖或独立人审的手段。

新候选使用 `script-package-validator/1.2` 与 `source-verifier/1.2`，保存到导入、模型、人工报告及发布依据中。现有数据库表、批准与发布接口继续复用，审核依据变化仍使旧确认失效。来源文件一致、结构通过和模型审核完成均不自动批准内容。

结构检查不证明每个可选真人角色都能消耗完阶段点数，也不把未实现的 AI 调查算作可执行动作。新版 PLAYABILITY 审核须逐个选角检查卡关、前置、预算与结尾；不确定时报告阻断，不能改权限或成本补出通路。

## 实现前先核对

4E 新增[规则与来源对照](rule-review.md)，用于人工查明动作前置与材料开放条件是否忠实于各自原文。它是管理员只读证据，不替代本节编译、Audit、人审与发布门禁。

新的 Authoring 收据可包含 `response_fingerprint`：网络未返回时为 null；返回后为 `content_sha256` 与 `response_id_sha256` 两项，各为 64 位小写 SHA-256 或 null。前者取 SDK `message.content` 的 UTF-8 内容，在用量和 JSON 解析前计算；后者取 SDK 响应 ID 的摘要。它们不是原始 HTTP 响应或 HTTP 请求 ID，不能据此证明供应商缓存行为。收据不保存原文/原始 ID。字段缺失的历史收据保持原字节和哈希，不回填不存在的证据；未知用量继续保留原预占规则。

主仓库 [ScriptDBModel](../../backend/src/db/models/script_model.py) 仍是历史平面剧本及子实体。旧基础质检不构成发布批准；旧直接 PUBLISHED 路径已阻断。新候选使用独立的[人审发布服务](../../backend/src/fusion/script_publication.py)和[开场预览服务](../../backend/src/fusion/package_runtime.py)，不转换成旧两轮规则的可玩剧本，不改写旧游戏会话。

项目父目录的 `存档/待合并开发副本/ai-jubensha-fusion/` 中 `juben_design` schema、migration、tests 等差异已完成[独立审查](../development/ARCHIVED_COPY_REVIEW.md)，未合并。新候选包与旧创作数据分开，历史副本保留。

## 流程（设计目标）

OCR 规范化结果 → 可选的本剧本后台检索索引 → ScriptCompilerAgent → 确定性校验 → ScriptAuditAgent → 人工审核 → 发布版本。

- RAG 是可选辅助，不是强制前置条件；没有检索索引也应能导入结构化材料。
- 如保留 OpenKB，仅用于每本剧本独立的后台检索与审计；不得成为游戏运行时权威来源。
- Compiler 把有来源的材料整理成候选包，不补写缺失真相或自创规则；不直接写 PUBLISHED 数据。
- 确定性校验负责“字段和引用是否正确、规则是否合法”；Audit 负责“证据、时间线、视角和可玩性是否有疑点”；人决定是否接受风险并批准具体版本。
- 校验失败不能被 Audit 一句“通过”覆盖；Audit 完成不能替代人工批准。

## 产物与最小字段（设计目标）

以下仍是完整流程的目标清单。候选 v1/v1.1、来源核验、报告、人审确认和不可变发布记录已实现；会话绑定已覆盖固定角色开场与独立阶段演练。现有契约的阶段/公开证据条件解锁已接线，商业本专属机制、AI/主持完整运行时和生产部署尚未完成，不能把该表全部视为上线能力。

| 产物 | 必须表达的信息 |
| --- | --- |
| 来源/规范化清单 | 本剧本稳定标识、文件 `source_id`、相对路径、SHA-256、文件类型、原件与规范化文件的对应关系、页/锚点与审核状态 |
| `script-package.v1.json` | schema 版本、剧本及内容版本、来源清单引用、公开介绍、角色阶段材料、线索、阶段、触发条件、可见性/披露规则、结算与仅后台可见的真相 |
| 校验报告 | 包哈希、校验器/契约版本、检查项 ID、严重程度、对象路径、来源定位、通过/失败和未支持机制 |
| Audit 报告 | 包哈希、审计配置/模型/提示版本、问题及来源证据、不确定项、建议与结论；不得只给一个总分 |
| 人审记录 | 被批准的包哈希、关联报告版本、审核人、决定、时间、阻断项处理记录；不是可被模型填写的“已批准”字段 |
| 发布记录 | 不可变内容版本、包哈希、关联人审记录、发布人和时间；会话绑定该版本 |

文件哈希用于确认“是否同一份内容、之后是否改变”，不证明 OCR 正确。原件与规范化文件分别计算哈希，保留对应关系；不得为了去重丢掉来源定位。

候选包采用固定序列化后计算 SHA-256，摘要存入外部任务/审核记录，避免包内自引用哈希。实体引用使用稳定 ID，不以角色名字或线索标题充当唯一键。报告必须定位到具体实体和来源页/锚点。

## 确定性检查与 Audit（设计目标）

确定性检查至少包括：

1. schema、必填字段、唯一 ID、外键/跨实体引用、类型及枚举合法；未知字段按 schema 策略处理，不静默丢弃规则。
2. 来源引用可定位，角色/线索/阶段覆盖清单完整；缺页、缺线索、冲突材料显式报错或挂起。
3. 阶段转换、触发条件、资源限制和结算引用合法；不支持的机制列为阻断，不能降级成模型自由解释。
4. 每条知识/线索具有可见性与解锁归属，真相与玩家可见材料分离；不能把客观凶手标签自动当成角色自知信息。
5. 包版本及报告关联一致。基础字段齐全不代表证据链充分，也不代表整局可玩。

Audit 只读取该候选包及被授权的来源材料，输出有证据的问题列表。重点核对时间线、证据相互支持/冲突、角色实际知道什么、提前泄露和卡关风险。无法确定的地方标为待人审，不悄悄改正文、不自动消除阻断。

## 任务、重试与发布（设计目标）

任务状态与剧本发布状态分开。当前 3C 任务使用 `QUEUED/RUNNING/NEEDS_RECONCILIATION/BLOCKED/COMPLETED/CANCELLED`，步骤另存；旧平面剧本保留历史状态，新候选则使用独立人工确认与不可变发布记录。任务完成不等于剧本已发布。

- 任务记录输入清单哈希、步骤、配置/契约/模型/提示版本、产物哈希、领取租约、重试次数、耗时与已知用量。素材路径仅指向私有存储，不写入公共日志。
- 步骤缓存键依赖输入与实现版本，不依赖并发完成顺序。输入变化重新执行受影响步骤；恢复前验证既有产物完整性。
- 慢步骤通过受控 worker 运行，提供查询、取消和恢复；不让模型提交可执行工作流代码或任意 Shell。
- 当前模型步骤最多单次派发，无自动重试；恢复只继续未发送步骤，不重发未知请求。远端可能已计费但本地结果丢失时标为不确定，不声称“恰好一次调用”。
- 发布前必须同时满足确定性检查通过、Audit 阻断项已处理、授权人员批准同一包及报告版本。
- 候选包、规则或关键报告变更使原批准失效。已发布内容冻结；旧编辑/子实体接口不得原地修改在玩版本。
- 发布的数据库写入在明确事务内完成；失败不得留下部分公开内容。重复发布同一批准版本不重复创建实体。
- 当前开场与独立阶段演练固定绑定发布版本和角色。已发布候选收到后续审核变更后，不再出现在新开场目录，也拒绝新演练/新动作，必须新 `content_version` 重新审核；已有 GET 与原操作重放保留已获内容，旧会话不改写。完整 AI/主持游戏会话仍待后续接线。

## 验收与私有边界（设计目标）

| 编号 | 最小用例与预期 |
| --- | --- |
| IM-01 | 相同内容重复提交可识别；文件/规范化结果改变使关联缓存失效，来源对应仍可追溯 |
| IM-02 | 缺失来源、重复 ID、悬空引用、非法可见性和不支持机制阻断，不进入可发布状态 |
| IM-03 | Compiler 非法输出和 Audit 无证据结论不会触发发布；模型输出没有发布权限 |
| IM-04 | 任务中断、取消、重复提交和租约恢复不产生重复实体或半公开剧本 |
| IM-05 | 未人审、批准过期、包/报告哈希不匹配时发布拒绝；已发布版本不能被旧编辑接口修改 |
| IM-06 | 无 RAG 可以走完整导入；有 RAG 时另一剧本内容不能进入编译/审计结果，索引不覆盖权威包 |
| IM-07 | 虚构分阶段材料证明初始私本不泄露后续内容；旧局不随新版本变化 |

测试优先使用虚构最小样本。商业原件、OCR、候选包、检索索引、完整 Audit 证据及真实对局日志保存在 Git 之外的私有目录；公开仓库只保存通用 schema、方法、脱敏样例与测试。通用下载接口在对象授权验收前不得承载私有正文。真实素材导入、数据库迁移、模型付费调用和最终发布另行执行与验收。

## 已接入的阶段 3C 任务接口

管理员入口 `/admin/authoring-jobs`；创建请求见 [authoring-request.v1](authoring-request.v1.schema.json)。当前模型与请求契约分别为 `authoring-model/1.1`、`bailian-authoring-json/1.1`，Compiler 的模型响应采用 [compiler-draft.v1](compiler-draft.v1.schema.json)。

模型响应包含 `schema_version: "compiler-draft/1.0"`、`status`、`content` 和 `blockers`。`content` 仅允许 `introduction`、`characters`、`initial_phase_id`、`phases`、`knowledge`、`evidence`、`truth`、`settlement` 八个内容字段；额外的包元数据、审批或运行时动作字段会被拒绝。`CANDIDATE` 必须有内容且无阻断项；`BLOCKED` 必须为 `content: null` 且有阻断项。

Compiler 提示输入仅包含标题、人数、显式选择的文字材料、来源说明及可用定位符；不要求模型重写路径、文件哈希或原件关系。服务器从冻结上下文装配 `script-package/1.1` 的 `script_key`、`title`、`content_version`、`player_count` 和完整 `sources`，再校验候选结构、实体关系、授权来源、定位符及正文原样摘录。Schema 由 `CompilerDraftOutput.model_json_schema()` 直接生成；分支一致性和这些上下文校验仍须由服务端执行，单独通过 JSON Schema 不等于编译通过。

[compiler-output.v1](compiler-output.v1.schema.json) 保留为服务器装配后的领域结果及历史完整包结果契约，仍使用 `status`、`package`、`blockers`，供任务持久化和后续候选/Audit 步骤消费。它不再是新 Compiler 模型响应的格式。旧 `authoring-model/1.0` 与 `bailian-authoring-json/1.0` 的配对记录、产物和收据保持可读，不转换成新 draft，也不按 1.1 配置恢复派发；版本混搭会被拒绝。

- `GET/POST /api/admin/fusion/authoring-jobs`：查询或排队；创建返回 202，不在 HTTP 请求里运行模型。
- `GET /api/admin/fusion/authoring-jobs/{id}`：进度、脱敏配置、调用收据和候选/模型审核产物，仅管理员。
- `POST .../{id}/cancel`、`POST .../{id}/recover`：携带 `expected_revision`，初始 0；未知派发不可重发，取消后仍可记迟到收据。
- 单任务 worker 显式启用付费执行；最多一个 Compiler 和一个 Audit，逐步预占、持久派发、结果复用。状态/恢复契约见[阶段记录](../development/PHASE_3_AUTHORING_WORKFLOW.md)，当前小样本真实验证及限制见[规则契约与真实验收](../development/PHASE_3_RULE_CONTRACT_REVIEW.md)。

这些任务产物是私有候选与 MODEL 审核建议，不自动创建人工报告、最终确认或发布记录。管理员必须通过下列独立 3D 流程确认与登记。

## 已接入的阶段 3D 确认、发布与开场接口

管理员使用 `/admin/script-reviews?version_id=<id>`：按需查看完整候选，核对来源、人工报告与模型建议，再“确认本版内容”；确认保存后，另行点击“登记发布版本”。模型 WARNING/BLOCKER 的人工处理不会自动变成人工审核报告，也不预填为误报或已通过。

| 接口 | 当前行为 |
| --- | --- |
| `GET /api/admin/fusion/script-packages/{id}/publication` | 返回候选概要、完整人工报告和关联模型任务、唯一来源快照、`basis_hash`、逐项检查、`can_approve/can_publish`、最新确认和发布记录；动态有效性只在此实时判断 |
| `POST /api/admin/fusion/script-packages/{id}/approvals` | [人工确认请求](script-publication-approve.v1.schema.json)：`idempotency_key`、`expected_package_hash`、`bundle_hash`、`expected_basis_hash`、`model_dispositions`、`note`；身份由服务端认证，不接受模型声明的批准 |
| `POST /api/admin/fusion/script-packages/{id}/publish` | [登记发布请求](script-package-publish.v1.schema.json)：`idempotency_key`、`approval_id`、`expected_approval_hash`、`expected_basis_hash`；重新检查来源与完整依据，原子创建不可变发布记录 |
| `GET /api/fusion/package-releases` | 登录用户可选的新开场发布版本；确认已失效的发布不出现在目录中 |
| `POST /api/fusion/package-sessions` | [创建开场请求](package-session-create.v1.schema.json)：显式 `release_id`、`character_id`、`idempotency_key`；固定当前发布版本、角色与所属用户 |
| `GET /api/fusion/package-sessions/{session_id}` | 仅所属用户读取既有开场；只返回本角色与公共的授权初始材料，不返回其他角色私有内容、后期内容或后台真相 |

接口使用 `success/data` 包装和 `Cache-Control: no-store`。401/403 拒绝未认证或越权请求，409 表示依据过期或当前条件不满足，不能自动再次批准。相同内容失败重试复用幂等键；成功或编辑后新建请求。POST 返回不可变保存记录，不附加动态有效标志，页面必须随后 GET 门禁刷新。

门禁至少要求候选当前校验通过、来源及历史核验记录完整、一份五维人工报告、一份有效模型报告、全部人工阻断与警告已处理、没有待完成或未知派发的关联任务。`basis_hash` 覆盖全部关联报告、处理版本、任务收据与来源及策略版本，不只计算页面最近显示的报告，不允许选择干净报告绕过旧问题。已结束的失败模型任务保留在依据中，不冒充成功报告，也不自动否定另一份有效报告。

每条有效模型报告的 WARNING/BLOCKER 都必须由管理员独立填写处理说明，WARNING 可 `ACKNOWLEDGED` 或 `DISMISSED`，BLOCKER 只能在确认为误报后 `DISMISSED`；问题属实则需新候选。INFO 仅供参考，不据此断言实际可玩。最终确认与模型建议、人工审核报告分别保留。

新报告、处置、来源或规则依据变化使旧确认失效；来源重新核验的时间戳产生独立收据，不拿新旧报告哈希是否相等来判断文件是否变化。已发布版本除原请求幂等重放，不再次确认或发布；后续变化必须使用新内容版本，既有开场不改写。

`/play/package-preview` 继续返回 `status=READING_PREVIEW`、`runtime_ready=false`、`available_actions=[]`。该开场切片无模型动作或阶段推进，不把新包套到旧两轮搜证，也不会静默升级为下述演练。3D 新增 `m3f4a5b6c7d8` 迁移仅离线验证，未应用真实 PostgreSQL；该轮没有商业正文处理、付费模型调用或对真实候选的实际批准。该阶段工程证据和未测范围见[阶段 3D 记录](../development/PHASE_3_PUBLICATION_BINDING.md)。

3D 最终自动化结果：后端 1085 passed（363 条存量弃用警告）、前端 89 passed，完整 TypeScript、修改文件 lint 与 24 页生产构建通过。隔离合成夹具浏览器的人工确认、独立发布、普通玩家固定角色开场与刷新、权限/no-store 验收通过，管理员和玩家的 390px 页面无横向溢出，新增模型尝试为 0；这些工程结果不替代真实 PostgreSQL、商业本或完整试玩验收。

## 已接入的阶段 4A 演练接口

`/play/package-flow` 为独立的固定角色确定性演练，从开场中的“检查后续阶段”显式进入。先查询已有演练，没有记录时由用户点击创建；创建重新检查当前发布有效性，旧开场不升级。准确请求、投影、错误与历史规则见[阶段演练协议](package-flow.md)。

| 接口 | 当前行为 |
| --- | --- |
| `GET /api/fusion/package-flows?opening_session_id=...` | 查询本人已有演练，未创建返回 null；不会自动创建或解锁 |
| `POST /api/fusion/package-flows` | [创建 schema](package-flow-create.v1.schema.json)：显式 `opening_session_id` 与 `idempotency_key`；固定角色/版本，每份开场最多一份演练 |
| `GET /api/fusion/package-flows/{flow_id}` | 验证绑定和完整历史后返回最新已保存授权投影，不授予新权限 |
| `POST /api/fusion/package-flows/{flow_id}/actions` | [动作 schema](package-flow-action.v1.schema.json)：`idempotency_key`、`expected_revision` 和 ADVANCE_PHASE 或 SHARE_MATERIAL；仅分享动作携带 knowledge/evidence 的目标 ID |

阶段使用包的显式顺序，材料解锁要求达到阶段下限并满足全部公开证据前置条件；原生公开证据连续解锁，知识公开不能冒充证据公开。仅本人已解锁的 MAY_SHARE/MUST_SHARE 材料可手动公开，KEEP_PRIVATE 禁止，其他角色私密不代为分享。MUST_SHARE 不隐含主持截止、自动分享或新增推进条件；阶段末端也不代表故事结算。

演练使用 `RULES_PREVIEW`、`runtime_ready=false`，只投影当前已获公共与本角色材料，不返回未来/锁定 ID、来源路径、其他私本、后台真相或结算。本切片没有 AI、主持、投票或完整整局。

新建和新动作必须重核当前发布；发布失效后新动作冻结，但本人历史 GET 与同键同完整请求的原动作重放保留已获授权。重放返回原动作 revision 的投影，不是自动执行下一步；前端可再 GET 最新投影，拒绝覆盖到更低 revision 或另一角色/版本，409 需显式刷新而非自动换键重发。

新增两表迁移 `n4a5b6c7d8e9` 仅离线验证，未应用真实 PostgreSQL。4A 后端 1195 passed（363 条已有弃用警告；新增 110 项已计入）、前端 114 passed，完整 TypeScript 和修改文件 lint 无诊断，预检退出码 0、自测 16 项及最新代码的 25 页生产构建通过。隔离合成夹具浏览器通过显式创建、分享解锁、阶段推进、刷新与重进恢复、权限及 390px 无溢出验收；最终 1 份演练、3 次动作，旧开场保持只读，新增模型调用为 0。最新证据与验收范围见[阶段 4A 记录](../development/PHASE_4_RULES_PREVIEW.md)。
