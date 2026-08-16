"""Global test isolation.

mac-upkeep resolves its config and state paths from the real user environment
(`XDG_CONFIG_HOME`/`~/.config`, `XDG_STATE_HOME`/`~/.local/state`). Typer's
`CliRunner.invoke` does NOT sandbox those, so without this fixture every
`runner.invoke(app, ["run"])` reads the developer's live
`~/.config/mac-upkeep/config.toml` and their real `last-run.json`.

That makes the suite pass in CI (clean runner, no user config) and fail on the
machine of anyone who actually uses mac-upkeep -- extra tasks from their config
appear in the run order and break task-count and notification assertions.

The paths are module-level constants evaluated at import time, so setting the
env vars here would be too late; the constants are patched directly instead.

Prior art: pip's autouse `isolate` fixture and pyscaffold's
`fake_xdg_config_home`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Captured at collection time, before any fixture rewrites HOME, so isolation
# assertions can compare against the developer's genuine home directory.
REAL_HOME = Path(os.path.expanduser("~"))


@pytest.fixture
def real_home() -> Path:
    """The developer's actual home directory, unaffected by isolation."""
    return REAL_HOME


@pytest.fixture(autouse=True)
def isolate_user_environment(tmp_path_factory, monkeypatch):
    """Point config and state at a per-test temp dir, never the real user's."""
    root = tmp_path_factory.mktemp("upkeep-home")
    config_dir = root / "config" / "mac-upkeep"
    state_dir = root / "state" / "mac-upkeep"
    config_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    # Env vars for any subprocess or late path resolution.
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))

    # Import-time constants: patch the bound objects themselves.
    config_path = config_dir / "config.toml"
    monkeypatch.setattr("mac_upkeep.config.DEFAULT_CONFIG_DIR", config_dir)
    monkeypatch.setattr("mac_upkeep.config.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("mac_upkeep.cli.DEFAULT_CONFIG_DIR", config_dir)
    monkeypatch.setattr("mac_upkeep.cli.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("mac_upkeep.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_dir / "last-run.json")
    monkeypatch.setattr("mac_upkeep.tasks._RETRY_FILE", state_dir / "retry-state.json")

    yield root
