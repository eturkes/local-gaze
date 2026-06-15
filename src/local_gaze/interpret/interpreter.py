from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from ..types import Action, ActionKind
from .gaze import GazeTracker
from .gestures import FlickDetector

if TYPE_CHECKING:
    from ..calibration.model import CalibrationModel
    from ..config import Config
    from ..types import PerceptionResult


class Interpreter:
    """Fuse gaze + flick into a single gated Action per frame. PURE: no I/O, no D-Bus.

    step(result):
      1. Feed the hand to FlickDetector and the gaze to GazeTracker (ts from the result).
      2. A non-zero flick wins -> SWITCH(direction). Otherwise a satisfied dwell ->
         FOCUS(nx, ny). Flick is prioritised because a workspace switch should pre-empt a
         focus that may target the outgoing workspace.
      3. Enforce a GLOBAL ceiling of max_actions_per_sec across BOTH action kinds using a
         sliding 1-second window keyed on the result ts; over-budget intents collapse to
         NONE (dropped, not queued).
    Returns Action(kind=NONE) when nothing fires or when rate-limited. The daemon owns the
    enabled/dry-run gate and is the final wire gate; this stays decision-only.
    """

    def __init__(self, cfg: Config, calib: CalibrationModel | None) -> None:
        freq = float(cfg.general.fps)
        self._gaze = GazeTracker(cfg.gaze, calib, freq)
        self._flick = FlickDetector(cfg.flick)
        self._max_per_sec = cfg.limits.max_actions_per_sec
        self._emitted: deque[float] = deque()

    def step(self, result: PerceptionResult) -> Action:
        ts = result.ts
        flick = self._flick.update(result.hand, ts)
        self._gaze.update(result.gaze, ts)

        if flick != 0:
            return self._gate(Action(kind=ActionKind.SWITCH, direction=flick), ts)

        dwell = self._gaze.dwell_point()
        if dwell is not None:
            return self._gate(Action(kind=ActionKind.FOCUS, nx=dwell[0], ny=dwell[1]), ts)

        return Action(kind=ActionKind.NONE)

    def _gate(self, action: Action, ts: float) -> Action:
        # Evict timestamps older than the 1-second window, then admit iff under ceiling.
        window_start = ts - 1.0
        while self._emitted and self._emitted[0] <= window_start:
            self._emitted.popleft()
        if len(self._emitted) >= self._max_per_sec:
            return Action(kind=ActionKind.NONE)
        self._emitted.append(ts)
        return action
