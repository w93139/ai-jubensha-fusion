# PostgreSQL + 单次真实模型纵向验收

这是第一阶段的最小真实核心链路验收：

`内置虚构游戏 → 角色授权知识投影 → PostgreSQL 预占费用 → 一次真实模型 → AI_TURN_RECEIPT 落库 → 删除随机 schema`

这条命令调用的是核心 `FusionGameService` (`perform_action` + 角色回复服务)，**不是** HTTP `/api/fusion` 传输层端到端测试。路由、认证和 HTTP 传输层的当前证据仍是离线回归测试，不能由本验收替代。

它不读取《孽岛疑云》、任何商业剧本或用户指定文件，也不接受 prompt、剧本、session 或输出路径参数。程序只会读取固定的本地 Prompt 模板和所选 `.env` 配置。数据库内会短暂保存一条真实模型回复以证明全链路，但随机 schema 必须在命令返回前删除。终端和本地收据只有长度、状态、用量、本地估算费用与哈希，没有台词、密钥或 provider request ID 原文。

## 2026-09-04 实测结果

| 顺序 | 结果 | 请求与用量 | 本地费用估算 | 整体耗时 | 安全结果 |
|---|---|---|---:|---:|---|
| 1 | 数据库身份脱敏检查不一致，联网前拒绝 | `request_count=0` | ¥0 | 不适用 | 未调用模型；问题修复后 fake-model PostgreSQL 测试复跑通过 |
| 2 | 百炼 `safe_failure`，96 Token 上限导致 `length` 截断 | 1 次、0 重试；输入 1008、输出 96、思考 0 Token | ¥0.0002784 | 3004 ms | 使用安全降级，随机 schema 删除 |
| 3 | 百炼 `qwen3.7-flash-2026-07-15` 核心纵向链 `passed` | 1 次、0 重试；输入 1023、输出 85、思考 0 Token | ¥0.0002726 | 1407 ms | 知识投影哨兵通过；玩家查询看不到 `AI_TURN_RECEIPT`；随机 schema 删除 |
| 4 | 火山 `doubao-seed-character-260628` 核心纵向链 `passed` | 1 次、0 重试；输入 1244、输出 36、思考 0 Token | ¥0.0010672 | 1843 ms | 知识投影哨兵通过；玩家查询看不到 `AI_TURN_RECEIPT`；随机 schema 删除 |

第二次记录是输出上限调整到 256 Token 之前的受控失败证据，不应隐藏或改写成成功。第三、四次分别证明百炼与默认火山已经跑通 `FusionGameService` 的核心技术纵向链；它们仍不是 HTTP `/api/fusion` 传输层、浏览器页面、人工角色口吻或商业剧本整局验收。

## 安全门

- 数据库只能是 `fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox`，且必须给出 `CREATE_EPHEMERAL_SCHEMA` 确认词。
- 每个进程最多进入一次网络请求，应用重试和 SDK 重试都是 0。
- 供应商、付费确认和本地估算上限都是 CLI 必填项；上限不能超过 ¥0.01。
- 模型调用前检查授权角色信息和公开事件确已出现，系统真相和其他角色秘密的哨兵值确实不在请求中。
- 落库后再通过玩家可见事件查询确认 `AI_TURN_RECEIPT` 不会被返回给玩家。
- 这个金额是基于当前配置费率的本地预估保护线，不是云账户侧的绝对扣费封顶；账单以云厂商为准。
- 当次专用冒烟测试没有修改项目根 `.env`，并只在测试进程内临时放行。用户后来另行授权将正常 Fusion 的本机付费开关打开，不改变本测试入口的隔离规则。

## 运行

先确认隔离 PostgreSQL 已经在 `127.0.0.1:55432` 运行，然后从仓库根目录执行：

```sh
FUSION_POSTGRES_TEST_CONFIRM=CREATE_EPHEMERAL_SCHEMA \
FUSION_POSTGRES_TEST_DATABASE_URL='postgresql+psycopg://fusion_it@127.0.0.1:55432/fusion_pg_it_sandbox' \
backend/.venv/bin/python backend/scripts/real_fusion_vertical_smoke.py \
  --provider aliyun_bailian \
  --max-cost-cny 0.01 \
  --confirm-one-paid-call
```

验证默认火山时只把 `--provider aliyun_bailian` 改为 `--provider volcengine_ark`；不要在一次运行中自动切换供应商。

退出码 `0` 表示权限投影、真实模型、用量结算、回执落库和 schema 清理全部通过。`2` 表示联网前被安全配置拒绝；`3` 表示已进入验收但某项失败，此时用量可能已经产生；`4` 表示 API 可能已调用，但本地收据写入失败。

脱敏收据保存在已忽略的 `.runtime/live-vertical-smoke/`，文件权限为 600。不要把这个专用入口换成全量 `pytest`，也不要把商业剧本改成测试参数。
