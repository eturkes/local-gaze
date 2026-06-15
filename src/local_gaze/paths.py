from __future__ import annotations

import os
import stat
from pathlib import Path

_APP = "local-gaze"


def _xdg(var: str, default: str) -> Path:
    raw = os.environ.get(var)
    base = Path(raw).expanduser() if raw else Path.home() / default
    return base


def ensure_dir(p: Path, mode: int = 0o700) -> Path:
    """Create ``p`` (with parents), chmod to ``mode``, and verify the perm bits."""
    p.mkdir(parents=True, exist_ok=True)
    p.chmod(mode)
    actual = stat.S_IMODE(p.stat().st_mode)
    if actual != mode:
        raise PermissionError(f"{p}: mode {actual:#o} != required {mode:#o}")
    return p


def config_dir() -> Path:
    return ensure_dir(_xdg("XDG_CONFIG_HOME", ".config") / _APP)


def state_dir() -> Path:
    return ensure_dir(_xdg("XDG_STATE_HOME", ".local/state") / _APP)


def model_cache_dir() -> Path:
    return ensure_dir(_xdg("XDG_CACHE_HOME", ".cache") / _APP / "ov")


def config_file() -> Path:
    return config_dir() / "config.toml"


def token_file() -> Path:
    # Created 0600 by the token writer; this only resolves the path.
    return state_dir() / "token"


def calibration_file() -> Path:
    # Created 0600 by calibration.save(); this only resolves the path.
    return state_dir() / "calibration.json"
