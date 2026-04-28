import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from reachy_mini.daemon.app.main import LocalhostOnlyMiddleware
from reachy_mini.daemon.app.routers.hf_auth import _oauth_result_page


def _test_app(*, localhost_only: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalhostOnlyMiddleware, enabled=localhost_only)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("ok")
        await websocket.close()

    return app


def test_localhost_only_allows_loopback_http() -> None:
    client = TestClient(_test_app(localhost_only=True), client=("127.0.0.1", 1234))

    response = client.get("/ok")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_localhost_only_rejects_non_loopback_http() -> None:
    client = TestClient(_test_app(localhost_only=True), client=("203.0.113.10", 1234))

    response = client.get("/ok")

    assert response.status_code == 403
    assert "localhost-only" in response.text


def test_localhost_only_rejects_non_loopback_websocket() -> None:
    client = TestClient(_test_app(localhost_only=True), client=("203.0.113.10", 1234))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws"):
            pass

    assert exc_info.value.code == 1008


def test_disabled_localhost_only_allows_non_loopback_http() -> None:
    client = TestClient(_test_app(localhost_only=False), client=("203.0.113.10", 1234))

    response = client.get("/ok")

    assert response.status_code == 200


def test_oauth_result_page_escapes_message_html() -> None:
    page = _oauth_result_page(False, '<script>alert("x")</script>')

    assert '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;' in page
    assert '<script>alert("x")</script>' not in page
