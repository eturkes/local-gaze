from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from local_gaze.calibration.model import CalibrationModel, fit, load, save
from local_gaze.types import GazePoint

# Known affine ground truth: sx = 0.8*nx + 0.1 ; sy = 1.2*ny - 0.05.
_A, _B, _C = 0.8, 0.0, 0.1
_D, _E, _F = 0.0, 1.2, -0.05


def _truth(nx: float, ny: float) -> tuple[float, float]:
    return (_A * nx + _B * ny + _C, _D * nx + _E * ny + _F)


def _samples() -> list[tuple[GazePoint, tuple[float, float]]]:
    pts = [(0.1, 0.2), (0.9, 0.1), (0.5, 0.8), (0.3, 0.5), (0.7, 0.4)]
    return [(GazePoint(nx=x, ny=y, confidence=0.9), _truth(x, y)) for x, y in pts]


def test_fit_recovers_known_affine() -> None:
    m = fit(_samples())
    expected = [_A, _B, _C, _D, _E, _F]
    for got, exp in zip(m.coeffs, expected, strict=True):
        assert abs(got - exp) < 1e-6
    # And applies correctly at unseen points.
    for x, y in [(0.25, 0.65), (0.6, 0.15)]:
        sx, sy = m.apply(x, y)
        ex, ey = _truth(x, y)
        assert abs(sx - ex) < 1e-6 and abs(sy - ey) < 1e-6


def test_fit_requires_three_samples() -> None:
    with pytest.raises(ValueError):
        fit(_samples()[:2])


def test_apply_matches_coeffs() -> None:
    m = CalibrationModel(coeffs=[2.0, 0.0, 1.0, 0.0, 3.0, -1.0], created="t")
    assert m.apply(1.0, 1.0) == (3.0, 2.0)


def test_save_load_round_trip(tmp_path: Path) -> None:
    m = fit(_samples())
    p = tmp_path / "calibration.json"
    save(m, p)
    loaded = load(p)
    assert loaded is not None
    assert loaded.coeffs == m.coeffs
    assert loaded.created == m.created


def test_save_writes_mode_0600(tmp_path: Path) -> None:
    p = tmp_path / "calibration.json"
    save(fit(_samples()), p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_save_atomic_overwrites(tmp_path: Path) -> None:
    p = tmp_path / "calibration.json"
    save(CalibrationModel(coeffs=[1, 0, 0, 0, 1, 0], created="a"), p)
    save(CalibrationModel(coeffs=[2, 0, 0, 0, 2, 0], created="b"), p)
    loaded = load(p)
    assert loaded is not None and loaded.created == "b"
    # No leftover temp files in the directory.
    assert {q.name for q in tmp_path.iterdir()} == {"calibration.json"}


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load(tmp_path / "absent.json") is None


def test_load_malformed_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert load(p) is None


def test_load_wrong_coeff_count_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "short.json"
    p.write_text(json.dumps({"coeffs": [1, 2, 3], "created": "t"}), encoding="utf-8")
    assert load(p) is None
