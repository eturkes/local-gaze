from __future__ import annotations

import dataclasses as dc

from local_gaze.config import Config, default_config
from local_gaze.interpret.interpreter import Interpreter
from local_gaze.types import ActionKind, GazePoint, HandSample, PerceptionResult

_DT = 1.0 / 30.0
_RAMP_R = [0.55, 0.65, 0.78, 0.92]
_NEUTRAL8 = [0.5] * 8
_Out = list[tuple[str, int]]


def _result(ts: float, *, cx: float = 0.5, gaze: GazePoint | None = None) -> PerceptionResult:
    return PerceptionResult(
        ts=ts, gaze=gaze, hand=HandSample(present=True, cx=cx, cy=0.5, confidence=0.9)
    )


def _run(itp: Interpreter, cxs: list[float], t0: float = 0.0) -> tuple[_Out, float]:
    out: _Out = []
    t = t0
    for cx in cxs:
        a = itp.step(_result(t, cx=cx))
        out.append((a.kind.name, a.direction))
        t += _DT
    return out, t


def _switches(out: _Out) -> list[int]:
    return [d for k, d in out if k == "SWITCH"]


def test_flick_produces_switch() -> None:
    cfg = default_config()
    itp = Interpreter(cfg, None)
    out, _ = _run(itp, _NEUTRAL8 + _RAMP_R)
    assert _switches(out) == [1]


def test_dwell_produces_focus() -> None:
    cfg = default_config()  # dwell_ms=400, min_confidence=0.5
    itp = Interpreter(cfg, None)
    # Hand absent (no flicks), gaze fixed -> a FOCUS once dwell satisfied.
    t = 0.0
    focus: tuple[float, float] | None = None
    for _ in range(40):
        a = itp.step(
            PerceptionResult(
                ts=t, gaze=GazePoint(nx=0.3, ny=0.7, confidence=0.9), hand=HandSample(present=False)
            )
        )
        if a.kind is ActionKind.FOCUS and focus is None:
            focus = (a.nx, a.ny)
        t += _DT
    assert focus is not None
    assert abs(focus[0] - 0.3) < 0.05 and abs(focus[1] - 0.7) < 0.05


def test_flick_preempts_focus() -> None:
    # A flick frame that also has a dwell-ready gaze must emit SWITCH, not FOCUS.
    cfg = dc.replace(default_config(), gaze=dc.replace(default_config().gaze, dwell_ms=0))
    itp = Interpreter(cfg, None)
    fixed = GazePoint(nx=0.5, ny=0.5, confidence=0.9)
    t = 0.0
    # prime presence + dwell with a steady gaze
    for cx in _NEUTRAL8:
        itp.step(_result(t, cx=cx, gaze=fixed))
        t += _DT
    # now a rightward flick burst: each step still has dwell-ready gaze but flick wins
    kinds = []
    for cx in _RAMP_R:
        a = itp.step(_result(t, cx=cx, gaze=fixed))
        kinds.append(a.kind)
        t += _DT
    assert ActionKind.SWITCH in kinds
    assert ActionKind.FOCUS not in kinds  # flick pre-empts on the firing frame


def test_rate_limit_caps_actions_per_sec() -> None:
    base = default_config()
    capped = _fast_flick_cfg(base, max_per_sec=2)
    itp = Interpreter(capped, None)
    out = _flood_flicks(itp)
    assert len(_switches(out)) == 2  # ceiling enforced within the 1-second window

    # Same flood with a high ceiling must exceed 2 -> proves the cap (not the stream) limits.
    loose = _fast_flick_cfg(base, max_per_sec=100)
    out2 = _flood_flicks(Interpreter(loose, None))
    assert len(_switches(out2)) > 2


def test_dry_run_and_enabled_gating_are_not_interpreter_concerns() -> None:
    # The interpreter is decision-only; gating returns NONE only via rate limit, never from
    # enabled/dry_run (the daemon owns those). With no input, output is NONE.
    itp = Interpreter(default_config(), None)
    a = itp.step(
        PerceptionResult(ts=0.0, gaze=None, hand=HandSample(present=False))
    )
    assert a.kind is ActionKind.NONE


def _fast_flick_cfg(base: Config, *, max_per_sec: int) -> Config:
    return dc.replace(
        base,
        flick=dc.replace(base.flick, refractory_ms=50, min_present_frames=2),
        limits=dc.replace(base.limits, max_actions_per_sec=max_per_sec),
    )


def _flood_flicks(itp: Interpreter) -> _Out:
    # Pack many rightward flicks into ~1 s at high frame rate; gaze=None suppresses FOCUS.
    pattern = [0.5, 0.5, 0.55, 0.65, 0.8, 0.95, 0.5, 0.5, 0.5]
    dt = 1.0 / 120.0
    out: _Out = []
    t = 0.0
    while t < 1.0:
        for cx in pattern:
            a = itp.step(_result(t, cx=cx, gaze=None))
            out.append((a.kind.name, a.direction))
            t += dt
    return out
