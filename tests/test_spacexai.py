import json
from pathlib import Path

import pytest

import spaceten
from spaceten import CompletionRequest, CompletionResponse, Provider, ProviderError
from spaceten.kernel.energy import MIN_COMPLETION_MJ, Energy
from spaceten.providers.base import Message, ToolCall, ToolSpec, Usage
from spaceten.providers.spacexai import (
    API_KEY_ENV,
    API_KEY_ENV_ALT,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    SpaceXAIProvider,
    _max_tokens_for,
    _post_json,
    _serialize_messages,
)


def _req(**overrides: object) -> CompletionRequest:
    payload: dict[str, object] = {
        "messages": [Message(role="user", content="Summarize IN.txt into OUT.md")],
        "tools": [],
        "budget_hint": Energy(100),
    }
    payload.update(overrides)
    return CompletionRequest(**payload)  # type: ignore[arg-type]


def _ok_body(
    *,
    text: str | None = "ok",
    tool_calls: list[dict[str, object]] | None = None,
    tokens_in: int = 4,
    tokens_out: int = 2,
    cached: int = 0,
    model: str = DEFAULT_MODEL,
) -> dict[str, object]:
    output: list[dict[str, object]] = []
    if text:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        )
    for call in tool_calls or []:
        output.append(
            {
                "type": "function_call",
                "call_id": call["id"],
                "name": call["name"],
                "arguments": json.dumps(call["arguments"]),
            }
        )
    return {
        "model": model,
        "output": output,
        "usage": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "input_tokens_details": {"cached_tokens": cached},
        },
    }


class _Capture:
    def __init__(
        self, status: int = 200, body: dict[str, object] | None = None
    ) -> None:
        self.status = status
        self.body = body if body is not None else _ok_body()
        self.calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> tuple[int, dict[str, object]]:
        self.calls.append((url, headers, payload))
        return self.status, self.body


def test_satisfies_provider_protocol() -> None:
    capture = _Capture()
    provider: Provider = SpaceXAIProvider(api_key="k", _post=capture)
    assert provider.name == "spacexai"
    response = provider.complete(_req())
    assert isinstance(response, CompletionResponse)
    assert response.model == DEFAULT_MODEL
    assert response.text == "ok"


def test_public_surface_does_not_export_adapter() -> None:
    assert not hasattr(spaceten, "SpaceXAIProvider")


def test_budget_below_minimum_does_not_http() -> None:
    capture = _Capture()
    provider = SpaceXAIProvider(api_key="k", _post=capture)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_req(budget_hint=Energy(MIN_COMPLETION_MJ - 1)))
    assert excinfo.value.code == "budget_too_small"
    assert capture.calls == []


def test_budget_cannot_afford_one_output_token() -> None:
    capture = _Capture()
    messages = [Message(role="user", content="x" * 80)]
    est = max(1, len(_serialize_messages(messages)) // 4)
    budget = Energy(est + 2)
    assert budget.mj >= MIN_COMPLETION_MJ
    provider = SpaceXAIProvider(api_key="k", _post=capture)
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_req(messages=messages, budget_hint=budget))
    assert excinfo.value.code == "budget_too_small"
    assert capture.calls == []


