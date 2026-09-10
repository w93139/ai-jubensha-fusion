import asyncio
import os
import logging

import uvicorn

from src.core.environment import load_project_environment

load_project_environment()

from src.core.server import app

def main():
    """启动AI剧本杀游戏服务器"""

    
    # 配置日志：同时输出到终端（方便开发）和文件（方便排查）
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),                                   # 输出到终端 / Docker 日志
            logging.FileHandler('ai_agent.log', encoding='utf-8'),    # 同时保存到文件
        ],
        force=True
    )
    
    # 测试日志输出
    logger = logging.getLogger(__name__)
    logger.info("日志系统已启动，AI代理日志将记录在此")
    print("✅ 日志配置完成，日志文件：ai_agent.log")
    
    print("🎭 AI剧本杀游戏服务器启动中...")
    print("="*50)
    print("请确保已在.env文件中设置OPENAI_API_KEY")
    print("游戏特色:")
    print("- 多个AI角色自动扮演")
    print("- 完整的剧本杀流程")
    print("- 实时WebSocket同步")
    print("- 精美的Web界面")
    print("="*50)
    
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8010))
    reload = os.getenv("RELOAD", "false").lower() == "true"

    if reload:
        print("🔁 热重载已开启，代码变更后自动重启")
    print(f"🌐 服务器地址: http://{host}:{port}")
    print(f"🎮 游戏页面: http://{host}:{port}")
    print("\n按 Ctrl+C 停止服务器")
    
    try:
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            log_level="info",
            reload=reload,
            reload_dirs=["src", "main.py"],
        )
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")

if __name__ == "__main__":
    main()
 
