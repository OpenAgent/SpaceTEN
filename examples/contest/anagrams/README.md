# Anagrams (STEN contest)

A discrete-event competition on the SpaceTEN kernel. Six names. Four hidden
source words. Every inference is an Observe or Act; Energy is the score.

The checker is **not** in the jail. Spoilers live only in the package, not here.

```bash
uv run spaceten contest init ./play --fixture anagrams
cd play
uv run spaceten --root . observe NAMES.md
# write steps/*.md, then:
uv run spaceten --root . contest step steps/01.md
# ...
uv run spaceten --root . contest step SOLUTION.md
uv run spaceten --root . contest verify
uv run spaceten --root . contest pack
```

Human and agent divisions are separate boards. Default provider stays `null`.

A solved human run (spoilers) is in [SAMPLE.md](SAMPLE.md).

Rank packs in this directory (or any folder of `*.sten.tgz`):

```bash
uv run spaceten contest leaderboard examples/contest/anagrams
uv run spaceten contest leaderboard examples/contest/anagrams --write examples/contest/anagrams/LEADERBOARD.md
```

See [LEADERBOARD.md](LEADERBOARD.md).
