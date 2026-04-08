"""Configuration loading from TOML files and environment variables."""

from __future__ import annotations

import importlib.resources
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
DEFAULT_CONFIG_DIR = Path(_xdg) / "maintenance"
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
        importlib.resources.files("maintenance")
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

    # Parse default tasks
    task_defs: dict[str, TaskDef] = {}
    for name, data in defaults.get("tasks", {}).items():
        task_defs[name] = _parse_task_def(name, data)

    # Default run order
    run_order = defaults.get("run", {}).get("order", list(task_defs.keys()))

    # Merge user overrides
    if user_data:
        for name, user_fields in user_data.get("tasks", {}).items():
            if name in task_defs:
                # Field-level override: only specified fields change
                td = task_defs[name]
                for field_name, value in user_fields.items():
                    if hasattr(td, field_name):
                        setattr(td, field_name, value)
            else:
                # New custom task
                task_defs[name] = _parse_task_def(name, user_fields)

        # User run order replaces default entirely
        if "run" in user_data and "order" in user_data["run"]:
            run_order = user_data["run"]["order"]

    # Env var overrides (MAINTENANCE_<TASK>=false, MAINTENANCE_<TASK>_FREQUENCY=monthly)
    for task_name, td in task_defs.items():
        env_key = f"MAINTENANCE_{task_name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            td.enabled = env_val.lower() not in ("false", "0", "no")

        freq_key = f"MAINTENANCE_{task_name.upper()}_FREQUENCY"
        freq_val = os.environ.get(freq_key)
        if freq_val is not None:
            td.frequency = freq_val.lower()

    # Resolve variables in command, detect, require_file
    for td in task_defs.values():
        td.command = resolve_variables(td.command, variables)
        if td.detect:
            td.detect = resolve_variables(td.detect, variables)
        if td.require_file:
            td.require_file = resolve_variables(td.require_file, variables)

    return task_defs, run_order


def _parse_task_def(name: str, data: dict) -> TaskDef:
    """Parse a TOML task table into a TaskDef."""
    return TaskDef(
        name=name,
        description=data.get("description", ""),
        command=data.get("command", ""),
        detect=data.get("detect", ""),
        frequency=data.get("frequency", "weekly"),
        enabled=data.get("enabled", True),
        sudo=data.get("sudo", False),
        shell=data.get("shell", ""),
        require_file=data.get("require_file", ""),
        timeout=data.get("timeout", 300),
    )


@dataclass
class Config:
    """Maintenance configuration loaded from TOML + environment overrides."""

    task_defs: dict[str, TaskDef] = field(default_factory=dict)
    run_order: list[str] = field(default_factory=list)
    brewfile: str | None = None
    notify: bool = True
    notify_sound: str = "Submarine"

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> Config:
        """Load config from TOML file, then apply environment variable overrides."""
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

        # Extract brewfile from user config
        if user_data and "paths" in user_data and "brewfile" in user_data["paths"]:
            config.brewfile = user_data["paths"]["brewfile"]

        # Notification env override
        env_notify = os.environ.get("MAINTENANCE_NOTIFY")
        if env_notify is not None:
            config.notify = env_notify.lower() not in ("false", "0", "no")

        # Brewfile path: env var → config file → HOMEBREW_BUNDLE_FILE → auto-discover
        if os.environ.get("MAINTENANCE_BREWFILE"):
            config.brewfile = os.environ["MAINTENANCE_BREWFILE"]
        if not config.brewfile:
            config.brewfile = os.environ.get("HOMEBREW_BUNDLE_FILE")
        if not config.brewfile:
            config.brewfile = _discover_brewfile()

        # Build variables and load task definitions
        variables = _build_variables(config.brewfile or "")
        config.task_defs, config.run_order = load_task_defs(user_data, variables)

        return config

    def is_enabled(self, task: str) -> bool:
        """Check if a task is enabled in config."""
        td = self.task_defs.get(task)
        return td.enabled if td else True

    def get_frequency(self, task: str) -> str:
        """Get the frequency for a task ('weekly' or 'monthly')."""
        td = self.task_defs.get(task)
        return td.frequency if td else "weekly"


def _discover_brewfile() -> str | None:
    """Auto-discover Brewfile from common locations."""
    candidates = [
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Brewfile",
        Path.home() / ".Brewfile",
        Path("Brewfile"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None
