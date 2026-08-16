from dataclasses import dataclass
from typing import Literal, Protocol

from spaceten.kernel.energy import Energy


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class Usage:
    tokens_in: int
    tokens_out: int
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    messages: list[Message]
    tools: list[ToolSpec]
    budget_hint: Energy
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    text: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    energy: Energy


class ProviderError(Exception):
    """Adapter failure. Not a kernel event; the loop converts it to Failed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        usage: Usage | None = None,
        energy: Energy | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.usage = usage
        self.energy = energy
        super().__init__(message)


class Provider(Protocol):
    name: str

    def complete(self, req: CompletionRequest) -> CompletionResponse: ...
