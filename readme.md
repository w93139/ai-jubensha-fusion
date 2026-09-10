# 🎭 人生海海

> 2026-09-08：M3 唐小姐与四 AI 的完整文字验证入口已准备好，等待用户试玩。真实整局已完成调查、五席封卷结算和 15 点恢复；本机入口为 http://127.0.0.1:18032。角色仍有必讲遗漏、确定性和电话重复问题，未宣称内容全部通过。详见 [M3 当前交付状态](docs/development/M3_CONTINUOUS_VALIDATION.md)；下文的旧“最新”表述为历史记录，M4、语音及正式发布仍待完成。

> VS Code 开发请先读 [开发交接](docs/development/README.md) 与 [验收清单](docs/development/ACCEPTANCE.md)。
> 下面保留了上游与历史功能介绍，不能当作当前真人模式已完成的证明；已有 .env 不要覆盖。

[English Version](README_EN.md)

面向手机浏览器的单真人 AI 剧本杀：玩家选择一个角色，其余 3–7 名角色由 AI 演绎。项目以 FastAPI + Next.js 为单体基座，规则引擎是阶段、搜证、证据可见性和投票的唯一权威；云模型只生成角色候选发言，不可直接修改游戏状态。

> 当前主线按用户 2026-09-07 确认的[交付计划](docs/development/DELIVERY_PLAN.md)推进：第一版是《孽岛疑云》完整文字试玩，M1 规则与来源对照 → M2 一段真实角色互动 → M3 完整文字整局 → M4 五角色及用户试玩；随后语音体验与上线。后台审核仍未真实验收通过，保持支线，不默认追加提示版本。计划批准不等于编辑规则或发布批准。当前 M1 进行中，尚无新增运行功能；本轮复用旧规则并做来源摘要复核。

> 最新 [4N 引用目录任务接线](docs/development/PHASE_4_CITATION_AUTHORING.md)：显式 Authoring 1.12 已将引用目录接入请求、动态 schema/预算、持久账本、worker 和工作台。旧任务保留原版本；跨目标/越界引用拒绝，未知调用不重发，BLOCKER 不删除。后端 1893 项、前端 188 项、类型/lint/26 页构建通过；四份旧冻结样本的新契约核对通过，旧 1.11 请求逐项未变。真实模型调用 0、新增费用 ¥0；供应商兼容性和语义准确性仍未验证，runtime_ready=false。下一步准备独立受限格式与语义验收，无需用户先改配置。

> 最新 [4M 引用目录审核离线原型](docs/development/PHASE_4_CITATION_PROTOTYPE.md)：新增只选择已有引用的候选格式，服务器确定目标和来源，跨目标/越界/重复索引拒绝，保留 BLOCKER 及原完整领域校验。四份旧冻结样本共 60 项引用只读验证通过，后端 1869 项、前端 186 项通过；零模型调用、零新增费用。仅完成纯本地原型，尚未接入任务/worker/工作台，现有流程仍为 1.11，供应商兼容性与真实审核准确性未验证。下一步以显式新版本接入动态目录/schema 的快照、字节预算及持久核对，不直接替换旧任务；runtime_ready=false。

> 此前 [4L 固定样本真实审核验收](docs/development/PHASE_4_SEMANTIC_ACCEPTANCE.md)：完成四组虚构 Audit 的冻结输入、单次执行凭据、预算及失败账本。真实请求均正常 stop，但 4/4 被格式或来源编号校验拒绝，语义结果 NOT_EVALUABLE，不能宣称误报改善。4 次请求估算 ¥0.0058458、预占 ¥0.0302446；停止本轮付费调用。后续补上目标不存在/来源编号不可用的安全位置诊断，仅离线与模拟验证、不回填旧收据。后端 1844 项、前端 186 项、类型/lint/26 页构建通过；无人审批准发布，runtime_ready=false。下一步先离线核对目标目录与输出约束，不靠重发、补引用或删发现制造通过。

