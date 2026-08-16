from collections.abc import Callable, Sequence

from spaceten.kernel.energy import AccountView, Energy, Spend
from spaceten.kernel.event import Act, Event, Failed, Observe, Plan
from spaceten.kernel.number import Id


def spend_from(event: Event) -> Spend | None:
    """Rebuild a Spend from a committed Event. The only I3 path."""
    if event.energy_delta_mj == 0:
        return None
    meta: dict[str, object] = {}
    if isinstance(event.op, Plan | Failed):
        meta = {
            "tokens_in": event.op.tokens_in,
            "tokens_out": event.op.tokens_out,
        }
    return Spend(
        event_id=Id(event.id),
        amount=Energy(-event.energy_delta_mj),
        reason="io" if event.energy_reason == "io" else "plan",
        meta=meta,
    )


def invariant_issues(
    events: Sequence[Event],
    account: AccountView,
    *,
    in_jail: Callable[[str], bool] | None = None,
) -> list[tuple[str, str]]:
    """Return (code, message) pairs for I1–I7. Empty means the world holds."""
    issues: list[tuple[str, str]] = []

    if account.remaining.mj < 0:
        issues.append(("I1", f"remaining {account.remaining.mj} mj is negative"))

    if account.cap.mj != account.spent.mj + account.remaining.mj:
        issues.append(
            (
                "I2",
                (
                    f"cap {account.cap.mj} != spent {account.spent.mj}"
                    f" + remaining {account.remaining.mj}"
                ),
            )
        )

    replayed = sum(s.amount.mj for event in events if (s := spend_from(event)))
    if replayed != account.spent.mj:
        issues.append(("I3", f"sum(spend_from) {replayed} != spent {account.spent.mj}"))

    ids = [event.id for event in events]
    if len(ids) != len(set(ids)):
        issues.append(("I4", "event ids are not unique"))

    seqs = [event.seq for event in events]
    expected = list(range(1, len(events) + 1))
    if seqs != expected:
        issues.append(("I5", f"seq values {seqs} are not 1..{len(events)}"))

    for index, event in enumerate(events):
        if event.seq == 1:
            if event.parent is not None:
                issues.append(("I6", "seq 1 parent must be None"))
        elif index == 0 or event.parent != events[index - 1].id:
            issues.append(
                ("I6", f"seq {event.seq} parent must equal the previous event id")
            )

    if in_jail is not None:
        for event in events:
            if isinstance(event.op, Act | Observe) and not in_jail(event.op.address):
                issues.append(
                    (
                        "I7",
                        f"{event.op.op} address {event.op.address!r} "
                        "is outside the jail",
                    )
                )

    return issues
