import ast
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spaceten.agent.loop import RunConfig, plan, run
from spaceten.agent.tools import SCHEMAS, dispatch
from spaceten.cli.main import app
from spaceten.demo.workshop import EXPECTED_OUT, GOAL, IN_TEXT, install
from spaceten.kernel.energy import Energy
from spaceten.kernel.event import Act, Event, Failed, Finish, Observe, Plan
from spaceten.kernel.space import Workspace
from spaceten.kernel.world import World
from spaceten.providers.base import (
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    ToolCall,
    Usage,
)
from spaceten.providers.null import NullProvider
from spaceten.store.memory import MemoryStore

runner = CliRunner()
_REPO = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO / "examples" / "workshop" / "IN.txt"


def _world(tmp_path: Path, *, energy_cap: int = 1_000) -> World:
    return World.init(tmp_path, energy_cap=energy_cap, store=MemoryStore())


def _events(world: World) -> list[Event]:
    return list(getattr(world, "_events"))


def _ops(world: World) -> list[object]:
    return [event.op for event in _events(world)]


def _response(
    *calls: ToolCall, energy: int = 5, text: str | None = None
) -> CompletionResponse:
    return CompletionResponse(
        text=text,
        tool_calls=list(calls),
        usage=Usage(0, 0, 0),
        model="script",
        energy=Energy(energy),
    )


