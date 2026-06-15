from __future__ import annotations

import dataclasses as dc

from local_gaze.config import FlickCfg
from local_gaze.interpret.gestures import FlickDetector
from local_gaze.types import HandSample

# Fast cadence so a single flick clears v_on; refractory generous to test suppression.
_FLICK = FlickCfg(v_on=0.9, v_off=0.3, refractory_ms=600, min_present_frames=3)
_DT = 1.0 / 30.0
_RAMP_R = [0.55, 0.65, 0.78, 0.92]  # rightward burst: |vx| over the window exceeds v_on
_RAMP_L = [0.45, 0.35, 0.22, 0.08]  # leftward burst
# The velocity window is ~5 samples deep; this many neutral frames flush an excursion so
# |vx| falls under v_off and the detector disarms (return-to-neutral).
_NEUTRAL = [0.5] * 8


def _present(cx: float) -> HandSample:
    return HandSample(present=True, cx=cx, cy=0.5, confidence=0.9)


def _drive(det: FlickDetector, cxs: list[float], t0: float = 0.0) -> tuple[list[int], float]:
    out: list[int] = []
    t = t0
    for cx in cxs:
        out.append(det.update(_present(cx), t))
        t += _DT
    return out, t


def _fires(out: list[int]) -> list[int]:
    return [x for x in out if x != 0]


def test_rightward_flick_fires_plus_one_once() -> None:
    det = FlickDetector(_FLICK)
    _, t = _drive(det, _NEUTRAL)  # prime presence + settle buffer
    out, _ = _drive(det, _RAMP_R, t0=t)
    assert _fires(out) == [1]


def test_leftward_flick_fires_minus_one() -> None:
    det = FlickDetector(_FLICK)
    _, t = _drive(det, _NEUTRAL)
    out, _ = _drive(det, _RAMP_L, t0=t)
    assert _fires(out) == [-1]


def test_below_v_on_does_not_fire() -> None:
    det = FlickDetector(_FLICK)
    _, t = _drive(det, _NEUTRAL)
    out, _ = _drive(det, [0.51, 0.52, 0.53, 0.54, 0.55], t0=t)  # slow drift under v_on
    assert _fires(out) == []


def test_min_present_frames_gates_arming() -> None:
    # min_present larger than the whole burst -> can never arm even at high velocity.
    det = FlickDetector(dc.replace(_FLICK, min_present_frames=50))
    out, _ = _drive(det, _NEUTRAL + _RAMP_R)
    assert _fires(out) == []


def test_refractory_suppresses_immediate_second_flick() -> None:
    det = FlickDetector(_FLICK)  # refractory_ms = 600
    _, t = _drive(det, _NEUTRAL)
    out1, t = _drive(det, _RAMP_R, t0=t)
    assert _fires(out1) == [1]
    _, t = _drive(det, _NEUTRAL, t0=t)  # disarm via return-to-neutral
    out2, _ = _drive(det, _RAMP_R, t0=t)  # still well inside 600ms refractory window
    assert _fires(out2) == []


def test_neutral_return_rearms_after_refractory() -> None:
    det = FlickDetector(dc.replace(_FLICK, refractory_ms=10))  # tiny refractory
    _, t = _drive(det, _NEUTRAL)
    out1, t = _drive(det, _RAMP_R, t0=t)
    assert _fires(out1) == [1]
    _, t = _drive(det, _NEUTRAL, t0=t)  # disarm + clear the (10ms) refractory window
    out2, _ = _drive(det, _RAMP_R, t0=t)
    assert _fires(out2) == [1]


def test_absence_clears_buffer_and_presence() -> None:
    det = FlickDetector(_FLICK)
    _, t = _drive(det, _NEUTRAL)
    det.update(HandSample(present=False), t)  # wipes buffer + present_count
    t += _DT
    # First two frames after re-presence cannot fire (presence/buffer not rebuilt yet),
    # proving the absence reset took effect.
    out, _ = _drive(det, _RAMP_R[:2], t0=t)
    assert _fires(out) == []


def test_reset_requires_repriming() -> None:
    det = FlickDetector(_FLICK)
    _, t = _drive(det, _NEUTRAL)
    _drive(det, _RAMP_R, t0=t)
    det.reset()
    # After reset, the first two frames cannot fire (state cleared, presence rebuilding).
    out, _ = _drive(det, _RAMP_R[:2])
    assert _fires(out) == []
