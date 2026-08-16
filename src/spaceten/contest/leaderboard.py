from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from spaceten.contest.pack import unpack_world
from spaceten.contest.verify import ContestReport, verify_root
from spaceten.kernel.event import Plan
from spaceten.store.jsonl import JsonlStore


@dataclass(frozen=True)
class BoardRow:
    name: str
    pack: Path
    division: str
    ok: bool
    spent_mj: int
    events: int
    step_bytes: int
    issues: tuple[str, ...]


def detect_division(root: Path) -> str:
    events = JsonlStore(root, skip_torn=True).load_events()
    for event in events:
        if isinstance(event.op, Plan):
            return "agent"
    return "human"


def pack_name(archive: Path) -> str:
    name = archive.name
    suffix = ".sten.tgz"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return archive.stem


def score_pack(archive: Path, scratch: Path) -> BoardRow:
    dest = scratch / pack_name(archive)
    try:
        root = unpack_world(archive, dest)
        report = verify_root(root)
        division = detect_division(root)
        return _row(archive, division, report)
    except Exception as exc:
        return BoardRow(
            name=pack_name(archive),
            pack=archive,
            division="unknown",
            ok=False,
            spent_mj=0,
            events=0,
            step_bytes=0,
            issues=(f"pack_error: {exc}",),
        )


def _row(archive: Path, division: str, report: ContestReport) -> BoardRow:
    return BoardRow(
        name=pack_name(archive),
        pack=archive,
        division=division,
        ok=report.ok,
        spent_mj=report.spent_mj,
        events=report.events,
        step_bytes=report.step_bytes,
        issues=tuple(f"{i.code}: {i.message}" for i in report.issues),
    )


def rank(rows: list[BoardRow]) -> list[BoardRow]:
    return sorted(
        rows,
        key=lambda r: (not r.ok, r.spent_mj, r.events, r.step_bytes, r.name),
    )


def scan_packs(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.sten.tgz"))


def render_markdown(rows: list[BoardRow]) -> str:
    lines = [
        "# STEN anagrams leaderboard",
        "",
        "Valid runs first. Rank is Energy (mj), then events, then `steps/` bytes.",
        "Human and agent divisions are listed together.",
        "`division` is inferred from the event log.",
        "",
        "| Rank | Run | Division | Energy | Events | Step bytes | Status |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    rank_n = 0
    for row in rows:
        if row.ok:
            rank_n += 1
            place = str(rank_n)
            status = "ok"
        else:
            place = "—"
            status = row.issues[0] if row.issues else "fail"
        lines.append(
            f"| {place} | `{row.name}` | {row.division} | {row.spent_mj} | "
            f"{row.events} | {row.step_bytes} | {status} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_text(rows: list[BoardRow]) -> str:
    lines = [
        "rank  division  energy  events  step_bytes  run",
        "----  --------  ------  ------  ----------  ---",
    ]
    rank_n = 0
    for row in rows:
        if row.ok:
            rank_n += 1
            place = f"{rank_n:>4}"
        else:
            place = "   —"
        lines.append(
            f"{place}  {row.division:<8}  {row.spent_mj:>6}  {row.events:>6}  "
            f"{row.step_bytes:>10}  {row.name}"
        )
        if not row.ok:
            for issue in row.issues:
                lines.append(f"      {issue}")
    return "\n".join(lines) + "\n"
