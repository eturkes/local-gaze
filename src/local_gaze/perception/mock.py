from __future__ import annotations

from ..types import HandSample, PerceptionResult


class MockBackend:
    """Test-injected backend: yields a fixed frame list in order, then idle NONE frames.

    Structurally satisfies PerceptionBackend. After the scripted frames are exhausted,
    read() returns hand-absent / no-gaze results with a monotonically advancing frame_id
    and the last frame's ts (or 0.0 if empty), so a daemon loop never blocks or repeats.
    """

    def __init__(self, frames: list[PerceptionResult]) -> None:
        self._frames = list(frames)
        self._idx = 0
        self._started = False
        self._last_ts = self._frames[-1].ts if self._frames else 0.0

    def start(self) -> None:
        self._started = True

    def read(self) -> PerceptionResult:
        if self._idx < len(self._frames):
            frame = self._frames[self._idx]
            self._idx += 1
            self._last_ts = frame.ts
            return frame
        self._idx += 1
        return PerceptionResult(
            ts=self._last_ts,
            gaze=None,
            hand=HandSample(present=False),
            frame_id=self._idx - 1,
        )

    def stop(self) -> None:
        self._started = False

    @property
    def info(self) -> dict:
        return {
            "backend": "mock",
            "device": "none",
            "models": {},
            "camera": "none",
            "frames": len(self._frames),
        }
