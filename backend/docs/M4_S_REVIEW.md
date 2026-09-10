# M4 S 真人目录：离线审核交付

2026-09-10。H 已由用户审核，本工作包与另一剩余角色一起交付 S 的角色目录、注册函数和离线验证。

## 范围与完成条件

- 在仓库新建 `package_s_registration.py`、对应测试和本文；沿用 H 的精确包/真人绑定模式与错误关闭行为。所有既有文件、H/T/J 目录、运行框架、事实/证据/判分、依赖及迁移保持原样。
- 商业内容、私有注册与实际角色测试放仓库外独立目录。新候选只改变 `content_version`，与 H/T/J 及另一角色使用不同版本；正文和来源列表逐项一致。
- `single-player-content/1.0` 覆盖五阶段、本人操作稿、已获材料门槛、四名其他角色的有限问法和有源短答。伴随主持配置按真人身份保留必述链，不能自动代真人发言。
- 所有摘要从实际材料/来源计算；逐条核对目标、秘密、亲历和传闻的归属。既有框架的结构校验不能代替内容审查。
- 完成用户指定四模块、完整后端、仓库前端八套/类型/预检及其自测。实际角色以新建临时 SQLite、模拟 SDK、关闭 `.env` 与网络的安全入口验证；全问法、公私聊、动态解锁、回退、回忆和五席封卷结算均须有证据。

## 验证命令

用户给出的 pytest 模块使用 `backend/.venv/bin/python backend/scripts/test_fusion_security.py -k 'test_package_y_registration or test_package_s_registration or test_package_role_content or test_package_h_registration' -v` 执行；安全入口隔离可能初始化真实应用的旧父级 conftest。完整套件另执行该入口的 `-x -q`。

运行服务、真实模型和发布不属于本次离线交付。注册返回服务参数，并不是角色入口白名单，也不自动登记发布。

## 交付状态

离线交付完成，并与 Y 一起提交审核：12 个议题、18 个答者组合、54 条有源固定短答、5 阶段指引与 685 字符操作稿。候选版本为 `m4-s-review-20260910-v1`，包哈希 `1a765437892e9e6b818134b989a8e2de115a6051334cf93ee64a25bc486597f7`。与 H/T/J 以及另一新角色各用不同版本和包哈希，正文、来源、证据、规则及判分保持。

注册入口为 [`register_s_content`](../src/fusion/package_s_registration.py)，对应 [`test_package_s_registration.py`](../tests/fusion_security/test_package_s_registration.py)。只返回新的外层注册表，旧条目保持对象身份；使用 S_REGISTRATION_* 错误前缀并拒绝错误角色、双哈希不符、已有目标键或事实变更。运行框架、resolver 和所有 H/T/J 既有文件没有改动。

用户指定四模块 83 项、完整后端 2982 项、前端必需八套 348 项、TypeScript、预检及其 16 项自测全部通过；后端保留 363 条既有弃用警告。新注册两模块各 24 项计入完整后端。另有 13 项实际 Y/S 组合注册测试，验证两种注册顺序、旧映射身份、主持配置错误绑定拒绝、五个版本各异、原目标保留、七条必述除角色政策外逐字段一致。

本角色另有 15 项实际私有测试通过：29 项调查、54 个答者/问法组合（36 公聊、18 私聊）、5 条真人回忆、6 条其他 AI 必述各一次、五席独立封卷结算，241 次新数据库会话重读。单做调查只获得 4 条真人回忆，实际听到他人发言后才补齐；自己念词、提示与尚未得到回答的问题不会代领。真人必述仅提示义务，不自动代说。提前结束两轮调查也能按规则进入终局，未补发回忆。

每个问法故意使用无效模拟回复验证失败回执和显式固定短答；4 次 AI 封卷使用有效的空答弃权合成输出。本角色共 58 次模拟 SDK 调用、真实调用 0、新增费用 ¥0。公私共用问法次数、重复请求不再调用、私聊不进入公开记录、回退不新增费用、开场不改写和结算事件重读均有断言。这些结果证明离线工程流程，不证明真实模型生成语义、推理正确率或真人已履行转述义务。

作者以外的独立复审逐条核对固定答复、材料归属、阶段边界与隐私，并对新注册/测试作只读审查；20 个文件与证据摘要绑定均核对一致。3761 个既有文件摘要保持。未启动/重启服务、未接入新选角页面、未访问运行数据库、未提交或推送代码。

交付文件与证据：

- [完整私有交付索引](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/README.md)、[复现命令](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/COMMANDS.md)、[最终报告](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/final-report.json)。
- [single-S.json](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/S/single-S.json)、[guided-S.json](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/S/guided-S.json)、[候选包](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/S/candidate.json)、[私有注册脚本](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/S/register_S.py)。
- [实际角色测试日志](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/S/private-final.log)、[内容审查](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/S/content-review.md)、[代码审查](/Users/syk/Desktop/AI人生海海剧本杀/private-data/import-jobs/m3-full-play/20260907-strategy-candidate-2/m3-user-validation/m4-ys-adaptation-20260910/code-review.md)。

下一轮仍属 M4：整合已审核的 H/Y/S 目录与选角入口，交付可试玩链接；验收五角色选角、信息隔离、恢复旧局及真实模型整局。真实验证沿用用户已授权的 M4 累计新增费用上限 ¥10，并先核对已有账本。本轮未消耗该额度。建议开发采用极高思考强度，重点是多角色信息边界与旧局兼容；此处为建议，未切换会话或游戏模型设置。
