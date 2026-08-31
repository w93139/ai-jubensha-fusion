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
        for socket in self.connections.get(session_id, set()):
            try:
                await socket.send_json(message)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.disconnect(session_id, socket)


fusion_connections = FusionConnectionManager()

