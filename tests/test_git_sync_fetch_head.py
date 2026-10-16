"""git_sync must merge the upstream ref, never FETCH_HEAD (14 Oct 2026 race).

`git pull` is `git fetch` followed by a merge of FETCH_HEAD, and FETCH_HEAD is one file
per repository that every fetch rewrites. After a restart, RunAtLoad's git_sync and a
manual `git pull` in the same checkout overlapped, and the log recorded
"Cannot fast-forward to multiple branches." These tests run REAL git and put a manual
fetch in the window between git_sync's fetch and its merge.

The fetch itself is substituted: `_run_git` refuses local-path remotes
(`protocol.file.allow=never`), so the fixture's upstream cannot be fetched through it.
Everything after the fetch -- the merge, and how its result is read -- is the real code.
Each test carries a preflight showing the planted FETCH_HEAD is live, so a green result
cannot mean the race was never set up.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from mac_upkeep import git_sync
from mac_upkeep.git_sync import _sync_repo


def _git(path: str, *args: str) -> subprocess.CompletedProcess:
    """Plain git with no hardening, for building fixtures and the preflight arm."""
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _sha(path: str, rev: str) -> str:
    return _git(path, "rev-parse", rev).stdout.strip()


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    """A bare upstream where `main` and `other` both descend from the clone's HEAD.

    `other` descending from the clone's `main` is what makes the old behaviour dangerous:
    a fast-forward to `other`'s commit succeeds, so nothing reports an error.
    """
    if shutil.which("git") is None:  # pragma: no cover - git is present in CI
        pytest.skip("git not available")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@t")

    up = str(tmp_path / "up.git")
    seed = str(tmp_path / "seed")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", up], check=True)
    subprocess.run(["git", "clone", "-q", up, seed], check=True, capture_output=True)
    _git(seed, "commit", "-q", "--allow-empty", "-m", "c0")
    _git(seed, "push", "-q", "origin", "main")

    def clone(name: str) -> str:
        path = str(tmp_path / name)
        subprocess.run(["git", "clone", "-q", up, path], check=True, capture_output=True)
        return path

    def advance() -> None:
        _git(seed, "checkout", "-q", "-b", "other")
        _git(seed, "commit", "-q", "--allow-empty", "-m", "o1")
        _git(seed, "push", "-q", "origin", "other")
        _git(seed, "checkout", "-q", "main")
        _git(seed, "commit", "-q", "--allow-empty", "-m", "c1")
        _git(seed, "push", "-q", "origin", "main")

    return clone, advance


def _racing_fetch(monkeypatch, manual: list[str]) -> None:
    """git_sync's fetch, then the user's command lands before git_sync merges."""

    def fetch(path: str):
        assert _git(path, "fetch", "-q").returncode == 0
        assert _git(path, "fetch", "-q", *manual).returncode == 0
        return None

    monkeypatch.setattr(git_sync, "_fetch", fetch)


@pytest.mark.parametrize(
    "manual",
    [
        pytest.param(["origin", "other"], id="manual-fetch-of-another-branch"),
        pytest.param(["origin", "main", "other"], id="manual-multi-branch-fetch"),
    ],
)
def test_merge_ignores_a_fetch_head_rewritten_mid_sync(upstream, monkeypatch, manual):
    clone, advance = upstream
    preflight, work = clone("preflight"), clone("work")
    advance()

    # PREFLIGHT: the old merge step (what `pull` merges) must go wrong on this setup.
    _git(preflight, "fetch", "-q")
    _git(preflight, "fetch", "-q", *manual)
    old = _git(preflight, "merge", "--ff-only", "FETCH_HEAD")
    went_wrong = old.returncode != 0 or _sha(preflight, "HEAD") != _sha(preflight, "origin/main")
    assert went_wrong, "merging FETCH_HEAD landed correctly -- the race is not set up"

    _racing_fetch(monkeypatch, manual)
    assert _sync_repo(work, skip_dirty=True) == ("pulled", "")
    assert _sha(work, "HEAD") == _sha(work, "origin/main")
    assert _sha(work, "HEAD") != _sha(work, "origin/other")


def test_submodules_follow_the_merge_and_a_planted_update_command_does_not_run(
    tmp_path, monkeypatch
):
    """`pull` updated submodules under submodule.recurse; `merge` does not, so we do.

    Without the update the moved pointer reads as ` M sm`, and skip_dirty then skips
    the repo on every later run. The repo-local `submodule.sm.update = !command` is
    the sink a plain `submodule update` would run; `--checkout` must keep it inert.
    """
    if shutil.which("git") is None:  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@t")
    allow = ["-c", "protocol.file.allow=always"]  # fixture setup only, never via _run_git

    def setup_git(path, *args):
        r = _git(path, *allow, *args)
        assert r.returncode == 0, r.stderr
        return r

    sub_up, sup_up = str(tmp_path / "sub.git"), str(tmp_path / "sup.git")
    for bare in (sub_up, sup_up):
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
    seed_sub, seed_sup = str(tmp_path / "seed-sub"), str(tmp_path / "seed-sup")
    for up, seed in ((sub_up, seed_sub), (sup_up, seed_sup)):
        subprocess.run(["git", "clone", "-q", up, seed], check=True, capture_output=True)
        setup_git(seed, "commit", "-q", "--allow-empty", "-m", "init")
        setup_git(seed, "push", "-q", "origin", "main")
    setup_git(seed_sup, "submodule", "add", "-q", sub_up, "sm")
    setup_git(seed_sup, "commit", "-q", "-m", "add sm")
    setup_git(seed_sup, "push", "-q", "origin", "main")

    def clone(name: str) -> str:
        path = str(tmp_path / name)
        r = subprocess.run(
            ["git", *allow, "clone", "-q", "--recurse-submodules", sup_up, path],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        _git(path, "config", "submodule.recurse", "true")
        _git(path, "config", "submodule.sm.update", f"!touch {marker}")
        return path

    marker = tmp_path / "PWNED"
    preflight, work = clone("preflight"), clone("work")

    # Upstream moves the submodule pointer.
    setup_git(seed_sub, "commit", "-q", "--allow-empty", "-m", "sub-1")
    setup_git(seed_sub, "push", "-q", "origin", "main")
    setup_git(f"{seed_sup}/sm", "pull", "-q", "origin", "main")
    setup_git(seed_sup, "commit", "-q", "-am", "bump sm")
    setup_git(seed_sup, "push", "-q", "origin", "main")
    new_sm = _sha(seed_sub, "HEAD")

    # PREFLIGHT: a plain `submodule update` runs the planted command.
    setup_git(preflight, "fetch", "-q", "--recurse-submodules=yes")
    setup_git(preflight, "merge", "-q", "--ff-only", "@{upstream}")
    _git(preflight, *allow, "submodule", "update", "--recursive")
    assert marker.exists(), "the planted update command never ran -- the test is inert"
    marker.unlink()

    def fetch(path: str):
        setup_git(path, "fetch", "-q", "--recurse-submodules=yes")
        return None

    monkeypatch.setattr(git_sync, "_fetch", fetch)
    assert _sync_repo(work, skip_dirty=True) == ("pulled", "")
    assert _sha(f"{work}/sm", "HEAD") == new_sm
    assert _git(work, "status", "--porcelain").stdout == ""
    assert not marker.exists(), "submodule.<name>.update command executed"


def test_divergence_reports_the_fatal_line_not_a_hint(upstream, monkeypatch):
    """A divergent checkout prints ten `hint:` lines first; the reason must be the cause."""
    clone, advance = upstream
    work = clone("work")
    advance()
    _git(work, "commit", "-q", "--allow-empty", "-m", "local")
    _racing_fetch(monkeypatch, [])  # a second plain fetch: no rewrite, just divergence

    status, reason = _sync_repo(work, skip_dirty=True)
    assert status == "failed"
    assert reason.startswith("fatal:"), reason
