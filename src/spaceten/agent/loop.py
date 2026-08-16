from dataclasses import dataclass
from typing import Literal

from spaceten.agent.prompt import seed, system_message
from spaceten.agent.tools import SCHEMAS, ToolResult, dispatch, to_message
from spaceten.kernel.energy import MIN_COMPLETION_MJ, PER_CALL_CEILING_MJ, Energy
from spaceten.kernel.event import Failed, Plan
from spaceten.kernel.number import Id
from spaceten.kernel.world import World
from spaceten.providers.base import (
    CompletionRequest,
    CompletionResponse,
    Message,
    Provider,
    ProviderError,
    ToolCall,
)


@dataclass
class RunConfig:
    goal: str
    max_steps: int = 32


@dataclass(frozen=True)
class RunResult:
    reason: Literal[
        "finished",
        "no_tool_calls",
        "max_steps",
        "energy_exhausted",
        "energy_bound_broken",
        "provider_error",
        "rejected",
    ]
    artifact: str | None = None
    last_event_id: Id | None = None


def _spend_or_none(energy: Energy | None) -> Energy | None:
    if energy is None or energy.mj <= 0:
        return None
    return energy


def _complete(
    world: World,
    provider: Provider,
    actor: str,
    messages: list[Message],
) -> tuple[CompletionResponse | None, RunResult | None]:
    remaining = world.account.remaining
    if remaining.mj < MIN_COMPLETION_MJ:
        ev = world.propose(
            actor,
            Failed(code="energy_exhausted", message="below MIN_COMPLETION_MJ"),
        )
        return None, RunResult("energy_exhausted", last_event_id=Id(ev.event.id))

    quote = Energy(min(remaining.mj, PER_CALL_CEILING_MJ))
    # complete() must run outside the world lock; propose() releases before return
    try:
        completion = provider.complete(
            CompletionRequest(
                messages=list(messages),
                tools=list(SCHEMAS),
                budget_hint=quote,
            )
        )
    except ProviderError as exc:
        spend = _spend_or_none(exc.energy)
        usage = exc.usage
        ev = world.propose(
            actor,
            Failed(
                code="provider_error",
                message=str(exc),
                attempted_mj=None if spend is None else spend.mj,
                tokens_in=usage.tokens_in if usage is not None else 0,
                tokens_out=usage.tokens_out if usage is not None else 0,
            ),
            spend=spend,
        )
        return None, RunResult("provider_error", last_event_id=Id(ev.event.id))

    if completion.energy.mj > world.account.remaining.mj:
        ev = world.propose(
            actor,
            Failed(
                code="energy_bound_broken",
                message="usage exceeded remaining after bound",
                attempted_mj=completion.energy.mj,
                tokens_in=completion.usage.tokens_in,
                tokens_out=completion.usage.tokens_out,
            ),
            spend=completion.energy,
        )
        return None, RunResult("energy_bound_broken", last_event_id=Id(ev.event.id))

    world.propose(
        actor,
        Plan(
            provider=provider.name,
            model=completion.model,
            tokens_in=completion.usage.tokens_in,
            tokens_out=completion.usage.tokens_out,
            cached_tokens=completion.usage.cached_tokens,
            summary=completion.text or "",
        ),
        spend=completion.energy,
    )
    return completion, None


def plan(world: World, provider: Provider, cfg: RunConfig) -> RunResult:
    """One complete + Plan. Does not dispatch tools."""
    actor = f"agent:{provider.name}"
    messages = seed(
        world,
        cfg.goal,
        ToolResult(ok=True, content=""),
    )
    completion, failed = _complete(world, provider, actor, messages)
    if failed is not None:
        return failed
    assert completion is not None
    head = world.head
    return RunResult(
        "finished",
        last_event_id=None if head is None else Id(head.id),
    )


def run(world: World, provider: Provider, cfg: RunConfig) -> RunResult:
    actor = f"agent:{provider.name}"
    listing = dispatch(
        world,
        actor,
        ToolCall(id="seed", name="list_dir", arguments={"path": "."}),
    )
    if not listing.ok:
        ev = world.propose(
            actor,
            Failed(code="rejected", message=listing.error or "seed list_dir failed"),
        )
        return RunResult("rejected", last_event_id=Id(ev.event.id))

    messages = seed(world, cfg.goal, listing)
    last_id: Id | None = (
        Id(listing.receipt.event.id) if listing.receipt is not None else None
    )

    for _ in range(cfg.max_steps):
        messages[0] = system_message(world)
        completion, failed = _complete(world, provider, actor, messages)
        if failed is not None:
            return failed
        assert completion is not None
        head = world.head
        if head is not None:
            last_id = Id(head.id)

        messages.append(
            Message(
                role="assistant",
                content=completion.text or "",
                tool_calls=tuple(completion.tool_calls),
            )
        )

        if not completion.tool_calls:
            ev = world.propose(
                actor,
                Failed(code="no_tool_calls", message="assistant returned no tools"),
            )
            return RunResult("no_tool_calls", last_event_id=Id(ev.event.id))

        for call in completion.tool_calls:
            result = dispatch(world, actor, call)
            messages.append(to_message(call, result))
            if result.receipt is not None:
                last_id = Id(result.receipt.event.id)
            if not result.ok and result.stop:
                ev = world.propose(
                    actor,
                    Failed(code="rejected", message=result.error or call.name),
                )
                return RunResult("rejected", last_event_id=Id(ev.event.id))
            if call.name == "finish" and result.ok:
                return RunResult(
                    "finished",
                    artifact=result.artifact,
                    last_event_id=last_id,
                )

    ev = world.propose(
        actor,
        Failed(code="max_steps", message=f"exceeded {cfg.max_steps}"),
    )
    return RunResult("max_steps", last_event_id=Id(ev.event.id))
