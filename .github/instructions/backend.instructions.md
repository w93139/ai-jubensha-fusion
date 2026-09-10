---
applyTo: 'backend/**'
---

# 后端开发规范

当前入口与安全边界以根AGENTS.md、docs/development/README.md为准。
默认测试使用backend/.venv/bin/python backend/scripts/test_fusion_security.py（仓库根目录）。
不要自动发现旧全量pytest：tests/conftest.py可能初始化应用。下列启动/安装命令仅是手动操作，
必须先确认数据库目标和可恢复备份；不因为“准备开发”而连接真实库或调用付费模型。

## 基础配置

- **语言**: Python >=3.13,<3.14，包管理使用 `uv`，以pyproject.toml为准
- **框架**: FastAPI，入口 `main.py` → 应用组装在 `src/core/server.py`
- **ORM**: SQLAlchemy（同步模式），数据库 PostgreSQL
- **类型检查**: pyright（见 `pyrightconfig.json`）

```bash
uv sync                   # 安装依赖
uv run python main.py     # 启动开发服务器（端口 8010）
uv run pytest             # 运行全部测试
uv run pytest -m api      # 按标记运行（api / unit / integration / slow）
```

## 项目结构

```
backend/
├── main.py                    # 应用入口（端口 8010）
├── src/
│   ├── api/routes/            # 路由处理器（按业务域划分）
│   ├── core/                  # 应用组装、DI 容器、认证中间件、WebSocket、游戏引擎
│   ├── db/
│   │   ├── base.py            # ORM 基类（BaseSQLAlchemyModel）
│   │   ├── session.py         # Engine + Session 工厂
│   │   ├── models/            # SQLAlchemy 模型
│   │   ├── repositories/      # 数据访问层（Repository 模式）
│   │   └── migrations/        # Alembic 迁移
│   ├── schemas/               # Pydantic 请求/响应模型
│   └── services/              # 业务逻辑层
└── tests/                     # 测试用例
```

## 路由规范

路由文件位于 `src/api/routes/`，每个路由模块使用 `APIRouter`，前缀包含 `/api`：

```python
from fastapi import APIRouter, Depends
from src.core.container_integration import get_scoped_service
from src.services.my_service import MyService

router = APIRouter(prefix="/api/my-resource", tags=["my-resource"])

MyServiceDep = get_scoped_service(MyService)

@router.get("/")
async def list_items(svc: MyService = Depends(MyServiceDep)):
    return svc.list_all()
```

**注意**：
- 路由前缀直接使用 `/api/xxx`，无版本号前缀
- 路由函数使用 `async def`（即使调用同步 ORM）
- HTTP 方法严格对应语义（GET 查询、POST 创建、PUT/PATCH 更新、DELETE 删除）

## 依赖注入

项目使用自定义 DI 容器（非 FastAPI 原生），详见 [DEPENDENCY_INJECTION_MIGRATION.md](../docs/DEPENDENCY_INJECTION_MIGRATION.md)。

- **容器定义**: `src/core/dependency_container.py`，支持三种生命周期：`singleton`、`scoped`、`transient`
- **FastAPI 集成**: `src/core/container_integration.py` 提供辅助函数
- **Singleton**: `DatabaseManager`、`LLMService`
- **Scoped**（每请求，自动 commit/rollback）: 各 Repository、大部分 Service

```python
# 获取 scoped 服务作为 FastAPI 依赖
from src.core.container_integration import get_scoped_service, get_db_session_depends

# 方式一：使用 get_scoped_service（推荐）
MyServiceDep = get_scoped_service(MyService)
async def handler(svc: MyService = Depends(MyServiceDep)): ...

# 方式二：获取 DB session
async def handler(db: Session = get_db_session_depends()): ...
```

**重要**: Scoped session 在请求结束时自动 commit（成功时）或 rollback（异常时）。Repository 中使用 `flush()` 而非 `commit()`，事务边界由 scope 管理。

## 认证

