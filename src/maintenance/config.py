"""Configuration loading from TOML files and environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
DEFAULT_CONFIG_DIR = Path(_xdg) / "maintenance"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"

# All tasks enabled by default
DEFAULT_TASKS = {
    "brew_update": True,
    "brew_upgrade": True,
    "gcloud": True,
    "pnpm": True,
    "uv": True,
    "fisher": True,
    "mo_clean": True,
    "mo_optimize": True,
    "mo_purge": True,
    "brew_cleanup": True,
    "brew_bundle": True,
}

DEFAULT_FREQUENCIES = {
    "brew_update": "weekly",
    "brew_upgrade": "weekly",
    "gcloud": "monthly",
    "pnpm": "monthly",
    "uv": "monthly",
    "fisher": "weekly",
    "mo_clean": "weekly",
    "mo_optimize": "weekly",
    "mo_purge": "monthly",
    "brew_cleanup": "monthly",
    "brew_bundle": "weekly",
}


@dataclass
class Config:
    """Maintenance configuration loaded from TOML + environment overrides."""

    tasks: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_TASKS))
    brewfile: str | None = None
    notify: bool = True
    notify_sound: str = "Submarine"
    frequencies: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FREQUENCIES))

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> Config:
        """Load config from TOML file, then apply environment variable overrides."""
        config = cls()

        # Load TOML config if it exists
        if path.is_file():
            with open(path, "rb") as f:
                data = tomllib.load(f)
            if "tasks" in data:
                for task, enabled in data["tasks"].items():
                    if task in config.tasks:
                        config.tasks[task] = bool(enabled)
            if "paths" in data and "brewfile" in data["paths"]:
                config.brewfile = data["paths"]["brewfile"]
            if "frequency" in data:
                for task, freq in data["frequency"].items():
                    if task in config.frequencies:
                        config.frequencies[task] = str(freq)
            if "notifications" in data:
                notif = data["notifications"]
                if "enabled" in notif:
                    config.notify = bool(notif["enabled"])
                if "sound" in notif:
                    config.notify_sound = str(notif["sound"])

        # Environment variables override config file
        for task in DEFAULT_TASKS:
            env_key = f"MAINTENANCE_{task.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                config.tasks[task] = env_val.lower() not in ("false", "0", "no")

        # Frequency env overrides
        for task in DEFAULT_TASKS:
            env_key = f"MAINTENANCE_{task.upper()}_FREQUENCY"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                config.frequencies[task] = env_val.lower()

        env_notify = os.environ.get("MAINTENANCE_NOTIFY")
        if env_notify is not None:
            config.notify = env_notify.lower() not in ("false", "0", "no")

        # Brewfile path: env var → config file → auto-discover
        if os.environ.get("MAINTENANCE_BREWFILE"):
            config.brewfile = os.environ["MAINTENANCE_BREWFILE"]
        if not config.brewfile:
            config.brewfile = os.environ.get("HOMEBREW_BUNDLE_FILE")
        if not config.brewfile:
            config.brewfile = _discover_brewfile()

        return config

    def is_enabled(self, task: str) -> bool:
        """Check if a task is enabled in config."""
        return self.tasks.get(task, True)

    def get_frequency(self, task: str) -> str:
        """Get the frequency for a task ('weekly' or 'monthly')."""
        return self.frequencies.get(task, "weekly")


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
