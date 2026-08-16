# Workshop

Offline demo: explore the workspace, write `OUT.md`, print the ledger.

```bash
spaceten init . --energy 10000
spaceten run --goal "Summarize IN.txt into OUT.md" --provider null
spaceten ledger
spaceten check
```

`--provider null` is the default and does not call SpaceXAI.
