from __future__ import annotations

import dataclasses

import pytest

from local_gaze.types import (
    Action,
    ActionKind,
    GazePoint,
    Hand,
    HandSample,
    PerceptionResult,
)


def test_enum_values() -> None:
    assert (Hand.NONE.value, Hand.LEFT.value, Hand.RIGHT.value) == (0, 1, 2)
    assert (ActionKind.NONE.value, ActionKind.FOCUS.value, ActionKind.SWITCH.value) == (0, 1, 2)


def test_defaults() -> None:
    g = GazePoint(nx=0.1, ny=0.2, confidence=0.5)
    assert (g.yaw, g.pitch) == (0.0, 0.0)
    h = HandSample(present=False)
    assert (h.cx, h.cy, h.confidence) == (0.5, 0.5, 0.0)
    r = PerceptionResult(ts=1.0, gaze=g, hand=h)
    assert r.frame_id == 0
    a = Action(kind=ActionKind.NONE)
    assert (a.nx, a.ny, a.direction) == (0.0, 0.0, 0)


def test_frozen_immutable() -> None:
    a = Action(kind=ActionKind.FOCUS, nx=0.3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.nx = 0.9  # type: ignore[misc]


def test_perception_result_holds_none_gaze() -> None:
    r = PerceptionResult(ts=2.0, gaze=None, hand=HandSample(present=True))
    assert r.gaze is None and r.hand.present
