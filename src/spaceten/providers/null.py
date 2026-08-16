from spaceten.kernel.energy import Energy
from spaceten.providers.base import (
    CompletionRequest,
    CompletionResponse,
    ToolCall,
    Usage,
)

_OUT_MD = "Summary of IN.txt.\n"

# Fresh argument dicts so a caller cannot mutate the script.
_SCRIPT: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("null-1", "list_dir", {"path": "."}),
    ("null-2", "read_file", {"path": "IN.txt"}),
    ("null-3", "write_file", {"path": "OUT.md", "content": _OUT_MD}),
    (
        "null-4",
        "finish",
        {"summary": "Summarized IN.txt into OUT.md", "artifact": "OUT.md"},
    ),
)


class NullProvider:
    name = "null"

    def __init__(self) -> None:
        self._step = 0

    def complete(self, req: CompletionRequest) -> CompletionResponse:
        del req
        if self._step < len(_SCRIPT):
            call_id, name, arguments = _SCRIPT[self._step]
            self._step += 1
            tool_calls = [ToolCall(call_id, name, dict(arguments))]
        else:
            # Empty tools so the future loop can emit Failed(no_tool_calls).
            tool_calls = []
        return CompletionResponse(
            text=None,
            tool_calls=tool_calls,
            usage=Usage(0, 0, 0),
            model="null",
            energy=Energy(5),
        )
