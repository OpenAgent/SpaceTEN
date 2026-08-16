from pathlib import Path

GOAL = "Summarize IN.txt into OUT.md"
IN_TEXT = "Workshop fixture.\n\nSummarize this file into OUT.md.\n"
EXPECTED_OUT = "Summary of IN.txt.\n"


def install(root: Path) -> Path:
    dest = Path(root) / "IN.txt"
    dest.write_text(IN_TEXT, encoding="utf-8")
    return dest
