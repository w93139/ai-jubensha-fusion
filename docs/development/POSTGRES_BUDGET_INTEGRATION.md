# PostgreSQL 预算并发集成测试

这个入口只验证一件事：两个数据库连接同时为同一局预占模型预算时，
PostgreSQL 行锁能否保证最多只有一个请求获得调用资格。

它不读取项目 `.env`，不使用商业剧本，不创建真实模型客户端，也不会产生 API 费用。

## 2026-09-04 实测结果

- 隔离环境为 Postgres.app 2.9.6 内含的 PostgreSQL 17.11；目标固定为 `fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox`。
- 专用入口结果为 `1 passed`：两个真实连接都进入同一局的 PostgreSQL 行锁等待，最终只有一个请求完成 20 Token 预占，另一个以 `TOKEN_BUDGET` 阻断。
- 测试只用了虚构数据和阻塞 fake client；没有创建云模型客户端，也没有 API 费用。
- 随机 schema 在测试结束时删除。修复纵向验收中的地址格式检查后，预算锁专用入口再次复跑仍为 `1 passed`。

## 固定的安全目标

为避免小白把测试命令误指向现有库，入口只接受下列组合：

| 项目 | 必须是 |
|---|---|
| 主机 | `127.0.0.1` |
| 端口 | `55432` |
| 数据库 | `fusion_pg_it_sandbox` |
| 用户 | `fusion_it` |
| 确认口令 | `CREATE_EPHEMERAL_SCHEMA` |

主机、端口、库名、用户、协议或确认口令任一不符，程序都会在连接前拒绝执行。
不支持通过“强制开关”绕过这些限制。

## 数据库准备好后运行

在仓库根目录执行：

```sh
FUSION_POSTGRES_TEST_CONFIRM=CREATE_EPHEMERAL_SCHEMA \
FUSION_POSTGRES_TEST_DATABASE_URL='postgresql+psycopg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox' \
backend/.venv/bin/python backend/scripts/test_fusion_postgres_budget.py
```

只运行上述专用入口，不要直接对 `backend/tests/postgres_integration`执行全局 `pytest`；
历史 `tests/conftest.py` 会带入应用初始化，而专用入口会明确隔离它。

如果临时测试用户确实设置了密码，将密码放在 URL 的 `fusion_it:` 与 `@` 之间；
不要把真实密码写入本文档、示例文件或 Git。

## 测试实际会做什么

1. 第二次校验连接后的库名，防止 URL 与服务端身份不一致。
2. 在专用测试库内创建 `fusion_budget_it_`开头的随机 schema。
3. 只向该 schema 写入虚构角色、虚构线索和一个测试局。
4. 让两个独立连接真实等待同一条 `game_sessions` 行锁。
5. 确认只有一个连接通过 20 Token 预算预占，另一个被 `TOKEN_BUDGET` 拦截。
6. 测试结束后只删除本次创建的随机 schema。

测试进程会强制 `ENABLE_PAID_MODEL_CALLS=false`，清除模型密钥的进程变量，
并使用一个会阻塞的虚假模型回复来替代云 API。

## 这项测试不代表什么

- 不验证 Alembic 迁移链或旧数据恢复。
- 不验证事务失败、服务重启或租约过期后的真实 PostgreSQL 恢复。
- 不验证真实模型质量、价格或商业正文上云。
- 不证明应用已经可以上线或完成整局。
- 不会自动安装、启动或停止 PostgreSQL。
