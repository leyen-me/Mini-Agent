"""Token estimation and deterministic usage accounting helpers."""

from __future__ import annotations

import math
import unicodedata

from .config import ModelConfig


def estimate_tokens(text: str | None) -> int:
    """Cheap local estimate used for budgets and the context meter.

    ASCII runs are about four characters per token; CJK characters are counted as one
    token each. Provider-reported usage always takes precedence for billing.
    """

    if not text:
        return 0
    ascii_chars = 0
    other_chars = 0
    for char in text:
        if ord(char) < 128:
            ascii_chars += 1
        elif unicodedata.east_asian_width(char) in {"W", "F"}:
            other_chars += 1
        else:
            other_chars += 0.5
    return max(1, math.ceil(ascii_chars / 4 + other_chars))


def estimate_messages_tokens(messages: list[dict]) -> int:
    import json

    total = 0
    for message in messages:
        total += 4
        total += estimate_tokens(str(message.get("content") or ""))
        total += estimate_tokens(str(message.get("reasoning_content") or ""))
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False, separators=(",", ":")))
    return total


def calculate_cost(
    model: ModelConfig,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
) -> float:
    pricing = model.pricing
    uncached = cache_miss_tokens or max(0, input_tokens - cache_hit_tokens)
    cost = (
        cache_hit_tokens / 1_000_000 * pricing.cache_hit_per_million
        + uncached / 1_000_000 * pricing.input_per_million
        + output_tokens / 1_000_000 * pricing.output_per_million
    )
    return round(cost, 8)
