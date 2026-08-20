"""Configuration loading from TOML files and environment variables."""

from __future__ import annotations

import importlib.resources
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
DEFAULT_CONFIG_DIR = Path(_xdg) / "mac-upkeep"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"


@dataclass
class TaskDef:
    """A task definition loaded from TOML."""

    name: str
    description: str
    command: str
    detect: str = ""
    frequency: str = "weekly"
    enabled: bool = True
    sudo: bool = False
    shell: str = ""
    require_file: str = ""
    timeout: int = 300
    handler: str = ""
    # Set at load time when `require_file` was non-empty BEFORE variable resolution.
    # Lets run_all_tasks tell "requires a file that resolved to nothing" apart from
    # "never required one" -- the former must fail closed, not run without a file.
    require_file_declared: bool = False


# Field types accepted from user TOML. A quoted TOML boolean (`enabled = "false"`)
# is the most common TOML mistake, and it used to leave a truthy string behind --
# silently ENABLING a task the user meant to disable. Validation makes it an error.
_FIELD_TYPES: dict[str, type] = {
    "description": str,
    "command": str,
    "detect": str,
    "frequency": str,
    "enabled": bool,
    "sudo": bool,
    "shell": str,
    "require_file": str,
    "timeout": int,
    "handler": str,
}


def normalize_task_key(name: str) -> str:
    """Canonical task key: lowercased, spaces collapsed to underscores.

    tasks.py derives the same key from a task's display name, so `Config.is_enabled`
    only resolves if both sides normalise identically. Doing it once at load time
    makes the two keyspaces the same by construction -- previously `[tasks.DockerPrune]`
    was stored under its raw name, the lookup missed, and `enabled = false` was ignored.
    """
    return name.lower().replace(" ", "_")


def _check_field(task: str, field_name: str, value: object) -> object:
    """Validate one user-supplied field against its declared type."""
    expected = _FIELD_TYPES.get(field_name)
    if expected is None:
        return value
    # bool subclasses int, so `timeout = true` would otherwise pass an int check.
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        hint = ""
        if expected is bool:
            hint = ' TOML booleans are unquoted: `enabled = false`, not `enabled = "false"`.'
        raise ValueError(
            f"Task '{task}': '{field_name}' must be {expected.__name__}, "
            f"got {type(value).__name__} ({value!r}).{hint}"
        )
    return value


def get_brew_prefix() -> str:
    """Detect Homebrew prefix (portable: Apple Silicon /opt/homebrew, Intel /usr/local)."""
    brew = shutil.which("brew")
    if brew:
        try:
            result = subprocess.run([brew, "--prefix"], capture_output=True, text=True, timeout=5)
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
    return "/opt/homebrew" if os.uname().machine == "arm64" else "/usr/local"


def resolve_variables(value: str, variables: dict[str, str]) -> str:
    """Replace ${VAR} placeholders with values. Raises ValueError on unknown vars."""
    for var_name, var_value in variables.items():
        value = value.replace(f"${{{var_name}}}", var_value)
    unresolved = re.findall(r"\$\{(\w+)\}", value)
    if unresolved:
        msg = ", ".join(f"${{{v}}}" for v in unresolved)
        raise ValueError(f"Unknown variable(s): {msg}")
    return value


def _build_variables(brewfile: str) -> dict[str, str]:
    """Build the variable dict for template resolution."""
    return {
        "BREW_PREFIX": get_brew_prefix(),
        "BREWFILE": brewfile or "",
        "HOME": str(Path.home()),
    }


