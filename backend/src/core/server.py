import os
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from src.core.environment import load_project_environment

load_project_environment()

from src.core.websocket_server import game_server
from src.core.startup import initialize_application
from src.services.auth_service import AuthService
from src.db.session import db_manager

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
from src.api.routes.script_package_routes import router as script_package_router
from src.api.routes.source_bundle_routes import router as source_bundle_router
from src.api.routes.script_review_routes import router as script_review_router
from src.api.routes.authoring_routes import router as authoring_router
from src.api.routes.script_publication_routes import router as script_publication_router
from src.api.routes.package_runtime_routes import router as package_runtime_router
from src.api.routes.package_flow_routes import router as package_flow_router
from src.api.routes.package_play_routes import router as package_play_router
from src.api.routes.package_speech_input_routes import router as package_speech_input_router
from src.fusion.package_speech_input import cleanup_speech_input_receipts
from src.fusion.service import FusionGameError, FusionGameService
from src.fusion.websocket import fusion_connections
from src.schemas.fusion_game import FusionActionRequest

from src.db.session import get_db_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化，关闭时清理"""
    # --- startup ---
    try:
        initialize_application()
    except Exception:
        db_manager.close()
        logging.getLogger(__name__).exception("应用初始化失败")
        raise

    speech_cleanup = asyncio.create_task(cleanup_speech_input_receipts())
    try:
        yield
    finally:
        speech_cleanup.cancel()
        try:
            await speech_cleanup
        except asyncio.CancelledError:
            pass
        # --- shutdown ---
        try:
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

# 中间件注册：Starlette 后注册先执行，所以 CORS 必须最后注册（最外层先运行）
# 顺序：请求进来 → CORS → Auth → 路由处理
from src.core.auth_middleware import UnifiedAuthMiddleware

# 1. 先注册 Auth（内层，在 CORS 之后执行）
app.add_middleware(UnifiedAuthMiddleware)

# 2. 后注册 CORS（外层，最先执行，保证预检请求能通过）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# 数据模型已迁移到各自的路由文件中


# 挂载静态文件目录（仅当目录存在时才挂载，避免启动崩溃）
if os.path.isdir("static"):
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
app.include_router(script_package_router)
app.include_router(source_bundle_router)
app.include_router(script_review_router)
app.include_router(authoring_router)
app.include_router(script_publication_router)
app.include_router(package_runtime_router)
app.include_router(package_flow_router)
app.include_router(package_play_router)
app.include_router(package_speech_input_router)


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
        try:
            state = games.get_state(session_id, user.id)
        except FusionGameError:
            await websocket.close(code=1008, reason="Session not available")
            return
        await fusion_connections.connect(session_id, websocket)
        await websocket.send_json({"type": "FULL_STATE", "session_id": session_id, "payload": state})
        while True:
            try:
                body = FusionActionRequest.model_validate(await websocket.receive_json())
                before_event_id = games.get_state(session_id, user.id)["last_event_id"]
                state = games.perform_action(session_id, user.id, body.type, body.payload, body.idempotency_key)
                if body.type == "ask_question":
                    await websocket.send_json({"type": "AI_THINKING", "session_id": session_id, "payload": {"character_id": body.payload.get("target_character_id")}})
                    await games.answer_question(session_id, user.id, body.idempotency_key)
                    state = games.get_state(session_id, user.id)
                elif body.type == "advance_phase" and state["phase"] in ("INTRODUCTION", "DISCUSSION"):
                    await websocket.send_json({"type": "AI_THINKING", "session_id": session_id, "payload": {"phase": state["phase"]}})
                    await games.run_ai_phase(session_id, user.id, body.idempotency_key)
                    state = games.get_state(session_id, user.id)
                events = games.get_events(session_id, user.id, before_event_id)
                await fusion_connections.broadcast(session_id, {
                    "type": "STATE_UPDATED",
                    "session_id": session_id,
                    "events": events,
                    "payload": state,
                })
            except (FusionGameError, ValueError) as exc:
                db.rollback()
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

    # 旧 WS 同时暴露全本编辑、全知模拟及完整广播，仅管理员可进入。
    # 单真人玩家使用 /api/fusion/ws/{session_id} 的角色过滤通道。
    if not getattr(current_user, 'is_admin', False):
        await websocket.close(code=1008, reason="Administrator access required")
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

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8010))  # 与 main.py 保持一致

    print(f"Starting server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
