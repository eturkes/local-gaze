from __future__ import annotations

from pathlib import Path

import pytest

from local_gaze.config import (
    DEFAULTS,
    Config,
    ConfigError,
    default_config,
    load_config,
)


def _write(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    return p


def test_defaults_match_table() -> None:
    cfg = default_config()
    assert isinstance(cfg, Config)
    assert cfg.general.backend == DEFAULTS["general"]["backend"] == "synthetic"
    assert cfg.general.enabled_default is False
    assert cfg.ipc.require_token is True
    assert cfg.limits.max_actions_per_sec == 4
    # device_order is materialized as a tuple (frozen dataclass) from the default list.
    assert cfg.openvino.device_order == ("NPU", "GPU", "CPU")


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.general.fps == 30


def test_toml_override_by_section(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "c.toml",
        """
        [general]
        backend = "openvino"
        fps = 60
        [gaze]
        smoothing = "ema"
        ema_alpha = 0.25
        [openvino]
        device_order = ["GPU", "CPU"]
        """,
    )
    cfg = load_config(p)
    assert cfg.general.backend == "openvino"
    assert cfg.general.fps == 60
    assert cfg.gaze.smoothing == "ema"
    assert cfg.gaze.ema_alpha == 0.25
    assert cfg.openvino.device_order == ("GPU", "CPU")
    # untouched sections keep defaults
    assert cfg.limits.max_actions_per_sec == 4


def test_unknown_key_and_section_warn_and_ignore(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = _write(
        tmp_path / "c.toml",
        """
        [general]
        backend = "synthetic"
        bogus_key = 1
        [no_such_section]
        x = 2
        """,
    )
    with caplog.at_level("WARNING"):
        cfg = load_config(p)
    assert cfg.general.backend == "synthetic"
    text = caplog.text
    assert "general.bogus_key" in text
    assert "no_such_section" in text


@pytest.mark.parametrize(
    "body",
    [
        '[general]\nbackend = "nope"\n',
        "[general]\nfps = 0\n",
        "[gaze]\nema_alpha = 1.5\n",
        "[gaze]\nmin_confidence = -0.1\n",
        "[flick]\nv_on = 0.2\nv_off = 0.5\n",  # v_off !< v_on
        '[openvino]\ndevice_order = ["TPU"]\n',
        "[openvino]\ndevice_order = []\n",
        '[openvino]\nperformance_hint = "FAST"\n',
        "[camera]\nwidth = 0\n",
        "[limits]\nmax_actions_per_sec = 0\n",
        '[logging]\nlevel = "LOUD"\n',
    ],
)
def test_invalid_values_raise(tmp_path: Path, body: str) -> None:
    p = _write(tmp_path / "c.toml", body)
    with pytest.raises(ConfigError):
        load_config(p)


def test_non_table_section_raises(tmp_path: Path) -> None:
    p = _write(tmp_path / "c.toml", "general = 5\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_uses_paths_when_none(xdg_tmp: Path) -> None:
    # No config file written -> defaults; also exercises the paths.config_file() branch.
    cfg = load_config(None)
    assert cfg.general.backend == "synthetic"
