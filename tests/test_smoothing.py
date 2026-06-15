from __future__ import annotations

from local_gaze.config import GazeCfg
from local_gaze.interpret.smoothing import Ema, OneEuroFilter, make_smoother


def test_ema_first_returns_seed_then_approaches() -> None:
    e = Ema(0.4)
    assert e(0.0) == 0.0  # first call seeds
    ys = [e(1.0) for _ in range(8)]
    # strictly increasing toward 1, never overshooting on a positive step
    assert all(ys[i] < ys[i + 1] for i in range(len(ys) - 1))
    assert ys[-1] > 0.9
    assert all(y <= 1.0 for y in ys)
    # closed form check: after one step y = alpha * target
    e2 = Ema(0.4)
    e2(0.0)
    assert abs(e2(1.0) - 0.4) < 1e-12


def test_ema_alpha_one_tracks_instantly() -> None:
    e = Ema(1.0)
    e(0.0)
    assert e(1.0) == 1.0


def test_one_euro_first_call_passthrough() -> None:
    f = OneEuroFilter(freq=30.0)
    assert f(0.5, 0.0) == 0.5


def test_one_euro_step_lags_and_converges() -> None:
    f = OneEuroFilter(freq=30.0, min_cutoff=1.0, beta=0.7, d_cutoff=1.0)
    f(0.0, 0.0)
    t = 0.0
    ys = []
    for _ in range(12):
        t += 1.0 / 30.0
        ys.append(f(1.0, t))
    # lag: first response is between seed and target (not an instant jump)
    assert 0.0 < ys[0] < 1.0
    # monotonic approach, no overshoot above the target on a monotone input
    assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))
    assert all(y <= 1.0 + 1e-9 for y in ys)
    assert ys[-1] > 0.95


def test_one_euro_steady_state_is_fixed_point() -> None:
    f = OneEuroFilter(freq=30.0)
    f(0.7, 0.0)
    t = 0.0
    last = 0.7
    for _ in range(20):
        t += 1.0 / 30.0
        last = f(0.7, t)
    assert abs(last - 0.7) < 1e-9  # constant input -> output pinned to it


def test_one_euro_non_increasing_time_is_safe() -> None:
    f = OneEuroFilter(freq=30.0)
    f(0.5, 1.0)
    # dt <= 0 must not raise / divide by zero; returns a finite number.
    y = f(0.9, 1.0)
    assert isinstance(y, float)


def test_make_smoother_one_euro_pair_independent() -> None:
    cfg = GazeCfg(
        smoothing="one_euro", ema_alpha=0.4, min_confidence=0.5, dwell_ms=400, stability_px=0.04
    )
    sx, sy = make_smoother(cfg, 30.0)
    assert sx is not sy
    assert sx(0.1, 0.0) == 0.1
    assert sy(0.9, 0.0) == 0.9  # independent state: sy not polluted by sx's seed


def test_make_smoother_ema_uses_alpha() -> None:
    cfg = GazeCfg(
        smoothing="ema", ema_alpha=0.5, min_confidence=0.5, dwell_ms=400, stability_px=0.04
    )
    sx, _ = make_smoother(cfg, 30.0)
    assert sx(0.0, 0.0) == 0.0
    assert abs(sx(1.0, 0.033) - 0.5) < 1e-12  # ema ignores t, applies alpha
