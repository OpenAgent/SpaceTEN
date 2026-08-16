import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import typer

from spaceten import __version__
from spaceten.cli.render import (
    render_check,
    render_event,
    render_ledger,
    render_log,
    render_status,
)
from spaceten.errors import (
    DirtySpace,
    InvariantError,
    TruncatedLog,
    WorldExists,
    WorldLocked,
)
from spaceten.kernel.energy import AccountView, Energy, EnergyExhausted
from spaceten.kernel.event import Act, Event, Init, Observe
from spaceten.kernel.invariant import invariant_issues, spend_from
from spaceten.kernel.space import (
    Address,
    Cell,
    CellNotFound,
    OutsideSpace,
    Workspace,
    WriteTooLarge,
)
from spaceten.kernel.world import CheckIssue, CheckReport, World
from spaceten.store.jsonl import JsonlStore
from spaceten.store.protocol import WorldHeader

_DEFAULT_ENERGY = 100_000
_RESERVED = ".spaceten"

app = typer.Typer(name="spaceten", no_args_is_help=True, add_completion=False)


@dataclass
class _Opts:
    root: Path


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _opts(ctx: typer.Context) -> _Opts:
    obj = ctx.obj
    if not isinstance(obj, _Opts):
        raise RuntimeError("cli state missing")
    return obj


def _require_world(root: Path) -> None:
    if not (root / _RESERVED).exists():
        _fail(f"no {_RESERVED}/ under {root}; run spaceten init")


def _spent_mj(events: Sequence[Event]) -> int:
    return sum(s.amount.mj for event in events if (s := spend_from(event)))


def _replay_account(events: Sequence[Event], cap: int) -> AccountView:
    spent = _spent_mj(events)
    remaining = cap - spent
    # Energy forbids negatives; check records I1 from the raw remaining.
    if remaining < 0:
        remaining = 0
    return AccountView(Energy(cap), Energy(spent), Energy(remaining))


def _in_jail(root: Path) -> Callable[[str], bool]:
    workspace = Workspace(root)

    def check(address: str) -> bool:
        try:
            workspace.resolve(Address(address))
        except OutsideSpace:
            return False
        return True

    return check


def _snapshot(
    root: Path,
) -> tuple[WorldHeader, list[Event], AccountView, dict[str, Cell]]:
    # lock-free: skip a torn last line the same way readers must
    store = JsonlStore(root, skip_torn=True)
    header = store.load_header()
    events = store.load_events()
    account = _replay_account(events, header.energy_cap)
    caches = store.load_caches()
    cells = caches[1] if caches is not None else {}
    return header, events, account, cells


def _address_from_path(root: Path, path: Path) -> str:
    if path.is_absolute():
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise OutsideSpace(str(path)) from exc
        text = relative.as_posix()
        return "." if text == "." else text
    text = path.as_posix()
    while text.startswith("./"):
        text = text[2:]
    return text or "."


def _observe_op(root: Path, path: Path) -> Observe:
    address = _address_from_path(root, path)
    listing = False
    try:
        resolved = Workspace(root).resolve(Address(address))
    except OutsideSpace:
        resolved = None
    if resolved is not None and resolved.is_dir():
        listing = True
    return Observe(
        address=address,
        size_bytes=0,
        content_hash=None,
        listing=listing,
    )


def _cli_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, WorldExists):
        _fail(f"world already exists: {exc}")
    if isinstance(exc, TruncatedLog):
        _fail(f"{exc}; pass check --truncate-partial")
    _fail(str(exc))


def _dirty_space_issues(root: Path, events: Sequence[Event]) -> list[CheckIssue]:
    last: dict[str, str] = {}
    for event in events:
        op = event.op
        if isinstance(op, Act):
            last[op.address] = op.digest
        elif isinstance(op, Observe) and not op.listing and op.content_hash is not None:
            last[op.address] = op.content_hash
    workspace = Workspace(root)
    issues: list[CheckIssue] = []
    for address, digest in last.items():
        try:
            payload = workspace.read(Address(address))
        except (CellNotFound, OutsideSpace, IsADirectoryError, OSError):
            issues.append(CheckIssue("dirty_space", f"{address} missing or unreadable"))
            continue
        if hashlib.sha256(payload).hexdigest() != digest:
            issues.append(CheckIssue("dirty_space", f"{address} hash mismatch"))
    return issues


def _integrity_report(root: Path, *, rebuild: bool) -> CheckReport:
    """I1–I7 via spend_from. Does not debit (overspend must still report)."""
    header, events, account, _cells = _snapshot(root)
    remaining_mj = header.energy_cap - _spent_mj(events)
    issues: list[CheckIssue] = []
    if remaining_mj < 0:
        issues.append(CheckIssue("I1", f"remaining {remaining_mj} mj is negative"))
    if rebuild:
        caches = JsonlStore(root, skip_torn=True).load_caches()
        if caches is not None and caches[0] != account:
            issues.append(
                CheckIssue(
                    "cache_drift",
                    "energy cache does not match spend_from replay",
                )
            )
        issues.extend(_dirty_space_issues(root, events))
    if events and isinstance(events[0].op, Init):
        if events[0].op.energy_cap_mj != header.energy_cap:
            issues.append(
                CheckIssue(
                    "cache_drift",
                    "header energy_cap does not match Init.energy_cap_mj",
                )
            )
    for code, message in invariant_issues(events, account, in_jail=_in_jail(root)):
        issues.append(CheckIssue(code, message))
    return CheckReport(
        ok=not issues,
        issues=tuple(issues),
        events=len(events),
        remaining=account.remaining,
    )


