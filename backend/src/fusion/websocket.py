"""融合版 WebSocket 连接管理。单真人模式仍支持同账号跨设备恢复。"""
from collections import defaultdict
from fastapi import WebSocket


class FusionConnectionManager:
    def __init__(self):
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[session_id].add(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket):
        self.connections[session_id].discard(websocket)
        if not self.connections[session_id]:
            self.connections.pop(session_id, None)

    async def broadcast(self, session_id: str, message: dict):
        stale = []
        # send_json 会让出控制权；期间连接集合可能被加入/移除，不能跨 await 迭代活集合。
        for socket in tuple(self.connections.get(session_id, ())):
            try:
                await socket.send_json(message)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.disconnect(session_id, socket)


fusion_connections = FusionConnectionManager()
