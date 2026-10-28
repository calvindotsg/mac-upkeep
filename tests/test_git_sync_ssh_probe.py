"""git_sync: a locked ssh agent costs seconds, not one 60 s timeout per repo.

1Password's ssh agent answers a sign request with a Touch ID prompt. On a locked Mac the
prompt times out after 60 s, so on 22-28 Oct 2026 the morning run spent about 19 minutes
on 19 repos and ended with them all marked failed.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from mac_upkeep import git_sync
from mac_upkeep.config import Config
from mac_upkeep.git_sync import _probe_ssh_agent, _ssh_target, run_git_sync

REAL_PROBE = _probe_ssh_agent  # bound before the autouse fixture patches the module attr

HEALTHY = subprocess.CompletedProcess(
    ["ssh"],
    1,
    "",
    "Hi calvin! You've successfully authenticated, but GitHub does not provide shell access.\n",
)


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess(["x"], rc, out, err)


class Fake:
    """`subprocess.run` stand-in: answers git per repo path and ssh per host."""

    def __init__(self, urls: dict[str, str], ssh: dict[str, object]):
        self.urls = urls  # repo path -> remote url
        self.ssh = ssh  # host -> CompletedProcess or exception to raise
        self.ssh_hosts: list[str] = []
        self.fetched: list[str] = []

    def __call__(self, cmd, **kw):
        if cmd[0] == "ssh":
            host = cmd[-1].split("@", 1)[1]
            self.ssh_hosts.append(host)
            result = self.ssh[host]
            if isinstance(result, BaseException):
                raise result
            return result
        if "--show-scope" in cmd:
            return _cp()
        path = cmd[cmd.index("-C") + 1]
        args = cmd[cmd.index("-C") + 2 :]
        if args[0] == "ls-remote":
            return _cp(out=self.urls[path] + "\n")
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return _cp(out="true\n")
        if args == ["remote"]:
            return _cp(out="origin\n")
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp(out="main\n")
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return _cp(out="origin/main\n")
        if args == ["status", "--porcelain"]:
            return _cp()
        if args == ["fetch"]:
            self.fetched.append(path)
            return _cp()
        if args[0] == "merge":
            return _cp(out="Already up to date.\n")
        return _cp()


def _repos(tmp_path, names):
    out = []
    for n in names:
        d = tmp_path / n
        (d / ".git").mkdir(parents=True)
        out.append(str(d))
    return out


def _run(monkeypatch, repos, fake):
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake)
    monkeypatch.setattr("mac_upkeep.git_sync._probe_ssh_agent", REAL_PROBE)
    config = Config.load()
    config.git_sync_repos = list(repos)
    config.git_sync_skip_dirty = True
    return run_git_sync(config, MagicMock(), dry_run=False)


# --- URL parsing ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:o/r.git", ("git", "github.com")),
        ("git@github.com-heymax:o/r.git", ("git", "github.com-heymax")),
        ("ssh://git@ssh.github.com:443/o/r.git", ("git", "ssh.github.com")),
        ("github.com:o/r.git", ("git", "github.com")),
        ("https://github.com/o/r.git", None),
        ("/Users/x/repo", None),
        ("./repo", None),
        ("file:///tmp/r", None),
    ],
)
def test_ssh_target(url, expected):
    assert _ssh_target(url) == expected


# --- the fault ---


def test_hung_agent_skips_repos_without_fetching(tmp_path, monkeypatch):
    repos = _repos(tmp_path, ["a", "b", "c"])
    fake = Fake(
        {r: "git@github.com-heymax:o/r.git" for r in repos},
        {"github.com-heymax": subprocess.TimeoutExpired(["ssh"], 20)},
    )
    result = _run(monkeypatch, repos, fake)
    assert fake.fetched == []  # no 60 s timeout per repo
    assert result.status == "ok"  # skipped, not failed: no retry backoff, no report
    assert "3 skipped" in result.reason
    assert "Mac locked?" in result.reason


def test_signing_failure_skips_repos(tmp_path, monkeypatch):
    repos = _repos(tmp_path, ["a", "b"])
    err = 'sign_and_send_pubkey: signing failed for ED25519 "/Users/x/.ssh/k": agent refused\n'
    fake = Fake(
        {r: "git@github.com:o/r.git" for r in repos},
        {"github.com": _cp(255, "", err)},
    )
    result = _run(monkeypatch, repos, fake)
    assert fake.fetched == []
    assert result.status == "ok"
    assert "cannot sign for github.com" in result.reason


def test_healthy_agent_syncs_normally(tmp_path, monkeypatch):
    repos = _repos(tmp_path, ["a", "b"])
    fake = Fake({r: "git@github.com:o/r.git" for r in repos}, {"github.com": HEALTHY})
    result = _run(monkeypatch, repos, fake)
    assert sorted(fake.fetched) == sorted(repos)
    assert result.status == "ok"
    assert "2 pulled" in result.reason


def test_one_probe_per_host(tmp_path, monkeypatch):
    repos = _repos(tmp_path, ["a", "b", "c", "d"])
    fake = Fake({r: "git@github.com:o/r.git" for r in repos}, {"github.com": HEALTHY})
    _run(monkeypatch, repos, fake)
    assert fake.ssh_hosts == ["github.com"]


def test_only_the_bad_host_is_skipped(tmp_path, monkeypatch):
    a, b = _repos(tmp_path, ["a", "b"])
    fake = Fake(
        {a: "git@github.com-heymax:o/r.git", b: "git@github.com-personal:o/r.git"},
        {
            "github.com-heymax": _cp(255, "", "sign_and_send_pubkey: signing failed\n"),
            "github.com-personal": HEALTHY,
        },
    )
    result = _run(monkeypatch, [a, b], fake)
    assert fake.fetched == [b]
    assert "1 pulled" in result.reason and "1 skipped" in result.reason


def test_network_error_is_not_an_agent_fault(tmp_path, monkeypatch):
    (a,) = _repos(tmp_path, ["a"])
    fake = Fake(
        {a: "git@github.com:o/r.git"},
        {"github.com": _cp(255, "", "ssh: Could not resolve hostname github.com\n")},
    )
    _run(monkeypatch, [a], fake)
    assert fake.fetched == [a]  # left to the ordinary per-repo failure path


def test_https_remote_is_never_probed(tmp_path, monkeypatch):
    (a,) = _repos(tmp_path, ["a"])
    fake = Fake({a: "https://github.com/o/r.git"}, {})
    _run(monkeypatch, [a], fake)
    assert fake.ssh_hosts == []
    assert fake.fetched == [a]


# --- the whole-task budget ---


def test_budget_spent_skips_remaining_repos(tmp_path, monkeypatch):
    repos = _repos(tmp_path, ["a", "b"])
    fake = Fake({r: "https://github.com/o/r.git" for r in repos}, {})
    monkeypatch.setattr(git_sync, "_TASK_BUDGET", -1)
    result = _run(monkeypatch, repos, fake)
    assert fake.fetched == []
    assert result.status == "ok"
    assert "task budget" in result.reason
