from __future__ import annotations

import math
from collections.abc import Sequence

from ..config import Config
from ..types import GazePoint, HandSample, PerceptionResult


class SyntheticBackend:
    """Deterministic, dependency-free backend (container default).

    Structurally satisfies PerceptionBackend. Each read() advances an internal monotonic
    frame counter; ts is derived as frame_id / fps (NEVER wall-clock) so a fixed cfg yields
    a byte-for-byte reproducible stream. Gaze follows a slow Lissajous-style sweep across
    [0,1]^2; the hand is normally centred (neutral) and performs a scripted left/right
    flick (sharp cx excursion and return) on a fixed period so FlickDetector can fire.

    Pass ``script=`` to replay an explicit result sequence instead (cycled); ts is still
    rewritten from the frame counter so replay stays deterministic regardless of source ts.
    """

    # Flick choreography (in frames): a short ramp out to an extreme cx then back to centre.
    _FLICK_PERIOD = 90          # frames between flick onsets (3 s @ 30 fps)
    _FLICK_LEN = 6              # frames the excursion+return spans
    _FLICK_AMP = 0.45          # peak |cx - 0.5| during the flick

    def __init__(
        self, cfg: Config, *, script: Sequence[PerceptionResult] | None = None
    ) -> None:
        self._fps = float(cfg.general.fps)
        self._script = list(script) if script is not None else None
        self._frame = 0
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def read(self) -> PerceptionResult:
        fid = self._frame
        self._frame += 1
        ts = fid / self._fps

        if self._script is not None:
            base = self._script[fid % len(self._script)]
            return PerceptionResult(ts=ts, gaze=base.gaze, hand=base.hand, frame_id=fid)

        return PerceptionResult(
            ts=ts, gaze=self._gaze(fid), hand=self._hand(fid), frame_id=fid
        )

    def _gaze(self, fid: int) -> GazePoint:
        # Slow, smooth, bounded sweep well inside [0,1]; incommensurate rates avoid a
        # short loop so dwell/saccade logic sees realistic motion.
        nx = 0.5 + 0.35 * math.sin(fid * 0.05)
        ny = 0.5 + 0.30 * math.sin(fid * 0.037 + 1.0)
        return GazePoint(nx=nx, ny=ny, confidence=0.9)

    def _hand(self, fid: int) -> HandSample:
        phase = fid % self._FLICK_PERIOD
        if phase >= self._FLICK_LEN:
            return HandSample(present=True, cx=0.5, cy=0.5, confidence=0.9)
        # Alternate flick direction each period: +x, then -x, ...
        sign = 1.0 if (fid // self._FLICK_PERIOD) % 2 == 0 else -1.0
        # Triangle profile: out to peak at the midpoint, back to neutral by _FLICK_LEN.
        half = self._FLICK_LEN / 2.0
        tri = 1.0 - abs(phase - half) / half
        cx = 0.5 + sign * self._FLICK_AMP * tri
        return HandSample(present=True, cx=cx, cy=0.5, confidence=0.9)

    @property
    def info(self) -> dict:
        return {
            "backend": "synthetic",
            "device": "none",
            "models": {},
            "camera": "none",
            "fps": self._fps,
        }
