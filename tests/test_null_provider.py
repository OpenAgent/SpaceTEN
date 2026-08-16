import ast
from pathlib import Path

import pytest

import spaceten
from spaceten import CompletionRequest, CompletionResponse, Provider, ProviderError
from spaceten.kernel.energy import Energy
from spaceten.providers.base import Message, ToolCall, ToolSpec, Usage
from spaceten.providers.null import NullProvider

_HTTP_MODULES = frozenset(
    {
        "aiohttp",
        "http.client",
        "httpx",
        "openai",
        "requests",
        "urllib",
        "urllib.request",
        "urllib3",
        "xai_sdk",
    }
)


def _req(**overrides: object) -> CompletionRequest:
    payload: dict[str, object] = {
        "messages": [Message(role="user", content="Summarize IN.txt into OUT.md")],
        "tools": [],
        "budget_hint": Energy(100),
    }
    payload.update(overrides)
    return CompletionRequest(**payload)  # type: ignore[arg-type]


def _complete_meta(response: CompletionResponse) -> None:
    assert response.text is None
    assert response.usage == Usage(0, 0, 0)
    assert response.model == "null"
    assert response.energy == Energy(5)


def test_public_provider_exports() -> None:
    assert spaceten.Provider is Provider
    assert spaceten.CompletionRequest is CompletionRequest
    assert spaceten.CompletionResponse is CompletionResponse
    assert spaceten.ProviderError is ProviderError
    assert not hasattr(spaceten, "NullProvider")
    assert not hasattr(spaceten, "debit")


def test_null_provider_satisfies_protocol() -> None:
    provider: Provider = NullProvider()
    assert provider.name == "null"
    assert isinstance(provider.complete(_req()), CompletionResponse)


def test_workshop_script_one_tool_per_complete() -> None:
    provider = NullProvider()
    expected: tuple[tuple[str, str, dict[str, object]], ...] = (
        ("null-1", "list_dir", {"path": "."}),
        ("null-2", "read_file", {"path": "IN.txt"}),
        (
            "null-3",
            "write_file",
            {"path": "OUT.md", "content": "Summary of IN.txt.\n"},
        ),
        (
            "null-4",
            "finish",
            {"summary": "Summarized IN.txt into OUT.md", "artifact": "OUT.md"},
        ),
    )
    for call_id, name, arguments in expected:
        response = provider.complete(_req())
        _complete_meta(response)
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert call == ToolCall(call_id, name, arguments)


def test_exhausted_script_returns_empty_tool_calls() -> None:
    provider = NullProvider()
    for _ in range(4):
        provider.complete(_req())
    response = provider.complete(_req())
    _complete_meta(response)
    assert response.tool_calls == []
    again = provider.complete(_req())
    _complete_meta(again)
    assert again.tool_calls == []


def test_instances_do_not_share_cursor() -> None:
    first = NullProvider()
    second = NullProvider()
    assert first.complete(_req()).tool_calls[0].name == "list_dir"
    assert second.complete(_req()).tool_calls[0].name == "list_dir"


def test_complete_ignores_request_body() -> None:
    provider = NullProvider()
    response = provider.complete(
        _req(
            messages=[
                Message(role="system", content="other goal"),
                Message(role="user", content="do something else"),
            ],
            tools=[
                ToolSpec(
                    "list_dir",
                    "List a directory in the workspace.",
                    {"type": "object", "properties": {"path": {"type": "string"}}},
                )
            ],
            budget_hint=Energy(8),
            max_tokens=1,
        )
    )
    assert response.tool_calls[0].name == "list_dir"
    assert response.tool_calls[0].arguments == {"path": "."}


def test_script_arguments_are_not_shared() -> None:
    provider = NullProvider()
    first = provider.complete(_req()).tool_calls[0]
    first.arguments["path"] = "mutated"
    again = NullProvider().complete(_req()).tool_calls[0]
    assert again.arguments == {"path": "."}


def test_message_and_request_defaults() -> None:
    message = Message(role="assistant", content="")
    assert message.tool_call_id is None
    assert message.tool_calls == ()
    request = CompletionRequest(
        messages=[message],
        tools=[],
        budget_hint=Energy(8),
    )
    assert request.max_tokens is None
    with pytest.raises(AttributeError):
        request.max_tokens = 4  # type: ignore[misc]


def test_provider_error_keyword_only_usage() -> None:
    bare = ProviderError("timeout", "boom")
    assert bare.code == "timeout"
    assert bare.message == "boom"
    assert bare.usage is None
    assert bare.energy is None
    assert "boom" in str(bare)
    filled = ProviderError(
        "http_429",
        "rate limited",
        usage=Usage(1, 2, 3),
        energy=Energy(7),
    )
    assert filled.usage == Usage(1, 2, 3)
    assert filled.energy == Energy(7)
    with pytest.raises(TypeError):
        ProviderError("timeout", "boom", Usage(0, 0))  # type: ignore[misc]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_kernel_and_store_do_not_import_providers() -> None:
    root = Path(spaceten.__file__).resolve().parent
    for pkg in ("kernel", "store"):
        for path in (root / pkg).rglob("*.py"):
            imported = _imported_modules(path)
            assert not any(
                name == "spaceten.providers" or name.startswith("spaceten.providers.")
                for name in imported
            ), path


def test_providers_have_no_http_imports() -> None:
    root = Path(spaceten.__file__).resolve().parent / "providers"
    for path in root.rglob("*.py"):
        imported = _imported_modules(path)
        assert imported.isdisjoint(_HTTP_MODULES), path
