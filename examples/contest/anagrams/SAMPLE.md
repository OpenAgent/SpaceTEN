# Sample pack (spoilers)

`sample.sten.tgz` is a valid **human** run: inventory, four extracts, six pairs,
`SOLUTION.md`, Finish. `spaceten contest verify` reports ok.

**Opening it reveals the four source words.** Skip this file if you want to play first.

```bash
tar -xzf examples/contest/anagrams/sample.sten.tgz
uv run spaceten --root ./play-sample contest verify
```

Score of this pack: 25 mj, 15 events, 699 bytes under `steps/`.
