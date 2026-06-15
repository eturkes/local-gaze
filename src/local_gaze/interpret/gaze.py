from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .smoothing import make_smoother

if TYPE_CHECKING:
    from ..calibration.model import CalibrationModel
    from ..config import GazeCfg
    from ..types import GazePoint


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


class GazeTracker:
    """Calibrated gaze -> screen point + dwell gating.

    Pipeline per update(gaze, ts):
      1. Reject when gaze is None or confidence < min_confidence -> returns None and breaks
         the dwell streak (treated like a saccade / signal loss).
      2. Smooth nx, ny independently (One-Euro or EMA per cfg).
      3. Apply the calibration affine map (identity if calib is None).
      4. Clamp to [0,1]^2.
    The smoothed/clamped point is returned and recorded as the dwell anchor.

    dwell_point() (no ts arg) returns (nx, ny) once a continuous fixation of >= dwell_ms
    has stayed within stability_px of the dwell anchor, measured against the latest sample
    time captured by update(). Any sample outside stability_px is a saccade: it re-anchors
    and restarts the dwell clock. A reported dwell is latched so the same fixation is not
    reported twice (the caller acts once per dwell).
    """

    def __init__(self, cfg: GazeCfg, calib: CalibrationModel | None, freq: float) -> None:
        self._cfg = cfg
        self._calib = calib
        self._freq = freq
        self._min_conf = cfg.min_confidence
        self._dwell_s = cfg.dwell_ms / 1000.0
        self._stability = cfg.stability_px
        self.reset()

    def reset(self) -> None:
        self._sx, self._sy = make_smoother(self._cfg, self._freq)
        self._anchor: tuple[float, float] | None = None
        self._anchor_ts: float | None = None
        self._now: float = 0.0
        self._dwell_consumed = False

    def update(self, gaze: GazePoint | None, ts: float) -> tuple[float, float] | None:
        self._now = ts
        if gaze is None or gaze.confidence < self._min_conf:
            self._break_dwell()
            return None
        if not (math.isfinite(gaze.nx) and math.isfinite(gaze.ny)):
            # Garbage model output: treat as signal loss so NaN never poisons the smoother.
            self._break_dwell()
            return None

        nx = self._sx(gaze.nx, ts)
        ny = self._sy(gaze.ny, ts)
        if self._calib is not None:
            nx, ny = self._calib.apply(nx, ny)
        nx, ny = _clamp01(nx), _clamp01(ny)
        pt = (nx, ny)

        if self._anchor is None or self._anchor_ts is None:
            self._reanchor(pt, ts)
        elif math.hypot(nx - self._anchor[0], ny - self._anchor[1]) > self._stability:
            # Saccade: jumped outside the stability radius -> restart the dwell.
            self._reanchor(pt, ts)
        # else: still fixating; keep anchor + clock running.
        return pt

    def dwell_point(self) -> tuple[float, float] | None:
        if self._anchor is None or self._anchor_ts is None or self._dwell_consumed:
            return None
        if (self._now - self._anchor_ts) >= self._dwell_s:
            self._dwell_consumed = True
            return self._anchor
        return None

    def _reanchor(self, pt: tuple[float, float], ts: float) -> None:
        self._anchor = pt
        self._anchor_ts = ts
        self._dwell_consumed = False

    def _break_dwell(self) -> None:
        self._anchor = None
        self._anchor_ts = None
        self._dwell_consumed = False