> 此前 [4K 版本化审核运行规则与反例](docs/development/PHASE_4_RUNTIME_AUDIT.md)：显式 1.11 将程序规则固定在审核系统消息，并复制当前候选结尾引用供核对；来源不能覆盖规则，旧任务不升级。新增正常、点数无法用尽、角色权限卡关三组离线样本，同时检查误报与漏报。后端 1824 项、前端 185 项、类型/lint/26 页构建通过，零真实模型调用、零新增费用；尚未证明真实审核准确率改善，无人审批准发布，runtime_ready=false。下一步冻结独立留出样本和评价规则后进行有边界的真实语义验收，不能仅以少报问题视为成功。

> 此前 [4J 审核格式约束与程序规则核对](docs/development/PHASE_4_STRICT_AUDIT.md)：修复顶层 schema 并接入供应商结构约束，显式新任务 1.10 保留全部本地校验。真实 Audit 已能正常读取并通过来源编号校验，但 3 项意见含 1 BLOCKER，存在事实误报，烟测仍 FAILED；任务 COMPLETED 不代表人审通过。新增只读规则事实和实际结尾真相编号对照。后端 1800 项、前端 184 项、类型/lint/26 页构建通过。四轮共 8 次真实请求估算 ¥0.0102844，累计预占 ¥0.0616020；停止本轮付费调用，未批准发布，runtime_ready=false。下一步准备版本化审核运行上下文及独立语义验收集，不要求用户先换模型或改配置。

> 此前 [4I 简短审核与安全诊断](docs/development/PHASE_4_BOUNDED_AUDIT.md)：新增显式 1.6 的长度限制与完整性标记，未完成报告不接收。真实 Compiler 及固定小样本质量通过；Audit 本次正常结束、输出 341 tokens，但格式仍不合格，任务 BLOCKED。后续补上安全字段诊断，未追加付费验证。后端 1743 项、前端 178 项、类型/lint/26 页构建通过；2 次真实请求估算 ¥0.0017700，无批准发布，runtime_ready=false。

> 此前 [4H 审核来源编号](docs/development/PHASE_4_INDEXED_AUDIT.md)：新增显式 1.5，让 AI 选择目标自身的来源编号，服务端严格还原所选引用。后端 1700 项、前端 175 项及 26 页构建通过。真实 Compiler、来源核验和固定小样本质量通过；Audit 非正常结束，输出达到 4096 tokens，任务仍 BLOCKED，来源编号尚未通过真实验收。2 次真实请求估算 ¥0.0047386，未批准发布，runtime_ready=false。下一步处理审核长度与完整性，本轮不追加付费调用。

> 此前完成 [4G 固定正文与真实模型验收](docs/development/PHASE_4_FROZEN_TEXT_ACCEPTANCE.md)工程改进：1.4 由 AI 检查准备稿，服务器保持规则和正文原样。真实编译、来源核验和固定小样本质量通过，后续 Audit 来源字段格式失败，整链仍未通过且没有批准发布。后端 1672 项、前端 174 项及 26 页构建通过，本轮真实请求估算费用共 ¥0.0042334。

> 此前完成 [4F 冻结规则草案](docs/development/PHASE_4_FROZEN_RULE_PLAN.md)工程接线：准备人员提交带来源的规则草案，服务器固定费用、前置条件与权限，AI 只摘录指定原文。新任务、管理员工作台及版本隔离已接通；后端 1652 项、前端 172 项和隔离浏览器检查通过。本轮没有真实模型调用，草案仍需审核，新流程真实模型与完整 MVP 验收仍待完成。

> 此前完成[阶段 4E：规则与来源逐项核对](docs/development/PHASE_4_RULE_REVIEW.md)：审核页可并排核对动作／材料条件及其原文，标出可能重复的条件；新增响应摘要方便排查。三个历史编译失败样本已零调用复核，候选未修改。

