# AGENTS.md

Contributor map. SpaceTEN in prose, `spaceten` in code, STEN only as an abbreviation.

Start here after a new session: [docs/STATUS.md](docs/STATUS.md) (what shipped, decisions, pitfalls). Design of record: [docs/DESIGN.md](docs/DESIGN.md).

- Merge work to `main`, not into leftover feature/stack parent branches.
- Kernel and store must not import `providers` or HTTP.
- Contest answers in `src/spaceten/contest/anagrams.py` are public; the jail does not get a copy.
- CLI help tests: strip ANSI. Pyright: bind `event.op` after `isinstance`.

## Layout

```
src/spaceten/
  kernel/       Number, Time, Space, Energy, Event, World. No network.
  store/        Store protocol, MemoryStore, JsonlStore, flock. No network.
  providers/    Provider protocol, NullProvider, SpaceXAIProvider.
  agent/        tools, prompt, observe-plan-act loop.
  cli/          Typer: init, status, observe, log, ledger, check, plan, run, contest, version.
  contest/      Sealed fixtures, checker, pack, leaderboard. Not imported by kernel or store.
  demo/         Workshop demo wiring.
tests/          one module per area; jail cases in test_jail.py.
docs/DESIGN.md  design of record.
examples/workshop/
examples/contest/anagrams/
```

`spaceten.kernel` and `spaceten.store` must not import `spaceten.providers` or any HTTP client. Only `providers/spacexai.py` knows `api.x.ai`, `XAI_API_KEY`, or model names.

## Commands

```bash
uv sync
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
uv run spaceten version
```

CI runs those four checks on Python 3.12 and 3.13, ubuntu and macos.

## Conventions

- Python 3.12+, typed, ruff (`E`, `F`, `I`, `UP`), pyright standard.
- Tests travel with the code they prove. Prefer `tmp_path`.
- Docs PRs must not change runtime behavior.
- License is Apache-2.0. See [CONTRIBUTING.md](CONTRIBUTING.md).
