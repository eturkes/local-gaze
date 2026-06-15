from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import FlickCfg
    from ..types import HandSample


class FlickDetector:
    """Hand-center horizontal flick detector: hysteresis + refractory + return-to-neutral.

    State machine (per update, ts in seconds):
      - Track presence: hand must be present for >= min_present_frames consecutive frames
        before any arming; a single absent frame resets presence and the buffer.
      - Maintain a small ring buffer of (cx, ts); horizontal velocity vx is the finite
        difference between the newest and oldest buffered samples.
      - DISARMED: when |vx| > v_on (and primed + outside refractory), fire sign(vx) ONCE
        and enter ARMED with that sign latched.
      - ARMED: suppress further fires until |vx| < v_off (return toward neutral), which
        re-DISARMS so the next flick can fire. refractory_ms also gates re-fire by wall
        position in the injected ts stream.

    update() returns -1 / +1 exactly once per flick, else 0. ts is injected for testing.
    """

    _BUF = 5  # samples spanning the velocity window (~4 frame-intervals)

    def __init__(self, cfg: FlickCfg) -> None:
        self._v_on = cfg.v_on
        self._v_off = cfg.v_off
        self._refractory_s = cfg.refractory_ms / 1000.0
        self._min_present = cfg.min_present_frames
        self.reset()

    def reset(self) -> None:
        self._buf: deque[tuple[float, float]] = deque(maxlen=self._BUF)
        self._present_count = 0
        self._armed = False
        self._last_fire_ts: float | None = None

    def update(self, hand: HandSample, ts: float) -> int:
        if not hand.present:
            self._present_count = 0
            self._buf.clear()
            self._armed = False
            return 0

        self._present_count += 1
        self._buf.append((hand.cx, ts))

        # Need history and a settled presence before trusting velocity.
        if self._present_count < self._min_present or len(self._buf) < 2:
            return 0

        cx0, t0 = self._buf[0]
        cx1, t1 = self._buf[-1]
        dt = t1 - t0
        if dt <= 0.0:
            return 0
        vx = (cx1 - cx0) / dt

        speed = abs(vx)
        if self._armed:
            if speed < self._v_off:
                self._armed = False
            return 0

        if speed > self._v_on and not self._in_refractory(ts):
            self._armed = True
            self._last_fire_ts = ts
            return 1 if vx > 0.0 else -1
        return 0

    def _in_refractory(self, ts: float) -> bool:
        if self._last_fire_ts is None:
            return False
        return (ts - self._last_fire_ts) < self._refractory_s
