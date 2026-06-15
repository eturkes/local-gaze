from __future__ import annotations

import dataclasses as dc

from local_gaze.calibration.model import CalibrationModel
from local_gaze.config import GazeCfg
from local_gaze.interpret.gaze import GazeTracker
from local_gaze.types import GazePoint

# EMA smoothing keeps the math trivial/deterministic for assertions; alpha=1 => no lag.
_GAZE = GazeCfg(
    smoothing="ema", ema_alpha=1.0, min_confidence=0.5, dwell_ms=400, stability_px=0.04
)
_DT = 1.0 / 30.0


def _g(nx: float, ny: float, conf: float = 0.9) -> GazePoint:
    return GazePoint(nx=nx, ny=ny, confidence=conf)


def test_low_confidence_returns_none() -> None:
    tr = GazeTracker(_GAZE, None, 30.0)
    assert tr.update(_g(0.5, 0.5, conf=0.1), 0.0) is None


def test_none_gaze_returns_none() -> None:
    tr = GazeTracker(_GAZE, None, 30.0)
    assert tr.update(None, 0.0) is None


def test_point_clamped_to_unit_square() -> None:
    # Calibration that maps the input far outside [0,1]; output must be clamped.
    calib = CalibrationModel(coeffs=[10.0, 0.0, 0.0, 0.0, 10.0, 0.0], created="t")
    tr = GazeTracker(_GAZE, calib, 30.0)
    nx, ny = tr.update(_g(0.5, 0.5), 0.0)  # type: ignore[misc]
    assert (nx, ny) == (1.0, 1.0)
    tr2 = GazeTracker(_GAZE, None, 30.0)
    pt = tr2.update(_g(-2.0, 0.5), 0.0)
    assert pt is not None and pt[0] == 0.0


def test_calibration_map_applied() -> None:
    # sx = 0.5*nx + 0.25 ; sy = 0.5*ny + 0.25  (identity-ish affine, stays in range)
    calib = CalibrationModel(coeffs=[0.5, 0.0, 0.25, 0.0, 0.5, 0.25], created="t")
    tr = GazeTracker(_GAZE, calib, 30.0)
    pt = tr.update(_g(0.4, 0.6), 0.0)
    assert pt is not None
    assert abs(pt[0] - 0.45) < 1e-9 and abs(pt[1] - 0.55) < 1e-9


def test_dwell_fires_once_after_dwell_ms() -> None:
    tr = GazeTracker(_GAZE, None, 30.0)
    t = 0.0
    fired_at: float | None = None
    fire_count = 0
    for _ in range(40):  # ~1.3 s, dwell_ms is 400
        tr.update(_g(0.5, 0.5), t)
        d = tr.dwell_point()
        if d is not None:
            fire_count += 1
            if fired_at is None:
                fired_at = t
                assert abs(d[0] - 0.5) < 1e-9 and abs(d[1] - 0.5) < 1e-9
        t += _DT
    assert fire_count == 1  # latched: a single fixation reports exactly once
    assert fired_at is not None and fired_at >= 0.4 - 1e-9


def test_saccade_resets_dwell() -> None:
    # Jump outside stability_px every frame -> never accumulates a dwell.
    tr = GazeTracker(_GAZE, None, 30.0)
    t = 0.0
    fired = False
    for i in range(60):
        nx = 0.2 if i % 2 == 0 else 0.8  # 0.6 apart >> stability 0.04
        tr.update(_g(nx, 0.5), t)
        if tr.dwell_point() is not None:
            fired = True
        t += _DT
    assert fired is False


def test_low_confidence_breaks_dwell_streak() -> None:
    tr = GazeTracker(_GAZE, None, 30.0)
    t = 0.0
    # Build most of a dwell, then a low-confidence frame should reset the clock.
    for _ in range(10):
        tr.update(_g(0.5, 0.5), t)
        t += _DT
    tr.update(_g(0.5, 0.5, conf=0.0), t)  # signal loss -> break
    t += _DT
    assert tr.dwell_point() is None
    # Re-anchoring after the break: needs a fresh full dwell window again.
    start = t
    fired = False
    while t < start + 0.4 - _DT:
        tr.update(_g(0.5, 0.5), t)
        if tr.dwell_point() is not None:
            fired = True
        t += _DT
    assert fired is False  # not yet a full dwell_ms since the break


def test_reset_clears_dwell() -> None:
    tr = GazeTracker(_GAZE, None, 30.0)
    t = 0.0
    for _ in range(20):
        tr.update(_g(0.5, 0.5), t)
        t += _DT
    tr.reset()
    assert tr.dwell_point() is None


def test_one_euro_smoothing_path_runs() -> None:
    # Exercise the one_euro branch end-to-end (smoke: returns clamped point, dwells).
    cfg = dc.replace(_GAZE, smoothing="one_euro")
    tr = GazeTracker(cfg, None, 30.0)
    t = 0.0
    pt = None
    for _ in range(40):
        pt = tr.update(_g(0.5, 0.5), t)
        t += _DT
    assert pt is not None and 0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0
