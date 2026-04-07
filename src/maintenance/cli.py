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
from maintenance.notify import detect_terminal_bundle_id, format_summary, notify
from maintenance.output import Output
from maintenance.tasks import ALL_TASK_NAMES, TASKS, _load_state, get_brew_prefix, run_all_tasks

app = typer.Typer(
    help="Automated macOS maintenance CLI.\n\n"
    "Runs 11 tasks: brew update/upgrade, gcloud, pnpm, uv, fisher, "
    "mo clean/optimize/purge, brew cleanup, brew bundle cleanup.\n\n"
    "Install: brew install calvindotsg/tap/maintenance\n\n"
    "Schedule: brew services start maintenance (Monday 12 PM weekly)\n\n"
    "Config: ~/.config/maintenance/config.toml",
    no_args_is_help=True,
)


def _complete_force(ctx: typer.Context, incomplete: str) -> list[tuple[str, str]]:
    """Shell completion for --force task names."""
    already = set(ctx.params.get("force") or [])
    completions: list[tuple[str, str]] = []
    if "all".startswith(incomplete) and "all" not in already:
        completions.append(("all", "Force all tasks"))
    for name, desc in TASKS.items():
        if name.startswith(incomplete) and name not in already:
            completions.append((name, desc))
    return completions


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
    force: Annotated[
        list[str] | None,
        typer.Option(
            "--force",
            "-f",
            help="Run only specified task(s), ignoring schedule. Repeat for multiple.",
            autocompletion=_complete_force,
        ),
    ] = None,
) -> None:
    """Run all maintenance tasks.

    Tasks are auto-detected: missing tools are skipped with a log message.
    Disable specific tasks via config file or MAINTENANCE_<TASK>=false environment variables.

    Task order: brew_update, brew_upgrade, gcloud, pnpm, uv, fisher,
    mo_clean, mo_optimize, mo_purge, brew_cleanup, brew_bundle.
    brew_cleanup runs after mo_clean (which runs brew autoremove).
    brew_bundle runs last (homebrew/brew#21350).

    Exit codes: 0 = completed (some tasks may be skipped), 130 = interrupted.
    """
    _setup_logging(debug)
    config = Config.load()
    output = Output(debug=debug)

    # Validate and convert --force option
    force_set: set[str] | None = None
    if force is not None:
        valid_names = set(ALL_TASK_NAMES)
        if "all" in force:
            force_set = valid_names
        else:
            invalid = [t for t in force if t not in valid_names]
            if invalid:
                typer.echo(f"Unknown task(s): {', '.join(invalid)}", err=True)
                typer.echo(f"Valid tasks: {', '.join(ALL_TASK_NAMES)}", err=True)
                raise typer.Exit(1)
            force_set = set(force)

    output.header(dry_run=dry_run, task_names=ALL_TASK_NAMES)
    results = run_all_tasks(config=config, output=output, dry_run=dry_run, force_tasks=force_set)
    output.summary(results)

    if config.notify and not dry_run:
        title, message, subtitle = format_summary(results)
        brew_prefix = get_brew_prefix()
        log_url = f"file://{brew_prefix}/var/log/maintenance.log"
        bundle_id = detect_terminal_bundle_id()
        notify(
            title,
            message,
            subtitle=subtitle,
            sound=config.notify_sound,
            activate_bundle_id=bundle_id,
            open_url=log_url,
        )


@app.command()
def tasks() -> None:
    """List all tasks with frequency, status, and last run time."""
    config = Config.load()
    state = _load_state()

    if sys.stdout.isatty():
        from rich.console import Console
        from rich.table import Table

        table = Table(title="Tasks", title_style="bold", box=None, padding=(0, 2))
        table.add_column("Task", min_width=14)
        table.add_column("Description", min_width=20)
        table.add_column("Frequency", min_width=8)
        table.add_column("Enabled", min_width=7)
        table.add_column("Last Run", min_width=10)

        for name, desc in TASKS.items():
            freq = config.get_frequency(name)
            enabled = "[green]yes[/green]" if config.is_enabled(name) else "[dim]no[/dim]"
            last_run = state.get(name, "never")
            table.add_row(name, desc, freq, enabled, last_run)

        Console(highlight=False).print(table)
    else:
        for name, desc in TASKS.items():
            freq = config.get_frequency(name)
            enabled = "yes" if config.is_enabled(name) else "no"
            last_run = state.get(name, "never")
            typer.echo(f"{name}\t{desc}\t{freq}\t{enabled}\t{last_run}")


@app.command(name="notify-test")
def notify_test() -> None:
    """Send a test notification to verify macOS permissions.

    Useful after initial setup to confirm notifications are allowed in
    System Settings > Notifications.
    """
    config = Config.load()
    brew_prefix = get_brew_prefix()
    bundle_id = detect_terminal_bundle_id()
    log_url = f"file://{brew_prefix}/var/log/maintenance.log"
    ok = notify(
        "Maintenance",
        "Test notification",
        sound=config.notify_sound,
        activate_bundle_id=bundle_id,
        open_url=log_url,
    )
    if ok:
        typer.echo("Notification sent. Check your notification center.")
    else:
        typer.echo("Notification failed. Check System Settings > Notifications.")
        raise typer.Exit(1)


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
    typer.echo()
    typer.echo("# Log rotation (install separately):")
    log_path = f"{brew_prefix}/var/log/maintenance.log"
    typer.echo(f"# echo '{log_path}  {user}:admin  644  12  *  $M1D0  GN'")
    typer.echo("#   | sudo tee /etc/newsyslog.d/maintenance.conf")


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
