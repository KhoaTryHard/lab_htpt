"""Proxy wire behavior and delay placement, with a deterministic HTTPX upstream."""

import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from latency_lab.proxy import create_app


@pytest.mark.parametrize("status", [201, 409, 422])
def test_forwarding_body_query_headers_and_status(status) -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/register"
        assert request.url.query == b"example=1"
        assert request.content == b'{"email":"broken"}'
        assert request.headers["content-type"] == "application/json"
        assert "x-hop" not in request.headers
        return httpx.Response(status, json={"ok": status == 201}, headers={"x-upstream": "yes"})

    with TestClient(create_app(transport=httpx.MockTransport(upstream))) as client:
        result = client.post(
            "/api/register?example=1",
            content=b'{"email":"broken"}',
            headers={"content-type": "application/json", "connection": "x-hop", "x-hop": "no"},
        )
        assert result.status_code == status
        assert result.json() == {"ok": status == 201}
        assert result.headers["x-upstream"] == "yes"
        assert int(result.headers["content-length"]) == len(result.content)


def test_delay_is_split_and_only_registration_is_delayed() -> None:
    events = []

    async def sleep(seconds: float) -> None:
        events.append(seconds)

    async def upstream(request: httpx.Request) -> httpx.Response:
        events.append(request.url.path)
        return httpx.Response(422, json={"ok": False})

    with TestClient(create_app(rtt_ms=200, transport=httpx.MockTransport(upstream))) as client:
        with patch("latency_lab.proxy.asyncio.sleep", side_effect=sleep):
            client.post("/api/register", json={})
            assert events == [0.1, "/api/register", 0.1]
            events.clear()
            client.post("/api/register", json={}, headers={"x-lab-rtt": "500"})
            assert events == [0.25, "/api/register", 0.25]
            events.clear()
            client.get("/api/health")
            assert events == ["/api/health"]
        assert client.get("/api/lab-config").json() == {"rttMs": 200}


def test_upstream_unavailable_and_bad_rtt() -> None:
    async def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable", request=request)

    with TestClient(create_app(transport=httpx.MockTransport(unavailable))) as client:
        result = client.post("/api/register", json={})
        assert result.status_code == 502
        assert result.json()["code"] == "UPSTREAM_UNAVAILABLE"
        for rtt in ("NaN", "Infinity", "-1", "broken"):
            result = client.post("/api/register", json={}, headers={"x-lab-rtt": rtt})
            assert result.status_code == 400


def test_delay_does_not_block_health_requests() -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async def exercise() -> None:
        app = create_app(rtt_ms=500, transport=httpx.MockTransport(upstream))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://lab"
            ) as client:
                delayed = asyncio.create_task(client.post("/api/register", json={}))
                try:
                    await asyncio.sleep(0.02)
                    response = await asyncio.wait_for(client.get("/api/health"), timeout=0.2)
                    assert response.json() == {"ok": True}
                    assert not delayed.done()
                finally:
                    await delayed

    asyncio.run(exercise())
