"""Maintenance CLI — automated macOS maintenance."""

from __future__ import annotations

import getpass
import logging
import os
import signal
import subprocess
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer

from maintenance.config import Config
from maintenance.output import Output
from maintenance.tasks import get_brew_prefix, run_all_tasks

app = typer.Typer(
    help="Automated macOS maintenance CLI.\n\n"
    "Runs 8 tasks: gcloud, pnpm, uv, fisher, "
    "mo clean/optimize/purge, brew bundle cleanup.\n\n"
    "Install: brew install calvindotsg/tap/maintenance\n\n"
    "Schedule: brew services start maintenance (Monday 12 PM weekly)\n\n"
    "Config: ~/.config/maintenance/config.toml",
    no_args_is_help=True,
)


def _setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format="[maintenance] %(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
    )


def _handle_signal(signum: int, _frame: object) -> None:
    logging.getLogger("maintenance").warning("Interrupted (signal %d)", signum)
    sys.exit(130)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _version_callback(value: bool) -> None:
    if value:
        try:
            v = pkg_version("maintenance")
        except Exception:
            v = "unknown"
        typer.echo(f"maintenance {v}")
        raise typer.Exit()


@app.callback()
def main(
    _version: Annotated[
        bool | None,
        typer.Option(
            "--version", "-v", callback=_version_callback, is_eager=True, help="Show version."
        ),
    ] = None,
) -> None:
    """Automated macOS maintenance CLI."""


@app.command()
def run(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Preview tasks without executing.")
    ] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show detailed debug output.")] = False,
) -> None:
    """Run all maintenance tasks.

    Tasks are auto-detected: missing tools are skipped with a log message.
    Disable specific tasks via config file or MAINTENANCE_<TASK>=false environment variables.

    Task order: gcloud, pnpm, uv, fisher, mo_clean, mo_optimize, mo_purge, brew_bundle.
    brew_bundle runs last because mo_clean internally runs brew autoremove.

    Exit codes: 0 = completed (some tasks may be skipped), 130 = interrupted.
    """
    _setup_logging(debug)
    config = Config.load()
    output = Output(debug=debug)

    output.header(dry_run=dry_run)
    results = run_all_tasks(config=config, output=output, dry_run=dry_run)
    output.summary(results)


@app.command()
def setup() -> None:
    """Print sudoers rules for this machine.

    Generates machine-specific rules using your username and Homebrew prefix.
    Pipe to sudoers::

        maintenance setup | sudo tee /etc/sudoers.d/maintenance
        sudo chmod 0440 /etc/sudoers.d/maintenance

    The env_keep line preserves HOME so mole operates on your home directory,
    not /var/root (which is the default when running via sudo).
    """
    user = getpass.getuser()
    brew_prefix = get_brew_prefix()
    mo_bin = f"{brew_prefix}/bin/mo"

    typer.echo(f"# Sudoers rules for maintenance CLI ({user}@{brew_prefix})")
    typer.echo(
        "# Install: maintenance setup | sudo tee /etc/sudoers.d/maintenance"
        " && sudo chmod 0440 /etc/sudoers.d/maintenance"
    )
    typer.echo()
    typer.echo(f'Defaults!{mo_bin} env_keep += "HOME"')
    typer.echo(f"{user} ALL = (root) NOPASSWD: {mo_bin} clean")
    typer.echo(f"{user} ALL = (root) NOPASSWD: {mo_bin} optimize")


@app.command()
def status() -> None:
    """Show brew service status for maintenance."""
    try:
        result = subprocess.run(
            ["brew", "services", "info", "maintenance"],
            capture_output=True,
            text=True,
        )
        typer.echo(result.stdout.strip())
    except FileNotFoundError:
        typer.echo("brew not found. Install Homebrew first.")
        raise typer.Exit(1)


@app.command()
def logs(
    follow: Annotated[bool, typer.Option("-f", "--follow", help="Follow log output.")] = False,
    lines: Annotated[int, typer.Argument(help="Number of lines to show.")] = 20,
) -> None:
    """View maintenance log output.

    Logs are written by brew services to $(brew --prefix)/var/log/maintenance.log.
    """
    brew_prefix = get_brew_prefix()
    log_file = Path(brew_prefix) / "var" / "log" / "maintenance.log"

    if not log_file.is_file():
        typer.echo(f"No log file found at {log_file}")
        raise typer.Exit(1)

    cmd = ["tail"]
    if follow:
        cmd.append("-f")
    else:
        cmd.extend(["-n", str(lines)])
    cmd.append(str(log_file))

    os.execvp("tail", cmd)
