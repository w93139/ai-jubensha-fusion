# 显式完整文字整局接口

本扩展仅针对 `script-package/1.4`、`package-play-rules/1.3`，沿用[文字试玩](package-play.md)的用户认证、局所有者、固定发布版本、revision、幂等键和 no-store。旧包、旧 binding、历史事件均不自动升级。新局 binding/1.2 分别冻结整局输入字节（默认 65536）和正式决策输出额度（默认 4096 tokens）；普通问答/对白额度不变。

## 正式操作

下列路径均相对 `/api/fusion/package-plays/{play_id}`，成功返回完整本人视图。

| 方法/路径 | 命令契约 | 内容与权限 |
| --- | --- | --- |
| POST `/table` | `package-full-play-command/1.0` | 真人席的 `OPEN_BALLOT`、`CAST_BALLOT`、`BREAK_TIE`、`START_CALL`、`PRIVATE_SPEAK`、`STOP_CALL`、`SEAL_FINALE`；服务器固定本人，客户端不能代填 AI |
| POST `/decisions` | `package-table-decision-command/1.0` | 指定 AI 席及 `CAST_BALLOT`、`BREAK_TIE` 或 `SEAL_FINALE`；只请求一次模型，不接收客户端代填答案 |
| POST `/private-responses` | `package-private-dialogue-command/1.0` | 同线 AI 的 `RESPOND_PRIVATE`；指定本线实际已保存的 `reply_to` |
| POST `/phone-step` | `package-phone-command/1.0` | `PHONE_STEP`；服务器选择当前应判断的 AI，客户端不能指定隐藏参与者或台词 |
| POST `/phone-pause` | `package-phone-pause-command/1.0` | `PAUSE_PHONE`；免费释放仅 AI 参与的线路，保留已有私聊；本人在线使用 `STOP_CALL` |
| GET `/images/{visual_id}` | 无命令正文 | 只取本人当前已获材料的关联原图，绑定冻结来源文件并鉴权；不得任意读取来源路径 |

所有 POST 命令均要求 `expected_revision`、`idempotency_key` 和明确 `schema_version`/`action`。额外字段拒绝。`OPEN_BALLOT`、`STOP_CALL` 无 payload；其余真人命令使用对应严格结构。调查表决接收 `kind:CHOOSE|ABSTAIN|SKIP` 与 `choice_id`，只有 CHOOSE 可带合法选项；平票裁决接收 `choice_id`。电话邀请接收 `peer_character_id`，本人私聊接收非空 `text`。

五席独立封存调查选择；达到既定人数后按冻结规则归票、处理平票或跳过。只有程序能够扣调查点、发卡与推进。封卷提交完整 `structured-finale-submission/1.0`：所有个人题目的有限选项、正式指认、信任及可选复盘正文；主动不知道为该题空选项。提交后不可改答，五席全部完成前不显示答案键、个人分或结局。未知判据保留未判，不从 AI 自报分数补足。

## 匿名电话与模型边界

```json
{"schema_version":"package-phone-command/1.0","action":"PHONE_STEP","expected_revision":4,"idempotency_key":"phone-turn-1"}
```

`package-call-model/1.0` 的空闲决定只允许 INVITE/PASS。规划输入可含该 AI 自己的当前经历与目标；不输出目标或理由。接通后只允许 SPEAK/END，表达输入只有本人获准讲述材料、公共说法和本线实际听到的发言，私密目标不进入表达模型。SPEAK 按 `package-dialogue-model/1.3` 校验授权依据、复制原卡及明确越权索取；实际接受的发言才能触发听众回忆。固定邀请为 PROGRAM_OPENING，不作为关键词回忆触发。说过回忆仅记 ATTEMPTED_UNVERIFIED，不自动算履行或加分。

HTTP `phone_turns` 仅含 `schema_version:package-phone-view/1.0`、`available`、`can_pause` 和 `requests`；回执只含 `request_id`、发起前 `revision`、`status`，不包含 AI 角色、目标、台词或规划阶段。`full_game.call`、`private_discussion` 只向通话当事人提供；旁人只能看到 `phone_busy`。服务端轮换规划顺序及下一发言人，前端仅驱动单步，不在后台无限调用。