> 此前完成[阶段 4D：按预算调查](docs/development/PHASE_4_BUDGETED_INVESTIGATION.md)。`script-package/1.2` 已接入每阶段共享调查点、固定动作成本与前置条件、授权材料奖励及结束门槛；调查点与 AI 费用分别记账。管理员的新 Authoring/Audit、人工审核、确认及独立发布链已工程接通，玩家沿 `/play/package-preview` 进入 `/play/package-play`，1.2 不可绕到旧 4A 演练。
>
> 新版离线与隔离浏览器链通过：虚构资料完成审核、发布、调查、受限 AI 问答和指定结尾；前端 163 项、隔离 PostgreSQL 11 项及 26 页构建通过。浏览器的 2 次编译/审核和 1 次角色调用均为模拟 SDK。另行三次真实新版 Compiler v5/v6/v7 通过通用结构/摘录校验后，均未满足固定合成烟测的精确材料前置预期，Audit 未调用，本轮付费提示迭代已停止；这不证明通用语义判断能力，也不能宣称 1.2 真实完整链通过。自然对白、完整票制评分、主持和语音仍未完成，`runtime_ready=false`。以下 3C、4B、4C 记录保留各自历史范围。

> 第一阶段默认角色对白模型为火山方舟 `doubao-seed-character-260628`。阿里百炼固定版
> `qwen3.7-flash-2026-07-15` 已列为显式手动备用及未来主持总结候选；同一局只冻结一家供应商，
> 禁止自动跨供应商切换。用户已在 2026-09-04 明确授权将本机付费开关打开；打开开关本身不扣费，只有实际模型请求才计费。百炼与火山均已用虚构资料通过直连和核心
> `FusionGameService` 纵向验证。火山首次直连的 HTTP 404 未重试，随后在确认模型已开通、配置一致后复测通过。商业正文没有发送，这些结果也不等于
> HTTP 页面、人工口吻或真实剧本整局通过。详见[第一阶段实测状态](docs/development/PHASE_1_MODEL_AND_BUDGET.md)。

> 当前开发入口为 `/play`。基座权限与角色上下文升级、离线测试方式和未完成能力见
> [Fusion 基座升级记录](backend/docs/FUSION_BASE_UPGRADE.md)。旧全本管理、模拟器和历史回放已限管理员；
> 当前单真人入口尚未接入语音、分阶段回忆及真实剧本专属规则，不能将下方历史功能介绍视为已完成验收。

> 管理员入口 `/admin/authoring-jobs` 提供受控编译、候选校验与模型审核任务。2026-09-06 已通过双角色、双阶段合成资料的真实 script-package/1.1 Compiler v4 → 来源核验 → 候选 → Audit v3 链路，Audit 五维完整、2 项发现、0 BLOCKER；此前失败记录保留。这只证明该小样本通过，不代表商业剧本或完整产品验收，详见[3C 真实验收记录](docs/development/PHASE_3_RULE_CONTRACT_REVIEW.md)。
>
> [阶段 3D](docs/development/PHASE_3_PUBLICATION_BINDING.md)已接入 `/admin/script-reviews` 的全量审核门禁、逐项模型意见处理、人工确认与独立登记发布，以及 `/play/package-preview` 的固定版本、固定角色开场预览。旧直接 PUBLISHED 路径已阻断；已发布候选后续审核变化后需新内容版本重新确认，已有开场保留原版本。该轮未调用付费模型、处理商业正文或批准真实候选；新增迁移尚未应用真实 PostgreSQL。
>
> 3D 当时后端 1085 项、前端 89 项测试通过，完整类型检查、修改文件 lint 与 24 页生产构建通过。隔离合成夹具的人工确认、独立发布、普通玩家固定角色开场、刷新与权限验收通过；管理员和玩家的 390px 页面无横向溢出，新增模型尝试为 0。
>
> 此前[阶段 4A](docs/development/PHASE_4_RULES_PREVIEW.md)新增 `/play/package-flow` 固定角色阶段演练：从开场显式开始、重新核验发布后，按顺序手动推进，并按阶段与公开证据条件解锁材料。仅本角色允许分享的已解锁材料可手动公开，保密材料禁止公开；旧开场不会升级。发布失效后保留已获内容，冻结新动作。该演练仍为 `runtime_ready=false`，没有 AI 互动、主持结算或完整整局。该轮后端 1195、前端 114 项测试及类型/lint 检查、25 页生产构建通过。隔离合成资料浏览器的显式创建、分享解锁、推进、刷新恢复、权限及 390px 无溢出验收通过，新增模型调用为 0；新迁移未应用真实 PostgreSQL。

