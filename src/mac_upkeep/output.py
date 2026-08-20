"""Task result dataclass and adaptive output formatting."""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass, field

logger = logging.getLogger("mac_upkeep")

# Untrusted text reaches this module from subprocess output and from git remotes.
# The old sanitiser matched SGR colour codes only, which leaves OSC-8 hyperlinks,
# OSC-52 clipboard writes, cursor movement and bare C1 controls intact. Match every
# escape sequence, then drop any remaining control character except tab and newline.
#
# The OSC body is `[^\x07\x1b]*`, NOT `.*?` under DOTALL. A lazy dot has to rescan to
# end-of-input for every unterminated `\x1b]` introducer, which is quadratic: a git
# remote could send 80 KB of them and hang the run for ~12 s (measured) after the
# subprocess timeout had already passed. The negated class cannot backtrack, so an
# unterminated introducer fails in O(1) and is then swept up by the Fe branch below,
# which already covers `\x1b]` as a bare two-character escape.
_ESCAPE_SEQUENCES = re.compile(
    r"""
      \x1b \] [^\x07\x1b]* (?: \x07 | \x1b\\ )  # OSC ... terminated by BEL or ST
    | \x1b \[ [0-?]* [ -/]* [@-~]              # CSI ... params, intermediates, final
    | \x1b [@-Z\\-_]                           # other two-character Fe escapes
    | \x1b [ -/]* [0-~]                        # nF / independent escapes
    """,
    re.VERBOSE,
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_control_sequences(text: str) -> str:
    """Remove ANSI/OSC escape sequences and stray control characters.

    Keeps tab and newline; everything else in C0/C1 goes, including the lone
    U+009B CSI introducer and the carriage returns progress bars use to overwrite
    lines. Bracket characters are NOT touched -- Rich markup is neutralised by
    rendering untrusted text as a literal Text object, not by escaping here.
    """
    return _CONTROL_CHARS.sub("", _ESCAPE_SEQUENCES.sub("", text))


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
            elif t.status == "ok" and t.reason.startswith("dry-run"):
                icon = Text(_DRY, style="cyan")
                detail = Text(t.reason, style="dim")
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
            elif result.status == "ok" and result.reason.startswith("dry-run"):
                # Handlers return a richer reason ("dry-run: 17 repos"); command
                # tasks return the bare "dry-run". Surface the extra detail when
                # there is any, so a handler preview is not reduced to a stub.
                detail = result.reason[len("dry-run") :].lstrip(": ")
                if detail:
                    logger.info("DRY-RUN: would run %s (%s)", result.name, detail)
                else:
                    logger.info("DRY-RUN: would run %s", result.name)
            elif result.status == "ok":
                logger.info("Running %s... done", result.name)
            elif result.status == "failed":
                logger.info("Running %s...", result.name)
                logger.warning("%s %s", result.name, result.reason)

    def task_debug(self, line: str) -> None:
        if self.interactive:
            from rich.text import Text

            # Render as a literal Text: subprocess output and git remote messages
            # must never be parsed as Rich markup. A bracketed path such as
            # `[/usr/local]` raises MarkupError and aborts the whole run, and
            # `[link=https://evil/]click[/link]` renders a real OSC-8 hyperlink
            # with attacker-chosen target and anchor text inside our own output.
            if self._live is not None:
                running = next((t.name for t in self._task_states if t.status == "running"), "")
                if running and self._current_debug_task != running:
                    self._current_debug_task = running
                    self._live.console.print(Text(f"\n  ── {running} ──", style="dim"))
                self._live.console.print(Text(f"  {line}", style="dim"))
            else:
                self._console.log(Text(f"  {line}", style="dim"))
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
            from rich.text import Text

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
                    # `r.reason` carries remote-supplied text -- git_sync aggregates a
                    # server's error message into it. This is the line the product
                    # trains the user to act on, so it is exactly where a rendered
                    # hyperlink would be most credible. Build it as Text.
                    detail = Text("    ")
                    detail.append(_FAIL, style="red")
                    detail.append(f" {r.name} — {r.reason}")
                    self._console.print(detail)
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
