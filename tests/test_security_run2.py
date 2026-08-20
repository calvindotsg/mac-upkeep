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

from mac_upkeep import git_sync
from mac_upkeep.config import Config
from mac_upkeep.git_sync import _run_git, _sync_repo, _trusted_overrides, run_git_sync
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


# -------------------------------- F2-06, remaining half: the per-URL / per-remote families
#
# These key families are MORE SPECIFIC than the generic keys pinned by `-c`, so they win.
# The URL or remote name is part of the key name, so no fixed override set can enumerate
# them; the only closure is to read the repository's own config and refuse to enter it.
# `git config --list` parses files and executes nothing, so the pre-flight is not itself
# a new sink.

_PLANTED_URL = "https://127.0.0.1:9/x.git"


def _bare_repo(tmp_path, name: str = "r") -> str:
    repo = tmp_path / name
    repo.mkdir()
    p = str(repo)
    _plain_git(p, ["init", "-q", "-b", "main"])
    return p


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (f"http.{_PLANTED_URL}.proxy", "http://127.0.0.1:1"),
        ("http.https://127.0.0.1:9/.proxy", "http://127.0.0.1:1"),
        (f"http.{_PLANTED_URL}.sslVerify", "false"),
        ("remote.origin.proxy", "http://127.0.0.1:1"),
    ],
)
def test_per_url_and_per_remote_transport_keys_are_refused(tmp_path, monkeypatch, key, value):
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path)

    # PREFLIGHT: without the key the repo passes this gate and reaches a later one.
    status, reason = _sync_repo(p, skip_dirty=True)
    assert "unsafe repo config" not in reason, reason

    _plain_git(p, ["config", key, value])
    status, reason = _sync_repo(p, skip_dirty=True)
    assert status == "skipped"
    assert "unsafe repo config" in reason
    assert key.split(".")[-1].lower() in reason.lower()


def test_generic_pin_really_is_insufficient_for_the_per_url_form(tmp_path, monkeypatch):
    """The reason the refusal exists: `-c http.proxy=` does NOT beat `http.<url>.proxy`.

    If this ever starts failing, git's precedence changed and the refusal could be
    reconsidered -- until then, shipping only the generic pin is false closure.
    """
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path)
    _plain_git(p, ["config", f"http.{_PLANTED_URL}.proxy", "http://127.0.0.1:1"])

    r = _run_git(p, ["config", "--get-urlmatch", "http.proxy", _PLANTED_URL])
    assert r.stdout.strip() == "http://127.0.0.1:1", "generic pin now wins; revisit the refusal"


def test_worktree_scoped_key_is_refused(tmp_path, monkeypatch):
    """`git config --local --list` does NOT show this scope; `--show-scope` does."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path)
    _plain_git(p, ["config", "extensions.worktreeConfig", "true"])
    _plain_git(p, ["config", "--worktree", f"http.{_PLANTED_URL}.proxy", "http://127.0.0.1:1"])

    # The scope really is invisible to --local, which is why _REPO_SCOPES exists.
    assert "proxy" not in _plain_git(p, ["config", "--local", "--list"]).stdout

    assert "unsafe repo config" in _sync_repo(p, skip_dirty=True)[1]


def test_included_file_key_is_refused(tmp_path, monkeypatch):
    """`include.path` values are reported under the including file's scope."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path)
    extra = tmp_path / "extra.cfg"
    extra.write_text('[http "https://inc.example/"]\n\tproxy = http://127.0.0.1:1\n')
    _plain_git(p, ["config", "include.path", str(extra)])

    assert "unsafe repo config" in _sync_repo(p, skip_dirty=True)[1]


def test_the_users_own_global_config_does_not_refuse_the_repo(tmp_path, monkeypatch):
    """The config owner is not the attacker. Only repo-controlled scopes are checked."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    global_cfg = tmp_path / "gitconfig"
    global_cfg.write_text('[http "https://corp.example/"]\n\tproxy = http://corp:3128\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    p = _bare_repo(tmp_path)

    assert "unsafe repo config" not in _sync_repo(p, skip_dirty=True)[1]


def test_git_lfs_repositories_are_not_refused(tmp_path, monkeypatch):
    """Scoped narrowly on purpose: `filter.*` would refuse every git-lfs repository."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path)
    _plain_git(p, ["config", "filter.lfs.clean", "git-lfs clean -- %f"])
    _plain_git(p, ["config", "filter.lfs.smudge", "git-lfs smudge -- %f"])
    _plain_git(p, ["config", "remote.origin.url", "https://example.invalid/x.git"])

    assert "unsafe repo config" not in _sync_repo(p, skip_dirty=True)[1]


