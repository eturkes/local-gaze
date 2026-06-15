from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ..types import GazePoint


@dataclass(frozen=True, slots=True)
class CalibrationModel:
    """Affine gaze map raw->screen. coeffs is a 2x3 matrix row-major:

        [a, b, c, d, e, f]  =>  sx = a*nx + b*ny + c ;  sy = d*nx + e*ny + f
    """

    coeffs: list[float]
    created: str

    def apply(self, nx: float, ny: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.coeffs
        return (a * nx + b * ny + c, d * nx + e * ny + f)


def fit(samples: list[tuple[GazePoint, tuple[float, float]]]) -> CalibrationModel:
    """Least-squares affine fit (numpy) of raw gaze (nx,ny) -> screen (sx,sy).

    Requires >= 3 non-degenerate sample pairs (an affine map has 3 DOF per axis). Solves
    both axes at once against the design matrix [nx, ny, 1] via numpy.linalg.lstsq.
    """
    import numpy as np

    if len(samples) < 3:
        raise ValueError("affine calibration needs >= 3 samples")

    a = np.array([[g.nx, g.ny, 1.0] for g, _ in samples], dtype=np.float64)
    b = np.array([[sx, sy] for _, (sx, sy) in samples], dtype=np.float64)
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)  # sol: (3, 2) -> cols = [sx;sy] params
    sx_p = sol[:, 0]  # [a, b, c]
    sy_p = sol[:, 1]  # [d, e, f]
    coeffs = [float(v) for v in (*sx_p, *sy_p)]
    created = datetime.now(UTC).isoformat()
    return CalibrationModel(coeffs=coeffs, created=created)


def load(path: Path) -> CalibrationModel | None:
    """Load a persisted model; return None on missing file or malformed/invalid content."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None
    try:
        data = json.loads(raw)
        coeffs = [float(v) for v in data["coeffs"]]
        created = str(data["created"])
    except (ValueError, TypeError, KeyError):
        return None
    if len(coeffs) != 6:
        return None
    return CalibrationModel(coeffs=coeffs, created=created)


def save(model: CalibrationModel, path: Path) -> None:
    """Atomic 0600 write: temp file in the same dir -> chmod 0600 -> os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"coeffs": model.coeffs, "created": model.created})
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".calib-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
