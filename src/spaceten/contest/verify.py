from dataclasses import dataclass
from pathlib import Path

from spaceten.contest.anagrams import (
    NAMES,
    PAIRS,
    TERMS,
    name_letters,
    normalize_name,
    term_letters,
)
from spaceten.contest.letters import is_submultiset
from spaceten.contest.parse import (
    Extract,
    Inventory,
    Pair,
    Retract,
    parse_solution,
    parse_step,
)
from spaceten.kernel.energy import AccountView
from spaceten.kernel.event import Act, Event, Observe
from spaceten.kernel.invariant import spend_from
from spaceten.kernel.world import CheckReport, World
from spaceten.store.jsonl import JsonlStore


@dataclass(frozen=True)
class ContestIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ContestReport:
    ok: bool
    issues: tuple[ContestIssue, ...]
    spent_mj: int
    events: int
    step_bytes: int
    remaining_mj: int


def _known_name(name: str) -> str | None:
    key = normalize_name(name)
    for display in NAMES:
        if normalize_name(display) == key:
            return display
    return None


def verify_root(root: Path) -> ContestReport:
    world = World.load(root)
    kernel = world.check(rebuild=True)
    events = JsonlStore(root, skip_torn=True).load_events()
    return verify_events(root, world.account, events, kernel)


def verify_events(
    root: Path,
    account: AccountView,
    events: list[Event],
    kernel: CheckReport,
) -> ContestReport:
    issues: list[ContestIssue] = []
    if not kernel.ok:
        for item in kernel.issues:
            issues.append(ContestIssue(item.code, item.message))
        return _report(issues, account, events, root)

    observed_names = False
    for event in events:
        if isinstance(event.op, Observe) and event.op.address in {"NAMES.md", "."}:
            observed_names = True
    if not observed_names:
        issues.append(
            ContestIssue("names_unobserved", "Observe NAMES.md (or .) before solving")
        )

    step_acts: list[tuple[Event, Path]] = []
    solution_event: Event | None = None
    for event in events:
        if not isinstance(event.op, Act):
            continue
        address = event.op.address
        if address == "SOLUTION.md":
            solution_event = event
            continue
        if address.startswith("steps/") and not address.endswith("/"):
            step_acts.append((event, root / address))

    if solution_event is not None and any(
        event.seq > solution_event.seq for event, _path in step_acts
    ):
        issues.append(
            ContestIssue(
                "solution_too_early",
                "SOLUTION.md must be after every steps/ Act",
            )
        )

    extracted: set[str] = set()
    paired: dict[str, frozenset[str]] = {}
    saw_inventory = False
    saw_extract = False

    cutoff = solution_event.seq if solution_event is not None else 10**9
    for event, path in step_acts:
        if event.seq > cutoff:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            move = parse_step(text)
        except (OSError, ValueError) as exc:
            issues.append(ContestIssue("parse_error", f"{path.name}: {exc}"))
            continue
        before = len(issues)
        _apply_move(move, extracted, paired, issues)
        if isinstance(move, Inventory) and len(issues) == before:
            saw_inventory = True
        elif isinstance(move, Extract) and len(issues) == before:
            saw_extract = True

    if not saw_inventory:
        issues.append(ContestIssue("no_inventory", "need at least one inventory step"))
    if not saw_extract:
        issues.append(ContestIssue("no_extract", "need at least one extract step"))

    expected_keys = {normalize_name(n) for n in NAMES}
    if set(paired) != expected_keys:
        missing = expected_keys - set(paired)
        extra = set(paired) - expected_keys
        if missing:
            issues.append(
                ContestIssue("missing_pair", f"unpaired names: {sorted(missing)}")
            )
        if extra:
            issues.append(
                ContestIssue("bad_pair", f"unknown paired names: {sorted(extra)}")
            )

    if solution_event is None:
        issues.append(ContestIssue("solution_missing", "commit SOLUTION.md as an Act"))
        return _report(issues, account, events, root)

    try:
        terms, solution_pairs = parse_solution(
            (root / "SOLUTION.md").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        issues.append(ContestIssue("parse_error", f"SOLUTION.md: {exc}"))
        return _report(issues, account, events, root)

    if terms != TERMS:
        issues.append(
            ContestIssue("solution_mismatch", "TERMS are not the four source words")
        )
    if solution_pairs != PAIRS:
        issues.append(
            ContestIssue("solution_mismatch", "pairs do not match the sealed key")
        )

    return _report(issues, account, events, root)


def _apply_move(
    move: Inventory | Extract | Pair | Retract,
    extracted: set[str],
    paired: dict[str, frozenset[str]],
    issues: list[ContestIssue],
) -> None:
    if isinstance(move, Inventory):
        display = _known_name(move.name)
        if display is None:
            issues.append(ContestIssue("bad_inventory", f"unknown name {move.name!r}"))
            return
        if tuple(move.letters) != name_letters(display):
            issues.append(
                ContestIssue("bad_inventory", f"letters do not match {display!r}")
            )
        return
    if isinstance(move, Extract):
        word = move.word.upper()
        if word not in TERMS:
            issues.append(ContestIssue("bad_extract", f"{word} is not a source word"))
            return
        if len(move.evidence) < 1:
            issues.append(
                ContestIssue("bad_extract", f"{word} needs at least one from: name")
            )
            return
        bag = term_letters(word)
        ok = True
        for name in move.evidence:
            display = _known_name(name)
            if display is None:
                issues.append(
                    ContestIssue("bad_extract", f"unknown evidence name {name!r}")
                )
                ok = False
                continue
            if not is_submultiset(bag, name_letters(display)):
                issues.append(
                    ContestIssue(
                        "bad_extract", f"{word} is not in the letters of {display}"
                    )
                )
                ok = False
        if ok:
            extracted.add(word)
        return
    if isinstance(move, Pair):
        display = _known_name(move.name)
        if display is None:
            issues.append(ContestIssue("bad_pair", f"unknown name {move.name!r}"))
            return
        left, right = move.words
        if left not in extracted or right not in extracted:
            issues.append(
                ContestIssue(
                    "bad_pair", f"extract {left} and {right} before pairing {display}"
                )
            )
            return
        if left == right:
            issues.append(ContestIssue("bad_pair", "a pair uses two different words"))
            return
        union = tuple(sorted(term_letters(left) + term_letters(right)))
        if union != name_letters(display):
            issues.append(ContestIssue("bad_pair", f"{display} is not {left}+{right}"))
            return
        paired[normalize_name(display)] = frozenset({left, right})
        return
    if move.target == "extract":
        if not move.word:
            issues.append(ContestIssue("bad_retract", "extract retract needs word"))
            return
        extracted.discard(move.word.upper())
        gone = move.word.upper()
        for key, words in list(paired.items()):
            if gone in words:
                del paired[key]
        return
    if not move.name:
        issues.append(ContestIssue("bad_retract", "pair retract needs name"))
        return
    paired.pop(normalize_name(move.name), None)


def _report(
    issues: list[ContestIssue],
    account: AccountView,
    events: list[Event],
    root: Path,
) -> ContestReport:
    step_bytes = 0
    steps = root / "steps"
    if steps.is_dir():
        for path in steps.rglob("*"):
            if path.is_file():
                step_bytes += path.stat().st_size
    spent = sum(s.amount.mj for event in events if (s := spend_from(event)))
    return ContestReport(
        ok=not issues,
        issues=tuple(issues),
        spent_mj=spent,
        events=len(events),
        step_bytes=step_bytes,
        remaining_mj=account.remaining.mj,
    )
