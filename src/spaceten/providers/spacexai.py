"""SpaceXAI adapter. Only provider module that talks to api.x.ai."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from spaceten.kernel.energy import MIN_COMPLETION_MJ, Energy
from spaceten.providers.base import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)

DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.6"
API_KEY_ENV = "XAI_API_KEY"
API_KEY_ENV_ALT = "SPACEXAI_API_KEY"
DEFAULT_REASONING_EFFORT = "low"

_INPUT_MJ = 1
_OUTPUT_MJ = 3
_TIMEOUT_S = 30.0

_Post = Callable[
    [str, dict[str, str], dict[str, object]],
    tuple[int, dict[str, object]],
]


class SpaceXAIProvider:
    name = "spacexai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        _post: _Post | None = None,
    ) -> None:
        self._api_key_arg = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._reasoning_effort = reasoning_effort
        self._post = _post

    def complete(self, req: CompletionRequest) -> CompletionResponse:
        max_tokens = _max_tokens_for(req)
        api_key = self._resolve_api_key()
        if not api_key:
            raise ProviderError("http_401", "missing API key")
        payload: dict[str, object] = {
            "model": self._model,
            "input": _input_items(req.messages),
            "tools": _tools_payload(req.tools),
            "max_output_tokens": max_tokens,
            # grok-4.6 defaults to high; low keeps reasoning tokens inside the bound
            "reasoning": {"effort": self._reasoning_effort},
            "reasoning_effort": self._reasoning_effort,
        }
        url = f"{self._base_url}/responses"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            status, body = self._send(url, headers, payload)
        except TimeoutError as exc:
            raise ProviderError("timeout", "request timed out") from exc
        if status == 401:
            raise _http_error("http_401", status, body)
        if status == 429:
            raise _http_error("http_429", status, body)
        if 500 <= status <= 599:
            raise _http_error("http_5xx", status, body)
        if status != 200:
            raise _http_error("invalid_response", status, body)
        return _completion_from(body, fallback_model=self._model)

    def _resolve_api_key(self) -> str | None:
        if self._api_key_arg:
            return self._api_key_arg
        return os.environ.get(API_KEY_ENV) or os.environ.get(API_KEY_ENV_ALT)

    def _send(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> tuple[int, dict[str, object]]:
        if self._post is not None:
            return self._post(url, headers, payload)
        return _post_json(url, headers, payload)


def _max_tokens_for(req: CompletionRequest) -> int:
    budget = req.budget_hint
    if budget.mj < MIN_COMPLETION_MJ:
        raise ProviderError(
            "budget_too_small",
            f"budget {budget.mj} mj is below MIN_COMPLETION_MJ {MIN_COMPLETION_MJ}",
        )
    serialized = _serialize_messages(req.messages)
    est = max(1, len(serialized) // 4)
    if est + 3 > budget.mj:
        raise ProviderError(
            "budget_too_small",
            f"estimated input {est} mj cannot afford one output token",
        )
    max_tokens = max(1, (budget.mj - est) // 3)
    if req.max_tokens is not None:
        max_tokens = min(req.max_tokens, max_tokens)
    return max_tokens


def _serialize_messages(messages: list[Message]) -> str:
    return json.dumps(_input_items(messages), separators=(",", ":"), ensure_ascii=False)


def _input_items(messages: list[Message]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for message in messages:
        if message.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id or "",
                    "output": message.content,
                }
            )
            continue
        if message.role == "assistant" and message.tool_calls:
            if message.content:
                items.append({"role": "assistant", "content": message.content})
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, separators=(",", ":")),
                    }
                )
            continue
        items.append({"role": message.role, "content": message.content})
    return items


def _tools_payload(tools: list[ToolSpec]) -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in tools
    ]


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=_TIMEOUT_S) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200))
    except HTTPError as exc:
        raw = exc.read()
        return exc.code, _json_object(raw, fallback_status=exc.code)
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise TimeoutError(str(reason)) from exc
        raise ProviderError("invalid_response", "request failed") from exc
    return status, _json_object(raw, fallback_status=status)


def _json_object(raw: bytes, *, fallback_status: int) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError(
            "invalid_response",
            f"non-JSON body (HTTP {fallback_status})",
        ) from exc
    if not isinstance(parsed, dict):
        raise ProviderError("invalid_response", "response JSON is not an object")
    return cast(dict[str, object], parsed)


def _http_error(code: str, status: int, body: dict[str, object]) -> ProviderError:
    usage, energy = _usage_energy(body)
    return ProviderError(code, _error_message(status, body), usage=usage, energy=energy)


def _error_message(status: int, body: dict[str, object]) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        message = err.get("message")
        if isinstance(message, str) and message:
            return message
    if isinstance(err, str) and err:
        return err
    return f"HTTP {status}"


def _usage_energy(body: dict[str, object]) -> tuple[Usage | None, Energy | None]:
    if "usage" not in body:
        return None, None
    usage = _usage_from(body)
    return usage, _energy_from(usage)


def _usage_from(body: dict[str, object]) -> Usage:
    usage_obj = body.get("usage")
    if not isinstance(usage_obj, dict):
        return Usage(0, 0, 0)
    tokens_in = _as_int(usage_obj.get("input_tokens"), usage_obj.get("prompt_tokens"))
    tokens_out = _as_int(
        usage_obj.get("output_tokens"), usage_obj.get("completion_tokens")
    )
    cached = _as_int(usage_obj.get("cached_tokens"))
    details = usage_obj.get("input_tokens_details") or usage_obj.get(
        "prompt_tokens_details"
    )
    if isinstance(details, dict):
        cached = _as_int(details.get("cached_tokens"), cached)
    return Usage(tokens_in, tokens_out, cached)


def _energy_from(usage: Usage) -> Energy:
    return Energy(usage.tokens_in * _INPUT_MJ + usage.tokens_out * _OUTPUT_MJ)


def _as_int(value: object, default: object = 0) -> int:
    if isinstance(value, bool) or value is None:
        return _as_int(default, 0) if not isinstance(default, int) else default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(default, int):
        return default
    return 0


def _completion_from(
    body: dict[str, object], *, fallback_model: str
) -> CompletionResponse:
    usage = _usage_from(body)
    model = body.get("model")
    return CompletionResponse(
        text=_text_from(body),
        tool_calls=_tool_calls_from(body),
        usage=usage,
        model=model if isinstance(model, str) and model else fallback_model,
        energy=_energy_from(usage),
    )


def _text_from(body: dict[str, object]) -> str | None:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text
    output = body.get("output")
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "output_text":
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "".join(parts) if parts else None


def _tool_calls_from(body: dict[str, object]) -> list[ToolCall]:
    output = body.get("output")
    if not isinstance(output, list):
        return []
    calls: list[ToolCall] = []
    for item in output:
        if isinstance(item, dict) and item.get("type") == "function_call":
            calls.append(_tool_call_from(item))
    return calls


def _tool_call_from(item: dict[str, object]) -> ToolCall:
    raw_id = item.get("call_id") or item.get("id") or ""
    raw_name = item.get("name") or ""
    arguments = _arguments_from(item.get("arguments"))
    return ToolCall(
        id=raw_id if isinstance(raw_id, str) else "",
        name=raw_name if isinstance(raw_name, str) else "",
        arguments=arguments,
    )


def _arguments_from(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items()}
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): value for key, value in parsed.items()}
