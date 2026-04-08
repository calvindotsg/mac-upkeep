"""Task result dataclass and adaptive output formatting."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field

logger = logging.getLogger("mac_upkeep")

# Icons
_OK = "\u2713"  # ✓
_SKIP = "\u25cb"  # ○
_FAIL = "\u2717"  # ✗
_DRY = "\u2192"  # →
_BULLET = "\u2022"  # •


@dataclass
class TaskResult:
    """Result of a single mac-upkeep task."""

    name: str
    status: str  # "ok", "skipped", "failed"
    reason: str = ""  # "disabled", "not installed", "exit code 1", "timed out"
    duration: float = 0  # seconds elapsed


@dataclass
class _TaskState:
    """Internal state for a single task in the live TUI table."""

    name: str
    status: str = "pending"  # pending, running, ok, skipped, failed
    reason: str = ""
    duration: float = 0.0


@dataclass
class Output:
    """Adaptive output: Rich in interactive terminals, plain logging for launchd."""

    interactive: bool = field(default_factory=lambda: sys.stdout.isatty())
    debug: bool = False
    _console: object = field(default=None, init=False, repr=False)
    _live: object = field(default=None, init=False, repr=False)
    _task_states: list = field(default_factory=list, init=False, repr=False)
    _dry_run: bool = field(default=False, init=False, repr=False)
    _current_debug_task: str = field(default="", init=False, repr=False)
    _wall_start: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.interactive:
            from rich.console import Console

            self._console = Console(highlight=False)

    def header(self, *, dry_run: bool = False, task_names: list[str] | None = None) -> None:
        self._dry_run = dry_run
        suffix = " (dry-run)" if dry_run else ""
        if self.interactive:
            if task_names:
                self._task_states = [_TaskState(name=n) for n in task_names]
                from rich.live import Live

                self._live = Live(
                    self._generate_table(), refresh_per_second=4, console=self._console
                )
                self._live.__enter__()
            else:
                from rich.text import Text

                self._console.print(Text(f"mac-upkeep{suffix}", style="bold"))
                self._console.print()
        else:
            logger.info("Starting mac-upkeep%s...", suffix)
        self._wall_start = time.monotonic()

    def _generate_table(self) -> object:
        """Build a Rich Table from current task states."""
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text

        completed = sum(1 for t in self._task_states if t.status not in ("pending", "running"))
        total = len(self._task_states)
        suffix = " (dry-run)" if self._dry_run else ""
        title = f"mac-upkeep{suffix} [{completed}/{total}]"

        table = Table(title=title, title_style="bold", show_header=False, box=None, padding=(0, 2))
        table.add_column("icon", width=2)
        table.add_column("name", min_width=16)
        table.add_column("detail", min_width=20)

        for t in self._task_states:
            if t.status == "running":
                icon = Spinner("dots", style="yellow")
                detail = Text("running", style="yellow")
            elif t.status == "ok" and t.reason == "dry-run":
                icon = Text(_DRY, style="cyan")
                detail = Text("dry-run", style="dim")
            elif t.status == "ok":
                icon = Text(_OK, style="green")
                detail = Text(f"{t.duration:.1f}s", style="dim") if t.duration else Text("")
            elif t.status == "skipped":
                icon = Text(_SKIP, style="dim")
                detail = Text(t.reason, style="dim")
            elif t.status == "failed":
                icon = Text(_FAIL, style="red")
                detail = Text(t.reason, style="red")
            else:
                icon = Text(_BULLET, style="dim")
                detail = Text("pending", style="dim")
            table.add_row(icon, t.name, detail)
        return table

    def task_start(self, name: str) -> None:
        if self.interactive:
            if self._live is not None:
                for t in self._task_states:
                    if t.name == name:
                        t.status = "running"
                        break
                self._live.update(self._generate_table())
        # Non-interactive: no log here; task_done handles all messages

    def task_done(self, result: TaskResult) -> None:
        if self.interactive:
            if self._live is not None:
                for t in self._task_states:
                    if t.name == result.name:
                        t.status = result.status
                        t.reason = result.reason
                        t.duration = result.duration
                        break
                self._live.update(self._generate_table())
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
            if self._live is not None:
                running = next((t.name for t in self._task_states if t.status == "running"), "")
                if running and self._current_debug_task != running:
                    self._current_debug_task = running
                    self._live.console.print(f"\n  [dim]── {running} ──[/dim]")
                self._live.console.print(f"  [dim]{line}[/dim]")
            else:
                self._console.log(f"  [dim]{line}[/dim]")
        else:
            logger.debug("  %s", line)

    def summary(self, results: list[TaskResult]) -> None:
        ok = [r for r in results if r.status == "ok"]
        skipped = [r for r in results if r.status == "skipped"]
        failed = [r for r in results if r.status == "failed"]
        wall = time.monotonic() - self._wall_start

        if self.interactive:
            # Exit Live context
            if self._live is not None:
                self._live.__exit__(None, None, None)
                self._live = None

            from rich.rule import Rule

            self._console.print()
            self._console.print(Rule(style="dim"))

            if failed:
                summary_line = (
                    f"mac-upkeep finished with errors: "
                    f"{len(ok)} ran, {len(skipped)} skipped, {len(failed)} failed  "
                    f"[dim]{wall:.1f}s[/dim]"
                )
                self._console.print(f"  [red]{summary_line}[/]")
                for r in failed:
                    self._console.print(f"    [red]{_FAIL}[/red] {r.name} — {r.reason}")
            else:
                summary_line = (
                    f"mac-upkeep complete: "
                    f"{len(ok)} ran, {len(skipped)} skipped  "
                    f"[dim]{wall:.1f}s[/dim]"
                )
                self._console.print(f"  [green]{summary_line}[/]")

            self._console.print(Rule(style="dim"))
        else:
            if failed:
                logger.info(
                    "mac-upkeep complete: %d ran, %d skipped, %d failed.",
                    len(ok),
                    len(skipped),
                    len(failed),
                )
            else:
                logger.info("mac-upkeep complete: %d ran, %d skipped.", len(ok), len(skipped))
