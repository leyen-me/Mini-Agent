"""Model gateway with deterministic offline mock and OpenAI-compatible streaming.

The runtime only depends on :class:`BaseGateway` and the ``ModelChunk`` protocol, so
additional providers can be added without touching the turn state machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .config import AppConfig, ModelConfig, ProviderConfig
from .security import redact_text
from .tokens import estimate_tokens

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRIES = 3
DEEPSEEK_THINKING_HINTS = ("flash", "v4")
DEEPSEEK_EFFORT_MAP = {
    "minimal": "low",
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
    "ultra": "max",
}


@dataclass(slots=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: str

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelRequest:
    session_id: str
    turn_id: str
    step: int
    model_ref: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    reasoning_level: str
    max_output: int
    prefix_text: str
    prefix_hash: str
    temperature: float = 0.2
    structured_output: dict[str, Any] | None = None


@dataclass(slots=True)
class ModelChunk:
    kind: Literal["content", "reasoning", "retry", "completed"]
    text: str = ""
    attempt: int = 0
    reason: str = ""
    error_code: str = ""
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str = ""


class ModelError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        raw_code: str = "",
        request_id: str = "",
        http_status: int | None = None,
        retryable: bool = False,
        attempts: int = 1,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_code = raw_code
        self.request_id = request_id
        self.http_status = http_status
        self.retryable = retryable
        self.attempts = attempts
        self.retry_after = retry_after

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "rawCode": self.raw_code,
            "requestId": self.request_id,
            "httpStatus": self.http_status,
            "attempts": self.attempts,
            "retryAfter": self.retry_after,
        }


class BaseGateway:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def canonical_json(value: Any) -> str:
    """Stable serializer used for tool definitions and hash accounting."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def messages_prefix_text(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> tuple[str, str]:
    """Serialize the provider-visible prompt as an append-only token stream.

    Tool definitions come first (they are immutable for a session), followed by messages
    joined without a trailing wrapper. That mirrors prefix caching semantics: appending a
    message never changes the bytes of earlier content.
    """

    parts = ["TOOLS:", canonical_json(tools), "\nMESSAGES:\n"]
    parts.extend(canonical_json(message) + "\n" for message in messages)
    text = "".join(parts)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


