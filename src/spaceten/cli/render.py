import json
from collections.abc import Sequence

from spaceten.kernel.energy import AccountView
from spaceten.kernel.event import Act, Event, Failed, Finish, Init, Observe, Plan
from spaceten.kernel.invariant import spend_from
from spaceten.kernel.world import CheckReport
from spaceten.store.protocol import WorldHeader

_PROVIDER_FAIL_CODES = frozenset(
    {
        "provider",
        "provider_error",
        "http_401",
        "http_429",
        "http_5xx",
        "timeout",
        "energy_bound_broken",
    }
)


def metrics(
    header: WorldHeader,
    events: Sequence[Event],
    account: AccountView,
    *,
    cells: int,
    invariant_failures: int,
) -> dict[str, object]:
    spent_by_reason = {"io": 0, "plan": 0}
    provider_calls = 0
    provider_errors = 0
    for event in events:
        if event.energy_reason in spent_by_reason:
            spent_by_reason[event.energy_reason] += -event.energy_delta_mj
        if isinstance(event.op, Plan):
            provider_calls += 1
        elif isinstance(event.op, Failed) and event.op.code in _PROVIDER_FAIL_CODES:
            provider_errors += 1
    head_seq = events[-1].seq if events else 0
    return {
        "id": header.id,
        "seq": head_seq,
        "cells": cells,
        "events_total": head_seq,
        "energy_cap_mj": account.cap.mj,
        "energy_spent_mj": account.spent.mj,
        "energy_remaining_mj": account.remaining.mj,
        "energy_spent_by_reason": spent_by_reason,
        "invariant_failures": invariant_failures,
        "provider_calls": provider_calls,
        "provider_errors": provider_errors,
    }


def render_status(
    header: WorldHeader,
    events: Sequence[Event],
    account: AccountView,
    *,
    cells: int,
    invariant_failures: int,
    as_json: bool,
) -> str:
    payload = metrics(
        header,
        events,
        account,
        cells=cells,
        invariant_failures=invariant_failures,
    )
    if as_json:
        return json.dumps(payload, indent=2)
    return "\n".join(
        [
            f"id: {payload['id']}",
            f"seq: {payload['seq']}",
            f"remaining: {payload['energy_remaining_mj']} mj",
            f"spent: {payload['energy_spent_mj']} mj",
            f"cap: {payload['energy_cap_mj']} mj",
            f"cells: {payload['cells']}",
        ]
    )


def render_event(event: Event) -> str:
    op = event.op
    parts: list[str] = [f"seq {event.seq}"]
    if isinstance(op, Init):
        parts.append("Init")
        parts.append(f"energy_cap_mj={op.energy_cap_mj}")
    elif isinstance(op, Observe):
        parts.append("Observe")
        parts.append(f"address={op.address}")
        if op.listing:
            parts.append("listing=true")
    elif isinstance(op, Plan):
        parts.append("Plan")
        parts.append(f"provider={op.provider}")
        parts.append(f"tokens_in={op.tokens_in}")
        parts.append(f"tokens_out={op.tokens_out}")
    elif isinstance(op, Act):
        parts.append("Act")
        parts.append(f"tool={op.tool}")
        parts.append(f"address={op.address}")
    elif isinstance(op, Finish):
        parts.append("Finish")
        if op.artifact is not None:
            parts.append(f"artifact={op.artifact}")
    elif isinstance(op, Failed):
        parts.append("Failed")
        parts.append(f"code={op.code}")
    else:
        parts.append(type(op).__name__)
    if event.energy_delta_mj != 0:
        parts.append(f"energy_delta_mj={event.energy_delta_mj}")
        parts.append(f"energy_reason={event.energy_reason}")
    return " ".join(parts)


def render_log(events: Sequence[Event]) -> str:
    return "\n".join(render_event(event) for event in events)


def render_ledger(events: Sequence[Event], account: AccountView) -> str:
    lines: list[str] = []
    for event in events:
        spend = spend_from(event)
        if spend is None:
            continue
        lines.append(f"{spend.event_id} {spend.amount.mj} mj {spend.reason}")
    remaining = account.remaining.mj
    spent = account.spent.mj
    cap = account.cap.mj
    lines.append(f"remaining {remaining} + spent {spent} == cap {cap}")
    return "\n".join(lines)


def render_check(report: CheckReport) -> str:
    if report.ok:
        return f"ok events={report.events} remaining={report.remaining.mj} mj"
    lines = [f"{issue.code}: {issue.message}" for issue in report.issues]
    lines.append(f"events={report.events} remaining={report.remaining.mj} mj")
    return "\n".join(lines)
