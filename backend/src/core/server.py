from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
import os
import logging
import time
from uuid import uuid4
from dotenv import load_dotenv
from src.core.websocket_server import game_server
from src.services.auth_service import AuthService
from src.db.session import init_database, db_manager
from typing import Optional
from contextlib import asynccontextmanager

# 导入剧本管理相关路由
from src.api.routes.script_routes import router as script_management_router
from src.api.routes.script_editor_routes import router as script_editor_router
from src.api.routes.image_generation_routes import router as image_generation_router
from src.api.routes.evidence_routes import router as evidence_router
from src.api.routes.character_routes import router as character_router
from src.api.routes.location_routes import router as location_router
from src.api.routes.asset_routes import router as asset_router
# 导入游戏管理API路由
from src.api.routes.game_routes import router as game_router
from src.api.routes.game_history_routes import router as game_history_router
# 导入文件管理API路由
from src.api.routes.file_routes import router as file_router
# 导入TTS API路由
from src.api.routes.tts_routes import router as tts_router
# 导入用户认证路由
from src.api.routes.auth_routes import router as auth_router
from src.api.routes.fusion_game_routes import router as fusion_router, admin_router as fusion_admin_router
from src.fusion.service import FusionGameError, FusionGameService
from src.fusion.websocket import fusion_connections
from src.schemas.fusion_game import FusionActionRequest

from src.db.session import init_database, get_db_session

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化，关闭时清理"""
    # --- startup ---
    try:
        # 最先配置依赖注入容器，保证容器尽早可用
        from .dependency_container import configure_services
        configure_services()
        print("依赖注入容器配置完成")

        # 初始化SQLAlchemy数据库
        init_database()
        print("SQLAlchemy数据库初始化完成")

        # 如果启用了匿名访问，确保默认访客账户存在
        from src.core.config import config
        if config.allow_anonymous_access:
            from src.services.auth_service import AuthService
            db_gen = get_db_session()
            db = next(db_gen)
            try:
                AuthService.get_or_create_guest_user(
                    db, config.guest_username, config.guest_email
                )
                print(f"访客账户已就绪: {config.guest_username}")
            finally:
                db.close()
    except Exception as e:
        print(f"应用初始化失败: {e}")

    yield

    # --- shutdown ---
    try:
        # 关闭数据库连接池
        from src.db.session import db_manager
        db_manager.close()
        print("数据库连接池已关闭")
    except Exception as e:
        print(f"数据库关闭失败: {e}")


app = FastAPI(title="人生海海",docs_url="/docs",redoc_url="/redoc",lifespan=lifespan)


@app.middleware("http")
async def request_trace(request: Request, call_next):
    """使用不含请求正文的结构化访问日志，避免泄露 JWT 和私本。"""
    request_id = request.headers.get("X-Request-ID", uuid4().hex)
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logging.getLogger("access").info(
        "request_complete",
        extra={"request_id": request_id, "method": request.method, "path": request.url.path,
               "status_code": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)},
    )
    return response

# 添加全局验证错误处理器
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理请求验证错误，提供详细的错误信息"""
    logger = logging.getLogger(__name__)
    
    # 记录详细的错误信息
    error_details = []
    for error in exc.errors():
        error_details.append({
            "field": ".".join(str(x) for x in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
            "input": error.get("input")
        })
    
    logger.error(f"请求验证失败 - URL: {request.url}, 错误详情: {error_details}")
    
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "请求数据验证失败",
            "errors": error_details,
            "detail": "请检查请求数据格式和字段类型"
        }
    )

# 添加认证中间件（必须在CORS之后添加）
from src.core.auth_middleware import UnifiedAuthMiddleware
app.add_middleware(UnifiedAuthMiddleware)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 数据模型已迁移到各自的路由文件中


# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册剧本管理相关路由
app.include_router(script_management_router)
app.include_router(script_editor_router)
if os.getenv("ENABLE_MEDIA_FEATURES", "false").lower() == "true":
    app.include_router(image_generation_router)