class OpenAICompatibleGateway(BaseGateway):
    def __init__(
        self,
        config: AppConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleep=asyncio.sleep,
        rng: random.Random | None = None,
        header_resolver=None,
    ) -> None:
        super().__init__(config)
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._mock = MockGateway(config)
        self._header_resolver = header_resolver

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        provider, model = self.config.provider_for(request.model_ref)
        if provider.protocol == "mock":
            async for chunk in self._mock.stream(request):
                yield chunk
            return
        attempt = 0
        last_error: ModelError | None = None
        while attempt <= MAX_RETRIES:
            if attempt > 0:
                delay = self._backoff(attempt, last_error.retry_after if last_error else None)
                await self._sleep(delay)
                yield ModelChunk(
                    kind="retry",
                    attempt=attempt,
                    reason=last_error.message if last_error else "网络异常",
                    error_code=last_error.code if last_error else "NETWORK_ERROR",
                )
            try:
                async for chunk in self._stream_once(provider, model, request):
                    yield chunk
                return
            except ModelError as exc:
                last_error = exc
                attempt += 1
                if not exc.retryable or attempt > MAX_RETRIES:
                    exc.attempts = attempt
                    raise
                logger.warning(
                    "model request failed, retrying",
                    extra={
                        "session_id": request.session_id,
                        "turn_id": request.turn_id,
                        "attempt": attempt,
                        "error_code": exc.code,
                    },
                )

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(max(0.0, retry_after), 30.0)
        base = 2 ** (attempt - 1)
        jitter = self._rng.uniform(-0.2, 0.2)
        return max(0.05, base * (1 + jitter))

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=15, read=self.config.server.request_timeout_seconds, write=30, pool=15
                ),
                follow_redirects=False,
                proxy=self.config.network.proxy or None,
                verify=self.config.network.ca_bundle or True,
            )
        return self._client

    async def _stream_once(
        self, provider: ProviderConfig, model: ModelConfig, request: ModelRequest
    ) -> AsyncIterator[ModelChunk]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream", **provider.headers}
        if self._header_resolver is not None:
            headers.update(await self._header_resolver(provider))
        elif provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        if provider.protocol == "anthropic":
            if request.structured_output:
                raise ModelError(
                    "UNSUPPORTED_FEATURE",
                    "Anthropic 适配器暂不支持结构化输出；请使用 OpenAI Chat/Responses 或关闭该 Agent 的结构化输出",
                    retryable=False,
                )
            async for chunk in self._stream_anthropic(provider, model, request, headers):
                yield chunk
            return
        if provider.protocol == "openai-responses":
            async for chunk in self._stream_responses(provider, model, request, headers):
                yield chunk
            return
        payload: dict[str, Any] = {
            "model": model.id,
            "messages": request.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": min(request.max_output, model.max_output),
            "temperature": request.temperature,
        }
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = "auto"
        if request.structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_output",
                    "strict": True,
                    "schema": request.structured_output,
                },
            }
        if _deepseek_thinking_model(provider, model):
            # DeepSeek V4/Flash exposes an explicit thinking switch; legacy
            # deepseek-chat/deepseek-reasoner choose thinking by model id and must
            # not receive these OpenAI-style fields.
            if request.reasoning_level == "off":
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = DEEPSEEK_EFFORT_MAP.get(request.reasoning_level, "high")
        elif request.reasoning_level != "off" and not _is_deepseek(provider):
            payload["reasoning_effort"] = request.reasoning_level

        base = provider.base_url.rstrip("/")
        url = (
            f"{base}/chat/completions"
            if base.endswith("/v1") or "deepseek" in base
            else f"{base}/v1/chat/completions"
        )
        client = self._client_or_create()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish_reason = ""
        try:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise self._map_http_error(response, body, provider)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("event:") or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning(
                            "ignoring malformed SSE chunk", extra={"session_id": request.session_id}
                        )
                        continue
                    if chunk.get("usage"):
                        usage = self._parse_usage(chunk["usage"])
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield ModelChunk(kind="reasoning", text=reasoning)
                    text = delta.get("content") or ""
                    if text:
                        content_parts.append(text)
                        yield ModelChunk(kind="content", text=text)
                    for item in delta.get("tool_calls") or []:
                        index = int(item.get("index", 0))
                        target = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if item.get("id"):
                            target["id"] = item["id"]
                        function = item.get("function") or {}
                        if function.get("name"):
                            target["name"] += function["name"]
                        if function.get("arguments"):
                            target["arguments"] += function["arguments"]
        except ModelError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ModelError(
                "NETWORK_ERROR",
                "连接模型服务失败，请检查网络或代理设置",
                raw_code=type(exc).__name__,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelError(
                "NETWORK_ERROR",
                "模型服务通信异常",
                raw_code=type(exc).__name__,
                retryable=True,
            ) from exc

        if not content_parts and not reasoning_parts and not calls and not usage.input_tokens:
            raise ModelError(
                "EMPTY_RESPONSE",
                "模型返回了空响应，请稍后重试",
                retryable=True,
            )

        tool_calls = [
            ToolCallRequest(
                id=item["id"] or f"call_{index}_{int(time.time() * 1000)}",
                name=item["name"],
                arguments=item["arguments"] or "{}",
            )
            for index, item in sorted(calls.items())
            if item["name"]
        ]
        if usage.input_tokens == 0:
            usage = Usage(
                input_tokens=estimate_tokens(request.prefix_text),
                output_tokens=estimate_tokens("".join(content_parts) + "".join(reasoning_parts)),
                cache_hit_tokens=0,
                cache_miss_tokens=estimate_tokens(request.prefix_text),
            )
        yield ModelChunk(
            kind="completed",
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )

    async def _stream_anthropic(
        self,
        provider: ProviderConfig,
        model: ModelConfig,
        request: ModelRequest,
        headers: dict[str, str],
    ) -> AsyncIterator[ModelChunk]:
        anthropic_headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "anthropic-version": "2023-06-01",
            **headers,
        }
        system_text, messages = _to_anthropic_messages(request.messages)
        payload: dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "max_tokens": min(request.max_output, model.max_output),
            "stream": True,
            "temperature": request.temperature,
        }
        if system_text:
            payload["system"] = system_text
        if request.tools:
            payload["tools"] = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "input_schema": item["function"].get("parameters", {"type": "object"}),
                }
                for item in request.tools
            ]
        base = provider.base_url.rstrip("/")
        url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
        client = self._client_or_create()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage_raw: dict[str, Any] = {}
        finish_reason = ""
        async with client.stream("POST", url, headers=anthropic_headers, json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise self._map_http_error(response, body, provider)
            async for event in _iter_sse_json(response):
                etype = event.get("type") or ""
                if etype == "message_start":
                    usage_raw.update((event.get("message") or {}).get("usage") or {})
                elif etype == "content_block_start":
                    index = int(event.get("index", 0))
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        calls[index] = {
                            "id": str(block.get("id") or ""),
                            "name": str(block.get("name") or ""),
                            "arguments": "",
                        }
                    elif block.get("type") == "text" and block.get("text"):
                        content_parts.append(str(block["text"]))
                        yield ModelChunk(kind="content", text=str(block["text"]))
                elif etype == "content_block_delta":
                    index = int(event.get("index", 0))
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            content_parts.append(text)
                            yield ModelChunk(kind="content", text=text)
                    elif delta.get("type") == "input_json_delta":
                        calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        calls[index]["arguments"] += str(delta.get("partial_json") or "")
                    elif delta.get("type") in {"thinking_delta", "reasoning_delta"}:
                        text = str(delta.get("thinking") or delta.get("text") or "")
                        if text:
                            reasoning_parts.append(text)
                            yield ModelChunk(kind="reasoning", text=text)
                elif etype == "message_delta":
                    delta = event.get("delta") or {}
                    finish_reason = str(delta.get("stop_reason") or finish_reason)
                    if finish_reason == "max_tokens":
                        finish_reason = "length"
                    usage_raw.update(event.get("usage") or {})
                elif etype == "error":
                    error = event.get("error") or {}
                    raise ModelError(
                        "PROVIDER_ERROR",
                        f"Anthropic 返回错误：{error.get('message') or 'unknown'}",
                        raw_code=str(error.get("type") or "anthropic_error"),
                        retryable=False,
                    )
        if not content_parts and not reasoning_parts and not calls and not usage_raw.get("input_tokens"):
            raise ModelError("EMPTY_RESPONSE", "模型返回了空响应，请稍后重试", retryable=True)
        tool_calls = [
            ToolCallRequest(
                id=item["id"] or f"call_{index}_{int(time.time() * 1000)}",
                name=item["name"],
                arguments=item["arguments"] or "{}",
            )
            for index, item in sorted(calls.items())
            if item["name"]
        ]
        input_tokens = int(usage_raw.get("input_tokens") or 0)
        cache_hit = int(usage_raw.get("cache_read_input_tokens") or 0)
        output_tokens = int(usage_raw.get("output_tokens") or 0)
        usage = Usage(
            input_tokens=input_tokens or estimate_tokens(request.prefix_text),
            output_tokens=output_tokens or estimate_tokens("".join(content_parts)),
            cache_hit_tokens=cache_hit,
            # Anthropic input_tokens already excludes cache_read_input_tokens.
            cache_miss_tokens=input_tokens or estimate_tokens(request.prefix_text),
            raw=usage_raw,
        )
        yield ModelChunk(
            kind="completed",
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )

    async def _stream_responses(
        self,
        provider: ProviderConfig,
        model: ModelConfig,
        request: ModelRequest,
        headers: dict[str, str],
    ) -> AsyncIterator[ModelChunk]:
        instructions, input_items = _to_responses_input(request.messages)
        payload: dict[str, Any] = {
            "model": model.id,
            "input": input_items,
            "stream": True,
            "max_output_tokens": min(request.max_output, model.max_output),
        }
        if instructions:
            payload["instructions"] = instructions
        if request.structured_output:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "agent_output",
                    "strict": True,
                    "schema": request.structured_output,
                }
            }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "parameters": item["function"].get("parameters", {"type": "object"}),
                }
                for item in request.tools
            ]
            payload["tool_choice"] = "auto"
        base = provider.base_url.rstrip("/")
        url = f"{base}/responses" if base.endswith("/v1") else f"{base}/v1/responses"
        client = self._client_or_create()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[str, dict[str, str]] = {}
        current_call: str | None = None
        usage_raw: dict[str, Any] = {}
        finish_reason = ""
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise self._map_http_error(response, body, provider)
            async for event in _iter_sse_json(response):
                etype = str(event.get("type") or "")
                if etype in {"response.output_text.delta", "response.text.delta"}:
                    text = str(event.get("delta") or "")
                    if text:
                        content_parts.append(text)
                        yield ModelChunk(kind="content", text=text)
                elif etype in {
                    "response.reasoning_summary_text.delta",
                    "response.reasoning_text.delta",
                    "response.reasoning.delta",
                }:
                    text = str(event.get("delta") or "")
                    if text:
                        reasoning_parts.append(text)
                        yield ModelChunk(kind="reasoning", text=text)
                elif etype == "response.output_item.added":
                    item = event.get("item") or {}
                    if item.get("type") == "function_call":
                        call_id = str(item.get("call_id") or item.get("id") or "")
                        calls[call_id] = {
                            "id": call_id,
                            "name": str(item.get("name") or ""),
                            "arguments": str(item.get("arguments") or ""),
                        }
                        current_call = call_id
                elif etype == "response.function_call_arguments.delta":
                    call_id = str(event.get("item_id") or event.get("call_id") or current_call or "")
                    if call_id:
                        calls.setdefault(call_id, {"id": call_id, "name": "", "arguments": ""})
                        calls[call_id]["arguments"] += str(event.get("delta") or "")
                elif etype == "response.output_item.done":
                    item = event.get("item") or {}
                    if item.get("type") == "function_call":
                        call_id = str(item.get("call_id") or item.get("id") or "")
                        target = calls.setdefault(call_id, {"id": call_id, "name": "", "arguments": ""})
                        target["name"] = str(item.get("name") or target["name"])
                        if item.get("arguments"):
                            target["arguments"] = str(item["arguments"])
                elif etype == "response.completed":
                    response_data = event.get("response") or {}
                    usage_raw = response_data.get("usage") or {}
                    finish_reason = (
                        "length"
                        if response_data.get("status") == "incomplete"
                        or response_data.get("incomplete_details")
                        else "stop"
                    )
                elif etype in {"response.failed", "error"}:
                    error = event.get("error") or (event.get("response") or {}).get("error") or {}
                    raise ModelError(
                        "PROVIDER_ERROR",
                        f"OpenAI Responses 错误：{error.get('message') or error or 'unknown'}",
                        raw_code=str(error.get("code") or "responses_error"),
                        retryable=False,
                    )
        if not content_parts and not reasoning_parts and not calls and not usage_raw.get("input_tokens"):
            raise ModelError("EMPTY_RESPONSE", "模型返回了空响应，请稍后重试", retryable=True)
        tool_calls = []
        for item in calls.values():
            name = item["name"].rsplit(".", 1)[-1] if item["name"] else ""
            if not name:
                continue
            tool_calls.append(
                ToolCallRequest(
                    id=item["id"] or f"call_{int(time.time() * 1000)}",
                    name=name,
                    arguments=item["arguments"] or "{}",
                )
            )
        details = usage_raw.get("input_tokens_details") or {}
        input_tokens = int(usage_raw.get("input_tokens") or 0)
        cache_hit = int(details.get("cached_tokens") or 0)
        output_tokens = int(usage_raw.get("output_tokens") or 0)
        usage = Usage(
            input_tokens=input_tokens or estimate_tokens(request.prefix_text),
            output_tokens=output_tokens or estimate_tokens("".join(content_parts)),
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=max(0, (input_tokens or estimate_tokens(request.prefix_text)) - cache_hit),
            raw=usage_raw,
        )
        yield ModelChunk(
            kind="completed",
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )

    def _map_http_error(self, response: httpx.Response, body: str, provider: ProviderConfig) -> ModelError:
        raw_code = ""
        message = ""
        try:
            parsed = json.loads(body)
            error = parsed.get("error") or {}
            raw_code = str(error.get("code") or error.get("type") or "")
            message = str(error.get("message") or "")
        except (json.JSONDecodeError, AttributeError):
            message = body[:300]
        request_id = response.headers.get("x-request-id", "") or response.headers.get("request-id", "")
        retry_after = None
        header = response.headers.get("retry-after")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        status = response.status_code
        if status in {401, 403}:
            code, readable = "AUTH_FAILED", "模型服务认证失败，请检查 API Key 是否有效"
        elif status == 402:
            code, readable = "BALANCE_INSUFFICIENT", "模型服务余额不足，请充值后重试"
        elif status == 429:
            code, readable = "RATE_LIMITED", "请求过于频繁或额度受限，正在重试"
        elif status == 404:
            code, readable = "MODEL_NOT_FOUND", "模型不存在或当前账号无权使用"
        elif status >= 500:
            code, readable = "PROVIDER_UNAVAILABLE", "模型服务暂时不可用"
        elif status == 400:
            lowered = message.lower()
            if "context" in lowered and ("length" in lowered or "token" in lowered):
                code, readable = "CONTEXT_TOO_LONG", "上下文超过模型上限，需压缩后重试"
            else:
                code, readable = "INVALID_REQUEST", "模型请求参数不合法"
        elif status == 408:
            code, readable = "TIMEOUT", "模型服务响应超时"
        else:
            code, readable = "PROVIDER_ERROR", "模型服务返回错误"
        if message:
            readable = f"{readable}（{redact_text(message)[:240]}）"
        return ModelError(
            code,
            readable,
            raw_code=raw_code or str(status),
            request_id=request_id,
            http_status=status,
            retryable=status in RETRYABLE_STATUS or status == 408,
            retry_after=retry_after,
        )

    @staticmethod
    def _parse_usage(raw: dict[str, Any]) -> Usage:
        prompt_details = raw.get("prompt_tokens_details") or {}
        cache_hit = int(raw.get("prompt_cache_hit_tokens") or prompt_details.get("cached_tokens") or 0)
        input_tokens = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
        cache_miss = int(raw.get("prompt_cache_miss_tokens") or max(0, input_tokens - cache_hit))
        return Usage(
            input_tokens=input_tokens,
            output_tokens=int(raw.get("completion_tokens") or raw.get("output_tokens") or 0),
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
            raw=raw,
        )