def test_unreadable_config_fails_closed(tmp_path, monkeypatch):
    """ "Cannot tell" must not resolve to "enter it anyway"."""
    monkeypatch.setattr(
        "mac_upkeep.git_sync._run_git",
        lambda path, args, **kw: (
            _cp(returncode=0, stdout="true\n") if "rev-parse" in args else _cp(returncode=1)
        ),
    )
    status, reason = _sync_repo("/anywhere", skip_dirty=True)
    assert status == "skipped"
    assert "unsafe repo config" in reason


@pytest.mark.parametrize(
    ("key", "value", "refused"),
    [
        # The whole per-URL http namespace, not just the keys the audit happened to
        # name. sslCAInfo would have been the next miss: an attacker CA defeats
        # verification exactly as sslVerify=false does.
        (f"http.{_PLANTED_URL}.sslCAInfo", "/tmp/evil-ca.pem", True),
        (f"http.{_PLANTED_URL}.extraHeader", "Authorization: Bearer x", True),
        ("remote.origin.proxyAuthMethod", "basic", True),
        # Two-component keys have no subsection: they are handled as inherited
        # scalars and must NOT trigger a refusal, or every repo with a tweak is lost.
        ("http.postBuffer", "524288000", False),
        ("http.sslVerify", "false", False),
        ("http.proxy", "http://127.0.0.1:1", False),
        # Ordinary remote configuration must survive.
        ("remote.origin.url", "https://example.invalid/x.git", False),
        ("remote.origin.tagOpt", "--no-tags", False),
    ],
)
def test_refusal_pattern_boundaries(tmp_path, monkeypatch, key, value, refused):
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path)
    _plain_git(p, ["config", key, value])
    reason = _sync_repo(p, skip_dirty=True)[1]
    assert ("unsafe repo config" in reason) is refused, reason


# --- the structural half: a repository cannot set an environment variable ---


def test_no_proxy_is_set_when_the_user_has_no_proxy(monkeypatch):
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: _cp(returncode=1))
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    env = git_sync._build_env()
    assert env["NO_PROXY"] == "*"
    assert env["no_proxy"] == "*"


def test_no_proxy_is_not_set_over_a_users_own_git_proxy(monkeypatch):
    """Never blanket a legitimate corporate proxy -- that is the silent-breakage class."""
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "config", "--global"] and cmd[-1] == "http.proxy":
            return _cp(returncode=0, stdout="http://corp.proxy:3128\n")
        return _cp(returncode=1)

    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", fake_run)
    assert "NO_PROXY" not in git_sync._build_env()


@pytest.mark.parametrize("var", ["http_proxy", "https_proxy", "all_proxy", "HTTPS_PROXY"])
def test_no_proxy_is_not_set_over_a_users_env_proxy(monkeypatch, var):
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    monkeypatch.setattr("mac_upkeep.git_sync.subprocess.run", lambda *a, **k: _cp(returncode=1))
    monkeypatch.setenv(var, "http://corp.proxy:3128")
    assert "NO_PROXY" not in git_sync._build_env()


def test_no_proxy_defeats_a_per_url_proxy_without_the_refusal(tmp_path, monkeypatch):
    """Independent of _unsafe_repo_config: proven at the _run_git layer.

    This is the part that does not depend on the key pattern being complete, which is
    why it is worth having on top of the refusal.
    """
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path)
    url = "https://127.0.0.1:9/x.git"
    _plain_git(p, ["config", f"http.{url}.proxy", "http://127.0.0.1:1"])

    # PREFLIGHT: the proxy really is honoured without the block.
    assert "port 1" in _plain_git(p, ["ls-remote", url]).stderr

    assert "port 9" in _run_git(p, ["ls-remote", url]).stderr, "proxy was still used"


def test_refusal_is_a_skip_not_a_failure(tmp_path, monkeypatch):
    """A permanently-refused repo must stay quiet, not notify on every scheduled run."""
    if not _git_isolated(monkeypatch):  # pragma: no cover
        pytest.skip("git not available")
    monkeypatch.setattr("mac_upkeep.git_sync._trusted_cache", None)
    p = _bare_repo(tmp_path, "planted")
    _plain_git(p, ["config", f"http.{_PLANTED_URL}.proxy", "http://127.0.0.1:1"])
    output = MagicMock()

    result = run_git_sync(_config([p]), output, dry_run=False)
    assert result.status == "ok"  # skipped repos do not fail the task
    assert result.reason == "1 skipped"
    assert any("unsafe repo config" in c[0][0] for c in output.task_debug.call_args_list)
