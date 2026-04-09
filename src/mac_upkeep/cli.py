"""mac-upkeep CLI — automated macOS maintenance."""

from __future__ import annotations

import getpass
import importlib.resources
import logging
import os
import shutil
import signal
import subprocess
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer

from mac_upkeep.config import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_CONFIG_PATH,
    Config,
    _build_variables,
    _load_defaults,
    get_brew_prefix,
    resolve_variables,
)
from mac_upkeep.notify import detect_terminal_bundle_id, format_summary, notify
from mac_upkeep.output import Output
from mac_upkeep.tasks import TASKS, _load_state, run_all_tasks

app = typer.Typer(
    help="Automated macOS mac-upkeep CLI.\n\n"
    "Runs 11 tasks: brew update/upgrade, gcloud, pnpm, uv, fisher, "
    "mo clean/optimize/purge, brew cleanup, brew bundle cleanup.\n\n"
    "Install: brew install calvindotsg/tap/mac-upkeep\n\n"
    "Schedule: brew services start mac-upkeep (on boot + Monday 12 PM)\n\n"
    "Config: ~/.config/mac-upkeep/config.toml",
    no_args_is_help=True,
)


def _complete_force(ctx: typer.Context, incomplete: str) -> list[tuple[str, str]]:
    """Shell completion for --force task names."""
    try:
        config = Config.load()
        task_names = {
            name: config.task_defs[name].description
            for name in config.run_order
            if name in config.task_defs
        }
    except Exception:
        task_names = TASKS  # fallback to import-time defaults
    already = set(ctx.params.get("force") or [])
    completions: list[tuple[str, str]] = []
    if "all".startswith(incomplete) and "all" not in already:
        completions.append(("all", "Force all tasks"))
    for name, desc in task_names.items():
        if name.startswith(incomplete) and name not in already:
            completions.append((name, desc))
    return completions


def _setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format="[mac-upkeep] %(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
    )


