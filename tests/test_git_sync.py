"""Tests for the git_sync handler."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

from mac_upkeep.config import Config
from mac_upkeep.git_sync import (
    _format_failures,
    _resolve_paths,
    _run_git,
    _strip_ansi,
    _trusted_overrides,
    run_git_sync,
)


def _split_git_args(args: list[str]) -> tuple[str, list[str]]:
    """Return (repo path, args after `-C <path>`) from a built git command."""
    i = args.index("-C")
    return args[i + 1], args[i + 2 :]


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _sequenced(responses: list):
    """A `subprocess.run` stand-in that answers `_sync_repo`'s config pre-flight itself.

    The pre-flight (`git config --list --show-scope`) is not what these tests are
    about, and threading an extra entry through every fixed sequence would only make
    them brittle in a new way -- and brittle in a way that FAILS QUIETLY: an off-by-one
    here silently shifts every later response onto the wrong git command, so the test
    keeps passing while exercising a different code path.
    """
    calls = iter(responses)

    def run(*a, **k):
        cmd = a[0] if a else k.get("args", [])
        if "--show-scope" in cmd:
            return _cp(returncode=0, stdout="")  # nothing unsafe declared
        result = next(calls)
        if isinstance(result, BaseException):
            raise result
        return result

    return run


def _config(repos: list[str], *, skip_dirty: bool = True) -> Config:
    config = Config.load()
    config.git_sync_repos = list(repos)
    config.git_sync_skip_dirty = skip_dirty
    return config


def _make_repo(tmp_path, name: str) -> str:
    """Create a fake repo directory with a .git subdir so glob matches."""
    repo = tmp_path / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return str(repo)


# --- path resolution ---


def test_resolve_paths_literal(tmp_path):
    p = _make_repo(tmp_path, "biz")
    output = MagicMock()
    paths = _resolve_paths([p], output)
    assert paths == [p]


def test_resolve_paths_glob(tmp_path):
    _make_repo(tmp_path, "max-alpha")
    _make_repo(tmp_path, "max-beta")
    output = MagicMock()
    paths = _resolve_paths([f"{tmp_path}/max-*"], output)
    assert sorted(paths) == sorted([str(tmp_path / "max-alpha"), str(tmp_path / "max-beta")])


def test_resolve_paths_glob_no_match(tmp_path):
    output = MagicMock()
    paths = _resolve_paths([f"{tmp_path}/nothing-*"], output)
    assert paths == []
    output.task_debug.assert_called()
    assert "no match" in output.task_debug.call_args[0][0]


def test_resolve_paths_dedupes(tmp_path):
    p = _make_repo(tmp_path, "biz")
    output = MagicMock()
    paths = _resolve_paths([p, p, f"{tmp_path}/b*"], output)
    assert paths == [p]


# --- empty configuration ---


def test_run_git_sync_no_repos_configured(tmp_path):
    config = _config([])
    output = MagicMock()
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "skipped"
    assert result.reason == "no repos configured"


def test_run_git_sync_glob_no_match(tmp_path):
    config = _config([f"{tmp_path}/nothing-*"])
    output = MagicMock()
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "skipped"
    assert result.reason == "no repos matched"


# --- dry run ---


def test_run_git_sync_dry_run(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    run_mock = MagicMock()
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run_mock)
    result = run_git_sync(config, output, dry_run=True)
    assert result.status == "ok"
    assert "dry-run" in result.reason
    run_mock.assert_not_called()
    assert any("would pull" in c[0][0] for c in output.task_debug.call_args_list)


# --- per-repo skip paths ---


def test_skip_not_a_repo(tmp_path, monkeypatch):
    p = str(tmp_path / "not-repo")
    (tmp_path / "not-repo").mkdir()
    config = _config([p])
    output = MagicMock()
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: _cp(returncode=128))
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert result.reason == "1 skipped"


def test_skip_no_remote(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    run = _sequenced(
        [
            _cp(returncode=0, stdout="true\n"),  # is-inside-work-tree
            _cp(returncode=0, stdout=""),  # remote (empty)
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert result.reason == "1 skipped"
    assert any("no remote configured" in c[0][0] for c in output.task_debug.call_args_list)


def test_skip_no_upstream(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    run = _sequenced(
        [
            _cp(returncode=0, stdout="true\n"),  # is-inside-work-tree
            _cp(returncode=0, stdout="origin\n"),  # remote
            _cp(returncode=0, stdout="feature-branch\n"),  # current branch
            _cp(returncode=128, stderr="no upstream\n"),  # @{upstream}
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert any(
        "no upstream (branch=feature-branch)" in c[0][0] for c in output.task_debug.call_args_list
    )


def test_skip_dirty_worktree(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p], skip_dirty=True)
    output = MagicMock()
    run = _sequenced(
        [
            _cp(returncode=0, stdout="true\n"),
            _cp(returncode=0, stdout="origin\n"),
            _cp(returncode=0, stdout="main\n"),
            _cp(returncode=0, stdout="origin/main\n"),
            _cp(returncode=0, stdout=" M file.txt\n"),
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert any("dirty worktree" in c[0][0] for c in output.task_debug.call_args_list)


# --- aggregation ---


def test_aggregate_mixed(tmp_path, monkeypatch):
    """3 pulled, 2 skipped → 'ok' with reason '3 pulled, 2 skipped'."""
    repos = [_make_repo(tmp_path, f"r{i}") for i in range(5)]
    config = _config(repos)
    output = MagicMock()

    def fake_run(args, **kwargs):
        # Locate `-C <path>` rather than indexing: _run_git prepends a variable
        # number of `-c` hardening overrides ahead of it.
        path, rest = _split_git_args(args)
        op = rest[0] if rest else ""
        basename = os.path.basename(path)
        if basename in ("r0", "r1"):
            # not a repo
            if op == "rev-parse" and rest[1] == "--is-inside-work-tree":
                return _cp(returncode=128)
        # Successful repos
        if op == "rev-parse" and rest[1] == "--is-inside-work-tree":
            return _cp(returncode=0, stdout="true\n")
        if op == "remote":
            return _cp(returncode=0, stdout="origin\n")
        if op == "rev-parse" and rest[1] == "--abbrev-ref" and rest[2] == "HEAD":
            return _cp(returncode=0, stdout="main\n")
        if op == "rev-parse" and "@{upstream}" in args:
            return _cp(returncode=0, stdout="origin/main\n")
        if op == "config":  # the unsafe-config pre-flight: nothing declared
            return _cp(returncode=0, stdout="")
        if op == "status":
            return _cp(returncode=0, stdout="")
        if op == "pull":
            return _cp(returncode=0, stdout="Already up to date.\n")
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert result.reason == "3 pulled, 2 skipped"


# --- env hardening ---


def test_env_forces_no_terminal_prompt(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    run_mock = MagicMock(
        side_effect=_sequenced(
            [
                _cp(returncode=0, stdout="true\n"),
                _cp(returncode=0, stdout="origin\n"),
                _cp(returncode=0, stdout="main\n"),
                _cp(returncode=0, stdout="origin/main\n"),
                _cp(returncode=0, stdout=""),
                _cp(returncode=0, stdout="Already up to date.\n"),
            ]
        )
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run_mock)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    # Six sequenced responses plus the unsafe-config pre-flight _sequenced answers.
    assert run_mock.call_count == 7
    for call in run_mock.call_args_list:
        env = call.kwargs["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"] == "/usr/bin/true"


def test_env_respects_user_askpass(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_ASKPASS", "/opt/my-askpass")
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    run_mock = MagicMock(
        side_effect=_sequenced(
            [
                _cp(returncode=0, stdout="true\n"),
                _cp(returncode=0, stdout="origin\n"),
                _cp(returncode=0, stdout="main\n"),
                _cp(returncode=0, stdout="origin/main\n"),
                _cp(returncode=0, stdout=""),
                _cp(returncode=0, stdout="Already up to date.\n"),
            ]
        )
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run_mock)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    for call in run_mock.call_args_list:
        env = call.kwargs["env"]
        assert env["GIT_ASKPASS"] == "/opt/my-askpass"
        assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_failure_surfaces_basename_and_stderr(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    run = _sequenced(
        [
            _cp(returncode=0, stdout="true\n"),
            _cp(returncode=0, stdout="origin\n"),
            _cp(returncode=0, stdout="main\n"),
            _cp(returncode=0, stdout="origin/main\n"),
            _cp(returncode=0, stdout=""),  # clean
            _cp(returncode=128, stderr="ssh: Permission denied (publickey)\n"),
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "failed"
    # The cause must reach the aggregate reason, not just --debug output.
    assert result.reason == "1 failed: biz (ssh: Permission denied (publickey))"
    assert any("Permission denied" in c[0][0] for c in output.task_debug.call_args_list)


def test_pull_timeout_does_not_crash_run(tmp_path, monkeypatch):
    """A hung pull raises TimeoutExpired inside subprocess.run; it must be caught
    and surfaced as a failed repo rather than aborting the whole run."""
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    # MagicMock raises any exception instance found in side_effect, so the pull
    # step (last) raises TimeoutExpired exactly where _run_git calls subprocess.run.
    run_mock = MagicMock(
        side_effect=_sequenced(
            [
                _cp(returncode=0, stdout="true\n"),
                _cp(returncode=0, stdout="origin\n"),
                _cp(returncode=0, stdout="main\n"),
                _cp(returncode=0, stdout="origin/main\n"),
                _cp(returncode=0, stdout=""),  # clean worktree
                subprocess.TimeoutExpired(cmd=["git", "pull"], timeout=60),  # pull hangs
            ]
        )
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", run_mock)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "failed"
    assert result.reason == "1 failed: biz (timed out after 60s)"
    assert any("timed out after 60s" in c[0][0] for c in output.task_debug.call_args_list)


# --- failure reporting (regression: causes were only visible under --debug) ---


def test_format_failures_groups_shared_cause():
    """A network drop fails every repo with the same message; report it once."""
    failures = [(f"repo{i}", "could not read from remote repository") for i in range(13)]
    assert _format_failures(failures) == (
        "repo0, repo1, repo2, +10 more (could not read from remote repository)"
    )


def test_format_failures_keeps_distinct_causes_separate():
    out = _format_failures([("biz", "timed out after 60s"), ("ops", "Permission denied")])
    assert out == "biz (timed out after 60s); ops (Permission denied)"


def test_format_failures_truncates_long_reason():
    out = _format_failures([("biz", "x" * 200)])
    assert out.endswith("…)")
    assert len(out) < 120


def test_format_failures_handles_empty_reason():
    assert _format_failures([("biz", "")]) == "biz (unknown error)"


def test_multiple_repos_share_one_reason_in_result(tmp_path, monkeypatch):
    """End-to-end: the aggregate TaskResult carries the cause, not just names."""
    repos = [_make_repo(tmp_path, n) for n in ("biz", "ops")]
    config = _config(repos)
    output = MagicMock()

    def fake_run(cmd, **kwargs):
        if "pull" in cmd:
            return _cp(returncode=1, stderr="fatal: unable to access: network is down\n")
        if "rev-parse" in cmd and "--is-inside-work-tree" in cmd:
            return _cp(returncode=0, stdout="true\n")
        if cmd[-1] == "remote":
            return _cp(returncode=0, stdout="origin\n")
        if "--symbolic-full-name" in cmd:
            return _cp(returncode=0, stdout="origin/main\n")
        if "status" in cmd:
            return _cp(returncode=0, stdout="")
        return _cp(returncode=0, stdout="main\n")

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "failed"
    assert result.reason == "2 failed: biz, ops (fatal: unable to access: network is down)"


# --- F-04: repository-supplied config must not be able to execute a command ---


def test_run_git_neutralises_repo_execution_directives(tmp_path, monkeypatch):
    """Every git call carries the hardening overrides, ahead of `-C`.

    Verified on git 2.55: `core.fsmonitor` fires on `git status --porcelain` -- the
    skip_dirty *safety* check -- and `.git/hooks` fire on `pull`, so the overrides
    have to apply to every invocation, not just the pull.
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.setdefault("cmd", list(cmd))
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    _run_git(str(tmp_path), ["status", "--porcelain"])

    cmd = captured["cmd"]
    dash_c = cmd.index("-C")
    overrides = {cmd[i + 1] for i, a in enumerate(cmd[:dash_c]) if a == "-c"}
    assert "core.fsmonitor=" in overrides
    assert "core.hooksPath=/dev/null" in overrides
    assert "protocol.ext.allow=never" in overrides
    assert "protocol.file.allow=never" in overrides
    # Overrides must precede -C so they apply to the repository being entered.
    assert cmd[dash_c + 1] == str(tmp_path)
    assert cmd[dash_c + 2 :] == ["status", "--porcelain"]


