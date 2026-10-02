"""App-side storage in ~/.config/fanatec-pitbox.

- state.json: setup names (aliases), the last values seen per setup, and the loaded profile.
- profiles/*.toml: a profile is a snapshot of all 5 setups of the wheel base plus their names,
  used to back up the base or swap a complete set. Only one profile is "loaded" at a time.
"""

import json
import os
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .params import PROFILE_KEYS

CONFIG_DIR = Path(os.environ.get("FANATEC_PITBOX_CONFIG",
                                  Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "fanatec-pitbox"))
PROFILE_DIR = CONFIG_DIR / "profiles"
_OLD_CONFIG_DIR = CONFIG_DIR.parent / "fanatec-dash"  # the app's name before it became Fanatec Pitbox
STATE_FILE = CONFIG_DIR / "state.json"
SLOTS = range(1, 6)


@dataclass
class Profile:
    name: str
    slots: dict[int, dict[str, int]] = field(default_factory=dict)  # slot -> tuning values
    aliases: dict[int, str] = field(default_factory=dict)
    saved: str = ""
    path: Path | None = None

    def differs(self, slot_values: dict[int, dict[str, int]]) -> list[int]:
        """Setups whose known values differ from this profile."""
        out = []
        for slot, values in self.slots.items():
            seen = slot_values.get(slot)
            if seen and any(k in seen and seen[k] != v for k, v in values.items()):
                out.append(slot)
        return out


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.")
    return slug or "profile"


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON string escaping is valid TOML basic-string syntax


def migrate_old_config() -> None:
    """Carry over setup names and profiles saved under the app's previous name."""
    if "FANATEC_PITBOX_CONFIG" not in os.environ and _OLD_CONFIG_DIR.is_dir() and not CONFIG_DIR.exists():
        _OLD_CONFIG_DIR.rename(CONFIG_DIR)


def load_all() -> list[Profile]:
    profiles = []
    for path in sorted(PROFILE_DIR.glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        slots, aliases = {}, {}
        for n in SLOTS:
            table = data.get(f"setup{n}")
            if not isinstance(table, dict):
                continue
            if table.get("name"):
                aliases[n] = str(table["name"])
            slots[n] = {k: int(v) for k, v in table.items() if k in PROFILE_KEYS}
        if slots:
            profiles.append(Profile(data.get("name", path.stem), slots, aliases, data.get("saved", ""), path))
    return sorted(profiles, key=lambda p: p.name.lower())


def save(profile: Profile) -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = profile.path or _free_path(profile.name)
    profile.saved = profile.saved or time.strftime("%Y-%m-%d %H:%M")
    lines = [f"name = {_toml_str(profile.name)}", f"saved = {_toml_str(profile.saved)}"]
    for n in SLOTS:
        if n not in profile.slots:
            continue
        lines += ["", f"[setup{n}]"]
        if profile.aliases.get(n):
            lines.append(f"name = {_toml_str(profile.aliases[n])}")
        lines += [f"{k} = {profile.slots[n][k]}" for k in PROFILE_KEYS if k in profile.slots[n]]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)
    profile.path = path
    return path


def rename(profile: Profile, new_name: str) -> None:
    old = profile.path
    profile.name = new_name
    profile.path = _free_path(new_name, keep=old)
    save(profile)
    if old and old != profile.path:
        old.unlink(missing_ok=True)


def delete(profile: Profile) -> None:
    if profile.path:
        profile.path.unlink(missing_ok=True)


def _free_path(name: str, keep: Path | None = None) -> Path:
    base = _slug(name)
    path, n = PROFILE_DIR / f"{base}.toml", 2
    while path.exists() and path != keep:
        path, n = PROFILE_DIR / f"{base}-{n}.toml", n + 1
    return path


class State:
    """Small persisted app state; JSON keys are strings, exposed here with int slot numbers."""

    def __init__(self):
        try:
            data = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            data = {}
        self.aliases: dict[int, str] = {int(k): v for k, v in data.get("aliases", {}).items() if v}
        self.seen: dict[int, dict[str, int]] = {int(k): v for k, v in data.get("seen", {}).items()}
        self.loaded: str | None = data.get("loaded")
        self.last_slot: int | None = data.get("last_slot")  # to ask the base for its data after a reboot
        self.last_advanced: bool = data.get("last_advanced", True)

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {"aliases": {str(k): v for k, v in self.aliases.items()},
                "seen": {str(k): v for k, v in self.seen.items()},
                "loaded": self.loaded,
                "last_slot": self.last_slot,
                "last_advanced": self.last_advanced}
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(STATE_FILE)
