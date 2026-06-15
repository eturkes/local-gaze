from __future__ import annotations

from local_gaze.config import default_config
from local_gaze.perception.base import PerceptionBackend, make_backend
from local_gaze.perception.synthetic import SyntheticBackend
from local_gaze.types import GazePoint, HandSample, PerceptionResult


def _read_n(b: SyntheticBackend, n: int) -> list[PerceptionResult]:
    return [b.read() for _ in range(n)]


def test_satisfies_protocol() -> None:
    b = SyntheticBackend(default_config())
    assert isinstance(b, PerceptionBackend)


def test_make_backend_returns_synthetic() -> None:
    b = make_backend(default_config())
    assert isinstance(b, SyntheticBackend)
    assert isinstance(b, PerceptionBackend)


def test_deterministic_stream() -> None:
    cfg = default_config()
    a = _read_n(SyntheticBackend(cfg), 50)
    b = _read_n(SyntheticBackend(cfg), 50)
    assert a == b  # frozen dataclasses compare by value -> byte-for-byte reproducible


def test_ts_derived_from_frame_counter() -> None:
    cfg = default_config()
    b = SyntheticBackend(cfg)
    fps = float(cfg.general.fps)
    frames = _read_n(b, 10)
    for i, r in enumerate(frames):
        assert r.frame_id == i
        assert abs(r.ts - i / fps) < 1e-12


def test_gaze_in_unit_square_and_hand_present() -> None:
    b = SyntheticBackend(default_config())
    for r in _read_n(b, 120):
        assert r.gaze is not None
        assert 0.0 <= r.gaze.nx <= 1.0 and 0.0 <= r.gaze.ny <= 1.0
        assert r.hand.present  # synthetic hand is always present (flicks + neutral)


def test_scripted_replay_is_cycled_with_rewritten_ts() -> None:
    cfg = default_config()
    script = [
        PerceptionResult(
            ts=999.0,  # source ts is ignored; backend rewrites from its frame counter
            gaze=GazePoint(nx=0.1, ny=0.2, confidence=0.9),
            hand=HandSample(present=True, cx=0.3),
        ),
        PerceptionResult(
            ts=999.0,
            gaze=GazePoint(nx=0.4, ny=0.5, confidence=0.9),
            hand=HandSample(present=True, cx=0.7),
        ),
    ]
    b = SyntheticBackend(cfg, script=script)
    out = _read_n(b, 4)
    fps = float(cfg.general.fps)
    # cycles through the script in order
    assert out[0].gaze == script[0].gaze and out[1].gaze == script[1].gaze
    assert out[2].gaze == script[0].gaze and out[3].gaze == script[1].gaze
    # ts is rewritten deterministically, never the source 999.0
    assert [r.ts for r in out] == [i / fps for i in range(4)]


def test_info_shape() -> None:
    b = SyntheticBackend(default_config())
    info = b.info
    assert info["backend"] == "synthetic"
    assert set(info) >= {"backend", "device", "models", "camera"}


def test_make_backend_rejects_mock() -> None:
    import dataclasses as dc

    cfg = default_config()
    bad = dc.replace(cfg, general=dc.replace(cfg.general, backend="mock"))
    import pytest

    with pytest.raises(ValueError):
        make_backend(bad)
