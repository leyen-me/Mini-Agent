from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from mini_agent_sdk import MiniAgentClient  # noqa: E402


def test_sdk_builds_authenticated_requests():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/sessions":
            return httpx.Response(
                200,
                json={"code": "OK", "data": {"session": {"id": "s1", "title": "SDK"}}},
            )
        if request.url.path.endswith("/interactions/i1/answer"):
            return httpx.Response(200, json={"code": "OK", "data": {"answered": True}})
        return httpx.Response(200, json={"code": "OK", "data": {"queued": False, "turnId": "t1"}})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(base_url="http://test", transport=transport)
    client = MiniAgentClient("http://test", token="secret-token", client=http)
    session = client.create_session(title="SDK")
    assert session["id"] == "s1"
    result = client.send("s1", "hello", attachment_ids=["up1"])
    assert result["turnId"] == "t1"
    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert seen[1].url.path == "/api/v1/sessions/s1/messages"
    assert b"attachmentIds" in seen[1].content
    answered = client.respond_interaction("s1", "i1", {"answer": "yes"})
    assert answered["answered"] is True
    assert seen[2].url.path == "/api/v1/sessions/s1/interactions/i1/answer"
    assert b'"answer"' in seen[2].content
    client.close()
    http.close()
