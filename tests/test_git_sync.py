"""Tests for the git_sync handler."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from mac_upkeep.config import Config
from mac_upkeep.git_sync import _resolve_paths, run_git_sync


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


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
    calls = iter(
        [
            _cp(returncode=0, stdout="true\n"),  # is-inside-work-tree
            _cp(returncode=0, stdout=""),  # remote (empty)
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: next(calls))
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert result.reason == "1 skipped"
    assert any("no remote configured" in c[0][0] for c in output.task_debug.call_args_list)


def test_skip_no_upstream(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    calls = iter(
        [
            _cp(returncode=0, stdout="true\n"),  # is-inside-work-tree
            _cp(returncode=0, stdout="origin\n"),  # remote
            _cp(returncode=0, stdout="feature-branch\n"),  # current branch
            _cp(returncode=128, stderr="no upstream\n"),  # @{upstream}
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: next(calls))
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert any(
        "no upstream (branch=feature-branch)" in c[0][0] for c in output.task_debug.call_args_list
    )


def test_skip_dirty_worktree(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p], skip_dirty=True)
    output = MagicMock()
    calls = iter(
        [
            _cp(returncode=0, stdout="true\n"),
            _cp(returncode=0, stdout="origin\n"),
            _cp(returncode=0, stdout="main\n"),
            _cp(returncode=0, stdout="origin/main\n"),
            _cp(returncode=0, stdout=" M file.txt\n"),
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: next(calls))
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
        path = args[2]
        op = args[3] if len(args) > 3 else ""
        if "r0" in path or "r1" in path:
            # not a repo
            if op == "rev-parse" and args[4] == "--is-inside-work-tree":
                return _cp(returncode=128)
        # Successful repos
        if op == "rev-parse" and args[4] == "--is-inside-work-tree":
            return _cp(returncode=0, stdout="true\n")
        if op == "remote":
            return _cp(returncode=0, stdout="origin\n")
        if op == "rev-parse" and args[4] == "--abbrev-ref" and args[5] == "HEAD":
            return _cp(returncode=0, stdout="main\n")
        if op == "rev-parse" and "@{upstream}" in args:
            return _cp(returncode=0, stdout="origin/main\n")
        if op == "status":
            return _cp(returncode=0, stdout="")
        if op == "pull":
            return _cp(returncode=0, stdout="Already up to date.\n")
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "ok"
    assert result.reason == "3 pulled, 2 skipped"


def test_failure_surfaces_basename_and_stderr(tmp_path, monkeypatch):
    p = _make_repo(tmp_path, "biz")
    config = _config([p])
    output = MagicMock()
    calls = iter(
        [
            _cp(returncode=0, stdout="true\n"),
            _cp(returncode=0, stdout="origin\n"),
            _cp(returncode=0, stdout="main\n"),
            _cp(returncode=0, stdout="origin/main\n"),
            _cp(returncode=0, stdout=""),  # clean
            _cp(returncode=128, stderr="ssh: Permission denied (publickey)\n"),
        ]
    )
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: next(calls))
    result = run_git_sync(config, output, dry_run=False)
    assert result.status == "failed"
    assert "1 failed: biz" == result.reason
    assert any("Permission denied" in c[0][0] for c in output.task_debug.call_args_list)
