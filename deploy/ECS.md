# 火山引擎 ECS 部署

1. 准备安装了 Docker Engine 与 Compose 插件的 ECS，安全组只开放 80/443；数据库和 Redis 端口不要对公网开放。
2. 复制 `.env.example` 为 `.env`，生成强随机 `SECRET_KEY` 和数据库密码，填入服务端 `DEEPSEEK_API_KEY`。有域名时将 `SITE_ADDRESS` 设为域名并解析到 ECS 公网 IP，Caddy 会自动申请 HTTPS 证书。
3. 执行 `docker compose up -d --build`。首次启动自动运行 Alembic 迁移。
4. 使用 `docker compose ps` 和 `curl https://你的域名/health` 验收；用 `docker compose logs -f backend` 排错。
5. PostgreSQL 数据卷应位于独立云盘。用 cron 每日执行 `deploy/backup-postgres.sh` 并将备份同步到对象存储，定期演练恢复。

恢复示例：`docker compose exec -T db pg_restore -U jubensha -d jubensha --clean --if-exists < backup.dump`。

