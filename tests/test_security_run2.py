"""Regression tests for the run-2 security audit findings.

One test (or group) per finding, kept together so the negative-control procedure is
easy to run: revert one fix, run this file, watch exactly its test fail.

    PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_security_run2.py

`PYTHONDONTWRITEBYTECODE=1` is not decoration. CPython invalidates a `.pyc` on
`(mtime seconds, size)`, so a same-length revert reuses stale bytecode and the control
reports a pass for code that never ran.

Several tests here run REAL git. Mocking cannot answer these questions, because the
question *is* what git itself does with a repository's own configuration. Each such
test carries its own preflight asserting the payload fires WITHOUT the hardening, so a
green result can never silently mean "the payload was inert" -- which is exactly how
the first two reproduction attempts of F2-01 produced the wrong answer.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mac_upkeep.config import Config
from mac_upkeep.git_sync import _run_git, _trusted_overrides, run_git_sync
from mac_upkeep.output import Output, TaskResult

# --------------------------------------------------------------------------- helpers


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
    repo = tmp_path / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return str(repo)


def _git_isolated(monkeypatch) -> bool:
    """Point git at empty global/system config. False when git is unavailable."""
    if shutil.which("git") is None:  # pragma: no cover - git is present in CI
        return False
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return True


def _plain_git(path: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run git with NO hardening -- the preflight arm of the tests below."""
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _recording_output() -> tuple[Output, io.StringIO]:
    from rich.console import Console

    buf = io.StringIO()
    out = Output(interactive=True)
    out._console = Console(file=buf, force_terminal=True, width=200, highlight=False)
    return out, buf


# ------------------------------------------------------- F2-01: signature verification


def _make_payload(tmp_path) -> tuple[Path, Path]:
    marker = tmp_path / "marker.txt"
    payload = tmp_path / "payload.sh"
    payload.write_text(f'#!/bin/sh\necho FIRED > "{marker}"\nexit 0\n')
    payload.chmod(0o755)
    return payload, marker


def _repo_with_forged_signature(tmp_path, payload: Path) -> str:
    """A repo whose `@{upstream}` commit carries a forged `gpgsig` header.

    Without a signature git never invokes a verifier at all, so a repo built without
    one is INERT and every assertion about it is meaningless.
    """
    repo = tmp_path / "planted"
    repo.mkdir()
    p = str(repo)
    _plain_git(p, ["init", "-q", "-b", "main"])
    _plain_git(p, ["config", "user.email", "t@t"])
    _plain_git(p, ["config", "user.name", "t"])
    (repo / "a.txt").write_text("a\n")
    _plain_git(p, ["add", "a.txt"])
    _plain_git(p, ["commit", "-qm", "A"])
    parent = _plain_git(p, ["rev-parse", "HEAD"]).stdout.strip()
    (repo / "b.txt").write_text("b\n")
    _plain_git(p, ["add", "b.txt"])
    tree = _plain_git(p, ["write-tree"]).stdout.strip()
    _plain_git(p, ["reset", "-q", "--hard"])

    obj = tmp_path / "commit-object"
    obj.write_text(
        f"tree {tree}\n"
        f"parent {parent}\n"
        "author t <t@t> 0 +0000\n"
        "committer t <t@t> 0 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " \n"
        " iQIzBAABCgAdFiEE\n"
        " -----END PGP SIGNATURE-----\n"
        "\nB signed\n"
    )
    upstream = _plain_git(p, ["hash-object", "-w", "-t", "commit", str(obj)]).stdout.strip()
    _plain_git(p, ["update-ref", "refs/remotes/origin/main", upstream])
    _plain_git(p, ["config", "branch.main.remote", "origin"])
    _plain_git(p, ["config", "branch.main.merge", "refs/heads/main"])
    _plain_git(p, ["config", "remote.origin.url", "https://example.invalid/x.git"])
    # Without a fetch refspec git refuses to treat origin/main as a tracking branch,
    # and `@{upstream}` fails before any verification happens -- another way to build
    # an inert test.
    _plain_git(p, ["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"])

    # The planted payload.
    _plain_git(p, ["config", "merge.verifySignatures", "true"])
    for key in ("gpg.program", "gpg.openpgp.program", "gpg.x509.program", "gpg.ssh.program"):
        _plain_git(p, ["config", key, str(payload)])
    return p