app.include_router(evidence_router)
app.include_router(character_router)
app.include_router(location_router)
# 注册游戏管理API路由
app.include_router(game_router)
app.include_router(game_history_router)
# 注册文件管理API路由
app.include_router(file_router)
# 注册TTS API路由
if os.getenv("ENABLE_MEDIA_FEATURES", "false").lower() == "true":
    app.include_router(tts_router)
app.include_router(asset_router)
# 注册用户认证路由
app.include_router(auth_router)
app.include_router(fusion_router)
app.include_router(fusion_admin_router)


@app.get("/health")
def health():
    """容器和负载均衡器使用的轻量健康检查。"""
    return {"status": "ok", "service": "ai-jubensha-fusion"}


@app.websocket("/api/fusion/ws/{session_id}")
async def fusion_websocket(websocket: WebSocket, session_id: str, token: str):
    """认证后订阅状态，并复用 REST 的权威行动服务。"""
    db = db_manager.get_session()
    user = None
    try:
        user = AuthService.get_user_from_token(db, token)
        if not user or not user.is_active:
            await websocket.close(code=1008, reason="Authentication required")
            return
        games = FusionGameService(db)
        state = games.get_state(session_id, user.id)
        await fusion_connections.connect(session_id, websocket)
        await websocket.send_json({"type": "FULL_STATE", "session_id": session_id, "payload": state})
        while True:
            body = FusionActionRequest.model_validate(await websocket.receive_json())
            try:
                state = games.perform_action(session_id, user.id, body.type, body.payload, body.idempotency_key)
                if body.type == "ask_question":
                    await websocket.send_json({"type": "AI_THINKING", "session_id": session_id, "payload": {"character_id": body.payload.get("target_character_id")}})
                    await games.answer_question(session_id, user.id, int(body.payload.get("target_character_id", 0)), str(body.payload.get("content", "")))
                    state = games.get_state(session_id, user.id)
                elif body.type == "advance_phase" and state["phase"] in ("INTRODUCTION", "DISCUSSION"):
                    await websocket.send_json({"type": "AI_THINKING", "session_id": session_id, "payload": {"phase": state["phase"]}})
                    await games.run_ai_phase(session_id, user.id)
                    state = games.get_state(session_id, user.id)
                events = games.get_events(session_id, user.id, max(0, state["last_event_id"] - 2))
                await fusion_connections.broadcast(session_id, {
                    "type": "STATE_UPDATED",
                    "session_id": session_id,
                    "events": events,
                    "payload": state,
                })
            except (FusionGameError, ValueError) as exc:
                await websocket.send_json({"type": "ERROR", "session_id": session_id, "payload": {"message": str(exc)}})
    except WebSocketDisconnect:
        pass
    finally:
        fusion_connections.disconnect(session_id, websocket)
        db.close()

@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket, script_id: int = 1, token: str = None):
    """WebSocket端点 - 支持token认证，基于用户身份自动管理会话"""
    import logging
    
    logger = logging.getLogger(__name__)
    await websocket.accept()
    
    # 通过token获取当前用户（复用 AuthService 统一验证入口）
    current_user = None
    if token:
        try:
            # 获取数据库会话
            db_gen = get_db_session()
            db = next(db_gen)

            try:
                # 验证令牌并获取用户
                current_user = AuthService.get_user_from_token(db, token)

                if current_user and not getattr(current_user, 'is_active', False):
                    current_user = None
            finally:
                db.close()

        except Exception as e:
            logger.error(f"WebSocket token验证失败: {e}")
            await websocket.close(code=1008, reason="Invalid token")
            return
    
    if not current_user:
        logger.warning("WebSocket连接缺少有效的用户认证")
        await websocket.close(code=1008, reason="Authentication required")
        return
    
    # 使用验证后的用户ID注册客户端
    user_id = getattr(current_user, 'id', None)
    await game_server.register_client(websocket, script_id, user_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            await game_server.handle_client_message(websocket, data)
    except WebSocketDisconnect:
        await game_server.unregister_client(websocket)

# create_response函数已迁移到各自的路由文件中

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")  # 改为0.0.0.0允许外部访问
    port = int(os.getenv("PORT", 8000))

    print(f"Starting server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
