import hashlib
from dataclasses import dataclass

from spaceten.errors import StaleParent
from spaceten.kernel.energy import EnergyExhausted
from spaceten.kernel.event import Act, Finish, Observe, Op
from spaceten.kernel.space import (
    Address,
    CellNotFound,
    OutsideSpace,
    Workspace,
    WriteTooLarge,
)
from spaceten.kernel.world import Receipt, World
from spaceten.providers.base import Message, ToolCall, ToolSpec

SCHEMAS: list[ToolSpec] = [
    ToolSpec(
        "list_dir",
        "List a directory in the workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
    ),
    ToolSpec(
        "read_file",
        "Read a utf-8 (or hex-preview) file.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        "write_file",
        "Write a utf-8 file, creating parents.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        "finish",
        "End the run.",
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "artifact": {"type": "string"},
            },
            "required": ["summary"],
        },
    ),
]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    artifact: str | None = None
    receipt: Receipt | None = None
    error: str | None = None
    stop: bool = False


def to_message(call: ToolCall, result: ToolResult) -> Message:
    text = result.content if result.ok else (result.error or result.content or "error")
    return Message(role="tool", content=text, tool_call_id=call.id)


def dispatch(world: World, actor: str, call: ToolCall) -> ToolResult:
    if call.name == "list_dir":
        return _list_dir(world, actor, call.arguments)
    if call.name == "read_file":
        return _read_file(world, actor, call.arguments)
    if call.name == "write_file":
        return _write_file(world, actor, call.arguments)
    if call.name == "finish":
        return _finish(world, actor, call.arguments)
    return ToolResult(ok=False, content="", error="unknown_tool", stop=True)


def _workspace_of(world: World) -> Workspace:
    workspace = getattr(world, "_workspace")
    if not isinstance(workspace, Workspace):
        raise TypeError("world workspace missing")
    return workspace


def _str_arg(arguments: dict[str, object], key: str) -> str | None:
    if key not in arguments:
        return None
    value = arguments[key]
    if not isinstance(value, str):
        return None
    return value


def _fail(error: str, *, stop: bool) -> ToolResult:
    return ToolResult(ok=False, content="", error=error, stop=stop)


def _commit(
    world: World,
    actor: str,
    op: Op,
    *,
    content: str,
    data: bytes | None = None,
    artifact: str | None = None,
) -> ToolResult:
    try:
        # Observe/Act/Finish spend is None; the kernel prices I/O.
        if data is not None:
            receipt = world.propose(actor, op, data=data)
        else:
            receipt = world.propose(actor, op)
    except CellNotFound:
        return _fail("not_found", stop=False)
    except (OutsideSpace, EnergyExhausted, StaleParent, WriteTooLarge) as exc:
        return _fail(str(exc), stop=True)
    except (IsADirectoryError, NotADirectoryError) as exc:
        return _fail(str(exc), stop=True)
    return ToolResult(ok=True, content=content, artifact=artifact, receipt=receipt)


def _list_dir(world: World, actor: str, arguments: dict[str, object]) -> ToolResult:
    if "path" in arguments:
        path = _str_arg(arguments, "path")
        if path is None:
            return _fail("invalid_arguments", stop=True)
    else:
        path = "."
    result = _commit(
        world,
        actor,
        Observe(address=path, size_bytes=0, content_hash=None, listing=True),
        content="",
    )
    if not result.ok or result.receipt is None:
        return result
    cells = _workspace_of(world).list_dir(Address(path))
    listing = "\n".join(cell.address.path for cell in cells)
    return ToolResult(ok=True, content=listing, receipt=result.receipt)


def _read_file(world: World, actor: str, arguments: dict[str, object]) -> ToolResult:
    path = _str_arg(arguments, "path")
    if path is None:
        return _fail("invalid_arguments", stop=True)
    result = _commit(
        world,
        actor,
        Observe(address=path, size_bytes=0, content_hash=None),
        content="",
    )
    if not result.ok:
        return result
    receipt = result.receipt
    assert receipt is not None
    payload = receipt.bytes_read
    if payload is None:
        text = "(directory)"
    else:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            digest = receipt.cell.content_hash if receipt.cell is not None else ""
            text = f"binary sha256={digest} hex={payload[:32].hex()}"
    return ToolResult(ok=True, content=text, receipt=receipt)


def _write_file(world: World, actor: str, arguments: dict[str, object]) -> ToolResult:
    path = _str_arg(arguments, "path")
    content = _str_arg(arguments, "content")
    if path is None or content is None:
        return _fail("invalid_arguments", stop=True)
    data = content.encode("utf-8")
    op = Act(
        tool="write_file",
        address=path,
        digest=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
    return _commit(
        world,
        actor,
        op,
        data=data,
        content=f"wrote {path} ({len(data)} bytes)",
    )


def _finish(world: World, actor: str, arguments: dict[str, object]) -> ToolResult:
    summary = _str_arg(arguments, "summary")
    if summary is None:
        return _fail("invalid_arguments", stop=True)
    artifact: str | None
    if "artifact" not in arguments:
        artifact = None
    else:
        artifact = _str_arg(arguments, "artifact")
        if artifact is None:
            return _fail("invalid_arguments", stop=True)
    return _commit(
        world,
        actor,
        Finish(summary=summary, artifact=artifact),
        content=summary,
        artifact=artifact,
    )
