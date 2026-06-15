"""Tests for the editor_cache handler."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

from mac_upkeep import editor_cache
from mac_upkeep.config import Config
from mac_upkeep.editor_cache import (
    DEFAULT_APPS,
    _dir_size,
    _is_safe_target,
    _pgrep_running,
    run_editor_cache,
)


def _config(apps: list[dict] | None = None) -> Config:
    config = Config.load()
    config.editor_cache_apps = apps if apps is not None else []
    return config


def _app(name: str, targets: list[str], *, process: str = "FakeApp", min_size_mb: int = 0) -> dict:
    return {"name": name, "process": process, "min_size_mb": min_size_mb, "targets": targets}


def _make_cache(root, *parts, size: int = 1024) -> str:
    """Create a cache dir at least two levels under root with `size` bytes inside."""
    d = root.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    (d / "blob").write_bytes(b"x" * size)
    return str(d)


def _use_tmp_root(monkeypatch, tmp_path, *, running: bool = False) -> None:
    """Point the safety root at tmp_path and control the pgrep guard."""
    monkeypatch.setattr(editor_cache, "_SAFE_ROOT", tmp_path)
    monkeypatch.setattr(editor_cache, "_pgrep_running", lambda _proc: running)


# --- _dir_size ---


def test_dir_size_sums_files(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b").write_bytes(b"y" * 250)
    assert _dir_size(tmp_path) == 350


def test_dir_size_ignores_symlinks(tmp_path):
    real = tmp_path / "real"
    real.write_bytes(b"x" * 500)
    (tmp_path / "link").symlink_to(real)
    # Only the real 500 bytes count; the symlink adds nothing.
    assert _dir_size(tmp_path) == 500


# --- _pgrep_running ---


def test_pgrep_running_true(monkeypatch):
    monkeypatch.setattr(
        editor_cache.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0),
    )
    assert _pgrep_running("Notion") is True


def test_pgrep_running_false(monkeypatch):
    monkeypatch.setattr(
        editor_cache.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=1),
    )
    assert _pgrep_running("Notion") is False


def test_pgrep_running_assumes_running_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("pgrep missing")

    monkeypatch.setattr(editor_cache.subprocess, "run", _boom)
    # Fail closed: if we can't tell, never delete.
    assert _pgrep_running("Notion") is True


def test_pgrep_running_error_code_assumes_running(monkeypatch):
    # pgrep exit >=2 = pgrep itself errored (bad args/internal), NOT "no match".
    monkeypatch.setattr(
        editor_cache.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=2),
    )
    assert _pgrep_running("--weird") is True


def test_pgrep_running_timeout_assumes_running(monkeypatch):
    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["pgrep"], timeout=10)

    monkeypatch.setattr(editor_cache.subprocess, "run", _timeout)
    assert _pgrep_running("Notion") is True


def test_pgrep_running_empty_process_assumes_running():
    # A missing `process` must not silently bypass the running-app guard.
    assert _pgrep_running("") is True


# --- _is_safe_target ---


def test_is_safe_target_accepts_deep_path(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_cache, "_SAFE_ROOT", tmp_path)
    target = tmp_path / "Notion" / "CacheStorage"
    target.mkdir(parents=True)
    assert _is_safe_target(target) is True


def test_is_safe_target_rejects_root_and_app_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_cache, "_SAFE_ROOT", tmp_path)
    assert _is_safe_target(tmp_path) is False  # the root itself
    app = tmp_path / "Notion"
    app.mkdir()
    assert _is_safe_target(app) is False  # one segment below root


def test_is_safe_target_rejects_outside_root(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_cache, "_SAFE_ROOT", tmp_path / "inside")
    (tmp_path / "inside").mkdir()
    outside = tmp_path / "elsewhere" / "cache"
    outside.mkdir(parents=True)
    assert _is_safe_target(outside) is False


def test_is_safe_target_rejects_symlink(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_cache, "_SAFE_ROOT", tmp_path)
    real = tmp_path / "Notion" / "CacheStorage"
    real.mkdir(parents=True)
    link = tmp_path / "Notion" / "link"
    link.symlink_to(real)
    assert _is_safe_target(link) is False


# --- run_editor_cache ---


def test_skips_when_app_running(monkeypatch, tmp_path):
    _use_tmp_root(monkeypatch, tmp_path, running=True)
    target = _make_cache(tmp_path, "Notion", "CacheStorage", size=4096)
    config = _config([_app("Notion", [target])])
    output = MagicMock()

    result = run_editor_cache(config, output, dry_run=False)

    assert os.path.isdir(target)  # not deleted
    assert result.status == "ok"
    assert "running" in result.reason


def test_cleans_when_app_closed(monkeypatch, tmp_path):
    _use_tmp_root(monkeypatch, tmp_path, running=False)
    target = _make_cache(tmp_path, "Notion", "CacheStorage", size=1024)
    config = _config([_app("Notion", [target])])
    output = MagicMock()

    result = run_editor_cache(config, output, dry_run=False)

    assert not os.path.exists(target)  # deleted
    assert result.status == "ok"
    assert "MB freed" in result.reason


def test_reason_combines_freed_and_running(monkeypatch, tmp_path):
    # Two apps: one running (skipped), one closed with a large cache (cleaned).
    # Exercises the ", ".join(parts) path with both segments populated.
    monkeypatch.setattr(editor_cache, "_SAFE_ROOT", tmp_path)
    monkeypatch.setattr(editor_cache, "_pgrep_running", lambda proc: proc == "Zed")
    closed = _make_cache(tmp_path, "Notion", "CacheStorage", size=1024)
    config = _config(
        [
            _app("Notion", [closed], process="Notion"),
            _app("Zed", [str(tmp_path / "Zed" / "cache")], process="Zed"),
        ]
    )
    output = MagicMock()

    result = run_editor_cache(config, output, dry_run=False)

    assert result.status == "ok"
    assert "MB freed" in result.reason
    assert "running" in result.reason


def test_size_gate_skips_small_cache(monkeypatch, tmp_path):
    _use_tmp_root(monkeypatch, tmp_path, running=False)
    target = _make_cache(tmp_path, "Zed", "node", "cache", size=1024)
    config = _config([_app("Zed", [target], min_size_mb=2048)])
    output = MagicMock()
    # Report a sub-threshold size without writing 2 GB.
    monkeypatch.setattr(editor_cache, "_dir_size", lambda _p: 100 * 1024 * 1024)

    result = run_editor_cache(config, output, dry_run=False)

    assert os.path.isdir(target)  # below threshold → kept
    assert result.reason == "nothing to clean"


def test_size_gate_cleans_large_cache(monkeypatch, tmp_path):
    _use_tmp_root(monkeypatch, tmp_path, running=False)
    target = _make_cache(tmp_path, "Zed", "node", "cache", size=1024)
    config = _config([_app("Zed", [target], min_size_mb=2048)])
    output = MagicMock()
    # Report an over-threshold size without writing 3 GB.
    monkeypatch.setattr(editor_cache, "_dir_size", lambda _p: 3000 * 1024 * 1024)

    result = run_editor_cache(config, output, dry_run=False)

    assert not os.path.exists(target)  # above threshold → cleaned
    assert "3000MB freed" in result.reason


def test_dry_run_deletes_nothing(monkeypatch, tmp_path):
    _use_tmp_root(monkeypatch, tmp_path, running=False)
    target = _make_cache(tmp_path, "Notion", "CacheStorage", size=1024)
    config = _config([_app("Notion", [target])])
    output = MagicMock()

    result = run_editor_cache(config, output, dry_run=True)

    assert os.path.isdir(target)  # preserved
    assert result.status == "ok"
    assert "would free" in result.reason
    assert any("would clean" in c[0][0] for c in output.task_debug.call_args_list)


def test_dry_run_with_running_app(monkeypatch, tmp_path):
    # dry-run + every app running: exercises the dry-run early return when
    # nothing would be freed (n_running > 0).
    _use_tmp_root(monkeypatch, tmp_path, running=True)
    target = _make_cache(tmp_path, "Notion", "CacheStorage", size=1024)
    config = _config([_app("Notion", [target])])
    output = MagicMock()

    result = run_editor_cache(config, output, dry_run=True)

    assert os.path.isdir(target)  # nothing deleted
    assert result.status == "ok"
    assert result.reason == "dry-run: would free 0MB"


def test_missing_target_is_skipped(monkeypatch, tmp_path):
    _use_tmp_root(monkeypatch, tmp_path, running=False)
    missing = str(tmp_path / "Notion" / "CacheStorage")  # never created
    config = _config([_app("Notion", [missing])])
    output = MagicMock()

    result = run_editor_cache(config, output, dry_run=False)

    assert result.status == "ok"
    assert result.reason == "nothing to clean"


def test_empty_config_falls_back_to_default_apps(monkeypatch, tmp_path):
    # No app override → DEFAULT_APPS used. Force every app "running" so nothing
    # is deleted; the running count proves all defaults were iterated.
    monkeypatch.setattr(editor_cache, "_pgrep_running", lambda _proc: True)
    config = _config([])
    output = MagicMock()

    result = run_editor_cache(config, output, dry_run=False)

    assert result.status == "ok"
    assert result.reason == f"{len(DEFAULT_APPS)} running"


def test_rmtree_failure_reports_failed(monkeypatch, tmp_path):
    _use_tmp_root(monkeypatch, tmp_path, running=False)
    target = _make_cache(tmp_path, "Notion", "CacheStorage", size=1024)
    config = _config([_app("Notion", [target])])
    output = MagicMock()

    def _boom(_path):
        raise OSError("permission denied")

    monkeypatch.setattr(editor_cache.shutil, "rmtree", _boom)

    result = run_editor_cache(config, output, dry_run=False)

    assert result.status == "failed"
    assert "Notion" in result.reason
