# AGENTS.md

Contributor map. SpaceTEN in prose, `spaceten` in code, STEN only as an abbreviation.

## Layout

```
src/spaceten/
  kernel/       Number, Time, Space, Energy, Event, World. No network.
  store/        Store protocol, MemoryStore, JsonlStore, flock. No network.
  providers/    Provider protocol, NullProvider, SpaceXAIProvider.
  agent/        tools, prompt, observe-plan-act loop.
  cli/          Typer: init, status, observe, log, ledger, check, plan, run, version.
  demo/         Workshop demo wiring.
tests/          one module per area; jail cases in test_jail.py.
docs/DESIGN.md  design of record.
examples/workshop/
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
