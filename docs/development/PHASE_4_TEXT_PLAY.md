# 阶段 4B：角色材料问答与结尾揭晓

2026-09-06。编码前已建立本技术切片；现已完成代码接线与离线/隔离浏览器验证，真实模型和商业本验收仍待完成。用户已授权继续开发；保留 FastAPI、Next.js、PostgreSQL 与既有模型渠道。

## 行为与契约

从固定版本开场显式创建独立文字试玩 `/play/package-play`，重新核验当前发布。旧开场与 4A 演练不升级、不迁移。新引擎保留阶段下限、全部公开证据条件、连续解锁与 MAY_SHARE/MUST_SHARE/KEEP_PRIVATE 边界，但共享账本覆盖全部角色。

真人选择另一角色并提问。AI 输入仅有该角色身份、当前阶段、公开材料和本人当前可分享材料；没有 KEEP_PRIVATE、他人私本、未来材料、后台真相或来源。首版模型只选择最多三条材料引用，服务器校验后原样组装回答、保留事实/说法/推测类别，并把选中的私有材料正式公开，触发确定性解锁。空列表使用固定无信息回复。自然语言自由改写、隐藏心理和自动主持不属于本切片。

结算只接当前包明确表达的内容：终末阶段显式“结束并揭晓真相”，展示 `settlement.instructions.text` 与 `truth_ids` 明确引用的真相，冻结后续动作。阶段标题、自由说明文字不能推导投票、凶手判定、分数或个人胜负；这些仍需原本规则的机器契约，不能宣称完整商业本结算已完成。

## 持久化与模型调用

独立两表绑定和不可变追加事件；固定包、角色、发布哈希、模型配置与预算费率。请求使用所有者、revision、幂等键。全历史按哈希链及确定性状态重放；不把数据库自身的哈希视作抵抗恶意整库回滚的外部信任锚。

模型请求先核验当前发布并在局锁内预占预算、持久化 AI_REQUEST，提交并释放锁后只调用一次，不做 SDK 或应用自动重试。等待期间不持有事务。结果另记 AI_RESULT：仅当原版本/阶段 revision 仍匹配、未过期且发布仍有效才发言；过期或失效结果只结用量账，不新增知识。未知用量按预占保守计费，重复键不重新调用。同局冻结一家渠道，不自动切换。

新动作锁顺序为发布候选 → 试玩，来源核验在试玩锁外完成。HTTP GET 仅重放已有授权内容，发布变化不撤回已获得资料；新动作重新核验。手动刷新或网络重试不能创建额外模型调用。配置不可用时阶段推进、分享和结尾仍可使用。

## 本轮验收计划

仅虚构包和隔离数据库：规则、引用权限、条件解锁、结尾范围、预算与未知用量、重复请求、过期结果、权限/严格请求、迁移离线 SQL、前端异步页面生命周期与类型检查。浏览器使用独立虚构夹具和阻断出网的模拟模型。真实模型质量、真实 PostgreSQL 并发/迁移、商业素材审批、语音与完整 MVP 不由本轮离线结果代表。

## 实际实现与检查结果

- 规则与模型：`package_play_rules.py`、`package_role_model.py`。全局角色分享账本、受限材料引用、显式结尾；复用现有渠道白名单和预算类型。自建 SDK 仅在调用时初始化，完成、失败或取消后关闭；注入测试客户端由调用方管理。
- 持久化与 API：`package_play.py`、`package_play_routes.py`，独立绑定与事件表迁移 `o5b6c7d8e9f0`，接在 4A `n4a5b6c7d8e9` 后。新增注册与路由已接线，迁移仅离线检查，未应用真实 PostgreSQL。
- UI：`/play/package-play`、角色提问、材料分享、阶段推进、结尾揭晓、重进恢复。旧开场保留 4A 演练链接，另加显式文字试玩链接；创建本身不调用模型。协议详见[文字试玩契约](../contracts/package-play.md)。
- 后端安全入口 **1378 passed，363 条已有弃用警告，12.35 秒**。新增 183 项：纯规则 26、模型适配 51、DTO/表/迁移 45、HTTP 28、持久化与预算 33。旧开场和 4A 检查均保留。
- 前端八套 **152 passed**（本轮 38、此前 114）；完整 TypeScript 无诊断，修改的五个 TS/TSX 文件、CommonJS 测试与 ESLint 配置检查通过。CommonJS 测试的 `require()` 规则已按 `.test.cjs` 文件类型明确配置，业务源码规则不受影响。
- 开发预检退出码 0，自测 16 项通过；最新代码的 **26 页生产构建通过**，生成配置中的 `NEXT_PUBLIC_API_URL` 为 `http://127.0.0.1:8010`。
- 独立审查发现并修复了真人误作 AI 提问对象、极大已知用量被旧函数截断、结果时间倒置、SDK 关闭和过期预占后前端 revision 重试等问题，均有回归覆盖。网络未知时仍保持原完整请求；只有明确 revision 冲突并刷新后才调整 revision，保留同一幂等键。