> 此前[阶段 4B](docs/development/PHASE_4_TEXT_PLAY.md)已接线：从开场点击“与AI角色开始文字试玩”，显式创建独立的 `/play/package-play` 会话；旧开场和阶段演练保持不变。各角色共享本局公开记录与预算，AI 只选择自己获准公开的材料，核验后按原文回答并正式分享，保密材料仍隔离。玩家可手动推进，到末阶段点击结束后仅揭晓本版指定结尾和真相；AI 不可用时也可走到结尾。接口见[文字试玩协议](docs/contracts/package-play.md)。
>
> 后续 4C 已通过一次真实火山模型的虚构材料选择→公开解锁→指定结尾→重读，估算费用 ¥0.000616；真实本机 PostgreSQL 随机隔离 schema 中 j0→o5 迁移及并发/恢复 9 项通过，临时 schema 已核验清理，未迁移主 schema。4C 当时后端离线检查 1443 passed（363 条存量警告，12.41 秒）。这些结果不代表商业本、自然对白或真实人审发布通过；`runtime_ready=false`，商业本胜负规则、自然对白、主持和语音仍待接线。4C 同时复核了《孽岛疑云》来源与编辑规则差异，详见阶段 4C 记录。
>
> 4B 历史验收状态：后端 1378 passed（363 条已有弃用警告，12.35 秒；新增 183 项已计入），前端八套 152 passed，完整 TypeScript 无诊断，CommonJS 测试文件类型配置下的修改文件 lint 通过，预检退出码 0、自测 16 项通过。隔离合成夹具浏览器通过从旧开场显式创建、AI 材料原文问答与正式分享、真人分享后条件解锁、三阶段推进、指定真相揭晓、刷新/重进、权限与 390px 无溢出验收；最终 1 份试玩、6 条事件、1 次模拟 SDK 调用、0 次真实模型调用，旧开场保持不变，浏览器空间 60 已关闭，临时 API 18017/前端 13017 已正常退出且端口关闭。4B 当时最终生产构建通过，共 26 页，生产 API 配置仍为 `http://127.0.0.1:8010`。

## 🚀 快速启动

当前这台 Mac 已准备好不依赖 Docker 的专用 PostgreSQL 与 Redis。第一次开发请优先照着[小白本地启动说明](docs/development/LOCAL_DEVELOPMENT.md)在 VS Code 中运行，不要复制模板覆盖已有 `.env`。

```bash
test -e .env || cp .env.example .env
# 编辑 .env：至少设置 SECRET_KEY、DB_PASSWORD；模型 Key 按所选官方供应商填写
docker compose up -d --build
```

打开 `http://localhost`，注册登录后进入 `/play`。新候选的管理员准备入口为 `/admin/authoring-jobs` 和 `/admin/script-reviews`：先完成人工审核与模型意见处理，再确认本版内容、单独登记发布版本。玩家从 `/play/package-preview` 阅读固定开场，点击“与AI角色开始文字试玩”显式进入 `/play/package-play`；1.2 包在这里按共享调查点搜证、问答及揭晓指定结尾。仅 1.0/1.1 包支持另行进入 `/play/package-flow` 检查后续阶段。当前仍非商业本完整 MVP。旧 `/script-manager` 及基础质检入口不能绕过新门禁直接 PUBLISHED，AI 生成内容不会自动发布。火山引擎部署见 `deploy/ECS.md`。

## 🌟 项目特色

