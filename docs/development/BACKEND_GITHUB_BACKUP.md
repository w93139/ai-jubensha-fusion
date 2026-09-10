# 后端代码版本备份

这是后端工程快照，固定目标为私有 GitHub 仓库 `w93139/ai-jubensha-backend`。
它不备份数据库、用户进度、商业剧本、OCR、导入任务、模型录制、日志或密钥。
它也不替代原工程仓库，不改动原有 public `origin`、分支或历史。

每个版本来自当前已验证的源码文件；`snapshot-manifest.json` 记录相对路径、字节数和 SHA-256，
`VERSION` 记录版本名。快照仓库从全新 Git 历史开始，后续版本使用普通提交和 annotated tag。
源工程可能含尚未提交的开发文件，因此文件摘要是该快照的精确依据，不能把旧工程 HEAD 当作新代码摘要。

## 安装和离线检查

要求 Python 3.13 与 uv。保持仓库内 `backend/` 目录层级，契约位于 `docs/contracts/`。

```sh
cd backend
uv sync --locked --group dev
cd ..
backend/.venv/bin/python backend/scripts/test_fusion_security.py
python3 -B -m unittest discover -s scripts -p 'test_sync_backend_github.py'
```

专用后端测试入口禁用 dotenv 和网络，使用虚构数据。不要直接运行旧全量 pytest/conftest：
它可能初始化真实应用。安装依赖需要联网，离线检查不会主动调用模型或启动服务。
本仓库不包含全栈前端和原 Compose 依赖，不能当作原工程的一键部署包。
运行真实后端需要自行配置根 `.env`、明确数据库目标和权限；启动可能建表，付费开关需遵循现行授权。

## 同步已验证的新后端版本

先完成源工程所需测试和独立复核，停止修改将导出的后端文件。`--source-root` 指向原工程根；
`--snapshot-dir` 指向原工程之外的独立目录。不要指向原工程或任意已有 Git 仓库。
首次目标须不存在或为空，之后只接受本工具管理且工作区干净的快照仓库。

```sh
python3 -B scripts/sync_backend_github.py \
  --source-root /path/to/source-project \
  --snapshot-dir /path/to/private-code-backup \
  --version m3-20260908.1
```

默认仅检查并打印清单摘要，不复制、不提交、不创建仓库、不推送、不调用 GitHub。
核对清单后，增加 `--sync --message '说明本次已验证的后端改动'` 生成本地提交和版本标签。

首次远端仓库由维护者另外创建为 **private**；工具不创建仓库、不改变仓库可见性。
确认现有用户授权涵盖此次代码同步后，在同一命令增加 `--sync --push`：

```sh
python3 -B scripts/sync_backend_github.py \
  --source-root /path/to/source-project \
  --snapshot-dir /path/to/private-code-backup \
  --version m3-20260908.1 \
  --message '已验证的后端版本' --sync --push
```

推送前会使用已登录的 `gh` 核对仓库完整名称与私有状态，固定 HTTPS origin；
Git 凭据只通过本次命令临时指定的 `gh auth git-credential` 获取，不改全局配置。
`main` 与当前 annotated tag 一起原子推送，随后校验远端提交、tag 对象与解引用提交。
若推送后的远端读取暂时失败，只对读取最多尝试三次，不额外推送；持续失败仍报告未完成核验。
不执行强推、远端历史重写或自动合并。

同版本、同清单可以重试：已提交但标签创建失败时补齐标签，网络失败时可幂等重推。
同版本不同源码拒绝执行；请另起新版本。远端分叉、输出目录有未知文件或未提交修改时停止，
不会删除未知文件来制造干净状态。后续版本只删除上一份清单明确管理而本次不再包含的文件。
代码快照中的 `--sync` 是版本保存操作，不能充当测试通过、发布批准或模型语义验收的证据。

## 包含与排除

- 包含：后端非忽略文本源码、提示模板、测试、配置和迁移，
  `docs/contracts/*.json`、占位 `.env.example`、原 LICENSE、同步工具和本说明。
- 排除：`.git` 历史、`.env` 实值、虚拟环境、缓存、数据库及日志、运行输出、商业原件、
  二进制文件和私有资料目录。即使这些文件被错误跟踪，也不会仅凭 Git 跟踪状态纳入。
- 候选符号链接、非文本文件、常见真实密钥形状直接阻断；诊断只报告路径和类别，不打印匹配值。
  自动形状扫描不能证明任意文本都不含隐私；新增测试与提示必须继续使用虚构材料并经过复核。

恢复时先核对 `snapshot-manifest.json` 和对应版本标签，再按安装步骤搭建新环境。
数据库与商业材料必须从各自独立、经过授权的私有备份恢复，本代码仓库不提供这些数据。