def test_max_tokens_derived_from_budget() -> None:
    capture = _Capture()
    req = _req(budget_hint=Energy(100))
    SpaceXAIProvider(api_key="k", _post=capture).complete(req)
    assert len(capture.calls) == 1
    url, _headers, payload = capture.calls[0]
    assert url == f"{DEFAULT_BASE_URL}/responses"
    est = max(1, len(_serialize_messages(req.messages)) // 4)
    assert payload["max_output_tokens"] == max(1, (100 - est) // 3)
    assert payload["max_output_tokens"] == _max_tokens_for(req)
    assert payload["reasoning_effort"] == DEFAULT_REASONING_EFFORT
    assert payload["reasoning"] == {"effort": DEFAULT_REASONING_EFFORT}
    assert payload["model"] == DEFAULT_MODEL


def test_caller_max_tokens_clamps_derived_bound() -> None:
    capture = _Capture()
    req = _req(budget_hint=Energy(100), max_tokens=2)
    derived = _max_tokens_for(_req(budget_hint=Energy(100)))
    assert derived > 2
    SpaceXAIProvider(api_key="k", _post=capture).complete(req)
    assert capture.calls[0][2]["max_output_tokens"] == 2


def test_token_to_mj_mapping() -> None:
    capture = _Capture(body=_ok_body(tokens_in=10, tokens_out=4, cached=0))
    response = SpaceXAIProvider(api_key="k", _post=capture).complete(_req())
    assert response.usage == Usage(10, 4, 0)
    assert response.energy == Energy(10 * 1 + 4 * 3)


def test_cached_tokens_recorded_at_zero_mj() -> None:
    capture = _Capture(body=_ok_body(tokens_in=10, tokens_out=4, cached=7))
    response = SpaceXAIProvider(api_key="k", _post=capture).complete(_req())
    assert response.usage == Usage(10, 4, 7)
    assert response.energy == Energy(10 + 12)


def test_tool_calls_from_function_call_items() -> None:
    capture = _Capture(
        body=_ok_body(
            text=None,
            tool_calls=[{"id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
        )
    )
    response = SpaceXAIProvider(api_key="k", _post=capture).complete(
        _req(
            tools=[
                ToolSpec(
                    "list_dir",
                    "List a directory.",
                    {"type": "object", "properties": {"path": {"type": "string"}}},
                )
            ]
        )
    )
    assert response.text is None
    assert response.tool_calls == [ToolCall("c1", "list_dir", {"path": "."})]
    tools = capture.calls[0][2]["tools"]
    assert tools == [
        {
            "type": "function",
            "name": "list_dir",
            "description": "List a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        }
    ]


def test_http_401_429_5xx_include_usage_when_present() -> None:
    cases = (
        (401, "http_401"),
        (429, "http_429"),
        (503, "http_5xx"),
    )
    for status, code in cases:
        capture = _Capture(
            status=status,
            body={
                "error": {"message": "nope"},
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 1,
                    "input_tokens_details": {"cached_tokens": 2},
                },
            },
        )
        with pytest.raises(ProviderError) as excinfo:
            SpaceXAIProvider(api_key="k", _post=capture).complete(_req())
        err = excinfo.value
        assert err.code == code
        assert err.message == "nope"
        assert err.usage == Usage(5, 1, 2)
        assert err.energy == Energy(5 + 3)


def test_timeout_is_provider_error_without_retry() -> None:
    calls = {"n": 0}

    def post(
        url: str, headers: dict[str, str], payload: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        del url, headers, payload
        calls["n"] += 1
        raise TimeoutError("timed out")

    with pytest.raises(ProviderError) as excinfo:
        SpaceXAIProvider(api_key="k", _post=post).complete(_req())
    assert excinfo.value.code == "timeout"
    assert excinfo.value.usage is None
    assert excinfo.value.energy is None
    assert calls["n"] == 1


def test_missing_api_key_is_401_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(API_KEY_ENV_ALT, raising=False)
    capture = _Capture()
    with pytest.raises(ProviderError) as excinfo:
        SpaceXAIProvider(_post=capture).complete(_req())
    assert excinfo.value.code == "http_401"
    assert capture.calls == []


def test_api_key_env_and_alt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setenv(API_KEY_ENV_ALT, "alt-key")
    capture = _Capture()
    SpaceXAIProvider(_post=capture).complete(_req())
    assert capture.calls[0][1]["Authorization"] == "Bearer alt-key"

    monkeypatch.setenv(API_KEY_ENV, "primary-key")
    capture = _Capture()
    SpaceXAIProvider(_post=capture).complete(_req())
    assert capture.calls[0][1]["Authorization"] == "Bearer primary-key"


def test_error_message_does_not_include_api_key() -> None:
    secret = "sk-secret-test-key"

    def post(
        url: str, headers: dict[str, str], payload: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        del url, headers, payload
        return 401, {"error": {"message": "unauthorized"}}

    with pytest.raises(ProviderError) as excinfo:
        SpaceXAIProvider(api_key=secret, _post=post).complete(_req())
    assert secret not in excinfo.value.message
    assert secret not in str(excinfo.value)


def test_post_json_maps_http_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from urllib.error import HTTPError, URLError

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "spaceten.providers.spacexai.urlopen", lambda *_a, **_k: _Resp()
    )
    status, body = _post_json("https://api.x.ai/v1/responses", {}, {"model": "x"})
    assert status == 200
    assert body == {"ok": True}

    def boom(*_a: object, **_k: object) -> object:
        raise HTTPError(
            "https://api.x.ai/v1/responses",
            429,
            "rate",
            None,  # type: ignore[arg-type]
            BytesIO(b'{"error":{"message":"slow"}}'),
        )

    monkeypatch.setattr("spaceten.providers.spacexai.urlopen", boom)
    status, body = _post_json("https://api.x.ai/v1/responses", {}, {})
    assert status == 429
    assert body["error"] == {"message": "slow"}

    def timed_out(*_a: object, **_k: object) -> object:
        raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr("spaceten.providers.spacexai.urlopen", timed_out)
    with pytest.raises(TimeoutError):
        _post_json("https://api.x.ai/v1/responses", {}, {})


def test_kernel_and_store_do_not_import_spacexai() -> None:
    root = Path(spaceten.__file__).resolve().parent
    for pkg in ("kernel", "store"):
        for path in (root / pkg).rglob("*.py"):
            text = path.read_text()
            assert "spacexai" not in text, path
            assert "XAI_API_KEY" not in text, path
            assert "api.x.ai" not in text, path
