# 第一阶段离线验证记录

最近复验日期：2026-09-04（Asia/Shanghai）  
基线 revision：`343689a837a5c53c8ae4c5ee49696b614b5b4cc5`（`main`）  
工作树：`dirty`。验证发生在用户已有未提交改动的工作区；本记录不能把全部差异都归因于本阶段。

## 验证边界

- 使用虚构测试资料和 fake client。
- 安全测试入口清除模型、费用和密钥相关环境变量，并禁止网络连接。
- 没有启动应用服务，没有连接 PostgreSQL、Redis 或真实模型，没有发生付费调用。
- 本记录证明离线代码行为，不证明真实数据库并发、真实供应商计费或商业剧本质量。PostgreSQL 与真实模型的独立证据分别见[预算并发集成测试](POSTGRES_BUDGET_INTEGRATION.md)、[真实模型最小冒烟](REAL_API_SMOKE.md)和[核心服务纵向验收](LIVE_VERTICAL_SMOKE.md)。

## 结果

| 检查 | 结果 | 退出码 |
|---|---:|---:|
| `backend/.venv/bin/python backend/scripts/test_fusion_security.py` | 359 passed | 0 |
| `backend/.venv/bin/python -m pytest tests/test_llm_service_tools.py -q`（在 `backend`） | 5 passed | 0 |
| `backend/.venv/bin/python -m pyright src/fusion/providers.py src/fusion/agents.py src/fusion/budget.py`（在 `backend`） | 0 errors | 0 |
| `backend/.venv/bin/python -m unittest discover -s scripts -p 'test_dev_preflight.py'` | 16 passed | 0 |
| `node --test tests/fusion-evidence-panel.test.cjs`（在 `frontend`） | 9 passed | 0 |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false`（在 `frontend`） | 无类型错误 | 0 |
| `backend/.venv/bin/python scripts/dev_preflight.py` | 全部开发环境元数据检查通过 | 0 |
| `git diff --check` | 无空白错误 | 0 |

此前预检的退出码2来自 `debugpy`、迁移后失效的 Python 启动脚本、Docker/VS Code CLI 等准备项。现已安装 debugpy、重建虚拟环境入口、接通 VS Code CLI，并以专用 PostgreSQL/Redis 代替本机 Docker，最新预检退出码为0。生产 Docker/托管服务和备份恢复仍须单独验收。

## 已验证的第一阶段行为

- 火山方舟 Character 和百炼 Qwen 固定版各自使用独立的 Key、地址、模型、思考参数、输出上限字段和价格；默认走火山，百炼只能新局手动选择，两家之间不会自动转发。
- Fusion 付费开关未开启、Key 缺失或仍为 `CHANGE_ME` 占位值、渠道不受支持或费用字段不完整时，不创建真实 Fusion 模型客户端或不进入模型调用。
- 普通角色对白显式发送非思考配置；内部思考字段和思考标签不能成为公开台词。
- 同一局先计算历史用量、在途预占和本次最坏预占，再决定是否调用。
- SDK 自动重试关闭；应用尝试次数纳入预占。
- 只对暂时性网络／服务错误有限重试；格式错误、被截断、被过滤或响应模型不匹配时不公开，也不重新发送同一份正文。
- 百炼的输出预占覆盖其文档所述输出上限浮动；供应商 usage 中的空明细不会误判为矛盾账单。
- 成功、预算阻断、取消、未知用量、过期、迟到和超预占用量都有受控收据状态。
- 损坏收据、无法核验的旧账和冲突的供应商 usage 会失败关闭，不按零费用继续。
- 迟到台词不会覆盖新结果；系统计费收据不会进入玩家事件流。
- 离线测试未使用商业剧本正文。

## 本离线记录不覆盖，以及另项证据状态

- 两个真实 PostgreSQL 连接争用同一会话的预算行锁已由专用入口另行验证通过；事务失败、服务重启和租约恢复仍未做真实 PostgreSQL 验收。
- 百炼固定版与默认火山 Character 的单次直连冒烟和 `FusionGameService` 核心纵向链均已用虚构资料另行通过。火山首次直连 HTTP 404 未重试，后续在确认控制台模型已开通、Key／地域／地址／模型一致后复测通过，首次失败仍作为历史证据保留。
- 已记录两家请求的整体耗时、用量和本地费用估算，但未记录流式首包时间、p50/p95、人工口吻结论或云厂商账单；本地估算不能写成实际扣费。
- 商业正文云端处理许可已经确认；尚未验证的是版本化知识包、阶段回忆边界，以及《孽岛疑云》的导入完整性、专属机制和整局可玩性。本轮所有真实请求仍只使用内置虚构资料，没有发送商业正文。
