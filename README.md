# SpaceTEN

Space Time Energy Number

SpaceTEN is an experimental local Python kernel and CLI for agent work. Every action is a transaction in four primitives: **Space** (a jailed workspace of addressable cells), **Time** (an append-only causal event log), **Energy** (a conserved millijoule budget), and **Number** (sortable IDs and conserved quantities). It is not a chatbot, hosted platform, or multi-agent OS. There are no users, SLOs, or dates beyond this v0.

## Install

Python 3.12 or newer. [uv](https://docs.astral.sh/uv/) is the supported installer. License: [Apache-2.0](LICENSE).

```bash
uv sync
uv run spaceten version
```

## Workshop (Null)

The default planner is `null`: scripted, offline, no API key. From a clone:

```bash
cd examples/workshop
uv run spaceten init . --energy 10000
uv run spaceten run --goal "Summarize IN.txt into OUT.md" --provider null
uv run spaceten ledger
uv run spaceten check
```

`OUT.md` exists, `check` exits 0, and `ledger` shows `remaining + spent == cap`. Events live in `.spaceten/events.jsonl`.

## SpaceXAI (opt-in)

`--provider spacexai` calls Grok through the Responses API at `https://api.x.ai/v1/responses` with `reasoning_effort=low` (default `high` would bill unbounded reasoning tokens as output). Set `XAI_API_KEY` (or `SPACEXAI_API_KEY`). Optional extra: `uv sync --extra spacexai`.

**This sends tool results — including file contents the agent just read — to `api.x.ai`.** Do not point it at secrets you would not paste into a third-party API. The kernel and store never import the adapter. Default provider stays `null` so a clone cannot surprise-bill.

Same directory as the Null block (`examples/workshop` after `init`):

```bash
export XAI_API_KEY=...
uv run spaceten run --goal "Summarize IN.txt into OUT.md" --provider spacexai
```

## Jail and threats

v0 is a local process on a trusted laptop. Whoever can write the workspace can write the world. There is no auth, no server, and no telemetry.

- Tools cannot write outside the workspace (`..`, absolute paths, symlink escape) or into `.spaceten/`.
- Energy is quoted before I/O; provider spend is bounded before HTTP. Overdraft is refused.
- Prompt injection from files the agent reads is expected. The kernel still enforces jail + energy; it does not solve injection.
- No shell / subprocess tool. Writes over 1 MiB (default) are refused.

## Contest (anagrams)

A discrete-event competition on the kernel: six names, four hidden source words,
every inference an Observe or Act, Energy as the score. The checker is not in
the jail.

```bash
uv run spaceten contest init ./play --fixture anagrams
uv run spaceten --root ./play observe NAMES.md
# commit steps/*.md then SOLUTION.md via spaceten contest step
uv run spaceten --root ./play contest verify
uv run spaceten contest leaderboard examples/contest/anagrams
```

See [examples/contest/anagrams/README.md](examples/contest/anagrams/README.md)
and [examples/contest/anagrams/LEADERBOARD.md](examples/contest/anagrams/LEADERBOARD.md).

The design of record is [docs/DESIGN.md](docs/DESIGN.md).
