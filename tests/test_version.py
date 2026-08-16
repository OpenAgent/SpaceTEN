from typer.testing import CliRunner

from spaceten import __version__
from spaceten.cli.main import app

runner = CliRunner()


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_version_cli() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"
