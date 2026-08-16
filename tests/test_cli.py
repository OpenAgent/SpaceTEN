import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from typer.testing import CliRunner

from spaceten.cli.main import app
from spaceten.kernel.event import Event, Init, Observe

runner = CliRunner()


class _CliResult(Protocol):
    @property
    def exit_code(self) -> int: ...

    @property
    def output(self) -> str: ...

    @property
    def stdout(self) -> str: ...


def _init(root: Path, *args: str) -> _CliResult:
    return runner.invoke(app, ["init", str(root), *args])


def _root(root: Path, *args: str) -> _CliResult:
    return runner.invoke(app, ["--root", str(root), *args])


def test_init_creates_genesis_default_energy(tmp_path: Path) -> None:
    result = _init(tmp_path)
    assert result.exit_code == 0, result.output
    meta = tmp_path / ".spaceten"
    assert (meta / "world.json").is_file()
    assert (meta / "events.jsonl").is_file()
    header = json.loads((meta / "world.json").read_text(encoding="utf-8"))
    assert header["energy_cap"] == 100_000
    assert "[energy]" not in result.output
    assert not (meta / "config.toml").exists()
    line = (meta / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
    event = Event.model_validate_json(line)
    assert isinstance(event.op, Init)
    assert event.op.energy_cap_mj == 100_000
    assert event.seq == 1
    assert "remaining=100000 mj" in result.stdout


def test_init_custom_energy(tmp_path: Path) -> None:
    result = _init(tmp_path, "--energy", "10000")
    assert result.exit_code == 0, result.output
    header = json.loads((tmp_path / ".spaceten" / "world.json").read_text())
    assert header["energy_cap"] == 10_000
    assert "energy_cap_mj=10000" in result.stdout


def test_init_fail_if_exists(tmp_path: Path) -> None:
    first = _init(tmp_path)
    assert first.exit_code == 0, first.output
    second = _init(tmp_path)
    assert second.exit_code != 0
    assert "already exists" in second.output


def test_init_does_not_write_energy_config(tmp_path: Path) -> None:
    result = _init(tmp_path, "--energy", "50")
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".spaceten" / "config.toml").exists()
    for path in (tmp_path / ".spaceten").iterdir():
        if path.suffix == ".toml":
            assert "[energy]" not in path.read_text(encoding="utf-8")


def test_version_without_spaceten(tmp_path: Path) -> None:
    result = _root(tmp_path, "version")
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_status_refuses_without_spaceten(tmp_path: Path) -> None:
    result = _root(tmp_path, "status")
    assert result.exit_code != 0
    assert ".spaceten" in result.output


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_subcommand_help_without_spaceten(tmp_path: Path) -> None:
    status_help = _root(tmp_path, "status", "--help")
    assert status_help.exit_code == 0, status_help.output
    assert "--json" in _plain(status_help.output)
    check_help = _root(tmp_path, "check", "--help")
    assert check_help.exit_code == 0, check_help.output
    assert "--rebuild" in _plain(check_help.output)
    assert "--truncate-partial" in _plain(check_help.output)
    assert ".spaceten" not in _plain(check_help.output)


def test_check_ok_after_observe(tmp_path: Path) -> None:
    assert _init(tmp_path, "--energy", "1000").exit_code == 0
    (tmp_path / "IN.txt").write_text("hello", encoding="utf-8")
    observed = _root(tmp_path, "observe", "IN.txt")
    assert observed.exit_code == 0, observed.output
    assert "Observe" in observed.stdout
    assert "address=IN.txt" in observed.stdout
    checked = _root(tmp_path, "check")
    assert checked.exit_code == 0, checked.output
    assert checked.stdout.startswith("ok")
    rebuilt = _root(tmp_path, "check", "--rebuild")
    assert rebuilt.exit_code == 0, rebuilt.output


