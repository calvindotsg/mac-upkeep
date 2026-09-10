"""A timeout must ask before it kills.

Every other test in this suite mocks `_run_guarded`, so none of them can say
anything about what a timeout actually does to a child process. These spawn real
ones.

The bug being guarded: `subprocess.run(timeout=)` answers an expiry with
`Popen.kill()` -- SIGKILL, uncatchable -- so the child never reaches its own
cleanup. For a package manager that is not cosmetic. Homebrew's `Keg#unlink`
removes the prefix symlinks first and its linked-keg record last, so a SIGKILL
between the two leaves a formula whose binary is gone while `brew info` still
reports it `[Linked]`, and `brew link` refuses to repair it forever after.

`test_plain_subprocess_run_never_lets_the_child_clean_up` is the negative control:
it exercises the OLD behaviour and asserts the cleanup does NOT happen. If someone
reverts `_run_guarded` to `subprocess.run`, that test keeps passing and the two
above it go red -- which is the point. A control that cannot fail proves nothing,
so it also asserts the child got far enough to have cleaned up if it could.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

import pytest

from mac_upkeep.config import Config
from mac_upkeep.tasks import _run_guarded, run_task

# Long enough that the child is certainly asleep in the timer when we signal it,
# short enough that the suite stays fast. The child sleeps far longer than either.
_TIMEOUT = 2.0
_GRACE = 1.0

_HANDLES_SIGTERM = """
import signal, sys, time
started, cleaned = sys.argv[1], sys.argv[2]
def _on_term(_s, _f):
    open(cleaned, "w").write("cleanup ran")
    sys.exit(0)
signal.signal(signal.SIGTERM, _on_term)
open(started, "w").write("x")
time.sleep(60)
"""

_IGNORES_SIGTERM = """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(sys.argv[1], "w").write("x")
time.sleep(60)
"""


def _child(script: str, *args) -> list[str]:
    return [sys.executable, "-c", script, *(str(a) for a in args)]


def _wait_for(path, limit=10.0) -> bool:
    """Block until the child says it is running, so we never signal a corpse."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def _run(cmd, **kw):
    return _run_guarded(
        cmd,
        timeout=_TIMEOUT,
        grace=_GRACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
        **kw,
    )


def test_timeout_sends_sigterm_and_the_child_cleans_up(tmp_path):
    """The whole point: the child's own cleanup path runs."""
    started, cleaned = tmp_path / "started", tmp_path / "cleaned"
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run(_child(_HANDLES_SIGTERM, started, cleaned))
    elapsed = time.monotonic() - began

    assert started.exists(), "child never got going -- the test proves nothing"
    assert cleaned.read_text() == "cleanup ran"
    # Reaped on its own exit, not held for the full grace and then shot.
    assert elapsed < _TIMEOUT + _GRACE, f"waited {elapsed:.2f}s -- SIGTERM was not honoured"


def test_a_child_that_ignores_sigterm_is_still_killed(tmp_path):
    """Graceful must not mean unkillable."""
    started = tmp_path / "started"
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run(_child(_IGNORES_SIGTERM, started))
    elapsed = time.monotonic() - began

    assert started.exists(), "child never got going -- the test proves nothing"
    assert elapsed >= _TIMEOUT, "returned before the timeout even expired"
    # It ignored SIGTERM, so it can only have died to the SIGKILL after the grace.
    assert elapsed >= _TIMEOUT + _GRACE * 0.5, f"killed after {elapsed:.2f}s -- no grace given"


def test_plain_subprocess_run_never_lets_the_child_clean_up(tmp_path):
    """NEGATIVE CONTROL for the two tests above -- exercises the old behaviour.

    Asserts the defect is real: `subprocess.run` SIGKILLs, so the cleanup handler
    never runs. Reverting `_run_guarded` to `subprocess.run` leaves this green and
    turns the others red.
    """
    started, cleaned = tmp_path / "started", tmp_path / "cleaned"
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(
            _child(_HANDLES_SIGTERM, started, cleaned),
            timeout=_TIMEOUT,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    # The control is only meaningful if the child was alive and had a handler
    # installed -- otherwise "no cleanup" is trivially true for the wrong reason.
    assert started.exists(), "child never got going -- this control is inert"
    assert not cleaned.exists(), "subprocess.run let the child clean up -- control is inert"


def test_output_written_before_the_timeout_survives(tmp_path):
    """A killed task still has to be able to say what it was doing."""
    script = "import sys, time; print('made it this far'); sys.stdout.flush(); time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        _run(_child(script))
    assert "made it this far" in (exc.value.output or "")


def test_a_fast_child_is_untouched_by_any_of_this():
    """The ordinary path must not have acquired a signal or a delay."""
    began = time.monotonic()
    result = _run(_child("print('ok')"))
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert time.monotonic() - began < _TIMEOUT


def test_run_task_still_reports_a_timeout_as_timed_out(tmp_path, monkeypatch):
    """The escalation is internal: callers see exactly what they saw before."""
    monkeypatch.setattr("mac_upkeep.tasks._TERM_GRACE_SECONDS", _GRACE)
    monkeypatch.setattr("mac_upkeep.tasks.shutil.which", lambda _c: sys.executable)
    result = run_task(
        "brew_update",
        _child("import time; time.sleep(60)"),
        config=Config.load(),
        timeout=int(_TIMEOUT),
    )
    assert result.status == "failed"
    assert result.reason == "timed out"


def test_sigkill_is_what_the_old_path_used(tmp_path):
    """Pins the premise the whole change rests on, so it cannot rot silently."""
    proc = subprocess.Popen(
        _child(_IGNORES_SIGTERM, tmp_path / "started"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    assert _wait_for(tmp_path / "started")
    proc.terminate()  # ignored by the child, by construction
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(timeout=0.5)
    proc.kill()
    assert proc.wait(timeout=10) == -signal.SIGKILL
