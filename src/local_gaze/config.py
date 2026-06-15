from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from . import paths

_log = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when a config value is out of range or otherwise invalid."""


# Single source of truth: section -> field -> default. Mirrors build-spec §3 and the
# dataclasses below (field names are kept identical so merge/construct stays DRY).
DEFAULTS: dict[str, dict[str, Any]] = {
    "general": {
        "backend": "synthetic",
        "enabled_default": False,
        "dry_run": False,
        "fps": 30,
    },
    "ipc": {
        "require_token": True,
        "bus_name": "com.eturkes.LocalGaze",
        "object_path": "/com/eturkes/LocalGaze",
        "interface": "com.eturkes.LocalGaze",
    },
    "camera": {
        "device": "/dev/video0",
        "width": 640,
        "height": 480,
    },
    "openvino": {
        "device_order": ["NPU", "GPU", "CPU"],
        "performance_hint": "LATENCY",
        "cache_dir": "",
        "models_dir": "",
    },
    "gaze": {
        "smoothing": "one_euro",
        "ema_alpha": 0.4,
        "min_confidence": 0.5,
        "dwell_ms": 400,
        "stability_px": 0.04,
    },
    "flick": {
        "v_on": 0.9,
        "v_off": 0.3,
        "refractory_ms": 600,
        "min_present_frames": 3,
    },
    "limits": {
        "max_actions_per_sec": 4,
    },
    "logging": {
        "level": "INFO",
        "log_gaze": False,
        "dump_frames": False,
    },
}

_BACKENDS = {"synthetic", "openvino", "mock"}
_SMOOTHERS = {"one_euro", "ema"}
_DEVICES = {"NPU", "GPU", "CPU"}
_HINTS = {"LATENCY", "THROUGHPUT", "CUMULATIVE_THROUGHPUT", "UNDEFINED"}
_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


@dataclass(frozen=True, slots=True)
class GeneralCfg:
    backend: str
    enabled_default: bool
    dry_run: bool
    fps: int


@dataclass(frozen=True, slots=True)
class IpcCfg:
    require_token: bool
    bus_name: str
    object_path: str
    interface: str


@dataclass(frozen=True, slots=True)
class CameraCfg:
    device: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class OpenvinoCfg:
    device_order: tuple[str, ...]
    performance_hint: str
    cache_dir: str
    models_dir: str


@dataclass(frozen=True, slots=True)
class GazeCfg:
    smoothing: str
    ema_alpha: float
    min_confidence: float
    dwell_ms: int
    stability_px: float


@dataclass(frozen=True, slots=True)
class FlickCfg:
    v_on: float
    v_off: float
    refractory_ms: int
    min_present_frames: int


@dataclass(frozen=True, slots=True)
class LimitsCfg:
    max_actions_per_sec: int


@dataclass(frozen=True, slots=True)
class LoggingCfg:
    level: str
    log_gaze: bool
    dump_frames: bool


@dataclass(frozen=True, slots=True)
class Config:
    general: GeneralCfg
    ipc: IpcCfg
    camera: CameraCfg
    openvino: OpenvinoCfg
    gaze: GazeCfg
    flick: FlickCfg
    limits: LimitsCfg
    logging: LoggingCfg


_SECTIONS: dict[str, type] = {
    "general": GeneralCfg,
    "ipc": IpcCfg,
    "camera": CameraCfg,
    "openvino": OpenvinoCfg,
    "gaze": GazeCfg,
    "flick": FlickCfg,
    "limits": LimitsCfg,
    "logging": LoggingCfg,
}


def _merge(overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = {sec: dict(vals) for sec, vals in DEFAULTS.items()}
    for sec, vals in overrides.items():
        if sec not in DEFAULTS:
            _log.warning("ignoring unknown config section [%s]", sec)
            continue
        if not isinstance(vals, dict):
            raise ConfigError(f"section [{sec}] must be a table")
        for key, val in vals.items():
            if key not in DEFAULTS[sec]:
                _log.warning("ignoring unknown config key %s.%s", sec, key)
                continue
            merged[sec][key] = val
    return merged


def _build(merged: dict[str, dict[str, Any]]) -> Config:
    sections: dict[str, Any] = {}
    for name, cls in _SECTIONS.items():
        vals = dict(merged[name])
        if name == "openvino" and "device_order" in vals:
            vals["device_order"] = tuple(vals["device_order"])
        allowed = {f.name for f in fields(cls)}
        sections[name] = cls(**{k: v for k, v in vals.items() if k in allowed})
    return Config(**sections)


def _validate(cfg: Config) -> None:
    if cfg.general.backend not in _BACKENDS:
        raise ConfigError(f"general.backend must be one of {sorted(_BACKENDS)}")
    if cfg.general.fps <= 0:
        raise ConfigError("general.fps must be > 0")

    if cfg.camera.width <= 0 or cfg.camera.height <= 0:
        raise ConfigError("camera.width/height must be > 0")

    if not cfg.openvino.device_order:
        raise ConfigError("openvino.device_order must be non-empty")
    bad = set(cfg.openvino.device_order) - _DEVICES
    if bad:
        raise ConfigError(f"openvino.device_order has invalid devices: {sorted(bad)}")
    if cfg.openvino.performance_hint not in _HINTS:
        raise ConfigError(f"openvino.performance_hint must be one of {sorted(_HINTS)}")

    if cfg.gaze.smoothing not in _SMOOTHERS:
        raise ConfigError(f"gaze.smoothing must be one of {sorted(_SMOOTHERS)}")
    if not 0.0 <= cfg.gaze.ema_alpha <= 1.0:
        raise ConfigError("gaze.ema_alpha must be in [0, 1]")
    if not 0.0 <= cfg.gaze.min_confidence <= 1.0:
        raise ConfigError("gaze.min_confidence must be in [0, 1]")
    if cfg.gaze.dwell_ms < 0:
        raise ConfigError("gaze.dwell_ms must be >= 0")
    if cfg.gaze.stability_px < 0.0:
        raise ConfigError("gaze.stability_px must be >= 0")

    if not cfg.flick.v_off < cfg.flick.v_on:
        raise ConfigError("flick.v_off must be < flick.v_on")
    if cfg.flick.refractory_ms < 0:
        raise ConfigError("flick.refractory_ms must be >= 0")
    if cfg.flick.min_present_frames < 0:
        raise ConfigError("flick.min_present_frames must be >= 0")

    if cfg.limits.max_actions_per_sec <= 0:
        raise ConfigError("limits.max_actions_per_sec must be > 0")

    if cfg.logging.level.upper() not in _LEVELS:
        raise ConfigError(f"logging.level must be one of {sorted(_LEVELS)}")


def default_config() -> Config:
    cfg = _build(_merge({}))
    _validate(cfg)
    return cfg


def load_config(path: Path | None = None) -> Config:
    target = path if path is not None else paths.config_file()
    overrides: dict[str, Any] = {}
    if target.exists():
        with target.open("rb") as fh:
            overrides = tomllib.load(fh)
    cfg = _build(_merge(overrides))
    _validate(cfg)
    return cfg
