"""Timing logs for finding where requests spend their time.

Every line carries the plot it belongs to, so the output of plots processed in
parallel stays readable:

    12:01:02.345 DEBUG [plot 2] sh POST /api/v1/statistics   1834 ms  status=200 kb=412

Level is set with LOG_LEVEL (default DEBUG); INFO keeps only the per-plot and
per-request summaries.
"""

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar

log = logging.getLogger("app.timing")

# which plot the current thread is working on; set at the start of each plot
plot_tag: ContextVar[str] = ContextVar("plot_tag", default="-")


def _fmt(info: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in info.items() if v is not None)


def emit(what: str, ms: float | None = None, level: int = logging.DEBUG, **info) -> None:
    took = f"{ms:7.0f} ms" if ms is not None else " " * 10
    log.log(level, "[%s] %-30s %s  %s", plot_tag.get(), what, took, _fmt(info))


@contextmanager
def timed(what: str, level: int = logging.DEBUG, **info):
    """Log how long the block took. Yields a dict the block can add fields to."""
    extra: dict = {}
    start = time.perf_counter()
    try:
        yield extra
    finally:
        emit(what, (time.perf_counter() - start) * 1000, level, **info, **extra)


def setup(level: str) -> None:
    """Send app.* loggers to stderr (uvicorn only configures its own loggers)."""
    app_log = logging.getLogger("app")
    app_log.setLevel(level.upper())
    if not any(getattr(h, "_app_timing", False) for h in app_log.handlers):
        handler = logging.StreamHandler()
        handler._app_timing = True  # type: ignore[attr-defined]
        handler.setFormatter(
            logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s", "%H:%M:%S")
        )
        app_log.addHandler(handler)
    app_log.propagate = False
