"""Tests for Output class and TaskResult."""

from __future__ import annotations

import io
from unittest.mock import patch

from mac_upkeep.output import Output, TaskResult, strip_control_sequences


def test_task_result_defaults():
    r = TaskResult("gcloud", "ok")
    assert r.name == "gcloud"
    assert r.status == "ok"
    assert r.reason == ""
    assert r.duration == 0


def test_task_result_with_reason():
    r = TaskResult("pnpm", "skipped", reason="not installed")
    assert r.status == "skipped"
    assert r.reason == "not installed"


def test_task_result_with_duration():
    r = TaskResult("uv", "failed", reason="exit code 1", duration=3.14)
    assert r.duration == 3.14


def _non_interactive(debug: bool = False) -> Output:
    """Create a non-interactive Output for log-based testing."""
    return Output(interactive=False, debug=debug)


def test_output_non_interactive_header(caplog):
    import logging

    output = _non_interactive()
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.header(dry_run=False)
    assert "Starting mac-upkeep..." in caplog.text


def test_output_non_interactive_header_dry_run(caplog):
    import logging

    output = _non_interactive()
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.header(dry_run=True)
    assert "dry-run" in caplog.text


def test_output_non_interactive_task_done_ok(caplog):
    import logging

    output = _non_interactive()
    result = TaskResult("gcloud", "ok", duration=2.5)
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.task_done(result)
    assert "Running gcloud... done" in caplog.text


def test_output_non_interactive_task_done_dry_run(caplog):
    import logging

    output = _non_interactive()
    result = TaskResult("gcloud", "ok", reason="dry-run")
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.task_done(result)
    assert "DRY-RUN" in caplog.text


def test_output_non_interactive_task_done_dry_run_handler_reason(caplog):
    """Handler tasks return 'dry-run: <detail>', not the bare 'dry-run'.

    An exact-equality check fell through to the 'Running X... done' branch, so a
    dry run reported handler tasks as if they had executed.
    """
    import logging

    output = _non_interactive()
    result = TaskResult("git_sync", "ok", reason="dry-run: 17 repos")
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.task_done(result)
    assert "DRY-RUN: would run git_sync (17 repos)" in caplog.text
    assert "done" not in caplog.text


def test_output_non_interactive_task_done_dry_run_bare_has_no_empty_detail(caplog):
    """The bare 'dry-run' reason must not render an empty '()' suffix."""
    import logging

    output = _non_interactive()
    result = TaskResult("gcloud", "ok", reason="dry-run")
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.task_done(result)
    assert "DRY-RUN: would run gcloud" in caplog.text
    assert "()" not in caplog.text


def test_output_non_interactive_ok_reason_is_not_treated_as_dry_run(caplog):
    """A successful handler reason must still read as a real run.

    Guards the prefix check against over-matching: only 'dry-run...' is a preview.
    """
    import logging

    output = _non_interactive()
    result = TaskResult("editor_cache", "ok", reason="nothing to clean")
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.task_done(result)
    assert "Running editor_cache... done" in caplog.text
    assert "DRY-RUN" not in caplog.text


def test_output_non_interactive_task_done_skipped(caplog):
    import logging

    output = _non_interactive()
    result = TaskResult("uv", "skipped", reason="not installed")
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.task_done(result)
    assert "SKIP" in caplog.text
    assert "not installed" in caplog.text


def test_output_non_interactive_task_done_failed(caplog):
    import logging

    output = _non_interactive()
    result = TaskResult("mo_clean", "failed", reason="exit code 1")
    with caplog.at_level(logging.WARNING, logger="mac_upkeep"):
        output.task_done(result)
    assert "mo_clean" in caplog.text


def test_output_non_interactive_summary_no_failures(caplog):
    import logging

    output = _non_interactive()
    output._wall_start = __import__("time").monotonic() - 5
    results = [
        TaskResult("gcloud", "ok"),
        TaskResult("uv", "skipped", reason="not installed"),
    ]
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.summary(results)
    assert "1 ran, 1 skipped" in caplog.text
    assert "failed" not in caplog.text


