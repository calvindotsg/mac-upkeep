"""Tests for Output class and TaskResult."""

from __future__ import annotations

from unittest.mock import patch

from maintenance.output import Output, TaskResult


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
    with caplog.at_level(logging.INFO, logger="maintenance"):
        output.header(dry_run=False)
    assert "Starting maintenance..." in caplog.text


def test_output_non_interactive_header_dry_run(caplog):
    import logging
    output = _non_interactive()
    with caplog.at_level(logging.INFO, logger="maintenance"):
        output.header(dry_run=True)
    assert "dry-run" in caplog.text


def test_output_non_interactive_task_done_ok(caplog):
    import logging
    output = _non_interactive()
    result = TaskResult("gcloud", "ok", duration=2.5)
    with caplog.at_level(logging.INFO, logger="maintenance"):
        output.task_done(result)
    assert "Running gcloud... done" in caplog.text


def test_output_non_interactive_task_done_dry_run(caplog):
    import logging
    output = _non_interactive()
    result = TaskResult("gcloud", "ok", reason="dry-run")
    with caplog.at_level(logging.INFO, logger="maintenance"):
        output.task_done(result)
    assert "DRY-RUN" in caplog.text


def test_output_non_interactive_task_done_skipped(caplog):
    import logging
    output = _non_interactive()
    result = TaskResult("uv", "skipped", reason="not installed")
    with caplog.at_level(logging.INFO, logger="maintenance"):
        output.task_done(result)
    assert "SKIP" in caplog.text
    assert "not installed" in caplog.text


def test_output_non_interactive_task_done_failed(caplog):
    import logging
    output = _non_interactive()
    result = TaskResult("mo_clean", "failed", reason="exit code 1")
    with caplog.at_level(logging.WARNING, logger="maintenance"):
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
    with caplog.at_level(logging.INFO, logger="maintenance"):
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
    with caplog.at_level(logging.INFO, logger="maintenance"):
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