def _handle_signal(signum: int, _frame: object) -> None:
    logging.getLogger("mac_upkeep").warning("Interrupted (signal %d)", signum)
    sys.exit(130)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _version_callback(value: bool) -> None:
    if value:
        try:
            v = pkg_version("mac-upkeep")
        except Exception:
            v = "unknown"
        typer.echo(f"mac-upkeep {v}")
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
    """Automated macOS mac-upkeep CLI."""
    if sys.platform != "darwin":
        typer.echo("mac-upkeep requires macOS.", err=True)
        raise typer.Exit(code=1)


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
    """Run all mac-upkeep tasks.

    Tasks are auto-detected: missing tools are skipped with a log message.
    Disable specific tasks via config file or MAC_UPKEEP_<TASK>=false environment variables.

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
        valid_names = set(config.run_order)
        if "all" in force:
            force_set = valid_names
        else:
            invalid = [t for t in force if t not in valid_names]
            if invalid:
                typer.echo(f"Unknown task(s): {', '.join(invalid)}", err=True)
                typer.echo(f"Valid tasks: {', '.join(config.run_order)}", err=True)
                raise typer.Exit(1)
            force_set = set(force)

    output.header(dry_run=dry_run, task_names=config.run_order)
    results = run_all_tasks(config=config, output=output, dry_run=dry_run, force_tasks=force_set)
    output.summary(results)

    has_activity = any(r.status in ("ok", "failed") for r in results)
    if config.notify and not dry_run and has_activity:
        title, message, subtitle = format_summary(results)
        brew_prefix = get_brew_prefix()
        log_url = f"file://{brew_prefix}/var/log/mac-upkeep.log"
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

    task_list = [
        (name, config.task_defs[name]) for name in config.run_order if name in config.task_defs
    ]

    if sys.stdout.isatty():
        from rich.console import Console
        from rich.table import Table

        table = Table(title="Tasks", title_style="bold", box=None, padding=(0, 2))
        table.add_column("Task", min_width=14)
        table.add_column("Description", min_width=20)
        table.add_column("Frequency", min_width=8)
        table.add_column("Enabled", min_width=7)
        table.add_column("Last Run", min_width=10)

        for name, td in task_list:
            enabled = "[green]yes[/green]" if td.enabled else "[dim]no[/dim]"
            last_run = state.get(name, "never")
            table.add_row(name, td.description, td.frequency, enabled, last_run)

        Console(highlight=False).print(table)
    else:
        for name, td in task_list:
            enabled = "yes" if td.enabled else "no"
            last_run = state.get(name, "never")
            typer.echo(f"{name}\t{td.description}\t{td.frequency}\t{enabled}\t{last_run}")


@app.command()
def init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing config.")] = False,
) -> None:
    """Generate a starter config based on detected tools.

    Probes your system to discover installed tools and writes a commented
    config to ~/.config/mac-upkeep/config.toml. Only detected tasks are
    listed. Built-in defaults apply automatically — uncomment to override.
    """
    config_path = DEFAULT_CONFIG_PATH

    if config_path.is_file() and not force:
        typer.echo(f"Config already exists: {config_path}")
        typer.echo("Use --force to overwrite.")
        raise typer.Exit(1)

    defaults = _load_defaults()
    variables = _build_variables("")
    tasks_data = defaults.get("tasks", {})
    run_order = defaults.get("run", {}).get("order", list(tasks_data.keys()))

    detected: list[tuple[str, dict]] = []
    not_detected: list[tuple[str, dict]] = []

    for task_name in run_order:
        data = tasks_data.get(task_name, {})
        detect_raw = data.get("detect", "")
        if detect_raw:
            try:
                detect_bin = resolve_variables(detect_raw, variables)
            except ValueError:
                detect_bin = detect_raw
        else:
            detect_bin = ""

        if detect_bin and shutil.which(detect_bin):
            detected.append((task_name, data))
        else:
            not_detected.append((task_name, data))

    config_text = _generate_init_config(detected, not_detected)

    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text)
    typer.echo(f"Created {config_path}")
    typer.echo(f"  {len(detected)} tasks detected, {len(not_detected)} not found")
    typer.echo("  Edit the file to customize. Run 'mac-upkeep tasks' to see status.")


def _generate_init_config(
    detected: list[tuple[str, dict]],
    not_detected: list[tuple[str, dict]],
) -> str:
    """Generate an all-comments config TOML string from detection results."""
    from datetime import date

    lines: list[str] = []
    total = len(detected) + len(not_detected)

    lines.append("# mac-upkeep configuration")
    lines.append(
        f"# Generated {date.today()} — {len(detected)} of {total} tasks detected on this system."
    )
    lines.append("#")
    lines.append("# Built-in defaults apply automatically. Only uncomment lines to change.")
    lines.append("# Run 'mac-upkeep tasks' to see task status and last run times.")
    lines.append("# Run 'mac-upkeep show-config --default' to see all defaults.")
    lines.append("")

    if detected:
        lines.append("# ── Detected tasks (enabled by default) ─────────────────────────")
        for task_name, data in detected:
            desc = data.get("description", "")
            freq = data.get("frequency", "weekly")
            lines.append(f"# {task_name:<16} {desc:<44} {freq}")
        lines.append("")

    if not_detected:
        lines.append("# ── Not detected (install to enable) ───────────────────────────")
        for task_name, data in not_detected:
            desc = data.get("description", "")
            detect_bin = data.get("detect", "?").split("/")[-1]
            lines.append(f"# {task_name:<16} {desc:<44} ({detect_bin} not found)")
        lines.append("")

    lines.append("# ── Customize " + "─" * 54)
    lines.append("# [tasks.brew_update]")
    lines.append('# frequency = "monthly"')
    lines.append("#")
    lines.append("# [tasks.fisher]")
    lines.append("# enabled = false")
    lines.append("#")
    lines.append("# [tasks.docker_prune]")
    lines.append('# description = "Prune Docker system"')
    lines.append('# command = "docker system prune -f"')
    lines.append('# detect = "docker"')
    lines.append('# frequency = "monthly"')
    lines.append("#")
    if detected:
        order_str = str([t for t, _ in detected]).replace("'", '"')
        lines.append("# [run]")
        lines.append(f"# order = {order_str}")
    lines.append("")

    return "\n".join(lines)


@app.command(name="show-config")
def show_config(
    default: Annotated[
        bool, typer.Option("--default", help="Show bundled defaults (all tasks).")
    ] = False,
) -> None:
    """Show configuration as TOML.

    With --default: outputs the bundled defaults.toml (all tasks and options).
    Without --default: outputs the user's config overrides, or a setup message.
    """
    if default:
        text = (
            importlib.resources.files("mac_upkeep")
            .joinpath("defaults.toml")
            .read_text(encoding="utf-8")
        )
        typer.echo(text.rstrip())
    else:
        if DEFAULT_CONFIG_PATH.is_file():
            typer.echo(DEFAULT_CONFIG_PATH.read_text().rstrip())
        else:
            typer.echo(f"No config file found at {DEFAULT_CONFIG_PATH}")
            typer.echo(
                "Run 'mac-upkeep init' to generate one, or "
                "'mac-upkeep show-config --default' to see all defaults."
            )


@app.command(name="notify-test")
def notify_test() -> None:
    """Send a test notification to verify macOS permissions.

    Useful after initial setup to confirm notifications are allowed in
    System Settings > Notifications.
    """
    config = Config.load()
    brew_prefix = get_brew_prefix()
    bundle_id = detect_terminal_bundle_id()
    log_url = f"file://{brew_prefix}/var/log/mac-upkeep.log"
    ok = notify(
        "mac-upkeep",
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

        mac-upkeep setup | sudo tee /etc/sudoers.d/mac-upkeep
        sudo chmod 0440 /etc/sudoers.d/mac-upkeep

    The env_keep line preserves HOME so mole operates on your home directory,
    not /var/root (which is the default when running via sudo).
    """
    user = getpass.getuser()
    brew_prefix = get_brew_prefix()
    mo_bin = f"{brew_prefix}/bin/mo"

    typer.echo(f"# Sudoers rules for mac-upkeep CLI ({user}@{brew_prefix})")
    typer.echo(
        "# Install: mac-upkeep setup | sudo tee /etc/sudoers.d/mac-upkeep"
        " && sudo chmod 0440 /etc/sudoers.d/mac-upkeep"
    )
    typer.echo()
    typer.echo(f'Defaults!{mo_bin} env_keep += "HOME"')
    typer.echo(f"{user} ALL = (root) NOPASSWD: {mo_bin} clean")
    typer.echo(f"{user} ALL = (root) NOPASSWD: {mo_bin} optimize")
    typer.echo()
    typer.echo("# Log rotation (install separately):")
    log_path = f"{brew_prefix}/var/log/mac-upkeep.log"
    typer.echo(f"# echo '{log_path}  {user}:admin  644  12  *  $M1D0  GN'")
    typer.echo("#   | sudo tee /etc/newsyslog.d/mac-upkeep.conf")


@app.command()
def status() -> None:
    """Show brew service status for mac-upkeep."""
    try:
        result = subprocess.run(
            ["brew", "services", "info", "mac-upkeep"],
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
    """View mac-upkeep log output.

    Logs are written by brew services to $(brew --prefix)/var/log/mac-upkeep.log.
    """
    brew_prefix = get_brew_prefix()
    log_file = Path(brew_prefix) / "var" / "log" / "mac-upkeep.log"

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
