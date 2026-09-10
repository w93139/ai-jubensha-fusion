# 冻结规则草案编译

本流程为显式新入口，不替换已有编译任务。规则草案是准备人员提交的待审核输入，程序不能从任意原文证明规则语义正确，也不自动采用历史模型候选中的规则。

下文原 1.3 摘录模式仍保留；新增 1.4 固定正文模式见文末，真实结果见 [4G](../development/PHASE_4_FROZEN_TEXT_ACCEPTANCE.md)。

## 请求与输入绑定

管理员 `POST /api/admin/fusion/authoring-jobs/with-rule-plan`，最大 1 MiB。原 `POST /authoring-jobs` 继续限制 16 KiB，拒绝含 `rule_plan` 的新请求。GET、取消、恢复及列表复用已有接口和权限。

请求为 [authoring-request/1.3](authoring-request.v1.3.schema.json)：原 1.2 请求字段，加必填 `rule_plan` 对象。该对象必须为完整 `script-package/1.2` 草案，运行时用对应严格 Pydantic 契约、包关系校验、任务标题/版本/人数/来源绑定和原文引用检查验证。不能含 approved/status 等批准字段。草案的文本也须先有合法原文依据；这不是从空白模板自动生成规则的接口。

草案原始字段与省略值保留在任务 request/context 的哈希中。规则、来源、文本或任务参数变化必须创建新的任务输入；相同幂等键不能更换草案。文件完整性会在执行前和生成候选后重新检查。不会自动修复已失败的历史候选。

## 模型与服务器各负责什么

- 新模型/请求契约为 `authoring-model/1.3`、`bailian-authoring-json/1.3`，Compiler 提示为 `compiler_text_system_v1.txt`，输出契约为 [compiler-text-draft/1.0](compiler-text-draft.v1.0.schema.json)。旧提示和请求哈希不改动。
- 模型只按服务端列出的槽位返回 `collection`、`id`、`text`。允许集合为 introduction、knowledge、evidence、truth、settlement.instructions；两个单例的 id 为 null。每个槽位恰好一次，所有槽位必须齐全。
- 每段 text 必须为其**冻结来源位置**中的连续原文。模型不能修改来源，不能把另一个槽位的专属原文放进这里。来源位置较宽时仍需人审核对摘录含义，原文检查不等于无剧透证明。
- 服务器复制冻结草案，只填上述 text 字段。其余所有字段、数组次序、费用、动作/公开证据前置、材料权限、预算、阶段、人物、真相绑定与省略值均不变；候选复查及发布依据校验也检查这一点。输出不是按 JSON Patch 执行，不接受任意路径或操作。
- 多余字段、少槽位、重复/未知槽位统一拒绝；不能“忽略非法规则后接收剩下的文本”。BLOCKED 分支沿用有来源的阻断报告。无自动付费重试，也无跨供应商切换。

候选仍为 `script-package/1.2`。Audit 沿用 1.2 的审核报告契约和提示，对完整候选提出建议；不重复传一份草案正文，传草案摘要且 prepared.context 保留完整输入。Audit 仍可能发现原草案的错误。完整人审、问题处理、最终确认与独立发布门禁保持；规则冻结和模型调用成功均不是批准。

## 工作台与执行

管理员工作台选择“固定规则草案，只摘录原文”，读取准备人员提供的 JSON（浏览器限制 256 KiB），标题/版本/人数须匹配。来源或请求信息变化清空已读草案，重新选择文件；文件读取失败不能排队，旧异步读取结果不能覆盖新表单。文件内容不直接展示到任务列表或日志。

排队本身零模型调用，worker 必须明确选择新流程：

```sh
backend/.venv/bin/python backend/scripts/authoring_worker.py --job-id <任务编号> --allow-paid --package-contract script-package/1.2 --frozen-rule-plan
```

这是有费用的实际执行命令，本文不表示已经执行。旧 worker/新 worker 与任务快照不符时停止，不迁移或重发任务。单步/单任务预算、用量未知保留与 SDK 响应摘要复用现有账本。模型配置及价格没有在本轮更改。

## 1.4：规则与正文都固定，AI 只检查

同一专用 POST 接口通过必填显式字段 `compiler_mode: "CONFIRM_FROZEN_TEXT"` 选择 [authoring-request/1.4](authoring-request.v1.4.schema.json)，其余准备稿与来源要求不变。没有该字段仍是旧 1.3，不自动升级。

模型/请求快照为 `authoring-model/1.4`、`bailian-authoring-json/1.4`。Compiler 使用 `compiler_text_confirmation_v1.txt` 与 [compiler-text-confirmation/1.0](compiler-text-confirmation.v1.0.schema.json)。输入 text_slots 包含准备稿中的 text 和来源，模型检查摘录是否合适；CANDIDATE 分支只返回所有槽位的 collection/id，各一次。禁止返回 text、sources 或任何额外字段；BLOCKED 仍返回来源绑定的问题。

服务器复制准备稿全文，槽位确认不会改变任何文字。后续候选复核要求整个原始 package 等于冻结准备稿，不能增加管理说明，不能把另一段“同样属于原文”的话替换进来。模型检查并不证明准备稿正确，也不授予人审批准。后续 Audit 契约仍为 script-audit/1.1。

工作台选“固定规则和正文，只检查内容”。从该模式切回其他模式会去除 compiler_mode 并清空文件；异步文件输入的 key 包含模式，旧读取结果不能覆盖新模式。执行 worker 时必须同时指定：

```sh
backend/.venv/bin/python backend/scripts/authoring_worker.py --job-id <任务编号> --allow-paid --package-contract script-package/1.2 --frozen-rule-plan --confirm-frozen-text
```

1.4 真实编译小样本已通过，后续 Audit 格式尚未通过；不能据此发布或宣称完整链路验收成功。
