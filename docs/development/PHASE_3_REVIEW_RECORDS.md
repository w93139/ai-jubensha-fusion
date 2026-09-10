# 第三阶段 B｜来源契约与审核记录

2026-09-05 完成当前工作包，依据指定技术栈手册 V2.1、既有导入契约与第三阶段 A 的实测边界。当前实现的是来源契约与人工审核记录，不是完整自动 Audit 或发布流程。

## 本轮目标

让候选材料完整表达多个原件与编辑补充，并打通“选择不可变候选 → 当前来源核验 → 登记人工审核发现 → 记录处理意见 → 查询历史”的管理员流程。这里实现审核工作的记录和校验基础，不生成语义 Audit 结论，不批准发布、不绑定游戏运行时，也不修改商业正文。

沿用 FastAPI、Pydantic、SQLAlchemy/PostgreSQL 和 Next.js，不添加库、模型或后台框架。候选 1.0 保持兼容及原哈希，新来源字段使用独立 1.1；数据库只增加审核报告与处理记录两张表，不覆盖候选和导入历史。只编写迁移并做隔离验收，本轮不迁移已有数据库。

## 数据与权限

- 来源 1.1：规范化材料列出全部原件 ID；编辑补充独立 kind，并填写来源说明；以冻结清单核对全部归属。旧 1.0 无法表达的关系继续阻断。
- 人工报告绑定候选哈希、来源快照哈希、实际核验报告、校验器版本和提交人。问题包含类别、严重程度、具体实体和该实体已声明的来源定位；格式正确不等于语义审核正确。
- 处理记录只追加，状态 OPEN / ACKNOWLEDGED / DISMISSED。BLOCKER 不能仅“已知悉”而关闭；误报可写明理由后排除，实际改本必须导入新版本。旧报告不随新版本迁移。
- 提交使用操作者＋幂等键及输入哈希。处理记录同时检查审核哈希、候选哈希和单调 revision，拒绝覆盖其他管理员的新意见；重试不重复落库。
- 所有接口限 active admin，正文和意见只在私有数据库保存，不进入公开日志/玩家接口。所有结果 `publication_ready=false`，不把人工处理误当成最终发布批准。
- 包内容和审核内容都视为非可信文本；来源引用仅使用声明的 ID 与定位，不执行指令或 URL。界面不渲染 HTML/Markdown。

## 接口与页面

- `GET /api/admin/fusion/review-candidates`：最多最近 100 个候选摘要。
- `GET /api/admin/fusion/script-packages/{version_id}/review`：该候选最近 20 份报告及各自处理历史，附候选实体/来源定位元数据，避免填写报告时猜测标识。更早报告仍保留在数据库，完整报告分页后续补齐。
- `POST /api/admin/fusion/script-packages/{version_id}/audits`：显式提交人工结构化报告；先复核当前文件和候选来源再保存。
- `POST /api/admin/fusion/script-audits/{audit_id}/dispositions`：追加单条问题处理；旧 revision 返回 409。
- `/admin/script-reviews`：选择候选/来源、录入报告、查看问题与历史处理；这是正式管理切片。报告 JSON 录入为本阶段最小工具；后续 Compiler/模型 Audit 提供候选与审核建议，模型建议与人工报告独立保存，不替代人工审核或最终批准。

## 验证与恢复

使用虚构包和内存 SQLite 验证兼容、幂等、事务回滚、非法引用、旧版本/旧 revision、不可变快照、权限和错误脱敏；新增迁移做内存上下行及 PostgreSQL SQL 编译。使用实际页面＋真实路由＋隔离虚构数据库做浏览器交互验收，测试结束关闭本轮临时服务。类型/代码/生产构建检查与已有安全测试一起运行。

未增加模型调用契约，因此按手册纯工程阶段不重复付费冒烟。报告提交成功只证明记录与引用完整性；不作为自动语义 Audit 或真实整局验收证据。内容哈希保护应用层误改，不宣称能防拥有数据库写权限的操作者伪造；发布门禁后续仍须复核当前来源与审批身份。

## 实现与兼容记录