def test_output_non_interactive_summary_with_failures(caplog):
    import logging

    output = _non_interactive()
    output._wall_start = __import__("time").monotonic() - 5
    results = [
        TaskResult("gcloud", "ok"),
        TaskResult("mo_clean", "failed", reason="exit code 1"),
        TaskResult("uv", "skipped", reason="not installed"),
    ]
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        output.summary(results)
    assert "1 ran, 1 skipped, 1 failed" in caplog.text


@patch("sys.stdout")
def test_output_interactive_detection_uses_isatty(mock_stdout):
    mock_stdout.isatty.return_value = True
    output = Output()
    assert output.interactive is True

    mock_stdout.isatty.return_value = False
    output2 = Output()
    assert output2.interactive is False


# --- F-08: untrusted text must never be parsed as Rich markup ---


def _recording_output() -> tuple[Output, io.StringIO]:
    """An interactive Output whose Rich console writes into a buffer."""
    from rich.console import Console

    buf = io.StringIO()
    out = Output(interactive=True)
    out._console = Console(file=buf, force_terminal=True, width=200, highlight=False)
    return out, buf


def test_task_debug_does_not_crash_on_bracketed_text():
    """`repository [/srv/git/x] is archived` used to raise MarkupError and abort the run."""
    out, buf = _recording_output()
    out.task_debug("repository [/srv/git/x] is archived")
    assert "[/srv/git/x]" in buf.getvalue()


def test_task_debug_does_not_render_hostile_hyperlink():
    """A remote-supplied `[link=...]` must stay literal, not become a real OSC-8 link."""
    out, buf = _recording_output()
    out.task_debug("ERR [link=https://evil.sh/x]Fix: run this[/link]")
    rendered = buf.getvalue()
    # Rich's own Console.log() adds a file:// source link, so assert specifically
    # that the ATTACKER's URL is never an OSC-8 target: a hyperlink target is
    # terminated by ST (ESC backslash), literal text is not.
    assert "https://evil.sh/x\x1b\\" not in rendered
    assert "[link=https://evil.sh/x]" in rendered


def test_summary_failure_line_does_not_render_hostile_hyperlink():
    """summary() is the line the product trains the user to act on -- the phish target."""
    out, buf = _recording_output()
    out.header(dry_run=False)
    out.summary(
        [TaskResult("git_sync", "failed", reason="[link=https://evil.sh/x]click here[/link]")]
    )
    rendered = buf.getvalue()
    assert "https://evil.sh/x\x1b\\" not in rendered
    assert "evil.sh" in rendered


def test_summary_does_not_crash_on_bracketed_reason():
    out, buf = _recording_output()
    out.header(dry_run=False)
    out.summary([TaskResult("git_sync", "failed", reason="repo [/srv/git/x] is archived")])
    assert "[/srv/git/x]" in buf.getvalue()


# --- F-08: the sanitiser covers more than SGR colour codes ---


def test_strip_control_sequences_removes_osc_hyperlink():
    payload = "a\x1b]8;;https://evil.sh/x\x1b\\click\x1b]8;;\x1b\\b"
    assert strip_control_sequences(payload) == "aclickb"


def test_strip_control_sequences_removes_osc52_clipboard_write():
    assert strip_control_sequences("x\x1b]52;c;aGF4\x07y") == "xy"


def test_strip_control_sequences_removes_cursor_moves_and_cr():
    assert strip_control_sequences("a\x1b[2Kb\rc") == "abc"


def test_strip_control_sequences_removes_bare_c1():
    assert strip_control_sequences("c1\x9b31mred") == "c131mred"


def test_strip_control_sequences_keeps_tabs_newlines_and_brackets():
    text = "keep\ttab\nand [/srv/git/x]"
    assert strip_control_sequences(text) == text
