# SpaceTEN status (2026-08-16)

Handoff for a **new** Grok session. Start Grok from this repo:

```bash
cd /Users/eric.eidem/SpaceTEN
grok
```

Then: *read `docs/STATUS.md` and `AGENTS.md`; we are modifying SpaceTEN.*

Do not start Grok from `$HOME`. That session will not auto-load this file.

## What exists on `main`

Public repo: https://github.com/OpenAgent/SpaceTEN  
Tip after contest + sample pack + leaderboard: look at `git log -1` (expected lineage includes merges #11–#16).

| Layer | What it is |
| --- | --- |
| Kernel | `World.propose` / jail / Energy mj / JSONL log / I1–I7 |
| CLI | `init status observe log ledger check plan run contest version` |
| Providers | `null` (default, offline), `spacexai` (opt-in, `XAI_API_KEY`) |
| Demo | Workshop: `examples/workshop/` |
| Contest | Anagrams of unique pairs of SPACE/TIME/ENERGY/NUMBER |
| Leaderboard | `spaceten contest leaderboard`; checked-in `examples/contest/anagrams/LEADERBOARD.md` |

Design of record: `docs/DESIGN.md` (Accepted 2026-08-15). It still describes the *plan*; the repo is no longer README-only.

## Contest (public demo)

- Player files: `examples/contest/anagrams/NAMES.md`, `RULES.md`
- Answers live in `src/spaceten/contest/anagrams.py` (`TERMS`, `PAIRS`) — **public on GitHub**
- Valid run: observe `NAMES.md` → ≥1 inventory, ≥1 extract, 6 pairs → `SOLUTION.md` last Act
- Sample pack (spoilers): `examples/contest/anagrams/sample.sten.tgz` (25 mj, 15 events, human)
- Rank: Energy, then events, then `steps/` bytes. Any `Plan` event ⇒ division `agent`; else `human`

## Decisions that should not be relitigated

- Product: local kernel + CLI, not a hosted agent OS
- License: Apache-2.0
- Language: Python 3.12+; CI 3.12/3.13 on ubuntu + macos
- Default model when networked: `grok-4.6`; `reasoning_effort=low`
- No `run_cmd` in v0
- Merge feature PRs to **`main`**, never into leftover stack-parent branches
- Kernel/store must not import `providers` or HTTP

## Pitfalls from the first implementation

- GitHub stacked PRs: merging #N into its *parent branch* does not land code on `main`
- `gh` needs `workflow` scope to push `.github/workflows/`
- Rich help splits `--json` / `--goal` with ANSI (`-` + `-json`). Tests must strip SGR
- Pyright does not narrow `event.op` after `isinstance` in a comprehension; bind `event.op` to a name
- `ruff format --check` is in CI; keep `typer.echo(...)` on one line when it fits
- `.spaceten/` is gitignored; submit artifacts are `*.sten.tgz`

## How to resume *this* conversation

The long design/implement chat lived in a Grok session whose cwd was the home directory, not this repo.

- In that same TUI: `/rename SpaceTEN v0 kernel + contest` then later `/resume`
- Or `grok --resume` from the **same cwd** the session used
- `/session-info` copies the session ID (`c` on the ID)

Transcripts live under `~/.grok/sessions/`. They are bulky and will compact. Prefer this file + `AGENTS.md` + `docs/DESIGN.md` for *modifying the code*.

Optional Grok memory (off by default): enable `[memory] enabled = true`, then `/flush` and `/remember` key facts. Still commit durable rules here.

## Sensible next work

- Another contest fixture (key off-repo if it must stay secret)
- Leaderboard submissions as extra `*.sten.tgz` + regenerate `LEADERBOARD.md`
- Sandboxed `run_cmd` (explicitly deferred)
- Per-actor Energy accounts (schema 2)
- Do not grow the kernel unless a client is blocked