def test_signature_verification_program_does_not_execute(tmp_path, monkeypatch):
    """A planted `gpg.<format>.program` must not run during an unattended merge.

    `merge` is exercised rather than `pull` deliberately: with a local-path remote the
    fetch is refused by `protocol.file.allow=never` before verification is ever
    reached, so a `pull`-based test reports "blocked" whether or not this sink is
    closed. That false negative is what made the first reproduction attempt wrong.
    """
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    payload, marker = _make_payload(tmp_path)
    repo = _repo_with_forged_signature(tmp_path, payload)

    # PREFLIGHT: the payload must be live, or this test proves nothing at all.
    _plain_git(repo, ["merge", "--ff-only", "@{upstream}"])
    assert marker.exists(), "payload never fired without hardening -- the test is inert"

    marker.unlink()
    _run_git(repo, ["merge", "--ff-only", "@{upstream}"])
    assert not marker.exists(), "gpg.<format>.program executed through _run_git"


def test_signature_verification_program_does_not_execute_for_ssh_format(tmp_path, monkeypatch):
    """`gpg.format = ssh` selects `gpg.ssh.program`; pinning only the synonym is not enough."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    payload, marker = _make_payload(tmp_path)
    repo = _repo_with_forged_signature(tmp_path, payload)
    _plain_git(repo, ["config", "gpg.format", "ssh"])

    _plain_git(repo, ["merge", "--ff-only", "@{upstream}"])
    assert marker.exists(), "payload never fired without hardening -- the test is inert"

    marker.unlink()
    _run_git(repo, ["merge", "--ff-only", "@{upstream}"])
    assert not marker.exists(), "gpg.ssh.program executed through _run_git"


def test_signature_pinning_does_not_block_a_normal_fast_forward(tmp_path, monkeypatch):
    """The pins must not break the ordinary case they guard."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    payload, _ = _make_payload(tmp_path)
    repo = _repo_with_forged_signature(tmp_path, payload)

    r = _run_git(repo, ["merge", "--ff-only", "@{upstream}"])
    assert r.returncode == 0, r.stderr
    head = _plain_git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    upstream = _plain_git(repo, ["rev-parse", "@{upstream}"]).stdout.strip()
    assert head == upstream


def test_all_four_signature_keys_are_pinned_in_argv(tmp_path, monkeypatch):
    """All four `gpg.<format>.program` keys, not only the legacy synonym."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.setdefault("cmd", list(cmd))
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    _run_git(str(tmp_path), ["pull", "--ff-only"])

    cmd = captured["cmd"]
    overrides = {cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"}
    assert "merge.verifySignatures=false" in overrides
    for fmt in ("gpg.program", "gpg.openpgp.program", "gpg.x509.program", "gpg.ssh.program"):
        assert f"{fmt}=/usr/bin/false" in overrides


# ------------------------------------------------ F2-06: transport redirection (generic)


def test_generic_proxy_and_sslverify_are_neutralised(tmp_path, monkeypatch):
    """A repo-local generic `http.proxy` / `http.sslVerify` must not survive."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    # conftest seeds the cache with the STATIC half only, so the inherited keys under
    # test would be missing from argv. Rebuild it for real -- the probes read the
    # /dev/null global and system config _git_isolated just installed, so the user
    # half is deterministically empty and the safe defaults apply.
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    repo = tmp_path / "r"
    repo.mkdir()
    p = str(repo)
    _plain_git(p, ["init", "-q", "-b", "main"])
    _plain_git(p, ["config", "http.proxy", "http://127.0.0.1:1"])
    _plain_git(p, ["config", "http.sslVerify", "false"])

    # PREFLIGHT: git really does honour the planted values.
    assert _plain_git(p, ["config", "--get", "http.proxy"]).stdout.strip() == "http://127.0.0.1:1"
    assert _plain_git(p, ["config", "--get", "http.sslVerify"]).stdout.strip() == "false"

    assert _run_git(p, ["config", "--get", "http.proxy"]).stdout.strip() == ""
    assert _run_git(p, ["config", "--get", "http.sslVerify"]).stdout.strip() == "true"


def test_proxy_is_inherited_from_the_user_not_blanked(monkeypatch):
    """The user's own global proxy survives; only the repository's value is dropped.

    Pinning these flat would have been safe from the empty-value angle -- git reads an
    empty `http.proxy` as "no proxy" -- but it would silently break git_sync for every
    user behind a corporate proxy. Single-valued keys are SET, never blanked.
    """
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "config", "--global"] and cmd[-1] == "http.proxy":
            return _cp(returncode=0, stdout="http://corp.proxy:3128\n")
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    values = list(_trusted_overrides())
    assert "http.proxy=http://corp.proxy:3128" in values
    assert "http.proxy=" not in values
    assert "http.sslVerify=true" in values


def test_proxy_defaults_to_no_proxy_when_the_user_has_none(monkeypatch):
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: _cp(returncode=1))
    values = list(_trusted_overrides())
    assert "http.proxy=" in values
    assert "http.sslVerify=true" in values