@app.callback()
def main(
    ctx: typer.Context,
    root: Path = typer.Option(
        Path("."),
        "--root",
        envvar="SPACETEN_ROOT",
        help="World root directory.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="INFO logging."),
    debug: bool = typer.Option(False, "--debug", help="DEBUG logging."),
) -> None:
    """SpaceTEN — Space Time Energy Number."""
    resolved = root.expanduser().resolve()
    ctx.obj = _Opts(root=resolved)
    logger = logging.getLogger("spaceten")
    if debug:
        logger.setLevel(logging.DEBUG)
    elif verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def init(
    ctx: typer.Context,
    directory: Path | None = typer.Argument(
        None,
        help="Directory to initialize (default: --root / cwd).",
    ),
    energy: int = typer.Option(
        _DEFAULT_ENERGY,
        "--energy",
        min=0,
        help="Energy cap in millijoules. Not written to config.toml.",
    ),
) -> None:
    """Create a world. Fails if .spaceten/ already exists."""
    if directory is not None:
        root = directory.expanduser().resolve()
    else:
        root = _opts(ctx).root
    try:
        world = World.init(root, energy_cap=energy)
    except (WorldExists, WorldLocked, ValueError, OSError) as exc:
        _cli_error(exc)
    header = world.header
    remaining = world.account.remaining.mj
    typer.echo(
        f"initialized {header.id} energy_cap_mj={header.energy_cap} "
        f"remaining={remaining} mj"
    )


@app.command()
def status(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit observability metrics."),
) -> None:
    """Print id, seq, remaining/spent/cap, and cell count."""
    root = _opts(ctx).root
    _require_world(root)
    try:
        header, events, account, cells = _snapshot(root)
    except (FileNotFoundError, ValueError, TruncatedLog) as exc:
        _cli_error(exc)
    issues = invariant_issues(events, account, in_jail=_in_jail(root))
    typer.echo(
        render_status(
            header,
            events,
            account,
            cells=len(cells),
            invariant_failures=len(issues),
            as_json=as_json,
        )
    )


@app.command()
def observe(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="Workspace path to observe."),
) -> None:
    """Commit a human-authored Observe."""
    root = _opts(ctx).root
    _require_world(root)
    try:
        world = World.load(root)
        receipt = world.propose("human", _observe_op(root, path))
    except (
        WorldLocked,
        TruncatedLog,
        DirtySpace,
        OutsideSpace,
        CellNotFound,
        EnergyExhausted,
        InvariantError,
        WriteTooLarge,
        FileNotFoundError,
        NotADirectoryError,
        IsADirectoryError,
        ValueError,
    ) as exc:
        _cli_error(exc)
    typer.echo(f"{render_event(receipt.event)} remaining={receipt.remaining.mj} mj")


@app.command("log")
def log_cmd(
    ctx: typer.Context,
    n: int = typer.Option(20, "--n", min=0, help="Number of trailing events."),
) -> None:
    """Tail events (lock-free)."""
    root = _opts(ctx).root
    _require_world(root)
    try:
        _header, events, _account, _cells = _snapshot(root)
    except (FileNotFoundError, ValueError, TruncatedLog) as exc:
        _cli_error(exc)
    tail = events[-n:] if n else []
    text = render_log(tail)
    if text:
        typer.echo(text)


@app.command()
def ledger(ctx: typer.Context) -> None:
    """Print spends via spend_from and the I2 remaining+spent==cap line."""
    root = _opts(ctx).root
    _require_world(root)
    try:
        _header, events, account, _cells = _snapshot(root)
    except (FileNotFoundError, ValueError, TruncatedLog) as exc:
        _cli_error(exc)
    typer.echo(render_ledger(events, account))


@app.command()
def check(
    ctx: typer.Context,
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Replay energy and report dirty_space.",
    ),
    truncate_partial: bool = typer.Option(
        False,
        "--truncate-partial",
        help="Drop a torn last JSONL line. Refused on a clean file.",
    ),
) -> None:
    """Check invariants I1–I7."""
    root = _opts(ctx).root
    _require_world(root)
    try:
        world = World.load(root, truncate_partial=truncate_partial)
        report = world.check(rebuild=rebuild)
    except EnergyExhausted:
        # load debit cannot represent remaining < 0; report I1/I2 from spend_from
        report = _integrity_report(root, rebuild=rebuild)
    except TruncatedLog as exc:
        _cli_error(exc)
    except (
        WorldLocked,
        DirtySpace,
        InvariantError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        _cli_error(exc)
    typer.echo(render_check(report))
    if not report.ok:
        raise typer.Exit(1)