def _is_deepseek(provider: ProviderConfig) -> bool:
    return "deepseek" in provider.id.lower() or "deepseek" in provider.base_url.lower()


def _deepseek_thinking_model(provider: ProviderConfig, model: ModelConfig) -> bool:
    return _is_deepseek(provider) and any(hint in model.id.lower() for hint in DEEPSEEK_THINKING_HINTS)


async def _iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line or line.startswith(":") or line.startswith("event:") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system_parts.append(str(message.get("content") or ""))
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(message.get("tool_call_id") or ""),
                            "content": str(message.get("content") or ""),
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            content = message.get("content")
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "input": args,
                    }
                )
            converted.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if item.get("type") == "text":
                    parts.append({"type": "text", "text": str(item.get("text") or "")})
                elif item.get("type") == "image_url":
                    url = str((item.get("image_url") or {}).get("url") or "")
                    if url.startswith("data:") and ";base64," in url:
                        header, data = url.split(";base64,", 1)
                        parts.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": header[5:] or "image/png",
                                    "data": data,
                                },
                            }
                        )
            converted.append({"role": "user", "content": parts})
        else:
            converted.append({"role": "user", "content": str(content or "")})
    return "\n\n".join(system_parts), converted


def _to_responses_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            instructions.append(str(message.get("content") or ""))
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(message.get("content") or ""),
                }
            )
            continue
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
            if message.get("content"):
                items.append({"role": "assistant", "content": str(message["content"])})
            continue
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if item.get("type") == "text":
                    parts.append({"type": "input_text", "text": str(item.get("text") or "")})
                elif item.get("type") == "image_url":
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": (item.get("image_url") or {}).get("url"),
                        }
                    )
            items.append({"role": "user", "content": parts})
        else:
            items.append({"role": "user", "content": str(content or "")})
    return "\n\n".join(instructions), items


