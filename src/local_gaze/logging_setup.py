from __future__ import annotations

import logging
from collections.abc import Callable

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: Records carrying ``extra={GAZE_KEY: True}`` are gaze coords/landmarks; they are
#: emitted only when ``log_gaze`` is enabled and dropped by the redaction filter otherwise.
GAZE_KEY = "gaze"


def redact_gaze(enabled: bool) -> Callable[[logging.LogRecord], bool]:
    """Return a logging filter; when ``enabled`` is False it drops gaze-tagged records."""

    def _filter(record: logging.LogRecord) -> bool:
        return enabled or not getattr(record, GAZE_KEY, False)

    return _filter


def configure(level: str, log_gaze: bool) -> None:
    """Configure the root logger: stream handler, fixed format, gaze redaction filter."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FMT))
    handler.addFilter(redact_gaze(log_gaze))

    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