def test_status_json_after_observe(tmp_path: Path) -> None:
    assert _init(tmp_path, "--energy", "500").exit_code == 0
    (tmp_path / "IN.txt").write_text("hello", encoding="utf-8")
    assert _root(tmp_path, "observe", "IN.txt").exit_code == 0
    result = _root(tmp_path, "status", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["events_total"] == 2
    assert payload["energy_cap_mj"] == 500
    assert payload["energy_spent_mj"] == 1
    assert payload["energy_remaining_mj"] == 499
    assert payload["energy_spent_by_reason"] == {"io": 1, "plan": 0}
    assert payload["invariant_failures"] == 0
    assert payload["provider_calls"] == 0
    assert payload["provider_errors"] == 0
    assert payload["cells"] >= 1
    assert payload["seq"] == 2
    assert payload["id"]


def test_status_human(tmp_path: Path) -> None:
    assert _init(tmp_path).exit_code == 0
    result = _root(tmp_path, "status")
    assert result.exit_code == 0, result.output
    assert "seq: 1" in result.stdout
    assert "remaining: 100000 mj" in result.stdout
    assert "spent: 0 mj" in result.stdout
    assert "cap: 100000 mj" in result.stdout
    assert "cells: 0" in result.stdout


def test_log_and_ledger(tmp_path: Path) -> None:
    assert _init(tmp_path, "--energy", "200").exit_code == 0
    (tmp_path / "IN.txt").write_text("hello", encoding="utf-8")
    assert _root(tmp_path, "observe", "IN.txt").exit_code == 0
    logged = _root(tmp_path, "log", "--n", "20")
    assert logged.exit_code == 0, logged.output
    assert "seq 1 Init energy_cap_mj=200" in logged.stdout
    assert "seq 2 Observe address=IN.txt" in logged.stdout
    assert "energy_delta_mj=-1" in logged.stdout
    tail = _root(tmp_path, "log", "--n", "1")
    assert tail.exit_code == 0, tail.output
    assert "seq 2" in tail.stdout
    assert "seq 1" not in tail.stdout
    led = _root(tmp_path, "ledger")
    assert led.exit_code == 0, led.output
    assert "1 mj io" in led.stdout
    assert "remaining 199 + spent 1 == cap 200" in led.stdout


def test_truncate_partial(tmp_path: Path) -> None:
    assert _init(tmp_path, "--energy", "20").exit_code == 0
    events = tmp_path / ".spaceten" / "events.jsonl"
    events.write_bytes(events.read_bytes() + b'{"schema": 1, "id": "partial')
    refused = _root(tmp_path, "check")
    assert refused.exit_code != 0
    assert "torn" in refused.output
    # lock-free log still tails complete events
    logged = _root(tmp_path, "log")
    assert logged.exit_code == 0, logged.output
    assert "seq 1 Init" in logged.stdout
    assert events.read_bytes().endswith(b"partial")
    truncated = _root(tmp_path, "check", "--truncate-partial")
    assert truncated.exit_code == 0, truncated.output
    assert b"partial" not in events.read_bytes()
    clean = _root(tmp_path, "check", "--truncate-partial")
    assert clean.exit_code != 0
    assert "clean" in clean.output


def test_observe_listing_dot(tmp_path: Path) -> None:
    assert _init(tmp_path, "--energy", "50").exit_code == 0
    result = _root(tmp_path, "observe", ".")
    assert result.exit_code == 0, result.output
    assert "listing=true" in result.stdout
    line = (tmp_path / ".spaceten" / "events.jsonl").read_text().splitlines()[-1]
    event = Event.model_validate_json(line)
    assert isinstance(event.op, Observe)
    assert event.op.listing


def test_observe_missing_cell(tmp_path: Path) -> None:
    assert _init(tmp_path).exit_code == 0
    result = _root(tmp_path, "observe", "gone.txt")
    assert result.exit_code != 0
    assert "gone.txt" in result.output


def test_check_overspent_reports_invariants(tmp_path: Path) -> None:
    assert _init(tmp_path, "--energy", "1").exit_code == 0
    events_path = tmp_path / ".spaceten" / "events.jsonl"
    genesis = Event.model_validate_json(events_path.read_text().splitlines()[0])
    extra = Event(
        id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        seq=2,
        wall=datetime(2026, 8, 16, tzinfo=UTC),
        parent=genesis.id,
        actor="human",
        energy_delta_mj=-100,
        energy_reason="io",
        op=Observe(address="IN.txt", size_bytes=0, content_hash=None),
    )
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(extra.model_dump_json() + "\n")
    result = _root(tmp_path, "check")
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "I1:" in result.stdout
    assert "I2:" in result.stdout
    assert "remaining" in result.stdout
