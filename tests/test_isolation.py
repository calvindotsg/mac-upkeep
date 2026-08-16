"""Guards the test-isolation contract in conftest.py.

Typer's CliRunner does not sandbox the user environment, so before conftest.py
existed the suite read the developer's live `~/.config/mac-upkeep/config.toml`.
CI stayed green (clean runner, no user config) while the suite failed for anyone
who actually used mac-upkeep -- their extra tasks entered the run order and
broke task-count and notification assertions.

If these tests fail, tests have started reading real user state again.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files

import mac_upkeep.cli as cli_mod
import mac_upkeep.config as config_mod
import mac_upkeep.tasks as tasks_mod
from mac_upkeep.config import Config


def _bundled_defaults() -> dict:
    return tomllib.loads(files("mac_upkeep").joinpath("defaults.toml").read_text())


def test_config_path_is_not_the_real_user_config(real_home):
    assert real_home not in config_mod.DEFAULT_CONFIG_PATH.parents
    assert real_home not in cli_mod.DEFAULT_CONFIG_PATH.parents


def test_state_paths_are_not_the_real_user_state(real_home):
    assert real_home not in tasks_mod._STATE_FILE.parents
    assert real_home not in tasks_mod._RETRY_FILE.parents


def test_config_load_sees_only_bundled_defaults():
    """The invariant that actually broke: no user tasks leak into run_order."""
    assert Config.load().run_order == _bundled_defaults()["run"]["order"]


def test_config_load_resolves_path_at_call_time(tmp_path, monkeypatch):
    """`Config.load()` must honour a patched DEFAULT_CONFIG_PATH.

    A default argument would bind at def time, silently ignoring the patch and
    making every isolation attempt a no-op.
    """
    custom = tmp_path / "config.toml"
    custom.write_text('[tasks.gcloud]\nfrequency = "daily"\n')
    monkeypatch.setattr("mac_upkeep.config.DEFAULT_CONFIG_PATH", custom)
    assert Config.load().get_frequency("gcloud") == "daily"


def test_writes_do_not_touch_real_state_file(real_home):
    tasks_mod._update_last_run("gcloud")
    tasks_mod._record_failure("gcloud")
    assert tasks_mod._STATE_FILE.is_file()
    assert tasks_mod._RETRY_FILE.is_file()
    assert real_home not in tasks_mod._STATE_FILE.parents
    assert real_home not in tasks_mod._RETRY_FILE.parents