def test_trusted_overrides_drops_repo_helper_but_keeps_global(monkeypatch):
    """credential.helper is reset, then the user's own global/system values re-added.

    A repo-local `credential.helper = !payload` executes on an HTTP 401 (confirmed;
    GIT_ASKPASS and GIT_TERMINAL_PROMPT do not prevent it). A bare reset would fix
    that but break every macOS user, because Homebrew git ships
    `credential.helper = osxkeychain` at *system* scope.
    """
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "config"] and cmd[2] == "--system":
            if cmd[-1] == "credential.helper":
                return _cp(returncode=0, stdout="osxkeychain\n")
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    args = _trusted_overrides()

    pairs = [(args[i + 1]) for i, a in enumerate(args) if a == "-c"]
    # Reset comes first, inherited value after, so git's list ends up as the user's.
    assert pairs.index("credential.helper=") < pairs.index("credential.helper=osxkeychain")


def test_trusted_overrides_survives_missing_git(monkeypatch):
    """A failing `git config` probe must not break the handler."""
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    monkeypatch.setattr(
        "mac_upkeep.git_sync.subprocess.run", MagicMock(side_effect=OSError("no git"))
    )
    args = _trusted_overrides()
    assert "core.fsmonitor=" in args


# --- F-08: a hostile remote must not inject Rich markup into our own output ---


