from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np


class Camera:
    """V4L2/OpenCV capture (lazy ``cv2``). Frames are BGR ``np.ndarray`` (H,W,3)."""

    def __init__(self, device: str, width: int, height: int) -> None:
        self._device = device
        self._width = width
        self._height = height
        self._cap: Any = None

    def open(self) -> None:
        import cv2

        # Numeric device strings (e.g. "0") open by index; paths open by name.
        target: Any = int(self._device) if self._device.isdigit() else self._device
        cap = cv2.VideoCapture(target)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"failed to open camera {self._device!r}")
        self._cap = cap

    def read(self) -> np.ndarray:
        if self._cap is None:
            raise RuntimeError("camera not open")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"camera {self._device!r} read failed")
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