class Scripted:
    name = "script"

    def __init__(self, responses: list[CompletionResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[CompletionRequest] = []

    def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.requests.append(req)
        if not self._responses:
            return _response()
        return self._responses.pop(0)


def test_tools_are_the_four_and_no_run_cmd() -> None:
    names = [spec.name for spec in SCHEMAS]
    assert names == ["list_dir", "read_file", "write_file", "finish"]
    assert "run_cmd" not in names


def test_fixture_matches_examples_workshop() -> None:
    assert _FIXTURE.is_file()
    assert _FIXTURE.read_text(encoding="utf-8") == IN_TEXT


def test_null_workshop_writes_out_and_conserves(tmp_path: Path) -> None:
    install(tmp_path)
    world = _world(tmp_path)
    result = run(world, NullProvider(), RunConfig(goal=GOAL))
    assert result.reason == "finished"
    assert result.artifact == "OUT.md"
    assert (tmp_path / "OUT.md").read_text(encoding="utf-8") == EXPECTED_OUT
    view = world.account
    assert view.remaining.mj + view.spent.mj == view.cap.mj
    assert world.check().ok
    kinds = {type(op).__name__ for op in _ops(world)}
    assert kinds >= {"Init", "Observe", "Plan", "Act", "Finish"}
    assert {event.actor for event in _events(world)[1:]} == {"agent:null"}


def test_seed_observe_is_real_list_dir(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider = Scripted([_response(ToolCall("1", "finish", {"summary": "done"}))])
    run(world, provider, RunConfig(goal="x"))
    first = _ops(world)[1]
    assert isinstance(first, Observe)
    assert first.listing
    assert first.address == "."
    assert _events(world)[1].actor == "agent:script"


def test_messages_are_system_user_assistant_tool(tmp_path: Path) -> None:
    install(tmp_path)
    recorder = Scripted(
        [
            _response(ToolCall("c1", "list_dir", {"path": "."})),
            _response(ToolCall("c2", "finish", {"summary": "done"})),
        ]
    )
    run(_world(tmp_path), recorder, RunConfig(goal=GOAL))
    assert len(recorder.requests) == 2
    first = recorder.requests[0].messages
    assert [m.role for m in first] == ["system", "user"]
    second = recorder.requests[1].messages
    assert [m.role for m in second] == ["system", "user", "assistant", "tool"]
    assistant = second[2]
    assert assistant.tool_calls
    assert assistant.tool_calls[0].name == "list_dir"
    assert second[3].tool_call_id == "c1"


def test_plan_one_complete_no_act(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider = Scripted(
        [_response(ToolCall("1", "write_file", {"path": "OUT.md", "content": "x"}))]
    )
    result = plan(world, provider, RunConfig(goal="think"))
    assert result.reason == "finished"
    assert len(provider.requests) == 1
    assert not (tmp_path / "OUT.md").exists()
    kinds = [type(op).__name__ for op in _ops(world)]
    assert kinds == ["Init", "Plan"]
    assert isinstance(_ops(world)[1], Plan)


def test_no_tool_calls_is_terminal_failed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    result = run(world, Scripted([_response()]), RunConfig(goal="x"))
    assert result.reason == "no_tool_calls"
    last = _ops(world)[-1]
    assert isinstance(last, Failed)
    assert last.code == "no_tool_calls"
    assert "no tools" in last.message


def test_max_steps_is_terminal_failed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    looping = Scripted(
        [
            _response(ToolCall("a", "list_dir", {"path": "."})),
            _response(ToolCall("b", "list_dir", {"path": "."})),
        ]
    )
    result = run(world, looping, RunConfig(goal="x", max_steps=2))
    assert result.reason == "max_steps"
    last = _ops(world)[-1]
    assert isinstance(last, Failed)
    assert last.code == "max_steps"
    assert "exceeded 2" in last.message


def test_energy_exhausted_before_complete(tmp_path: Path) -> None:
    world = _world(tmp_path, energy_cap=5)
    provider = Scripted([_response(ToolCall("1", "finish", {"summary": "x"}))])
    result = run(world, provider, RunConfig(goal="x"))
    assert result.reason == "energy_exhausted"
    assert provider.requests == []
    last = _ops(world)[-1]
    assert isinstance(last, Failed)
    assert last.code == "energy_exhausted"


def test_energy_bound_broken_skips_tools(tmp_path: Path) -> None:
    world = _world(tmp_path, energy_cap=20)
    provider = Scripted(
        [
            _response(
                ToolCall("1", "write_file", {"path": "OUT.md", "content": "nope"}),
                energy=20,
            )
        ]
    )
    result = run(world, provider, RunConfig(goal="x"))
    assert result.reason == "energy_bound_broken"
    assert not (tmp_path / "OUT.md").exists()
    last = _ops(world)[-1]
    assert isinstance(last, Failed)
    assert last.code == "energy_bound_broken"
    assert not any(isinstance(op, Act) for op in _ops(world))


def test_provider_error_commits_failed(tmp_path: Path) -> None:
    class Boom:
        name = "boom"

        def complete(self, req: CompletionRequest) -> CompletionResponse:
            del req
            raise ProviderError("timeout", "boom", energy=Energy(3))

    world = _world(tmp_path)
    result = run(world, Boom(), RunConfig(goal="x"))
    assert result.reason == "provider_error"
    last = _events(world)[-1]
    assert isinstance(last.op, Failed)
    assert last.op.code == "provider_error"
    assert last.energy_delta_mj == -3
    assert last.actor == "agent:boom"


def test_cell_not_found_is_recoverable(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider = Scripted(
        [
            _response(ToolCall("1", "read_file", {"path": "missing.txt"})),
            _response(ToolCall("2", "finish", {"summary": "ok"})),
        ]
    )
    result = run(world, provider, RunConfig(goal="x"))
    assert result.reason == "finished"
    assert not any(isinstance(op, Failed) for op in _ops(world))
    tool_msg = provider.requests[1].messages[-1]
    assert tool_msg.role == "tool"
    assert tool_msg.content == "not_found"


def test_mid_batch_jail_stops_remaining_tools(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider = Scripted(
        [
            _response(
                ToolCall("1", "write_file", {"path": "../escape.txt", "content": "x"}),
                ToolCall("2", "finish", {"summary": "should not run"}),
            )
        ]
    )
    result = run(world, provider, RunConfig(goal="x"))
    assert result.reason == "rejected"
    assert not any(isinstance(op, Finish) for op in _ops(world))
    last = _ops(world)[-1]
    assert isinstance(last, Failed)
    assert last.code == "rejected"


def test_directory_act_dest_stops(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    world = _world(tmp_path)
    result = dispatch(
        world,
        "agent:script",
        ToolCall("1", "write_file", {"path": "subdir", "content": "x"}),
    )
    assert not result.ok
    assert result.stop
    assert not any(isinstance(op, Act) for op in _ops(world))


def test_write_file_encodes_utf8(tmp_path: Path) -> None:
    world = _world(tmp_path)
    result = dispatch(
        world,
        "agent:script",
        ToolCall("1", "write_file", {"path": "café.txt", "content": "ñ"}),
    )
    assert result.ok
    assert (tmp_path / "café.txt").read_bytes() == "ñ".encode()
    act = _ops(world)[-1]
    assert isinstance(act, Act)
    assert act.tool == "write_file"
    assert act.size_bytes == len("ñ".encode())


def test_complete_does_not_hold_world_lock(tmp_path: Path) -> None:
    world = World.init(tmp_path, energy_cap=1_000)

    class Probe:
        name = "probe"
        acquired = False

        def complete(self, req: CompletionRequest) -> CompletionResponse:
            del req
            with world._store.lock():
                self.acquired = True
            return _response(ToolCall("1", "finish", {"summary": "done"}))

    probe = Probe()
    result = run(world, probe, RunConfig(goal="x"))
    assert result.reason == "finished"
    assert probe.acquired


def test_write_does_not_call_workspace_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Workspace.write must not be used")

    monkeypatch.setattr(Workspace, "write", boom)
    world = _world(tmp_path)
    result = dispatch(
        world,
        "agent:script",
        ToolCall("1", "write_file", {"path": "OUT.md", "content": "ok"}),
    )
    assert result.ok
    assert (tmp_path / "OUT.md").read_text(encoding="utf-8") == "ok"


def test_unknown_tool_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    result = dispatch(world, "agent:script", ToolCall("1", "run_cmd", {"cmd": "ls"}))
    assert not result.ok
    assert result.stop
    assert result.error == "unknown_tool"


def test_cli_workshop_null_golden(tmp_path: Path) -> None:
    install(tmp_path)
    inited = runner.invoke(app, ["init", str(tmp_path)])
    assert inited.exit_code == 0, inited.output
    ran = runner.invoke(
        app,
        ["--root", str(tmp_path), "run", "--goal", GOAL, "--provider", "null"],
    )
    assert ran.exit_code == 0, ran.output
    assert "finished" in ran.stdout
    assert "artifact=OUT.md" in ran.stdout
    assert (tmp_path / "OUT.md").read_text(encoding="utf-8") == EXPECTED_OUT
    checked = runner.invoke(app, ["--root", str(tmp_path), "check"])
    assert checked.exit_code == 0, checked.output
    assert checked.stdout.startswith("ok")
    events = [
        Event.model_validate_json(line)
        for line in (tmp_path / ".spaceten" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    kinds = {type(event.op).__name__ for event in events}
    assert {"Observe", "Plan", "Act", "Finish"} <= kinds
    spent = sum(-event.energy_delta_mj for event in events)
    cap = 100_000
    remaining = cap - spent
    assert remaining + spent == cap
    led = runner.invoke(app, ["--root", str(tmp_path), "ledger"])
    assert led.exit_code == 0, led.output
    assert f"remaining {remaining} + spent {spent} == cap {cap}" in led.stdout


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_cli_run_defaults_to_null(tmp_path: Path) -> None:
    install(tmp_path)
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    ran = runner.invoke(app, ["--root", str(tmp_path), "run", "--goal", GOAL])
    assert ran.exit_code == 0, ran.output
    assert (tmp_path / "OUT.md").is_file()
    events = [
        Event.model_validate_json(line)
        for line in (tmp_path / ".spaceten" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    plans = [event.op for event in events if isinstance(event.op, Plan)]
    assert plans
    assert all(plan.provider == "null" for plan in plans)


def test_cli_plan_does_not_act(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    planned = runner.invoke(app, ["--root", str(tmp_path), "plan", "--goal", "think"])
    assert planned.exit_code == 0, planned.output
    assert "Plan" in planned.stdout
    events = [
        Event.model_validate_json(line)
        for line in (tmp_path / ".spaceten" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [type(event.op).__name__ for event in events] == ["Init", "Plan"]
    assert not (tmp_path / "OUT.md").exists()


def test_cli_plan_and_run_help_without_world(tmp_path: Path) -> None:
    plan_help = runner.invoke(app, ["--root", str(tmp_path), "plan", "--help"])
    assert plan_help.exit_code == 0, plan_help.output
    assert "--goal" in _plain(plan_help.output)
    run_help = runner.invoke(app, ["--root", str(tmp_path), "run", "--help"])
    assert run_help.exit_code == 0, run_help.output
    assert "--provider" in _plain(run_help.output)
    assert "--max-steps" in _plain(run_help.output)
    assert ".spaceten" not in _plain(run_help.output)


def test_cli_unknown_provider(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    result = runner.invoke(
        app,
        ["--root", str(tmp_path), "run", "--goal", "x", "--provider", "openai"],
    )
    assert result.exit_code != 0
    assert "unknown provider" in result.output


def test_cli_run_max_steps(tmp_path: Path) -> None:
    inited = runner.invoke(app, ["init", str(tmp_path), "--energy", "1000"])
    assert inited.exit_code == 0
    result = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "run",
            "--goal",
            GOAL,
            "--max-steps",
            "1",
            "--provider",
            "null",
        ],
    )
    assert result.exit_code != 0
    assert "max_steps" in result.stdout
    events = [
        Event.model_validate_json(line)
        for line in (tmp_path / ".spaceten" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    last = events[-1].op
    assert isinstance(last, Failed)
    assert last.code == "max_steps"


def test_kernel_does_not_import_agent() -> None:
    root = Path(__import__("spaceten").__file__).resolve().parent
    for pkg in ("kernel", "store"):
        for path in (root / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
                elif isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
            assert not any(
                name == "spaceten.agent" or name.startswith("spaceten.agent.")
                for name in names
            ), path
