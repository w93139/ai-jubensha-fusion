"""Exercise real handler bodies with fake sockets, without starting the application.

AST extraction skips production application setup and decorators only. These
tests do not replace a deployed ASGI/browser end-to-end test.
"""
import __future__
import ast
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, WebSocketDisconnect

from src.fusion.agents import AgentTurn, FusionAgentOrchestrator
from src.fusion.service import FusionGameError
from src.fusion.websocket import FusionConnectionManager
from src.schemas.fusion_game import FusionActionRequest
from test_knowledge_boundary import game, append_canaries


BACKEND = Path(__file__).resolve().parents[2]


def load_handlers(relative_path, names, namespace):
    path = BACKEND / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node = deepcopy(node)
            node.decorator_list = []
            node.args.defaults = []  # All arguments are explicitly supplied by tests.
            functions.append(node)
    assert {node.name for node in functions} == set(names)
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    exec(compile(module, str(path), "exec", flags=__future__.annotations.compiler_flag), namespace)
    return namespace


class FakeSocket:
    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.sent = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, code, reason):
        self.closed = (code, reason)

    async def send_json(self, value):
        json.dumps(value)  # Same JSON-serializability requirement as a real WS.
        self.sent.append(value)

    async def receive_json(self):
        if not self.incoming:
            raise WebSocketDisconnect()
        return self.incoming.pop(0)

    async def receive_text(self):
        raise WebSocketDisconnect()


def fusion_ws(game, identity=None):
    games, db, user, _, _, _ = game
    manager = FusionConnectionManager()
    namespace = {
        "db_manager": SimpleNamespace(get_session=lambda: db),
        "AuthService": SimpleNamespace(get_user_from_token=lambda *_: identity or user),
        "FusionGameService": lambda _: games, "fusion_connections": manager,
        "FusionActionRequest": FusionActionRequest, "FusionGameError": FusionGameError,
        "WebSocketDisconnect": WebSocketDisconnect,
    }
    return load_handlers("src/core/server.py", ["fusion_websocket"], namespace)["fusion_websocket"]


def test_ws_sends_all_phase_replies_not_just_last_two_events(game, monkeypatch):
    games, db, _, _, _, session = game
    append_canaries(game)
    session.current_phase = "BACKGROUND"
    db.commit()

    async def fake(self, role, events, question):
        return AgentTurn(f"我是{role['name']}。")

    monkeypatch.setattr(FusionAgentOrchestrator, "player_reply", fake)
    socket = FakeSocket([{"type": "advance_phase", "payload": {}, "idempotency_key": "ws-phase-step-01"}])
    asyncio.run(fusion_ws(game)(socket, session.session_id, "fake-token"))
    update = next(item for item in socket.sent if item["type"] == "STATE_UPDATED")
    assert len([event for event in update["events"] if event["type"] == "AI_MESSAGE"]) == 3
    assert "AI_SECRET_A" not in json.dumps(update) and "SYSTEM_ANSWER" not in json.dumps(update)
    assert any(item["type"] == "FULL_STATE" for item in socket.sent)


def test_malformed_ws_action_returns_error_without_closing_valid_session(game):
    _, _, _, _, _, session = game
    socket = FakeSocket([
        {"type": "unsupported", "idempotency_key": "malformed-action"},
        {"type": "send_message", "payload": {"content": "合法发言"}, "idempotency_key": "valid-ws-message"},
    ])
    asyncio.run(fusion_ws(game)(socket, session.session_id, "fake-token"))
    assert any(item["type"] == "ERROR" for item in socket.sent)
    assert any(item["type"] == "STATE_UPDATED" for item in socket.sent)


def test_ws_rejects_another_users_session_before_accepting(game):
    _, _, user, _, _, session = game
    socket = FakeSocket()
    outsider = SimpleNamespace(id=user.id + 999, is_active=True)
    asyncio.run(fusion_ws(game, outsider)(socket, session.session_id, "other-token"))
    assert socket.closed[0] == 1008 and not socket.accepted
    assert socket.sent == []


@pytest.mark.parametrize("admin", [False, True])
def test_legacy_whole_script_ws_is_admin_only(admin):
    registered = []
    closed = []
    identity = SimpleNamespace(id=123, is_active=True, is_admin=admin)

    async def register(*args):
        registered.append(args)

    async def unregister(*args):
        return None

    namespace = {
        "get_db_session": lambda: iter([SimpleNamespace(close=lambda: closed.append(True))]),
        "AuthService": SimpleNamespace(get_user_from_token=lambda *_: identity),
        "game_server": SimpleNamespace(register_client=register, unregister_client=unregister),
        "WebSocketDisconnect": WebSocketDisconnect,
    }
    handler = load_handlers("src/core/server.py", ["websocket_endpoint"], namespace)["websocket_endpoint"]
    socket = FakeSocket()
    asyncio.run(handler(socket, 7, "fake-token"))
    assert bool(registered) is admin and closed == [True]
    if not admin:
        assert socket.closed[0] == 1008


def test_rest_action_broadcasts_filtered_events_to_other_same_owner_devices(game):
    games, _, user, _, _, session = game
    append_canaries(game)
    manager = FusionConnectionManager()
    socket = FakeSocket()
    namespace = {
        "get_current_active_user_from_request": lambda _: user,
        "FusionGameError": FusionGameError, "HTTPException": HTTPException,
        "fusion_connections": manager,
    }
    handlers = load_handlers("src/api/routes/fusion_game_routes.py", ["invoke", "response", "perform_action"], namespace)
    # response() normally has a default message; extraction made arguments explicit.
    handlers["response"] = lambda data: {"success": True, "message": "ok", "data": data}
    body = FusionActionRequest(type="send_message", payload={"content": "REST_PUBLIC"}, idempotency_key="rest-message-01")

    async def run():
        await manager.connect(session.session_id, socket)
        return await handlers["perform_action"](session.session_id, body, object(), games)

    result = asyncio.run(run())
    assert result["success"] is True
    assert len(socket.sent) == 1 and socket.sent[0]["type"] == "STATE_UPDATED"
    assert "REST_PUBLIC" in json.dumps(socket.sent)
    assert "AI_SECRET_A" not in json.dumps(socket.sent) and "SYSTEM_ANSWER" not in json.dumps(socket.sent)


def test_ws_broadcast_tolerates_disconnect_during_send():
    manager = FusionConnectionManager()
    regular = FakeSocket()

    class DisconnectingSocket(FakeSocket):
        async def send_json(self, value):
            manager.disconnect("test-session", self)

    async def run():
        await manager.connect("test-session", regular)
        await manager.connect("test-session", DisconnectingSocket())
        await manager.broadcast("test-session", {"type": "TEST"})

    asyncio.run(run())
    assert regular.sent == [{"type": "TEST"}]


def test_ws_broadcast_uses_snapshot_when_a_new_socket_connects():
    manager = FusionConnectionManager()
    newcomer = FakeSocket()

    class ConnectingSocket(FakeSocket):
        async def send_json(self, value):
            await manager.connect("test-session", newcomer)
            await super().send_json(value)

    first = ConnectingSocket()

    async def run():
        await manager.connect("test-session", first)
        await manager.broadcast("test-session", {"type": "TEST"})

    asyncio.run(run())
    assert first.sent == [{"type": "TEST"}] and newcomer.sent == []