命令：

```sh
backend/.venv/bin/python backend/scripts/test_fusion_security.py --tb=short
backend/.venv/bin/python scripts/dev_preflight.py
backend/.venv/bin/python -m unittest discover -s scripts -p test_dev_preflight.py
git diff --check
```

前端目录：

```sh
node --test tests/fusion-evidence-panel.test.cjs tests/source-bundle-panel.test.cjs tests/script-review-panel.test.cjs tests/authoring-jobs-panel.test.cjs tests/script-publication-panel.test.cjs tests/package-preview-service.test.cjs tests/package-flow-panel.test.cjs tests/package-play-panel.test.cjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
node node_modules/eslint/bin/eslint.js src/pages/play/package-play.tsx src/components/PackagePlayPanel.tsx src/services/packagePlayService.ts src/types/packagePlay.ts tests/package-play-panel.test.cjs src/pages/play/package-preview.tsx eslint.config.mjs
NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 node node_modules/next/dist/bin/next build
```

## 隔离浏览器证据

新夹具位于仓库外 `../private-data/import-jobs/package-play/20260905T200350Z-0uo_nt5s`，目录 700、文件 600。来源、模型编译/审核回执、人工报告、批准、发布和账号全部为测试代码构造；来源冻结与发布/开场/文字试玩服务使用真实代码，模型选择经过真实适配器与模拟 SDK，出网被显式阻断。不得把模拟人审记录用于真实内容发布。

浏览器通过旧开场的独立链接进入，读取不会建局；显式创建后向乙询问钥匙位置，模型仅选择乙可分享的资料，服务端原样回答并正式公开。随后甲公开卡片，三份证据及一条 CLAIM 连续解锁；推进到查验才看到后续本人资料。末段仍没有真相，点击“结束并揭晓真相”后只出现指定 `truth-one`。刷新和通过 opening 重新进入均恢复同一局、同一回答和结尾，禁止再写。

强制分享 KEEP_PRIVATE 为 409；向真人本人提问为 422 且无模型尝试；其他所有者 404、匿名 401；原提问用旧 key/revision 重放不再次调用，也不倒退视图。夹具全局中间件统一设置 no-store，这不能外推为生产认证中间件的所有 401/403 都有该头。390px 页面无横向溢出，四张截图实际查看过。

最终 **1 份文字试玩、6 条事件、1 次模拟 SDK 调用、0 次真实模型调用**，旧开场仍不含新阶段资料或后台真相；旧 4A 演练 0 份、0 动作。证据文件包括 `browser-permission-review.json`、`browser-restore-review.json`、`browser-final-review.json`、四张 `browser-*.png`、后端/前端/构建日志及 `verification-summary.json`。

首次页面读取因夹具的 `PlayerModelSettings` 构造参数缺失而失败，已仅修正夹具配置后重测；首次屏外点击未触发动作，滚动到按钮后成功。没有修改商业资料或以失败记录冒充通过。

临时 API 18017 与前端 13017 已正常关闭并验证端口关闭；原 PostgreSQL 55432、Redis 56379 保持运行。浏览器任务空间 60 以 `done:true` 关闭。没有真实数据库迁移、商业正文处理、真实模型调用、提交、推送或部署。

## 未完成范围与下一步

这轮完成的是问答接线与结尾资料揭晓，`runtime_ready=false`。自然角色对白、相关性/口吻质量、自动主持、商业本特有投票/胜负/个人结算、语音及完整商业整局仍待完成。新增运行时必须另做小虚构包真实模型端到端验收，不能用第一阶段旧模型或 3C Compiler/Audit 的历史成功替代；本轮没有产生模型费用。

当前最多 30 个提问、每局同一时刻一份未结预占。输入超界安全拒绝，不静默裁剪材料。已记为 EXPIRED 的请求继续按全额预占估算，迟到实际回执暂不另建对账记录；预算记录不是供应商账单。真实 PostgreSQL 并发/故障恢复、迟到账单对账及外部回滚锚仍未验收。后续应先验证新模型 wire 的真实适配与资料选择质量，再按原本明确规则补充可计算的结算字段，不能让 AI 从自由说明猜胜负。