def _load_defaults() -> dict:
    """Load bundled defaults.toml via importlib.resources."""
    text = (
        importlib.resources.files("mac_upkeep")
        .joinpath("defaults.toml")
        .read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def load_default_task_names() -> tuple[dict[str, str], list[str]]:
    """Load task names and order from bundled defaults.toml.

    Returns (task_name_to_description, run_order). Used at import time
    by tasks.py for shell completion.
    """
    data = _load_defaults()
    tasks = {name: defn.get("description", "") for name, defn in data.get("tasks", {}).items()}
    order = data.get("run", {}).get("order", list(tasks.keys()))
    return tasks, order


def load_task_defs(
    user_data: dict | None,
    variables: dict[str, str],
) -> tuple[dict[str, TaskDef], list[str]]:
    """Load task definitions from defaults.toml, merge with user config.

    Args:
        user_data: Pre-parsed user TOML dict (or None if no user config).
        variables: Variable dict for ${VAR} resolution.

    Returns:
        (task_defs, run_order)
    """
    defaults = _load_defaults()

    # Parse default tasks. Keys are normalised here so every downstream lookup
    # (run_order, is_enabled, env overrides, --force) shares one keyspace.
    task_defs: dict[str, TaskDef] = {}
    for raw_name, data in defaults.get("tasks", {}).items():
        name = normalize_task_key(raw_name)
        task_defs[name] = _parse_task_def(name, data)

    # Default run order
    run_order = [
        normalize_task_key(n) for n in defaults.get("run", {}).get("order", list(task_defs.keys()))
    ]

    # Merge user overrides
    if user_data:
        seen_raw: dict[str, str] = {}
        for raw_name, user_fields in user_data.get("tasks", {}).items():
            name = normalize_task_key(raw_name)
            # Two distinct TOML names collapsing to one key would make the winner
            # depend on table order -- reject instead of silently dropping one.
            if name in seen_raw and seen_raw[name] != raw_name:
                raise ValueError(
                    f"Tasks '{seen_raw[name]}' and '{raw_name}' both normalise to "
                    f"'{name}'; task names are case- and space-insensitive."
                )
            seen_raw[name] = raw_name

            if name in task_defs:
                # Field-level override: only specified fields change
                td = task_defs[name]
                for field_name, value in user_fields.items():
                    if hasattr(td, field_name):
                        setattr(td, field_name, _check_field(raw_name, field_name, value))
            else:
                # New custom task
                task_defs[name] = _parse_task_def(name, user_fields, raw_name=raw_name)

        # User run order replaces default entirely
        if "run" in user_data and "order" in user_data["run"]:
            run_order = [normalize_task_key(n) for n in user_data["run"]["order"]]
        else:
            # Auto-append custom tasks to default order
            for raw_name in user_data.get("tasks", {}):
                name = normalize_task_key(raw_name)
                if name not in run_order:
                    run_order.append(name)

    # Validate task definitions
    from mac_upkeep.tasks import KNOWN_HANDLERS  # local import to avoid cycle

    for name, td in task_defs.items():
        if td.command and td.handler:
            raise ValueError(f"Task '{name}': cannot set both 'command' and 'handler'")
        if not td.command and not td.handler:
            raise ValueError(f"Task '{name}' has no command or handler")
        if td.handler and td.handler not in KNOWN_HANDLERS:
            known = ", ".join(sorted(KNOWN_HANDLERS)) or "(none registered)"
            raise ValueError(f"Task '{name}': unknown handler '{td.handler}' (known: {known})")
        if td.frequency not in ("daily", "weekly", "monthly"):
            raise ValueError(
                f"Task '{name}': frequency must be 'daily', 'weekly', or 'monthly', "
                f"got '{td.frequency}'"
            )
    for entry in run_order:
        if entry not in task_defs:
            raise ValueError(f"run.order references unknown task '{entry}'")

    # Env var overrides (MAC_UPKEEP_<TASK>=false, MAC_UPKEEP_<TASK>_FREQUENCY=monthly)
    for task_name, td in task_defs.items():
        env_key = f"MAC_UPKEEP_{task_name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            # Allowlist, not denylist: `MAC_UPKEEP_EDITOR_CACHE=off` (or `disabled`,
            # or an empty string) used to fall through the denylist and ENABLE a
            # destructive task. Anything not explicitly truthy now disables.
            td.enabled = env_val.strip().lower() in ("true", "1", "yes", "on")

        freq_key = f"MAC_UPKEEP_{task_name.upper()}_FREQUENCY"
        freq_val = os.environ.get(freq_key)
        if freq_val is not None:
            td.frequency = freq_val.lower()

    # Resolve variables in command, detect, require_file
    for td in task_defs.values():
        td.command = resolve_variables(td.command, variables)
        if td.detect:
            td.detect = resolve_variables(td.detect, variables)
        if td.require_file:
            td.require_file_declared = True
            td.require_file = resolve_variables(td.require_file, variables)

    # Auto-infer detect from command for tasks that don't set it
    for td in task_defs.values():
        if not td.detect and td.command:
            td.detect = shlex.split(td.command)[0]

    return task_defs, run_order


def _parse_task_def(name: str, data: dict, *, raw_name: str | None = None) -> TaskDef:
    """Parse a TOML task table into a TaskDef.

    Every field is type-checked: a brand-new custom task gets the same validation
    as a field-level override, so `enabled = "false"` cannot create a task that
    reports itself disabled while running anyway. `raw_name` is the name as the
    user wrote it, used only so errors quote what is actually in their file.
    """
    label = raw_name or name

    def field(key: str, default: object) -> object:
        if key not in data:
            return default
        return _check_field(label, key, data[key])

    return TaskDef(
        name=name,
        description=field("description", ""),
        command=field("command", ""),
        detect=field("detect", ""),
        frequency=field("frequency", "weekly"),
        enabled=field("enabled", True),
        sudo=field("sudo", False),
        shell=field("shell", ""),
        require_file=field("require_file", ""),
        timeout=field("timeout", 300),
        handler=field("handler", ""),
    )


@dataclass
class Config:
    """mac-upkeep configuration loaded from TOML + environment overrides."""

    task_defs: dict[str, TaskDef] = field(default_factory=dict)
    run_order: list[str] = field(default_factory=list)
    brewfile: str | None = None
    notify: bool = True
    notify_sound: str = "Submarine"
    git_sync_repos: list[str] = field(default_factory=list)
    git_sync_skip_dirty: bool = True
    editor_cache_apps: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load config from TOML file, then apply environment variable overrides.

        `path` is resolved at call time (not bound as a default argument) so that
        patching `mac_upkeep.config.DEFAULT_CONFIG_PATH` actually redirects the
        lookup -- which is what test isolation relies on.
        """
        if path is None:
            path = DEFAULT_CONFIG_PATH
        config = cls()

        # Read user TOML once (used for both settings and task overrides)
        user_data: dict | None = None
        if path.is_file():
            with open(path, "rb") as f:
                user_data = tomllib.load(f)

        # Extract notifications from user config
        if user_data and "notifications" in user_data:
            notif = user_data["notifications"]
            if "enabled" in notif:
                config.notify = bool(notif["enabled"])
            if "sound" in notif:
                config.notify_sound = str(notif["sound"])

        # Extract git_sync settings from user config
        if user_data and "git_sync" in user_data:
            gs = user_data["git_sync"]
            config.git_sync_repos = list(gs.get("repos", []))
            config.git_sync_skip_dirty = bool(gs.get("skip_dirty", True))

        # Extract editor_cache app overrides from user config (else handler uses
        # its built-in DEFAULT_APPS).
        if user_data and "editor_cache" in user_data:
            config.editor_cache_apps = list(user_data["editor_cache"].get("apps", []))

        # Extract brewfile from user config
        if user_data and "paths" in user_data and "brewfile" in user_data["paths"]:
            config.brewfile = user_data["paths"]["brewfile"]

        # Notification env override
        env_notify = os.environ.get("MAC_UPKEEP_NOTIFY")
        if env_notify is not None:
            config.notify = env_notify.lower() not in ("false", "0", "no")

        # Brewfile path: env var → config file → HOMEBREW_BUNDLE_FILE → auto-discover
        if os.environ.get("MAC_UPKEEP_BREWFILE"):
            config.brewfile = os.environ["MAC_UPKEEP_BREWFILE"]
        if not config.brewfile:
            config.brewfile = os.environ.get("HOMEBREW_BUNDLE_FILE")
        if not config.brewfile:
            config.brewfile = _discover_brewfile()

        # Build variables and load task definitions
        variables = _build_variables(config.brewfile or "")
        config.task_defs, config.run_order = load_task_defs(user_data, variables)

        return config

    def is_enabled(self, task: str) -> bool:
        """Check if a task is enabled in config. Fails CLOSED on an unknown key.

        Every caller derives `task` from a real TaskDef via `normalize_task_key`,
        so a miss means the keyspaces diverged -- which must never be resolved by
        running a task the user disabled. Requires the load-time key normalisation
        to be in place; without it a capitalised custom task would never run.
        """
        td = self.task_defs.get(task)
        return bool(td.enabled) if td else False

    def get_frequency(self, task: str) -> str:
        """Get the frequency for a task ('weekly' or 'monthly')."""
        td = self.task_defs.get(task)
        return td.frequency if td else "weekly"


def _discover_brewfile() -> str | None:
    """Auto-discover Brewfile from absolute, user-owned locations only.

    A CWD-relative `Path("Brewfile")` candidate is deliberately absent. `brew bundle`
    evaluates a Brewfile as Ruby (`bundle/dsl.rb` `instance_eval`) and this project
    runs `cleanup --force`, which uninstalls everything the file does not list and
    resets Homebrew's trust store. Letting the process working directory pick the
    file turned any checkout a developer happened to be sitting in into both a
    code-execution and a mass-uninstall vector.
    """
    candidates = [
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Brewfile",
        Path.home() / ".Brewfile",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None