请求先事务预占费用，再释放数据库事务调用一次 SDK，最后保存回执和合法效果。相同键检查不重新调用；UNKNOWN/INVALID/STALE/EXPIRED 保留费用记录且不执行成功动作。处于 AI 通话时预留免费释放线路所需的事件位置；释放后旧请求可以结束记账，迟到的台词不重新接通。普通历史事件保持 1.5，首次电话决定后追加 1.6；新电话元数据冻结调度和表达策略摘要。

## 页面与验收边界

页面显示共同调查、本人的封卷表单、匿名电话进度、本人原图以及封卷后的个人计分与结局。请求绑定当前身份、局和 revision；切换身份或离开页面清理草稿、图片、在途状态。刷新以服务端回执恢复，相同请求键只检查原结果。

隔离测试及模拟 SDK 验证证明程序链路，不能证明真实模型语义准确、真实 PostgreSQL 锁行为或商业内容发布通过。当前实际证据与下一步见 [M3 记录](../development/M3_FULL_TEXT_PLAY.md)。


## 显式角色策略与有依据对白（1.4 / 电话1.1）

HTTP服务显式选择 `role-speech/1.1`；本地服务默认1.0便于保留旧适配器。新整局公开/私聊使用 `package-dialogue-model/1.4` 与 context1.2；新电话使用 `package-call-model/1.1` 与 context1.1。新增 `strategy_materials` 仅供本人选择说什么/隐瞒什么，来源为本人已解锁、KEEP_PRIVATE且无retelling的knowledge；不含其他席/未来材料，不在台词basis目录、HTTP玩家视图或公开记录中。电话空闲时仍使用原planning区，strategy为空；接通后才提供独立strategy区。MAY_RETELL允许表达，不要求全盘坦白；MUST_RETELL仍按原要求履行。

新输出schema把1至3个有依据段落（text非空、REPORT/INFERENCE/QUESTION、basis1至3项）与单段明确未知（空text、UNCERTAIN、空basis）分开。服务端继续验证依据授权、重复、控制字符、元信息请求和原卡复制；新策略区也纳入整段/长串原文复制保护。保护不能判定任意改述是否泄密或是否与事实一致，真实语义仍须独立验收。

新模型/上下文/窗口分别绑定摘要。重放AI_REQUEST时按该事件记录的模型版本构造上下文，之后按本局冻结版本恢复；旧schema/prompt/hash不改，不自动升级已冻结模型的局。切换服务配置后旧局仍能GET、检查原请求、免费退出；新模型动作显示CONFIG_CHANGED并阻止重新发起。策略属于必要输入，超额度直接阻止，不偷偷裁掉目标。


## 显式按目录引用（对白1.5 / 电话1.2）

HTTP服务当前显式采用 `role-speech/1.2`；前文1.1策略适配器保留用于历史验证。沿用完整对白context1.2和电话context1.1，不改变角色策略投影和提示原文。

新schema按最终输入的materials及discussion建立合法(collection,id)组合，分别按collection分组限定id枚举，避免两个独立枚举允许错误交叉组合。策略区/规划区不进入依据目录；裁掉的历史编号不再出现在schema；同一id真实存在于不同合法目录时分别保留。不按编号前缀猜类别。目录为空时只允许单段明确未知，电话空闲的null speech仍可用。

元数据增加/间接绑定 `speech-authorized-basis/1.0` 策略摘要，prepared同时冻结上下文及动态schema；窗口与费用预占包含schema字节。最终本地依据授权、原卡/策略复制、元信息请求和内容结构校验不放宽。不对模型错误引用做自动纠正。旧版本schema、提示、窗口和prepared不变，旧冻结模型的局保留GET/幂等检查/免费退出，新请求遇配置变化继续阻止。引用存在不证明它支持台词，真实内容质量仍需另验。
