import typer

from spaceten import __version__

app = typer.Typer(name="spaceten", no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """SpaceTEN — Space Time Energy Number."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)
