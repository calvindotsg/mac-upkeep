"""Task result dataclass and adaptive output formatting."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field

logger = logging.getLogger("maintenance")

# Icons
_OK = "\u2713"       # ✓
_SKIP = "\u25cb"     # ○
_FAIL = "\u2717"     # ✗
_DRY = "\u2192"      # →
_BULLET = "\u2022"   # •


@dataclass
class TaskResult:
    """Result of a single maintenance task."""

    name: str
    status: str  # "ok", "skipped", "failed"
    reason: str = ""  # "disabled", "not installed", "exit code 1", "timed out"
    duration: float = 0  # seconds elapsed


@dataclass
class Output:
    """Adaptive output: Rich in interactive terminals, plain logging for launchd."""

    interactive: bool = field(default_factory=lambda: sys.stdout.isatty())
    debug: bool = False
    _console: object = field(default=None, init=False, repr=False)
    _status_ctx: object = field(default=None, init=False, repr=False)
    _wall_start: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.interactive:
            from rich.console import Console
            self._console = Console(highlight=False)

    def header(self, *, dry_run: bool = False) -> None:
        suffix = " (dry-run)" if dry_run else ""
        if self.interactive:
            from rich.text import Text
            header = Text(f"Maintenance{suffix}", style="bold")
            self._console.print(header)
            self._console.print()
        else:
            logger.info("Starting maintenance%s...", suffix)
        self._wall_start = time.monotonic()

    def task_start(self, name: str) -> None:
        if self.interactive:
            from rich.status import Status  # noqa: F401
            self._status_ctx = self._console.status(
                f"[bold]Running {name}...[/bold]",
                spinner="dots",
            )
            self._status_ctx.__enter__()
        # Non-interactive: no log here; task_done handles all messages

    def task_done(self, result: TaskResult) -> None:
        if self.interactive:
            if self._status_ctx is not None:
                self._status_ctx.__exit__(None, None, None)
                self._status_ctx = None

            if result.status == "ok":
                if result.reason == "dry-run":
                    icon = f"[cyan]{_DRY}[/cyan]"
                    detail = "[dim]dry-run[/dim]"
                else:
                    icon = f"[green]{_OK}[/green]"
                    detail = f"[dim]{result.duration:.1f}s[/dim]" if result.duration else ""
            elif result.status == "skipped":
                icon = f"[dim]{_SKIP}[/dim]"
                detail = f"[dim]skipped ({result.reason})[/dim]"
            else:  # failed
                icon = f"[red]{_FAIL}[/red]"
                detail = f"[red]{result.reason}[/red]"

            name_col = f"{result.name:<20}"
            self._console.print(f"  {icon} {name_col}  {detail}")
        else:
            if result.status == "skipped":
                logger.info("SKIP: %s (%s)", result.name, result.reason)
            elif result.status == "ok" and result.reason == "dry-run":
                logger.info("DRY-RUN: would run %s", result.name)
            elif result.status == "ok":
                logger.info("Running %s... done", result.name)
            elif result.status == "failed":
                logger.info("Running %s...", result.name)
                logger.warning("%s %s", result.name, result.reason)

    def task_debug(self, line: str) -> None:
        if self.interactive:
            self._console.log(f"  [dim]{line}[/dim]")
        else:
            logger.debug("  %s", line)

    def summary(self, results: list[TaskResult]) -> None:
        ok = [r for r in results if r.status == "ok"]
        skipped = [r for r in results if r.status == "skipped"]
        failed = [r for r in results if r.status == "failed"]
        wall = time.monotonic() - self._wall_start

        if self.interactive:
            from rich.rule import Rule

            self._console.print()
            self._console.print(Rule(style="dim"))

            if failed:
                style = "bold red"
                summary_line = (
                    f"Maintenance finished with errors: "
                    f"{len(ok)} ran, {len(skipped)} skipped, {len(failed)} failed  "
                    f"[dim]{wall:.1f}s[/dim]"
                )
            else:
                style = "bold green"
                summary_line = (
                    f"Maintenance complete: "
                    f"{len(ok)} ran, {len(skipped)} skipped  "
                    f"[dim]{wall:.1f}s[/dim]"
                )

            self._console.print(f"  [{style.split()[1]}]{summary_line}[/]")
            for r in failed:
                self._console.print(f"    [red]{_FAIL}[/red] {r.name} — {r.reason}")
            self._console.print(Rule(style="dim"))
        else:
            if failed:
                logger.info(
                    "Maintenance complete: %d ran, %d skipped, %d failed.",
                    len(ok), len(skipped), len(failed),
                )
            else:
                logger.info("Maintenance complete: %d ran, %d skipped.", len(ok), len(skipped))
