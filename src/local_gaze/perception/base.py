from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..types import PerceptionResult

if TYPE_CHECKING:
    from ..config import Config


@runtime_checkable
class PerceptionBackend(Protocol):
    """Structural contract every backend satisfies. Synthetic/mock implement it without
    inheritance (duck typing); ``isinstance(x, PerceptionBackend)`` checks shape at runtime.
    """

    def start(self) -> None:
        """Open camera / compile models (host); no-op for synthetic/mock."""
        ...

    def read(self) -> PerceptionResult:
        """One frame's perception; blocking but bounded."""
        ...

    def stop(self) -> None:
        ...

    @property
    def info(self) -> dict:
        """{backend, device, models, camera} for status/probe."""
        ...


def make_backend(cfg: Config) -> PerceptionBackend:
    """Dispatch on cfg.general.backend. The 'openvino' branch imports the heavy backend
    lazily INSIDE this function so container import / tests never pull openvino+cv2. 'mock'
    is test-only and must be injected directly, so it is rejected here.
    """
    name = cfg.general.backend
    if name == "synthetic":
        from .synthetic import SyntheticBackend

        return SyntheticBackend(cfg)
    if name == "openvino":
        from .openvino_backend import OpenVinoBackend

        return OpenVinoBackend(cfg)
    if name == "mock":
        raise ValueError("backend 'mock' is test-only; inject MockBackend directly")
    raise ValueError(f"unknown backend {name!r}")