# ------------------------------------------------------------- F2-03: undecodable bytes


def test_run_git_survives_non_utf8_output(tmp_path, monkeypatch):
    """Invalid UTF-8 degrades to U+FFFD instead of raising out of the handler."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    repo = tmp_path / "r"
    repo.mkdir()
    p = str(repo)
    _plain_git(p, ["init", "-q", "-b", "main"])
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"before\x80\xffafter\n")
    sha = subprocess.run(
        ["git", "-C", p, "hash-object", "-w", str(blob)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    ).stdout.strip()

    r = _run_git(p, ["cat-file", "-p", sha])
    assert r.returncode == 0
    assert "�" in r.stdout
    assert "before" in r.stdout
    assert "after" in r.stdout


def test_one_repo_raising_does_not_strand_the_rest(tmp_path, monkeypatch):
    """An exception in one repository is that repository's failure, not the task's."""
    repos = [_make_repo(tmp_path, f"r{i}") for i in range(3)]
    config = _config(repos)
    output = MagicMock()

    def fake_sync(path, *, skip_dirty):
        if os.path.basename(path) == "r1":
            raise UnicodeDecodeError("utf-8", b"\x80", 0, 1, "invalid start byte")
        return "pulled", ""

    monkeypatch.setattr("mac_upkeep.git_sync._sync_repo", fake_sync)
    result = run_git_sync(config, output, dry_run=False)

    assert result.status == "failed"
    assert "r1" in result.reason
    assert "UnicodeDecodeError" in result.reason
    # The other two were still attempted -- that is the whole point.
    assert "r0" not in result.reason
    assert "r2" not in result.reason


# ------------------------------------------- F2-02: the repository NAME is untrusted too

_HOSTILE_NAME = "evil\x1b[2K\x1b[1Arepo"


def _failing_git(cmd, **kwargs):
    if "pull" in cmd:
        return _cp(returncode=1, stderr="fatal: denied\n")
    if "--is-inside-work-tree" in cmd:
        return _cp(returncode=0, stdout="true\n")
    if cmd[-1] == "remote":
        return _cp(returncode=0, stdout="origin\n")
    if "--symbolic-full-name" in cmd:
        return _cp(returncode=0, stdout="origin/main\n")
    if "status" in cmd:
        return _cp(returncode=0, stdout="")
    return _cp(returncode=0, stdout="main\n")


def _hostile_repo_result(tmp_path, monkeypatch) -> TaskResult:
    repo = tmp_path / _HOSTILE_NAME
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", _failing_git)
    result = run_git_sync(_config([str(repo)]), MagicMock(), dry_run=False)
    # The handler deliberately does NOT sanitise the name -- the SINK does. If this
    # ever stops holding, the tests below would pass without testing anything.
    assert "\x1b" in result.reason, "handler now pre-sanitises; these tests are inert"
    return result


def test_hostile_repo_name_is_sanitised_in_the_log(tmp_path, monkeypatch, caplog):
    """launchd's log is replayed later by `mac-upkeep logs`, through `tail`."""
    result = _hostile_repo_result(tmp_path, monkeypatch)
    out = Output(interactive=False)
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        out.task_done(result)
    assert "\x1b" not in caplog.text
    assert "evilrepo" in caplog.text


def test_hostile_repo_name_is_sanitised_in_the_live_summary(tmp_path, monkeypatch):
    result = _hostile_repo_result(tmp_path, monkeypatch)
    out, buf = _recording_output()
    out.header(dry_run=False)
    out.summary([result])
    rendered = buf.getvalue()
    # Rich itself emits SGR sequences, so assert on the PAYLOAD's sequences.
    assert "\x1b[2K" not in rendered
    assert "\x1b[1A" not in rendered
    assert "evilrepo" in rendered


def test_task_debug_sanitises_control_bytes(tmp_path, monkeypatch):
    """Every caller feeds filesystem-derived text here; none of them should have to know."""
    out, buf = _recording_output()
    out.task_debug(f"/Users/x/{_HOSTILE_NAME}: failed")
    rendered = buf.getvalue()
    assert "\x1b[2K" not in rendered
    assert "\x1b[1A" not in rendered
    assert "evilrepo" in rendered


def test_task_done_sanitises_the_task_name(tmp_path, monkeypatch, caplog):
    """`name` is sanitised alongside `reason` so the two cannot drift apart."""
    out = Output(interactive=False)
    with caplog.at_level(logging.INFO, logger="mac_upkeep"):
        out.task_done(TaskResult("task\x1b]0;PWNED\x07x", "skipped", reason="disabled"))
    assert "\x1b" not in caplog.text
    assert "taskx" in caplog.text