def test_remote_message_control_sequences_are_stripped(tmp_path, monkeypatch):
    """OSC-8 hyperlinks in a git error must not survive into the failure reason."""
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()

    payload = "fatal: \x1b]8;;https://evil.sh/x\x1b\\click\x1b]8;;\x1b\\ denied"

    def fake_run(cmd, **kwargs):
        if "pull" in cmd:
            return _cp(returncode=1, stderr=payload + "\n")
        if "rev-parse" in cmd and "--is-inside-work-tree" in cmd:
            return _cp(returncode=0, stdout="true\n")
        if cmd[-1] == "remote":
            return _cp(returncode=0, stdout="origin\n")
        if "--symbolic-full-name" in cmd:
            return _cp(returncode=0, stdout="origin/main\n")
        if "status" in cmd:
            return _cp(returncode=0, stdout="")
        return _cp(returncode=0, stdout="main\n")

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    result = run_git_sync(config, output, dry_run=False)

    assert result.status == "failed"
    assert "\x1b" not in result.reason
    assert "]8;" not in result.reason
    assert "fatal: click denied" in result.reason


def test_scalar_key_is_never_reset_to_the_empty_string(monkeypatch):
    """core.sshCommand must never be emitted empty -- git execs it, it is not a reset.

    Regression: it was originally handled like credential.helper. For a multi-valued
    key an empty entry resets the accumulated list; for a single-valued key it IS the
    value, so git tried to fork "" and EVERY ssh remote failed with
    `error: cannot run : No such file or directory` -- on every run, for every user
    without a global core.sshCommand, which is the documented default setup.
    """
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    # Nothing configured anywhere: the case that broke.
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: _cp(returncode=1))
    args = _trusted_overrides()

    values = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
    ssh = [v for v in values if v.startswith("core.sshCommand=")]
    assert ssh == ["core.sshCommand=ssh"], ssh
    assert "core.sshCommand=" not in values