class MockGateway(BaseGateway):
    """Offline deterministic model used for demos, tests and first-run onboarding."""

    def __init__(self, config: AppConfig, *, delay: float = 0.004) -> None:
        super().__init__(config)
        self.delay = delay
        self._prefix_cache: list[tuple[str, int]] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        hit, miss = self._cache_stats(request.prefix_text)
        last = request.messages[-1] if request.messages else {}
        last_role = last.get("role")
        last_text = str(last.get("content") or "")
        all_user_text = "\n".join(
            str(message.get("content") or "") for message in request.messages if message.get("role") == "user"
        )
        reasoning = ""
        if request.reasoning_level != "off":
            reasoning = "我正在检查当前请求所需的上下文，并确认是否需要调用工具。"
            for piece in self._split(reasoning):
                await asyncio.sleep(self.delay)
                yield ModelChunk(kind="reasoning", text=piece)

        tool_calls: list[ToolCallRequest] = []
        content = ""
        if last_role == "system" and "后台任务完成通知" in last_text:
            content = (
                "已收到后台任务完成通知，我完成了自检：\n\n"
                f"- {last_text[:900]}\n\n"
                "任务结果已经落库；如果还需要汇总，我可以继续读取完整输出。"
            )
        elif last_role == "tool":
            content = self._after_tool(last_text, all_user_text)
        elif "[slow]" in last_text or "慢速" in last_text:
            async for chunk in self._slow_stream():
                yield chunk
            content = ""
        elif "ask" in last_text.lower() or "提问" in last_text or "问卷" in last_text:
            tool_calls = [
                ToolCallRequest(
                    id=f"call_ask_{int(time.time() * 1000)}",
                    name="ask_question",
                    arguments=json.dumps(
                        {
                            "questions": [
                                {
                                    "id": "direction",
                                    "header": "演示方向",
                                    "question": "你希望我接下来重点演示哪个能力？",
                                    "type": "single",
                                    "options": [
                                        {"value": "tools", "label": "工具调用与权限"},
                                        {"value": "stream", "label": "流式输出与恢复"},
                                        {"value": "goal", "label": "目标模式"},
                                    ],
                                    "allow_skip": True,
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            ]
        elif "permission" in last_text.lower() or "权限" in last_text:
            tool_calls = [
                ToolCallRequest(
                    id=f"call_perm_{int(time.time() * 1000)}",
                    name="shell",
                    arguments=json.dumps({"command": "echo permission-check"}, ensure_ascii=False),
                )
            ]
        elif "long" in last_text.lower() or "长任务" in last_text:
            tool_calls = [
                ToolCallRequest(
                    id=f"call_long_{int(time.time() * 1000)}",
                    name="shell_background",
                    arguments=json.dumps(
                        {
                            "command": "python3 -c \"import time; time.sleep(2); print('long-task-done')\"",
                            "description": "演示长任务完成通知",
                        },
                        ensure_ascii=False,
                    ),
                )
            ]
        elif "文件" in last_text or "file" in last_text.lower():
            tool_calls = [
                ToolCallRequest(
                    id=f"call_file_{int(time.time() * 1000)}",
                    name="list_dir",
                    arguments=json.dumps({"path": "."}),
                )
            ]
        else:
            content = (
                "你好，我是 **Mini Agent**。当前使用离线 Mock 模型运行，因此不需要 API Key。\n\n"
                f"你刚才说：{last_text.strip() or '（空消息）'}\n\n"
                "可以试试这些演示：\n\n"
                "- 输入 `权限`：触发 shell 权限确认\n"
                "- 输入 `长任务`：启动后台任务并验证完成通知\n"
                "- 输入 `提问`：弹出 ask_question 表单\n"
                "- 输入 `文件`：调用工作区文件工具\n"
                "- 输入 `慢速`：观察流式输出与断线恢复\n\n"
                "配置 `DEEPSEEK_API_KEY` 后即可切换到真实模型。"
            )

        if tool_calls:
            for call in tool_calls:
                preview = json.dumps({"name": call.name, "arguments": call.arguments}, ensure_ascii=False)
                for piece in self._split(f"准备调用工具 {call.name}。"):
                    await asyncio.sleep(self.delay)
                    yield ModelChunk(kind="content", text=piece)
                _ = preview
        else:
            for piece in self._split(content):
                await asyncio.sleep(self.delay)
                yield ModelChunk(kind="content", text=piece)

        stripped = re.sub(r"\[slow\]|慢速|提问|权限|长任务|文件", "", last_text, flags=re.IGNORECASE).strip()
        output_tokens = estimate_tokens(
            reasoning + content + (json.dumps([c.to_openai() for c in tool_calls]) if tool_calls else "")
        )
        total_input = max(1, estimate_tokens(request.prefix_text))
        miss = min(miss, total_input)
        usage = Usage(
            input_tokens=total_input,
            output_tokens=output_tokens,
            cache_hit_tokens=max(0, hit),
            cache_miss_tokens=max(0, total_input - hit),
        )
        self._remember(request.prefix_text)
        _ = stripped
        yield ModelChunk(
            kind="completed",
            content=content,
            reasoning=reasoning,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

    def _cache_stats(self, prefix: str) -> tuple[int, int]:
        best = 0
        for cached, _tokens in self._prefix_cache:
            if prefix.startswith(cached) and len(cached) > best:
                best = len(cached)
        hit = estimate_tokens(prefix[:best]) if best else 0
        return hit, max(0, estimate_tokens(prefix) - hit)

    def _remember(self, prefix: str) -> None:
        already = any(cached == prefix for cached, _ in self._prefix_cache)
        if not already:
            self._prefix_cache.append((prefix, estimate_tokens(prefix)))

    @staticmethod
    def _split(text: str) -> list[str]:
        if not text:
            return []
        size = 8
        return [text[index : index + size] for index in range(0, len(text), size)]

    async def _slow_stream(self) -> AsyncIterator[ModelChunk]:
        paragraph = (
            "这是一段用于验证流式输出与刷新恢复的较长演示文本。"
            "刷新页面后，后端仍在继续运行，前端会先拉取快照，再用 Last-Event-ID 续传事件。"
            "你可以观察到内容不会重复，也不会丢失。\n\n"
        )
        for index in range(12):
            piece = f"第 {index + 1} 段：" + paragraph
            for part in self._split(piece):
                await asyncio.sleep(0.02)
                yield ModelChunk(kind="content", text=part)

    @staticmethod
    def _after_tool(tool_content: str, user_text: str) -> str:
        try:
            parsed = json.loads(tool_content)
        except json.JSONDecodeError:
            parsed = {"raw": tool_content[:500]}
        code = parsed.get("code")
        data = parsed.get("data") or {}
        if isinstance(data, dict) and data.get("skipped"):
            return "你跳过了问题，本轮先到这里。需要时我可以继续。"
        if code not in (0, None):
            return (
                f"工具执行没有成功（`{parsed.get('code')}`：{parsed.get('message', '未知错误')}）。"
                "我会遵循指示，不重复使用相同参数调用。"
            )
        source = "长任务通知" if "任务" in user_text else "工具结果"
        answer = ""
        if isinstance(data, dict):
            if "answers" in data:
                answer = f"\n\n你的回答是：`{json.dumps(data['answers'], ensure_ascii=False)}`"
            elif "tasks" in data:
                answer = f"\n\n任务状态：`{json.dumps(data['tasks'], ensure_ascii=False)}`"
            elif "content" in data:
                answer = f"\n\n内容片段：\n```\n{str(data['content'])[:800]}\n```"
        return (
            f"已收到{source}，我已完成自检：\n\n"
            f"- 工具返回码：`{code}`\n"
            f"- 摘要：{(parsed.get('message') or 'ok')[:200]}"
            f"{answer}\n\n以上结果来自真实工具执行，而不是模型猜测。"
        )


class UnavailableGateway(BaseGateway):
    """Used when no provider is configured; API remains usable for settings."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        raise ModelError(
            "NO_MODEL",
            "尚未配置可用模型，请先在设置中配置服务商、API Key 和模型",
            raw_code="NO_MODEL",
            retryable=False,
        )
        yield ModelChunk(kind="completed")


def create_gateway(config: AppConfig, *, header_resolver=None) -> BaseGateway:
    if not config.providers:
        return UnavailableGateway(config)
    enabled = [provider for provider in config.providers if provider.enabled]
    if not enabled:
        return UnavailableGateway(config)
    runnable = [provider for provider in enabled if provider.protocol == "mock" or bool(provider.api_key)]
    if not runnable:
        return UnavailableGateway(config)
    if all(provider.protocol == "mock" for provider in enabled):
        return MockGateway(config)
    return OpenAICompatibleGateway(config, header_resolver=header_resolver)
