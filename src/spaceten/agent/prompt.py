from spaceten.agent.tools import ToolResult
from spaceten.kernel.energy import MIN_COMPLETION_MJ
from spaceten.kernel.world import World
from spaceten.providers.base import Message

_SYSTEM = """You are a SpaceTEN agent. Every action is accounted in four primitives.

Space — the workspace is a jail. Use relative POSIX paths only. Never write
outside the world root. Never use '..', absolute paths, drive letters, or
'.spaceten'. list_dir before you invent a path. write_file creates parents.

Time — observe, plan, act, and finish are append-only events. You cannot
rewrite history. Finish or the kernel will record Failed.

Energy — plans and I/O cost millijoules (mj). remaining + spent == cap.
A completion is refused below {min_completion} mj. Finish before the
budget hits zero. Do not loop on the same observe.

Number — event ids are ULIDs. Bytes are hashed. Do not invent hashes.

Tools: list_dir, read_file, write_file, finish. There is no shell and no
network. Write one artifact that satisfies the goal, then call finish.

Status: remaining={remaining} mj seq={seq} cells={cells}
"""


def _cell_count(world: World) -> int:
    cells = getattr(world, "_cells", {})
    return len(cells) if isinstance(cells, dict) else 0


def system_message(world: World) -> Message:
    seq = world.head.seq if world.head is not None else 0
    return Message(
        role="system",
        content=_SYSTEM.format(
            min_completion=MIN_COMPLETION_MJ,
            remaining=world.account.remaining.mj,
            seq=seq,
            cells=_cell_count(world),
        ),
    )


def seed(world: World, goal: str, listing: ToolResult) -> list[Message]:
    user = goal
    if listing.ok and listing.content:
        user = f"{goal}\n\nWorkspace listing:\n{listing.content}"
    return [system_message(world), Message(role="user", content=user)]