- 旧 `ScriptPackage` 和旧 [v1.0 JSON Schema](../contracts/script-package.v1.schema.json) 未修改语义；新增独立 `ScriptPackageV11`、`SourceFileV11`、`parse_script_package()` 与 [v1.1 JSON Schema](../contracts/script-package.v1.1.schema.json)。旧包不会自动升级或改写哈希。
- 新来源必填 `original_source_ids`，规范化材料至少一份且不重复，原件和编辑补充必须为空；编辑补充独立 `kind=supplement` 且必填 `provenance_note`。归属集合必须与冻结清单精确匹配，后台参考不能伪装成原件或编辑补充。
- 确定性校验器 `script-package-validator/1.1`、文件核验器 `source-verifier/1.1`。历史 1.0 核验报告可读；新审核记录要求当前核验器的实际落盘收据和对应候选哈希。
- 新表为 `script_audit_records`、`script_finding_dispositions`；迁移 `k1d2e3f4a5b6` 依赖 `j0d1e2f3a4b5`。两张表与既有候选/导入记录相互独立，不修改 `scripts`、现有会话或原任务 `pending_gates`。
- 审核输入最多 512 KiB，单条处理最多 16 KiB；说明最多 4,000 字，一份报告最多 200 个问题、1,000 条追加处理。报告和处理都保留提交人，处理版本自 1 单调增加。
- 请求格式：[审核提交](../contracts/script-audit-submit.v1.schema.json)、[问题处理](../contracts/script-finding-disposition.v1.schema.json)。这些 JSON Schema 描述格式；合法实体、声明来源、当前哈希与 revision 仍由服务校验。
- 错误区分 401/403 权限、404 缺失、409 版本/并发/完整性冲突、413 超限、422 格式/实体/处理语义不符。HTTP 手动限额和解析防止默认验证器回显正文；返回禁止缓存。
- UI 增加用户菜单“剧本审核记录”，按需展开候选元数据与完整问题处理历史。旧版本报告被隐藏；请求取消和同次失败重试幂等键复用已覆盖。整页退出后的草稿/未完成请求恢复尚未实现。

## 实测结果

在当前未提交工作区运行；无新增依赖、模型调用、真实数据库迁移或商业候选导入。

| 验证 | 命令 / 范围 | 实际结果 |
|---|---|---|
| 后端 | `backend/.venv/bin/python backend/scripts/test_fusion_security.py --disable-warnings` | 589 passed；363 条存量弃用警告；本轮新增来源 34 项、审核 45 项 |
| 预检 | `backend/.venv/bin/python scripts/dev_preflight.py`；`backend/.venv/bin/python -m unittest discover -s scripts -p 'test_dev_preflight.py'` | 开发预检退出 0；16 tests 通过 |
| 前端组件/服务 | `node --test tests/fusion-evidence-panel.test.cjs tests/source-bundle-panel.test.cjs tests/script-review-panel.test.cjs` | 32 passed，本轮新增 14 项 |
| 类型和代码 | `tsc --noEmit --incremental false`；修改的页面、组件、service、type 定向 ESLint `--max-warnings 0` | 退出 0，无新增错误/警告 |
| 生产构建 | `NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 NEXT_TELEMETRY_DISABLED=1 npm run build` | 退出 0，22 页生成完成，含 `/admin/script-reviews`；未部署 |
| 数据层 | 虚构 SQLite 事务、原始 SQL 篡改、SAVEPOINT/UNIQUE 冲突；迁移上下行及 ORM 比对、PostgreSQL 离线 SQL 编译 | 均通过，不等于真实 PostgreSQL 双连接测试 |
| 浏览器 | 真实 Next 页面＋真实 FastAPI 路由＋内存 SQLite/虚构文件/模拟管理员 | 未登录跳转；读取候选与同剧本来源；显式提交报告；追加处理；整页刷新后保留记录；展开历史；390px 无横向溢出；未批准发布 |

浏览器记录全部为虚构测试数据，在隔离内存数据库内保存；退出后随测试环境清理。来源材料正文未进入审核报告/公开 Git，本轮没有代填真实剧本审核或审批结论。测试浏览器空间、临时 3001/8011 服务已关闭；原有 PostgreSQL/Redis 保留。

更新后的来源核验器重新检查真实快照 `2aaeef083b923a3435b9cbd7070e0f9ac015b4d90b4bd75bac70f13e39a96a89`：483 文件、0 问题，`publication_ready=false`。新报告 `b38c5db39cea4251777a99b23e2e1fd66cf801abe824c1c912e81d941b5da54d` 保存在仓库外原来源存储；文件和快照标识未修改。这只是文件一致性，原有缺页、OCR 人工核对和聚合来源关系问题继续保留。`git diff --check` 与 VS Code 任务 JSON 解析通过。

## 下一阶段接入点

来源 1.1 与审核记录接口可复用；实际补齐聚合修订材料的逐项原件关系仍需 Compiler 的来源定位工作，不能仅由新字段存在宣称真实本已经编译完成。

下一工作包是可恢复 Compiler/Audit 自动流程及人审发布门禁：审核自动任务身份/模型配置需独立契约与真实模型验收，最终人工批准需绑定候选、来源、Audit 和运行规则版本。发布前重新核验当前文件，并关闭旧发布/编辑入口绕过版本门禁的路径。阶段知识、条件线索、回忆、提示、结算与语音继续按原本另行实现。
