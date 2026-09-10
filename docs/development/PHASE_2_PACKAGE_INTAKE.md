# 第二阶段：候选包与导入记录

日期：2026-09-05。基于 `343689a` 工作树，保留此前全部未提交改动。本工作包实现候选包接收、确定性校验、版本快照及导入记录；没有运行真实数据库迁移、启动服务、调用模型或读取商业素材。

## 已实现的边界

- 严格契约 `script-package/1.0`：[Pydantic 定义](../../backend/src/schemas/script_package.py)、[导出的 JSON Schema](../contracts/script-package.v1.schema.json)。拒绝未知字段/枚举、类型强转、调用方提供的发布/审批字段和任意规则表达式。
- 来源清单：原件与规范化文件分别声明 SHA-256，后者绑定本包原件；路径只能是相对定位符，材料须有来源页/锚点。校验器不读取路径，也不验证声明的哈希与磁盘文件是否相符。
- 阶段材料：角色稳定 ID、公共/私有知识、FACT/CLAIM/INFERENCE 类型、披露规则、阶段下限及已公开线索的 AND 前置条件。真相单独存放，只能标记 `SYSTEM_TRUTH`，没有自动变成角色知识的凶手标签。
- 确定性检查：实体 ID 唯一、来源和原件关联、页码范围、角色数量与归属、初始私有材料覆盖、阶段引用/无环/可达性、线索依赖/可公开性、结算阶段和真相引用。长依赖链采用拓扑检查；报告最多保存 200 个问题，每项最多 3 个来源定位，并标明截断。
- 包哈希绑定原始 JSON 值。固定序列化为 `ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False` 后取 UTF-8 SHA-256，版本名为 `python-json-sort-utf8/1`；不声称实现 RFC 8785/JCS。对象键顺序不影响哈希，数组顺序、文本、显式默认字段和版本变化会影响哈希。
- 完整请求上限 2 MiB、最大 JSON 深度 40、最多 200000 个节点；拒绝重复 JSON 字段、非法数字、无效 UTF-8。包 schema 错误转为脱敏报告；请求格式错误不经过会回显输入的旧全局验证处理器。

v1 只表达线性阶段与上述线索条件，不代表《孽岛疑云》的完整机制已建模。不支持的条件/资源/分支字段会阻断，不能写进自由文本再当作可执行规则。公开介绍、知识类型、披露时机和结算说明的语义是否正确仍须来源核验及 Audit；结构校验不能判断正文是否剧透。

## 接口与事务

以下路由已注册到现有 FastAPI 应用；统一管理员路径策略和各处理函数均检查活跃管理员。

| 接口 | 输入与结果 |
| --- | --- |
| `POST /api/admin/fusion/script-imports` | `{ "idempotency_key": "...", "package": { ... } }`；返回任务与校验报告。结构/引用/权限校验失败仍返回 HTTP 200 的 `BLOCKED` 记录，调用方必须检查 `data.status` 和 `data.report.valid` |
| `GET /api/admin/fusion/script-imports/{job_id}` | 查询已落库的任务、报告和对应候选版本 ID；不返回正文 |
| `GET /api/admin/fusion/script-packages/{version_id}` | 管理员读取候选完整包及哈希，供后续审核；不是玩家接口或公开下载地址 |

非法请求返回 422，超长请求 413，内容版本/幂等冲突及完整性异常 409，不存在的记录 404。读取错误及 schema 错误不回显私本正文。通用全局验证处理器的历史行为未在本轮全面修改。

`script_package_versions` 独立于可变 `scripts`。同一 `(script_key, content_version)` 只能绑定一个内容哈希；改内容必须改版本。同一包使用新任务键可复用候选快照，同一提交人重复使用相同键和包则返回原任务；换包复用键拒绝。

`script_import_jobs` 存提交人、幂等键、输入哈希、校验器版本、步骤、状态、报告及报告哈希、创建/更新时间。当前是同步的 `DETERMINISTIC_VALIDATION` 步骤：合法包记录 `SUCCEEDED` 并关联候选快照，非法包记录 `BLOCKED` 且无快照。非法原始正文不写入任务表，修正后使用新幂等键提交。尚无 Compiler/Audit worker、队列、领取租约、取消或后台恢复。

候选与任务在同一 savepoint 内写入，仓储/服务只 `flush()`，最终 `commit/rollback` 归请求级 session scope。数据库唯一约束裁决冲突，只对本地唯一冲突作一次有限重试；无模型请求或外部副作用。SQLite 已验证插入失败及调用方回滚不留下半份数据；真实 PostgreSQL 并发仍须单独验收。

当前 API 不提供修改/删除快照，ORM 事件拒绝更新/删除，读取时复核正文/报告与关联哈希。管理员直接执行 SQL 能绕过 ORM 保护，哈希也不是签名；这不是数据库权限管理员不可篡改的审计系统。

## 发布与运行时仍未接通

所有导入结果固定 `publication_ready=false`，待办门禁为来源文件核验、运行时兼容检查、Audit、人审。`SUCCEEDED` 只代表此确定性步骤完成。

此服务不创建或修改 `scripts`/角色/线索/对局表，没有候选发布方法，也不将候选版本 ID 当作旧 `script_id`。现有旧管理员发布入口依然只有基础校验；本轮没有完成全面的人审发布门禁、已发布旧实体冻结或对局版本绑定。

新增迁移 `j0d1e2f3a4b5` 接在 `i9c0d1e2f3a4` 后。本轮只在 SQLite 内存库执行此新迁移上下行并对比 ORM 元数据，另生成 PostgreSQL SQL 验证编译。**真实数据库仍停留在先前已验收版本，未应用本迁移。** 项目原有启动 `create_all()` 机制也可能在下次启动时建出新表，但不能据此认为 Alembic 已升级。

## 验证与下一步

离线测试通过 `backend/scripts/test_fusion_security.py` 的禁 dotenv/禁网络入口运行；fixture 为虚构展馆，不含商业剧本或真实玩家内容。

| 验证 | 结果 |
| --- | --- |
| `backend/.venv/bin/python scripts/dev_preflight.py` | 退出 0，不读 `.env` 内容 |
| `backend/.venv/bin/python -m unittest discover -s scripts -p 'test_dev_preflight.py'` | 16 tests，OK |
| `backend/.venv/bin/python backend/scripts/test_fusion_security.py -k script_packages --disable-warnings` | 103 passed，覆盖非法 schema/引用/权限、来源路径、哈希变化、去重、唯一冲突恢复、事务回滚、旧快照保护、HTTP 管理员权限、错误脱敏及迁移 |
| `backend/.venv/bin/python backend/scripts/test_fusion_security.py --disable-warnings` | 462 passed（原有 359 + 新增 103），退出 0 |
| 前端 `node --test tests/fusion-evidence-panel.test.cjs` | 9 passed |
| 前端 `node node_modules/typescript/bin/tsc --noEmit --incremental false` | 退出 0 |
| `git diff --check` | 退出 0 |

测试中的唯一冲突通过真实 SQLite 约束加模拟过期查询覆盖服务恢复分支；没有把它称为双连接 PostgreSQL 并发证据。原有 SQLAlchemy/Pydantic 弃用告警保留。

下一步实现来源核验、Audit 报告与人审批准绑定同一包/报告哈希，再统一发布门禁和旧编辑路径保护；之后为选定的原本规则扩展契约、接入不可变版本与阶段知识。真实库迁移及商业输入冻结仍须在对应操作时核对目标和授权。
