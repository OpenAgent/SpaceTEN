import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from spaceten.kernel.energy import AccountView, Energy
from spaceten.kernel.event import utc_z
from spaceten.kernel.space import Address, Cell
from spaceten.store.protocol import WorldHeader


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def parse_utc_z(value: str) -> datetime:
    if value.endswith("Z"):
        return datetime.fromisoformat(value[:-1] + "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def dump_header(header: WorldHeader) -> bytes:
    payload = {
        "id": header.id,
        "schema": header.schema,
        "created_at": utc_z(header.created_at),
        "root_policy": header.root_policy,
        "energy_unit": header.energy_unit,
        "energy_cap": header.energy_cap,
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def load_header(data: bytes) -> WorldHeader:
    payload = json.loads(data)
    schema = payload.get("schema")
    if schema != 1:
        raise ValueError(f"unsupported world schema: {schema!r}")
    return WorldHeader(
        id=payload["id"],
        schema=1,
        created_at=parse_utc_z(payload["created_at"]),
        root_policy=payload.get("root_policy", "jail"),
        energy_unit=payload.get("energy_unit", "mj"),
        energy_cap=int(payload["energy_cap"]),
    )


def dump_energy(account: AccountView) -> bytes:
    payload = {
        "cap": {"mj": account.cap.mj},
        "spent": {"mj": account.spent.mj},
        "remaining": {"mj": account.remaining.mj},
    }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def load_energy(data: bytes) -> AccountView:
    payload = json.loads(data)
    return AccountView(
        cap=Energy(int(payload["cap"]["mj"])),
        spent=Energy(int(payload["spent"]["mj"])),
        remaining=Energy(int(payload["remaining"]["mj"])),
    )


def dump_cells(cells: dict[str, Cell]) -> bytes:
    payload: dict[str, Any] = {}
    for address, cell in cells.items():
        payload[address] = {
            "address": cell.address.path,
            "kind": cell.kind,
            "content_hash": cell.content_hash,
            "size_bytes": cell.size_bytes,
            "mtime_wall": utc_z(cell.mtime_wall) if cell.mtime_wall else None,
        }
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def load_cells(data: bytes) -> dict[str, Cell]:
    payload = json.loads(data)
    cells: dict[str, Cell] = {}
    for key, raw in payload.items():
        mtime_raw = raw.get("mtime_wall")
        kind = cast(Literal["file", "dir"], raw["kind"])
        cells[key] = Cell(
            address=Address(raw.get("address", key)),
            kind=kind,
            content_hash=raw.get("content_hash"),
            size_bytes=int(raw["size_bytes"]),
            mtime_wall=parse_utc_z(mtime_raw) if mtime_raw else None,
        )
    return cells
