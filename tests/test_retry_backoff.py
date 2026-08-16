"""Regression tests for retry backoff on failing tasks.

Production regression these cover: `mo optimize` began exiting 1 whenever any of
its sub-tasks reported a failure (mole >= 1.50 returns non-zero if any single
optimization fails, even when the run mostly succeeded). Because only successes
recorded a timestamp, the weekly task never became "recently run" and retried on
every invocation -- 17 consecutive failed attempts across 22 runs in 10 days,
each one a sudo system scan plus a failure notification.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import mac_upkeep.tasks as tasks_mod
from mac_upkeep.config import Config, TaskDef
from mac_upkeep.output import Output
from mac_upkeep.tasks import (
    _backoff_until,
    _clear_failure,
    _load_retry_state,
    _record_failure,
    _run,
    _should_run,
    _skip_reason,
    format_next_run,
)


def _weekly_config() -> Config:
    config = Config.load()
    config.task_defs["gcloud"] = TaskDef(
        name="gcloud", description="", command="gcloud", frequency="weekly"
    )
    return config


def _set_retry(task_key: str, failures: int, ago: timedelta) -> None:
    """Write a retry record as if the last attempt happened `ago` in the past."""
    tasks_mod._STATE_DIR.mkdir(parents=True, exist_ok=True)
    tasks_mod._RETRY_FILE.write_text(
        json.dumps(
            {
                task_key: {
                    "failures": failures,
                    "last_attempt": (datetime.now() - ago).isoformat(timespec="seconds"),
                }
            }
        )
    )


def _run_failing_gcloud(config: Config, *, returncode: int = 1, dry_run: bool = False):
    with (
        patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/gcloud"),
        patch("mac_upkeep.tasks.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=returncode, stdout="", stderr="boom")
        return _run(
            "gcloud",
            ["gcloud", "update"],
            config=config,
            output=Output(interactive=False),
            dry_run=dry_run,
            detect="gcloud",
        )


# --- recording ---


def test_failure_records_retry_state():
    config = _weekly_config()
    result = _run_failing_gcloud(config)
    assert result.status == "failed"
    assert _load_retry_state()["gcloud"]["failures"] == 1


def test_consecutive_failures_increment():
    _record_failure("gcloud")
    _record_failure("gcloud")
    _record_failure("gcloud")
    assert _load_retry_state()["gcloud"]["failures"] == 3


def test_success_clears_retry_state():
    """A task that recovers must not stay in backoff.

    The prior failure is aged past its 1h backoff so the task is eligible again;
    the successful run must then reset the counter to zero.
    """
    config = _weekly_config()
    _set_retry("gcloud", failures=1, ago=timedelta(hours=5))
    assert _should_run("gcloud", config) is True
    result = _run_failing_gcloud(config, returncode=0)
    assert result.status == "ok"
    assert "gcloud" not in _load_retry_state()


def test_dry_run_does_not_record_failure():
    config = _weekly_config()
    _run_failing_gcloud(config, dry_run=True)
    assert _load_retry_state() == {}


# --- gating ---


def test_backoff_blocks_immediate_retry():
    """The core regression: one failure must not permit an instant re-run."""
    config = _weekly_config()
    _set_retry("gcloud", failures=1, ago=timedelta(minutes=5))
    assert _should_run("gcloud", config) is False


def test_backoff_expires_after_delay():
    config = _weekly_config()
    _set_retry("gcloud", failures=1, ago=timedelta(hours=2))
    assert _should_run("gcloud", config) is True


def test_backoff_doubles_per_failure():
    """Two failures → 2h delay: still blocked at 90m, clear at 3h."""
    config = _weekly_config()
    _set_retry("gcloud", failures=2, ago=timedelta(minutes=90))
    assert _should_run("gcloud", config) is False
    _set_retry("gcloud", failures=2, ago=timedelta(hours=3))
    assert _should_run("gcloud", config) is True


def test_backoff_capped_at_frequency_threshold():
    """Backoff must never exceed the task's own interval (weekly = 6 days)."""
    config = _weekly_config()
    _set_retry("gcloud", failures=40, ago=timedelta(days=7))
    assert _should_run("gcloud", config) is True


def test_backoff_cap_still_blocks_within_threshold():
    config = _weekly_config()
    _set_retry("gcloud", failures=40, ago=timedelta(days=3))
    assert _should_run("gcloud", config) is False


def test_permanently_failing_weekly_task_is_bounded():
    """mo_optimize scenario: a task that always fails must not run every time.

    Simulates 10 days of invocations. With backoff, attempts are bounded by the
    doubling schedule (1h, 2h, 4h ... capped at 6 days) rather than running on
    all 22 invocations as observed in production.
    """
    config = _weekly_config()
    now = datetime.now()
    attempts = 0
    # 10 days of invocations, roughly two per day.
    for hours_elapsed in range(0, 240, 12):
        moment = now + timedelta(hours=hours_elapsed)
        with patch("mac_upkeep.tasks.datetime") as mock_dt:
            mock_dt.now.return_value = moment
            mock_dt.fromisoformat = datetime.fromisoformat
            if _should_run("gcloud", config):
                attempts += 1
                _record_failure("gcloud")
    assert attempts <= 8, f"expected bounded retries, got {attempts}"
    assert attempts >= 3, "backoff must still retry periodically, not give up"


# --- reporting ---


def test_skip_reason_names_backoff_not_recency():
    """A failing task must not be reported as having 'ran recently'."""
    config = _weekly_config()
    _set_retry("gcloud", failures=3, ago=timedelta(minutes=1))
    reason = _skip_reason("gcloud", config)
    assert reason is not None
    assert reason.startswith("retry backoff after 3 failures")


def test_skip_reason_singular_failure():
    config = _weekly_config()
    _set_retry("gcloud", failures=1, ago=timedelta(minutes=1))
    assert _skip_reason("gcloud", config).startswith("retry backoff after 1 failure,")


def test_skip_reason_none_when_eligible():
    assert _skip_reason("gcloud", _weekly_config()) is None


def test_format_next_run_reflects_backoff():
    """The tasks dashboard must not claim 'now' while a task is backing off."""
    config = _weekly_config()
    _set_retry("gcloud", failures=3, ago=timedelta(minutes=1))
    assert format_next_run("gcloud", config, state={}) != "now"


# --- resilience ---


def test_corrupt_retry_file_does_not_block():
    tasks_mod._STATE_DIR.mkdir(parents=True, exist_ok=True)
    tasks_mod._RETRY_FILE.write_text("{not json")
    assert _load_retry_state() == {}
    assert _should_run("gcloud", _weekly_config()) is True


def test_malformed_retry_entry_ignored():
    tasks_mod._STATE_DIR.mkdir(parents=True, exist_ok=True)
    tasks_mod._RETRY_FILE.write_text(json.dumps({"gcloud": {"failures": "many"}}))
    assert _backoff_until("gcloud", _weekly_config()) is None


def test_clear_failure_on_unknown_task_is_noop():
    _clear_failure("never-seen")
    assert _load_retry_state() == {}