当前统一认证入口为 `src/core/auth_middleware.py`：
路径策略为 `NONE / OPTIONAL / REQUIRED / ADMIN`，读取用户也使用该模块的
`get_current_user_from_request`、`get_current_active_user_from_request`、
`get_current_admin_user_from_request`。不要引用已不存在的旧middleware_dependencies/auth_dependencies模块。

身份认证与对象权限不同：Fusion仍须检查本局、角色、剧本与阶段归属。
管理员旧编辑入口不能绕过后续的人审/版本发布门禁。

```python
from src.core.auth_middleware import get_current_active_user_from_request

@router.get("/me")
async def get_profile(request: Request):
    return get_current_active_user_from_request(request)
```

## 数据库与 ORM

- **同步模式**: 使用 `sqlalchemy.orm.Session`（`sessionmaker(autocommit=False, autoflush=False)`）
- **ORM 基类**: `src/db/base.py` 的 `BaseSQLAlchemyModel` 提供 `id`、`created_at`、`updated_at` 和 `to_dict()`/`from_dict()` 方法
- **迁移**: Alembic 位于 `src/db/migrations/`
- **启动时**: `init_database()` 调用 `create_tables()` 自动建表（与 Alembic 共存）

### Repository 模式

```python
from src.db.repositories.base import BaseRepository

class MyRepository(BaseRepository):
    def __init__(self, session: Session):
        super().__init__(session, MyModel)

    def find_by_name(self, name: str) -> MyModel | None:
        return self.session.query(MyModel).filter_by(name=name).first()
```

Repository 中使用 `flush()` 推送变更，`commit()` 由 DI scope 自动管理。

## Pydantic 模型

- **基类**: `src/schemas/base.py` 的 `BaseDataModel`，使用 Pydantic v2 `ConfigDict(from_attributes=True)`
- 区分请求模型（`CreateXxx`）和响应模型（`XxxResponse`）
- 支持 Field alias 映射数据库字段名

```python
from src.schemas.base import BaseDataModel

class ScriptInfo(BaseDataModel):
    title: str
    difficulty: DifficultyLevel = Field(alias="difficulty_level")
```

## 测试规范

测试位于 `tests/`，使用 pytest，标记：`api`、`unit`、`integration`、`slow`。

- 旧 `conftest.py` 有历史fixture，但应用初始化可能先于mock发生，不能视为全局安全隔离。
- 默认通过 `scripts/test_fusion_security.py` 专用入口运行，禁用dotenv/网络并使用虚构数据；不要直接调用旧全量pytest。
- `factories.py` 提供历史模型工厂；新增单元测试仍应显式隔离数据库和模型。

```python
import pytest

@pytest.mark.api
def test_get_scripts(test_client):
    response = test_client.get("/api/scripts")
    assert response.status_code in [200, 401]
```

## 代码风格

- 变量/函数：`snake_case`（如 `user_id`、`get_user`）
- 类：`PascalCase`（如 `UserModel`、`AuthService`）
- 常量：`UPPER_SNAKE_CASE`（如 `MAX_RETRY`、`API_PREFIX`）
- 导入顺序：标准库 → 第三方库 → 本地模块，空行分隔
- 所有函数参数和返回值添加类型注释

## 关键文档

| 文档 | 主题 |
|------|------|
| [AUTH_MIDDLEWARE_GUIDE.md](../docs/AUTH_MIDDLEWARE_GUIDE.md) | 认证中间件使用指南 |
| [DEPENDENCY_INJECTION_MIGRATION.md](../docs/DEPENDENCY_INJECTION_MIGRATION.md) | DI 容器迁移和用法 |
| [SERVICE_ARCHITECTURE.md](../docs/SERVICE_ARCHITECTURE.md) | 服务层设计模式 |
| [MINIMAX_CLIENT_GUIDE.md](../docs/MINIMAX_CLIENT_GUIDE.md) | MiniMax TTS/图像集成 |
| [README_SCRIPT_MANAGER.md](../docs/README_SCRIPT_MANAGER.md) | 剧本管理功能 |
