from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Hand(Enum):
    NONE = 0
    LEFT = 1
    RIGHT = 2


class ActionKind(Enum):
    NONE = 0
    FOCUS = 1
    SWITCH = 2


@dataclass(frozen=True, slots=True)
class GazePoint:
    """Raw model gaze output, pre-calibration (model-space normalized)."""

    nx: float
    ny: float
    confidence: float
    yaw: float = 0.0
    pitch: float = 0.0


@dataclass(frozen=True, slots=True)
class HandSample:
    present: bool
    cx: float = 0.5
    cy: float = 0.5
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class PerceptionResult:
    """One frame's perception from a backend."""

    ts: float
    gaze: GazePoint | None
    hand: HandSample
    frame_id: int = 0


@dataclass(frozen=True, slots=True)
class Action:
    """Interpreter output the daemon dispatches."""

    kind: ActionKind
    nx: float = 0.0
    ny: float = 0.0
    direction: int = 0
