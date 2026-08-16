# Contributing

Small, independently reviewable PRs. Tests travel with the code they prove. Do not land kernel + provider + agent in one change.

## Setup

Python 3.12+. [uv](https://docs.astral.sh/uv/) is the supported installer.

```bash
uv sync
uv run spaceten version
```

## Checks

Same tools as CI:

```bash
uv run ruff check
uv run ruff format
uv run pyright
uv run pytest
```

`ruff format --check` is what CI runs. Format before you push.

## PR size

One concern per PR. If you change invariants, Event schema, or energy authority, follow [docs/DESIGN.md](docs/DESIGN.md).

`spaceten.kernel` and `spaceten.store` must not import providers.

License: Apache-2.0.
