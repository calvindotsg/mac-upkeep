"""git_sync [fetch_via]: a host with an entry is fetched by its command, never by ssh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mac_upkeep import git_sync
from mac_upkeep.config import Config
from mac_upkeep.git_sync import _probe_ssh_agent

REAL_PROBE = _probe_ssh_agent

ENV_DUMP = 'echo "$PWD" > "$OUT"; echo "$@" >> "$OUT"; env | grep ^GIT_CONFIG >> "$OUT"\n'


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess(["x"], rc, out, err)


def _script(tmp_path: Path, body: str, name: str = "fake-fetch") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


@pytest.fixture
def git_calls(monkeypatch):
    """Replace `_run_git`: ls-remote answers a url; a plain fetch is recorded and fails."""
    calls: list[list[str]] = []
    state = {"url": "git@github.com-work:o/r.git"}

    def fake(path, args, *, timeout=60):
        calls.append(args)
        if args[:2] == ["ls-remote", "--get-url"]:
            return _cp(out=state["url"] + "\n")
        if args == ["fetch"]:
            return _cp(rc=1, err="plain git fetch ran")
        return _cp()

    monkeypatch.setattr(git_sync, "_run_git", fake)
    return calls, state


def test_fetch_via_runs_command_in_repo_with_static_overrides(tmp_path, git_calls, monkeypatch):
    calls, _ = git_calls
    out = tmp_path / "out"
    monkeypatch.setenv("OUT", str(out))
    cmd = _script(tmp_path, ENV_DUMP)
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git_sync._fetch(str(repo), {"github.com-work": [str(cmd), "git", "fetch"]}) is None
    lines = out.read_text().splitlines()
    assert Path(lines[0]).resolve() == repo.resolve()  # cwd is the repo, no -C
    assert lines[1] == "git fetch"
    assert "GIT_CONFIG_KEY_0=core.fsmonitor" in lines
    assert ["fetch"] not in calls  # never plain git fetch


def test_host_without_entry_uses_git_fetch(git_calls):
    _, state = git_calls
    state["url"] = "git@github.com:o/r.git"
    result = git_sync._fetch("/r", {"github.com-work": ["/bin/true"]})
    assert result == ("failed", "plain git fetch ran")


def test_https_remote_ignores_entries(git_calls):
    _, state = git_calls
    state["url"] = "https://github.com/o/r.git"
    assert git_sync._fetch("/r", {"github.com": ["/bin/true"]})[1] == "plain git fetch ran"


def test_ssh_scheme_url_matches_host(tmp_path, git_calls):
    _, state = git_calls
    state["url"] = "ssh://git@github.com-work/o/r.git"
    cmd = _script(tmp_path, "exit 0\n")
    assert git_sync._fetch(str(tmp_path), {"github.com-work": [str(cmd)]}) is None


def test_missing_command_fails_without_ssh_fallback(tmp_path, git_calls):
    calls, _ = git_calls
    result = git_sync._fetch("/r", {"github.com-work": [str(tmp_path / "nope"), "git", "fetch"]})
    assert result is not None and result[0] == "failed"
    assert "not found or not executable" in result[1]
    assert ["fetch"] not in calls


def test_non_executable_command_fails(tmp_path, git_calls):
    f = tmp_path / "plain"
    f.write_text("x")
    result = git_sync._fetch("/r", {"github.com-work": [str(f)]})
    assert result is not None and "not found or not executable" in result[1]


def test_command_failure_reports_first_stderr_line(tmp_path, git_calls):
    cmd = _script(tmp_path, "echo 'bad token' >&2; echo more >&2; exit 4\n")
    result = git_sync._fetch(str(tmp_path), {"github.com-work": [str(cmd)]})
    assert result == ("failed", "bad token")


def test_probe_skips_hosts_with_entry(monkeypatch):
    probed: list[tuple[str, str]] = []
    monkeypatch.setattr(git_sync, "_probe_host", lambda u, h: probed.append((u, h)) or "")
    monkeypatch.setattr(
        git_sync, "_run_git", lambda p, a, timeout=60: _cp(out="git@github.com-work:o/r\n")
    )
    assert REAL_PROBE(["/a"], {"github.com-work"}) == {}
    assert probed == []
    REAL_PROBE(["/a"])
    assert probed == [("git", "github.com-work")]


def test_config_loads_and_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = tmp_path / "c.toml"
    p.write_text('[git_sync.fetch_via]\n"github.com-x" = ["~/bin/gh-bot", "git", "fetch"]\n')
    cfg = Config.load(p)
    assert cfg.git_sync_fetch_via == {
        "github.com-x": [str(tmp_path / "bin/gh-bot"), "git", "fetch"]
    }


@pytest.mark.parametrize(
    "body",
    [
        '[git_sync]\nfetch_via = "gh-bot"',
        '[git_sync.fetch_via]\nh = "gh-bot"',
        "[git_sync.fetch_via]\nh = []",
        "[git_sync.fetch_via]\nh = [1]",
    ],
)
def test_config_rejects_bad_fetch_via(tmp_path, body):
    p = tmp_path / "c.toml"
    p.write_text(body + "\n")
    with pytest.raises(ValueError):
        Config.load(p)


def _planted_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A real repo whose own config would run a command: core.fsmonitor and a hook."""
    repo = tmp_path / "planted"
    repo.mkdir()
    fsmon, hook = tmp_path / "fsmonitor-ran", tmp_path / "hook-ran"
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    g("init", "-q")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    g("config", "core.fsmonitor", f"touch {fsmon}; echo")
    hook_file = repo / ".git" / "hooks" / "reference-transaction"
    hook_file.write_text(f"#!/bin/sh\ntouch {hook}\n")
    hook_file.chmod(0o755)
    return repo, fsmon, hook


# What a fetch command's git does in a repo: refresh the index (fsmonitor) and update a ref
# (reference-transaction hook). Stands in for `gh-bot git fetch`, whose network step the
# test cannot reach (protocol.file.allow=never refuses a local upstream).
GIT_WORK = (
    "export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1\n"
    "git status --porcelain >/dev/null\n"
    "git update-ref refs/remotes/origin/x HEAD\n"
)


def test_planted_repo_config_does_not_run_under_fetch_via(tmp_path, git_calls):
    repo, fsmon, hook = _planted_repo(tmp_path)
    cmd = _script(tmp_path, GIT_WORK)
    # Negative control: with no overrides the planted config DOES fire.
    subprocess.run([str(cmd)], cwd=repo, check=True, capture_output=True)
    assert fsmon.exists() and hook.exists(), "payload must fire without the overrides"
    fsmon.unlink()
    hook.unlink()
    assert git_sync._fetch(str(repo), {"github.com-work": [str(cmd)]}) is None
    assert not fsmon.exists()
    assert not hook.exists()
