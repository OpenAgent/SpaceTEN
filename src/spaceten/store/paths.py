from dataclasses import dataclass
from pathlib import Path

_RESERVED_DIR = ".spaceten"


@dataclass(frozen=True, slots=True)
class StorePaths:
    root: Path
    meta: Path
    world: Path
    events: Path
    energy: Path
    cells: Path
    lock: Path
    tmp: Path

    @classmethod
    def for_root(cls, root: Path) -> "StorePaths":
        resolved = Path(root).resolve()
        meta = resolved / _RESERVED_DIR
        return cls(
            root=resolved,
            meta=meta,
            world=meta / "world.json",
            events=meta / "events.jsonl",
            energy=meta / "energy.json",
            cells=meta / "cells.json",
            lock=meta / "lock",
            tmp=meta / "tmp",
        )

    def staged(self, event_id: str) -> Path:
        return self.tmp / f"{event_id}.part"