- 🤖 **一名真人 + AI 配角** - 真人可选择包括凶手在内的角色，其他角色由 AI 补齐
- 🎯 **完整游戏流程** - 包含背景介绍、自我介绍、搜证、调查、讨论、投票、揭晓真相等完整阶段
- 🌐 **实时同步体验** - 使用WebSocket实现实时游戏状态同步
- 💻 **现代化界面** - 响应式Web界面，支持移动端访问
- 🧠 **智能推理引擎** - 基于大语言模型的AI推理能力
- 🔐 **权限隔离** - 私本、凶手秘密、证据和系统真相按角色过滤
- 💾 **事件恢复** - 阶段、行动、消息和模型用量持久化，支持断线继续
- ✏️ **AI剧本编辑** - 支持AI生成和编辑剧本内容

## 📸 屏幕截图

<div style="display: flex; flex-direction: column; gap: 20px;">
  <div style="display: flex; justify-content: space-between; gap: 20px;">
    <img src="screenshot/game_room.png" alt="游戏房间界面" style="width: 48%;">
    <img src="screenshot/scripts_center.png" alt="剧本中心界面" style="width: 48%;">
  </div>
  <div style="display: flex; justify-content: space-between; gap: 20px;">
    <img src="screenshot/edit_script.png" alt="剧本编辑界面" style="width: 48%;">
    <img src="screenshot/script_create.png" alt="剧本创建界面" style="width: 48%;">
  </div>
</div>

## 🚀 核心功能

### AI剧本生成与编辑
- 自动生成完整剧本杀内容，包括背景故事、角色设定、证据设计和场景描述
- 支持自然语言指令编辑剧本，如"添加一个善良的角色"或"修改凶手的动机"
- 可以随时调整剧本内容，AI会自动适应修改并保持逻辑一致性

### AI剧情推演
- 旧模式支持全AI模拟；当前MVP是一名真人＋其余AI角色，使用/play
- 8个游戏阶段：背景介绍、自我介绍、搜证、调查取证、自由讨论、投票表决、真相揭晓、游戏结束
- 每个AI角色都有独特性格和秘密，能够进行自然对话和推理

### TTS语音合成

