from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import GazeCfg


def _alpha(cutoff: float, dt: float) -> float:
    # Exponential-smoothing alpha for a given cutoff freq and sample period (1-Euro paper).
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """1-Euro filter (Casiez et al.): low jitter at rest, low lag during fast motion.

    Cutoff adapts to the signal derivative: ``cutoff = min_cutoff + beta*|dx/dt|``. The
    first call seeds state and returns x unchanged. ``t`` is seconds (monotonic); a
    non-increasing t reuses the previous dt-derived alpha defensively (no divide-by-zero).
    """

    def __init__(
        self,
        freq: float,
        min_cutoff: float = 1.0,
        beta: float = 0.0,
        d_cutoff: float = 1.0,
    ) -> None:
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev: float | None = None
        self._dx_prev = 0.0
        self._t_prev: float | None = None

    def __call__(self, x: float, t: float) -> float:
        if self._x_prev is None or self._t_prev is None:
            self._x_prev = x
            self._t_prev = t
            self._dx_prev = 0.0
            return x

        dt = t - self._t_prev
        if dt <= 0.0:
            dt = 1.0 / self.freq if self.freq > 0.0 else 1.0

        dx = (x - self._x_prev) / dt
        dx_hat = self._dx_prev + _alpha(self.d_cutoff, dt) * (dx - self._dx_prev)

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        x_hat = self._x_prev + _alpha(cutoff, dt) * (x - self._x_prev)

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        self._t_prev = t
        return x_hat


class Ema:
    """Exponential moving average: ``y = y + alpha*(x - y)``; first call returns x."""

    def __init__(self, alpha: float) -> None:
        self.alpha = alpha
        self._y: float | None = None

    def __call__(self, x: float) -> float:
        if self._y is None:
            self._y = x
        else:
            self._y += self.alpha * (x - self._y)
        return self._y


class _AxisSmoother:
    """Per-axis adapter giving One-Euro and EMA a uniform ``(x, t) -> float`` call."""

    def __init__(self, cfg: GazeCfg, freq: float) -> None:
        self._one_euro: OneEuroFilter | None = None
        self._ema: Ema | None = None
        if cfg.smoothing == "one_euro":
            # min_cutoff/beta tuned for normalized [0,1] gaze; host [H] may refine.
            self._one_euro = OneEuroFilter(freq=freq, min_cutoff=1.0, beta=0.7, d_cutoff=1.0)
        else:
            self._ema = Ema(cfg.ema_alpha)

    def __call__(self, x: float, t: float) -> float:
        if self._one_euro is not None:
            return self._one_euro(x, t)
        assert self._ema is not None
        return self._ema(x)


def make_smoother(cfg: GazeCfg, freq: float) -> tuple[_AxisSmoother, _AxisSmoother]:
    """Return an independent (x, y) smoother pair per cfg.smoothing; each is ``(v, t)->v``."""
    return _AxisSmoother(cfg, freq), _AxisSmoother(cfg, freq)
