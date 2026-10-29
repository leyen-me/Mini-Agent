"""Small synchronous/async Python SDK for the Mini Agent HTTP API.

Example:
    from mini_agent_client import MiniAgentClient

    with MiniAgentClient("http://127.0.0.1:8000", token="...") as client:
        session = client.create_session()
        client.send(session["id"], "你好")
        for event in client.stream_events(session["id"]):
            print(event["type"], event.get("payload"))
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

import httpx


@dataclass(slots=True)
class MiniAgentError(Exception):
    code: str
    message: str
    status: int

    def __str__(self) -> str:
        return f"{self.code} ({self.status}): {self.message}"


class MiniAgentClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        token: str | None = None,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers)
        if client is not None and headers:
            self._client.headers.update(headers)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MiniAgentClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, f"/api/v1{path}", **kwargs)
        try:
            payload = response.json()
        except json.JSONDecodeError:
            payload = {"code": "HTTP_ERROR", "message": response.text, "data": None}
        if response.status_code >= 400:
            raise MiniAgentError(
                str(payload.get("code") or "HTTP_ERROR"),
                str(payload.get("message") or response.reason_phrase),
                response.status_code,
            )
        return payload.get("data", payload)

    # ------------------------------------------------------------------ basics

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/healthz")

    def bootstrap(self) -> dict[str, Any]:
        return self._request("GET", "/bootstrap")

    def sessions(self, *, limit: int = 50, archived: bool | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if archived is not None:
            params["archived"] = archived
        return self._request("GET", "/sessions", params=params)

    def create_session(
        self,
        *,
        title: str | None = None,
        agent_id: str | None = None,
        model: str | None = None,
        workspace_id: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in {
                "title": title,
                "agentId": agent_id,
                "model": model,
                "workspaceId": workspace_id,
                "mode": mode,
            }.items()
            if value is not None
        }
        return self._request("POST", "/sessions", json=payload)["session"]

    def session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}")

    def delete_session(self, session_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/sessions/{session_id}", params={"confirm": True})

    def send(
        self,
        session_id: str,
        content: str,
        *,
        attachment_ids: list[str] | None = None,
        mode: str = "auto",
        client_message_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": content, "mode": mode}
        if attachment_ids:
            payload["attachmentIds"] = attachment_ids
        if client_message_id:
            payload["clientMessageId"] = client_message_id
        return self._request("POST", f"/sessions/{session_id}/messages", json=payload)

    def pause(self, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/sessions/{session_id}/pause")

    def cancel(self, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/sessions/{session_id}/cancel")

    def respond_interaction(
        self, session_id: str, interaction_id: str, answer: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/sessions/{session_id}/interactions/{interaction_id}/answer",
            json={"answer": answer},
        )

    def upload(
        self, path: str, *, session_id: str | None = None, filename: str | None = None
    ) -> dict[str, Any]:
        with open(path, "rb") as handle:
            files = {"file": (filename or path.rsplit("/", 1)[-1], handle)}
            data = {"session_id": session_id} if session_id else None
            return self._request("POST", "/uploads", files=files, data=data)["upload"]

    def compact(self, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/sessions/{session_id}/compact")

    def export(self, session_id: str, *, fmt: str = "json") -> Any:
        response = self._client.get(
            f"/api/v1/sessions/{session_id}/export", params={"format": fmt}
        )
        if response.status_code >= 400:
            raise MiniAgentError("EXPORT_FAILED", response.text, response.status_code)
        return response.json() if fmt == "json" else response.text

    def import_session(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/sessions/import", json={"data": data})["session"]

    def stream_events(
        self, session_id: str, *, last_event_id: int | None = None
    ) -> Iterator[dict[str, Any]]:
        headers = {"Last-Event-ID": str(last_event_id)} if last_event_id else {}
        with self._client.stream(
            "GET", f"/api/v1/sessions/{session_id}/events", headers=headers, timeout=None
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise MiniAgentError("SSE_FAILED", response.text, response.status_code)
            event_type = ""
            event_id = ""
            data_lines: list[str] = []
            for line in response.iter_lines():
                if not line:
                    if data_lines:
                        yield {
                            "id": event_id,
                            "type": event_type,
                            "data": json.loads("\n".join(data_lines)),
                        }
                    event_type = ""
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                key, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if key == "event":
                    event_type = value
                elif key == "id":
                    event_id = value
                elif key == "data":
                    data_lines.append(value)

    def providers(self) -> list[dict[str, Any]]:
        return self._request("GET", "/providers")

    def agents(self) -> list[dict[str, Any]]:
        return self._request("GET", "/agents")

    def skills(self, session_id: str | None = None) -> list[dict[str, Any]]:
        params = {"session_id": session_id} if session_id else {}
        return self._request("GET", "/skills", params=params)

    def commands(self) -> list[dict[str, Any]]:
        return self._request("GET", "/commands")