- MiniMax API: 支持多种语言和声音
- [CosyVoice 2.0](https://github.com/journey-ad/CosyVoice2-Ex): 本地部署的中文语音合成服务

### 文生图服务
支持多种图像生成服务：
- ComfyUI: 本地部署的稳定扩散图像生成平台
- MiniMax API: 云端图像生成服务

### 大语言模型(LLM)支持

当前 Fusion 角色链路只接受经过白名单和离线契约测试的火山方舟或阿里百炼配置，不能把“OpenAI 兼容”理解为可任意更换供应商。默认使用火山方舟 Character；切换百炼只能新开一局并显式选择，不能在失败后自动发送到另一家。


## 📁 项目结构

```
jubensha/
├── backend/     # 后端服务
│   ├── src/     # 核心源码
│   ├── docs/    # 文档资料
│   └── tests/   # 测试代码
└── frontend/    # 前端界面
    ├── src/     # 前端源码
    └── public/  # 静态资源
```

## 🚀 快速开始

### 方式一：Docker 一键部署（推荐）

只需安装 Docker，无需本地配置 Python/Node/PostgreSQL 环境：

1. **配置环境变量**

   ```bash
   test -e .env || cp .env.example .env
   # 编辑 .env，至少填入 OPENAI_API_KEY 等 AI 服务密钥
   ```

2. **构建并启动**

   ```bash
   docker compose up -d --build
   ```

3. **访问**

   - 前端界面：http://localhost:8009
   - 后端接口：http://localhost:8010/docs

说明：
- 首次启动会自动创建数据库表，无需手动迁移
- 文件存储默认使用本地目录模式（`FILE_STORAGE=dir`），数据保存在 Docker volume 中
- 部署到远程服务器时，把 `.env` 中的 `NEXT_PUBLIC_API_URL` 改为 `http://<服务器IP或域名>:8010` 后重新构建前端镜像
- 停止服务：`docker compose down`。不要附加清卷选项；删除数据库卷必须先核验备份并单独确认。

### 方式二：本地开发

### 环境准备

#### 后端服务
- Python 3.13+
- uv (Python包管理器)
- PostgreSQL数据库
- 至少一个AI服务API密钥（OpenAI兼容模型、TTS服务、图像生成服务等）

#### 前端界面
- Node.js >=20.9 且 <25（本机使用 Node 24）
- npm 10–11（本机使用 npm 11）

### 配置与运行

本机环境已经准备完成，请优先使用[小白本地启动说明](docs/development/LOCAL_DEVELOPMENT.md)里的 VS Code 任务。下面是给熟悉终端的开发者看的等价说明；`.env` 只放仓库根目录，不能在 `backend` 或 `frontend` 内重复复制。

#### 1. 后端服务配置与运行

1. **进入后端目录**
   ```bash
   cd backend
   ```

2. **安装依赖**
   ```bash
   uv sync
   ```

3. **配置环境变量**

   使用仓库根目录已有的受保护 `.env`。Fusion 默认角色模型读取 `ARK_API_KEY`，百炼备用读取 `DASHSCOPE_API_KEY`；不要把密钥放进前端变量或文档。

4. **初始化数据库**
   ```bash
   backend/.venv/bin/python -m alembic -c backend/src/db/migrations/alembic.ini upgrade head
   ```

   上述命令从仓库根目录运行；VS Code 后端任务会自动完成这一步。

5. **运行后端服务**
   ```bash
   uv run python main.py
   ```

#### 2. 前端界面配置与运行

1. **进入前端目录**
   ```bash
   cd frontend
   ```

2. **安装依赖**
   ```bash
   npm install
   ```

3. **配置环境变量**

   本地地址已经在仓库根 `.env` 和 VS Code 任务中统一为后端 `127.0.0.1:8010`；不要在 `frontend` 下另建一份 `.env`。

4. **运行前端开发服务器**
   ```bash
   npm run dev
   ```

5. **构建生产版本**
   ```bash
   npm run build
   npm run start
   ```

## 🎮 游戏流程

1. **背景介绍阶段** - 系统叙述案件背景故事
2. **自我介绍阶段** - AI角色依次介绍自己的身份背景
3. **搜证阶段** - AI角色搜查场景发现证据
4. **调查取证阶段** - AI角色互相提问推进调查
5. **自由讨论阶段** - AI角色分享推理和反驳观点
6. **投票表决阶段** - AI角色投票指认凶手
7. **真相揭晓阶段** - 公布案件真相和游戏结果

## 🛠 技术架构

### 后端技术栈
- **FastAPI** - 现代、快速(高性能)的Web框架
- **LangChain** - 构建AI应用的框架
- **OpenAI/兼容模型** - 大语言模型支持
- **PostgreSQL** - 关系型数据库
- **WebSocket** - 实时双向通信
- **MinIO** - 对象存储服务

### 前端技术栈
- **Next.js 16** - React框架
- **React 19** - 前端UI库
- **TypeScript** - JavaScript超集
- **Tailwind CSS** - CSS框架
- **Zustand** - 状态管理
- **Radix UI** - 无样式组件库

## 📝 开发计划

未来的优化方向包括：

### 剧本生成和编辑功能
- 增强AI剧本生成能力，支持更复杂的剧情结构
- 优化剧本编辑体验，提供更直观的编辑界面
- 支持更多类型的剧本模板和自定义选项
- 提升AI对剧本逻辑一致性的维护能力

### UI交互优化
- 改进用户界面设计，提升用户体验
- 优化移动端适配和交互效果
- 增强游戏过程中的可视化反馈
- 提供更流畅的操作流程和动画效果

### 多语言支持
- 支持国际化和多语言切换
- 支持中英文等多语言界面

## 📄 许可证

本项目采用 MIT 许可证 - 参见 [LICENSE](LICENSE) 文件了解详细信息。
您可以自由地：
- 使用本软件用于商业目的
- 修改和分发本软件
- 在您的项目中使用本软件的部分或全部代码

## 🤝 贡献

欢迎提交Issue和Pull Request来改进项目。
