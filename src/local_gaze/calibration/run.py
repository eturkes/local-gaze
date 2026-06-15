from __future__ import annotations

import asyncio
import logging
import math
from typing import TYPE_CHECKING

from .. import paths
from ..logging_setup import GAZE_KEY
from ..types import GazePoint
from . import model as calib_model

if TYPE_CHECKING:
    from ..config import Config
    from ..ipc.client import ExtensionClient
    from ..perception.base import PerceptionBackend
    from .model import CalibrationModel

_log = logging.getLogger(__name__)

# 3x3 normalized grid, slightly inset from the screen edges so dots stay on-monitor.
_INSET = 0.1
_GRID = (_INSET, 0.5, 1.0 - _INSET)
TARGETS: list[tuple[float, float]] = [(x, y) for y in _GRID for x in _GRID]

# Per-target sampling. Collect this many confident, spatially-stable raw samples.
_SAMPLES_PER_TARGET = 20
_MAX_FRAMES = 200
_SETTLE_FRAMES = 8  # discard initial frames so the eye saccade settles on the dot


async def calibrate(
    cfg: Config, client: ExtensionClient, backend: PerceptionBackend
) -> CalibrationModel:
    """Run the interactive 3x3 calibration and persist the fitted gaze map.

    For each target: show the dot, let the gaze settle, collect stable raw
    ``GazePoint`` samples, hide the dot. Fit a least-squares affine map
    (raw gaze -> normalized screen) and save it (0600) to the calibration path.
    """
    frame_interval = 1.0 / float(cfg.general.fps)
    min_conf = cfg.gaze.min_confidence
    stability = cfg.gaze.stability_px

    samples: list[tuple[GazePoint, tuple[float, float]]] = []
    for nx, ny in TARGETS:
        await client.show_calibration_target(nx, ny, True)
        try:
            collected = await _collect_target(
                backend, frame_interval, min_conf, stability
            )
        finally:
            await client.show_calibration_target(nx, ny, False)
        if not collected:
            _log.warning("no stable gaze for target (%.2f, %.2f); skipping", nx, ny)
            continue
        avg = _mean_gaze(collected)
        samples.append((avg, (nx, ny)))
        # Measured mean is biometric (eye-appearance-derived); tag it so the redaction
        # filter drops it unless logging.log_gaze=true, and keep it at DEBUG.
        _log.debug(
            "target (%.2f, %.2f): %d samples, mean=(%.3f, %.3f)",
            nx, ny, len(collected), avg.nx, avg.ny,
            extra={GAZE_KEY: True},
        )

    if len(samples) < 3:
        raise RuntimeError(
            f"calibration needs >=3 usable targets, got {len(samples)}"
        )

    model = calib_model.fit(samples)
    path = paths.calibration_file()
    calib_model.save(model, path)
    _log.info("calibration saved to %s (%d targets)", path, len(samples))
    return model


async def _collect_target(
    backend: PerceptionBackend,
    frame_interval: float,
    min_conf: float,
    stability: float,
) -> list[GazePoint]:
    """Collect up to ``_SAMPLES_PER_TARGET`` confident, stable raw gaze points."""
    kept: list[GazePoint] = []
    seen = 0
    while seen < _MAX_FRAMES and len(kept) < _SAMPLES_PER_TARGET:
        seen += 1
        result = await asyncio.to_thread(backend.read)
        await asyncio.sleep(frame_interval)
        if seen <= _SETTLE_FRAMES:
            continue
        g = result.gaze
        if g is None or g.confidence < min_conf:
            kept.clear()  # confidence dropout breaks the stable run; restart
            continue
        if kept and _dist(g, kept[-1]) > stability:
            kept = [g]  # jumped out of the stability radius; restart the run
            continue
        kept.append(g)
    return kept


def _dist(a: GazePoint, b: GazePoint) -> float:
    return math.hypot(a.nx - b.nx, a.ny - b.ny)


def _mean_gaze(points: list[GazePoint]) -> GazePoint:
    n = float(len(points))
    nx = sum(p.nx for p in points) / n
    ny = sum(p.ny for p in points) / n
    conf = sum(p.confidence for p in points) / n
    yaw = sum(p.yaw for p in points) / n
    pitch = sum(p.pitch for p in points) / n
    return GazePoint(nx=nx, ny=ny, confidence=conf, yaw=yaw, pitch=pitch)
