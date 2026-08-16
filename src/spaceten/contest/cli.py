import hashlib
import sys
from pathlib import Path
from typing import NoReturn

import typer

from spaceten.contest.anagrams import DEFAULT_ENERGY, FIXTURE_ID, NAMES_MD, RULES_MD
from spaceten.contest.pack import pack_world
from spaceten.contest.verify import verify_root
from spaceten.kernel.event import Act, Finish
from spaceten.kernel.world import World

contest_app = typer.Typer(
    name="contest",
    no_args_is_help=True,
    add_completion=False,
    help="Discrete-event STEN competitions.",
)


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _root(ctx: typer.Context) -> Path:
    obj = ctx.obj
    root = getattr(obj, "root", None)
    if not isinstance(root, Path):
        raise RuntimeError("cli state missing")
    return root


def _act(address: str, data: bytes) -> Act:
    return Act(
        tool="write_file",
        address=address,
        digest=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


@contest_app.command("init")
def contest_init(
    directory: Path = typer.Argument(
        Path("."),
        help="New contest world (created if needed).",
    ),
    fixture: str = typer.Option(FIXTURE_ID, "--fixture", help="Fixture id."),
    energy: int = typer.Option(
        DEFAULT_ENERGY,
        "--energy",
        min=0,
        help="Energy cap in millijoules.",
    ),
) -> None:
    """Copy a sealed fixture and initialize a world. Checker stays out of the jail."""
    if fixture != FIXTURE_ID:
        _fail(f"unknown fixture {fixture!r}; available: {FIXTURE_ID}")
    root = directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / ".spaceten").exists():
        _fail(f"world already exists: {root}")
    (root / "NAMES.md").write_text(NAMES_MD, encoding="utf-8")
    (root / "RULES.md").write_text(RULES_MD, encoding="utf-8")
    (root / "steps").mkdir(exist_ok=True)
    world = World.init(root, energy_cap=energy)
    typer.echo(
        f"contest {fixture} {world.header.id} energy_cap_mj={energy} "
        f"remaining={world.account.remaining.mj} mj"
    )


@contest_app.command("step")
def contest_step(
    ctx: typer.Context,
    address: str = typer.Argument(..., help="Workspace address, e.g. steps/01.md"),
    source: Path | None = typer.Option(
        None,
        "--from",
        help="Read bytes from this file (default: existing dest, else stdin).",
    ),
) -> None:
    """Commit an Act for a step or SOLUTION.md."""
    root = _root(ctx)
    if not (root / ".spaceten").exists():
        _fail(f"no .spaceten/ under {root}; run spaceten contest init")
    dest = root / address
    if source is not None:
        data = source.read_bytes()
    elif dest.is_file():
        data = dest.read_bytes()
    else:
        data = sys.stdin.buffer.read()
    if not data:
        _fail("no step content")
    try:
        world = World.load(root)
        receipt = world.propose("human", _act(address, data), data=data)
    except Exception as exc:
        _fail(str(exc))
    typer.echo(
        f"seq={receipt.event.seq} act {address} remaining={receipt.remaining.mj} mj"
    )


@contest_app.command("finish")
def contest_finish(ctx: typer.Context) -> None:
    """Commit Finish after SOLUTION.md."""
    root = _root(ctx)
    if not (root / ".spaceten").exists():
        _fail(f"no .spaceten/ under {root}; run spaceten contest init")
    try:
        world = World.load(root)
        receipt = world.propose(
            "human",
            Finish(summary="contest solution", artifact="SOLUTION.md"),
        )
    except Exception as exc:
        _fail(str(exc))
    typer.echo(f"seq={receipt.event.seq} finish remaining={receipt.remaining.mj} mj")


@contest_app.command("verify")
def contest_verify(ctx: typer.Context) -> None:
    """Judge the world. Does not live in the jail."""
    root = _root(ctx)
    if not (root / ".spaceten").exists():
        _fail(f"no .spaceten/ under {root}; run spaceten contest init")
    try:
        report = verify_root(root)
    except Exception as exc:
        _fail(str(exc))
    if report.ok:
        typer.echo("ok")
    else:
        typer.echo("fail")
        for issue in report.issues:
            typer.echo(f"{issue.code}: {issue.message}")
    typer.echo(f"energy_spent_mj={report.spent_mj}")
    typer.echo(f"events={report.events}")
    typer.echo(f"step_bytes={report.step_bytes}")
    typer.echo(f"remaining_mj={report.remaining_mj}")
    if not report.ok:
        raise typer.Exit(1)


@contest_app.command("pack")
def contest_pack(
    ctx: typer.Context,
    dest: Path | None = typer.Option(None, "--out", help="tarball path"),
    force: bool = typer.Option(False, "--force", help="Pack even if verify fails."),
) -> None:
    """Pack the world and print the score line."""
    root = _root(ctx)
    if not (root / ".spaceten").exists():
        _fail(f"no .spaceten/ under {root}; run spaceten contest init")
    try:
        report = verify_root(root)
    except Exception as exc:
        _fail(str(exc))
    if not report.ok and not force:
        for issue in report.issues:
            typer.echo(f"{issue.code}: {issue.message}", err=True)
        _fail("verify failed; pass --force to pack anyway")
    out = dest if dest is not None else Path(f"{root.name}.sten.tgz")
    pack_world(root, out)
    typer.echo(f"packed {out}")
    typer.echo(
        f"energy_spent_mj={report.spent_mj} events={report.events} "
        f"step_bytes={report.step_bytes}"
    )
