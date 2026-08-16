import hashlib
import tarfile
from pathlib import Path

from typer.testing import CliRunner

from spaceten.cli.main import app
from spaceten.contest.anagrams import NAMES, NAMES_MD, PAIRS, RULES_MD, TERMS
from spaceten.contest.letters import letters_of
from spaceten.contest.parse import parse_step
from spaceten.contest.verify import verify_root
from spaceten.kernel.event import Act, Observe
from spaceten.kernel.world import World

runner = CliRunner()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _act(address: str, data: bytes) -> Act:
    return Act(
        tool="write_file",
        address=address,
        digest=_sha(data),
        size_bytes=len(data),
    )


def _write(world: World, address: str, text: str) -> None:
    data = text.encode("utf-8")
    world.propose("human", _act(address, data), data=data)


def test_pair_letter_bags() -> None:
    for name, words in PAIRS.items():
        union = letters_of("".join(words))
        display = next(n for n in NAMES if n.casefold() == name)
        assert letters_of(display) == union


def test_examples_match_constants() -> None:
    root = Path("examples/contest/anagrams")
    assert (root / "NAMES.md").read_text(encoding="utf-8") == NAMES_MD
    assert (root / "RULES.md").read_text(encoding="utf-8") == RULES_MD


def test_sample_pack_verifies(tmp_path: Path) -> None:
    archive = Path("examples/contest/anagrams/sample.sten.tgz")
    assert archive.is_file()
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(tmp_path, filter="data")
    report = verify_root(tmp_path / "play-sample")
    assert report.ok, report.issues
    assert report.spent_mj == 25
    assert report.events == 15


def test_parse_inventory_and_extract() -> None:
    inv = parse_step("kind: inventory\nname: Pam Eisect\nletters: A,C,E,E,I,M,P,S,T\n")
    assert inv.kind == "inventory"
    ext = parse_step(
        "kind: extract\nword: TIME\nfrom:\n  - Pam Eisect\n  - Regine Tyme\n"
    )
    assert ext.kind == "extract"
    assert ext.word == "TIME"
    assert ext.evidence == ("Pam Eisect", "Regine Tyme")


def _play(tmp_path: Path) -> World:
    inited = runner.invoke(app, ["contest", "init", str(tmp_path)])
    assert inited.exit_code == 0, inited.output
    world = World.load(tmp_path)
    world.propose("human", Observe(address="NAMES.md", size_bytes=0, content_hash=None))
    _write(
        world,
        "steps/01.md",
        "kind: inventory\nname: Pam Eisect\nletters: A,C,E,E,I,M,P,S,T\n",
    )
    _write(
        world,
        "steps/02.md",
        "kind: extract\nword: TIME\nfrom:\n"
        "  - Pam Eisect\n  - Regine Tyme\n  - Ruben Timme\n",
    )
    _write(
        world,
        "steps/03.md",
        "kind: extract\nword: SPACE\nfrom:\n"
        "  - Pam Eisect\n  - Percy Geanes\n  - Reuben Camps\n",
    )
    _write(
        world,
        "steps/04.md",
        "kind: extract\nword: ENERGY\nfrom:\n"
        "  - Percy Geanes\n  - Regine Tyme\n  - Remy Bergunen\n",
    )
    _write(
        world,
        "steps/05.md",
        "kind: extract\nword: NUMBER\nfrom:\n"
        "  - Reuben Camps\n  - Ruben Timme\n  - Remy Bergunen\n",
    )
    for i, (name, words) in enumerate(PAIRS.items(), start=6):
        display = next(n for n in NAMES if n.casefold() == name)
        left, right = sorted(words)
        _write(
            world,
            f"steps/{i:02d}.md",
            f"kind: pair\nname: {display}\nwords: [{left}, {right}]\n",
        )
    lines = ["TERMS: " + ", ".join(sorted(TERMS))]
    for name, words in PAIRS.items():
        display = next(n for n in NAMES if n.casefold() == name)
        left, right = sorted(words)
        lines.append(f"{display} = {left} + {right}")
    _write(world, "SOLUTION.md", "\n".join(lines) + "\n")
    return World.load(tmp_path)


def test_valid_run_verifies(tmp_path: Path) -> None:
    world = _play(tmp_path)
    report = verify_root(tmp_path)
    assert report.ok, report.issues
    assert report.events >= 13
    assert report.spent_mj > 0
    assert world.check(rebuild=True).ok


def test_solution_only_is_rejected(tmp_path: Path) -> None:
    assert runner.invoke(app, ["contest", "init", str(tmp_path)]).exit_code == 0
    world = World.load(tmp_path)
    world.propose("human", Observe(address="NAMES.md", size_bytes=0, content_hash=None))
    lines = ["TERMS: " + ", ".join(sorted(TERMS))]
    for name, words in PAIRS.items():
        display = next(n for n in NAMES if n.casefold() == name)
        left, right = sorted(words)
        lines.append(f"{display} = {left} + {right}")
    _write(world, "SOLUTION.md", "\n".join(lines) + "\n")
    report = verify_root(tmp_path)
    assert not report.ok
    codes = {issue.code for issue in report.issues}
    assert "no_inventory" in codes
    assert "no_extract" in codes
    assert "missing_pair" in codes


def test_wrong_pair_rejected(tmp_path: Path) -> None:
    assert runner.invoke(app, ["contest", "init", str(tmp_path)]).exit_code == 0
    world = World.load(tmp_path)
    world.propose("human", Observe(address="NAMES.md", size_bytes=0, content_hash=None))
    _write(
        world,
        "steps/01.md",
        "kind: inventory\nname: Pam Eisect\nletters: A,C,E,E,I,M,P,S,T\n",
    )
    _write(
        world,
        "steps/02.md",
        "kind: extract\nword: TIME\nfrom:\n  - Pam Eisect\n",
    )
    _write(
        world,
        "steps/03.md",
        "kind: extract\nword: SPACE\nfrom:\n  - Pam Eisect\n",
    )
    _write(
        world,
        "steps/04.md",
        "kind: pair\nname: Ruben Timme\nwords: [SPACE, TIME]\n",
    )
    report = verify_root(tmp_path)
    assert not report.ok
    assert any(issue.code == "bad_pair" for issue in report.issues)


def test_cli_init_verify_pack(tmp_path: Path) -> None:
    play = tmp_path / "play"
    _play(play)
    verified = runner.invoke(app, ["--root", str(play), "contest", "verify"])
    assert verified.exit_code == 0, verified.output
    assert "ok" in verified.stdout
    packed = runner.invoke(
        app,
        ["--root", str(play), "contest", "pack", "--out", str(tmp_path / "run.tgz")],
    )
    assert packed.exit_code == 0, packed.output
    assert (tmp_path / "run.tgz").is_file()


def test_contest_help_without_world(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--root", str(tmp_path), "contest", "--help"])
    assert result.exit_code == 0
    assert "verify" in result.output