def test_scalar_key_prefers_the_users_own_value(monkeypatch):
    """A user with a global core.sshCommand keeps it; only the repo's value is dropped."""
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "config", "--global"] and cmd[-1] == "core.sshCommand":
            return _cp(returncode=0, stdout="ssh -i ~/.ssh/id_work\n")
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    values = [a for a in _trusted_overrides()]
    assert "core.sshCommand=ssh -i ~/.ssh/id_work" in values
    assert "core.sshCommand=ssh" not in values


def test_git_protocol_is_denied(monkeypatch):
    """core.gitProxy executes a command for git:// remotes and no -c override stops it.

    Verified on git 2.55: `-c core.gitProxy=` does NOT disable it. Denying the
    transport is the only thing that does, and git:// is an unauthenticated,
    unencrypted legacy protocol.
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.setdefault("cmd", list(cmd))
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    _run_git("/tmp/whatever", ["pull", "--ff-only"])

    cmd = captured["cmd"]
    overrides = {cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"}
    assert "protocol.git.allow=never" in overrides


def test_strip_ansi_is_linear_on_unterminated_osc(monkeypatch):
    """A hostile remote must not be able to hang the run inside the sanitiser.

    The OSC branch originally used `.*?` under re.DOTALL, which rescans to
    end-of-input for every unterminated `\x1b]`: quadratic, ~12s for 80KB of them,
    on stderr a git server fully controls and after the subprocess timeout has passed.
    """
    import time

    payload = "\x1b]" * 40000
    start = time.perf_counter()
    out = _strip_ansi(payload)
    elapsed = time.perf_counter() - start

    assert out == ""
    assert elapsed < 1.0, f"sanitiser took {elapsed:.2f}s on 80KB of OSC introducers"
