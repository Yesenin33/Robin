"""Optional stage reporting for a running scan (no signature or schema change).

The caller wraps `scan()` in `with sink(callback):`; scan() calls `report("stage")` as each stage starts. Thread-local,
so concurrent scans in different threads never see each other's callback. A failing callback never fails a scan."""
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_local = threading.local()

# stage names scan() reports, in order
CONTRACT, MARKET, TRANSFERS, HOLDERS, SCORING = "contract", "market", "transfers", "holders", "scoring"


@contextmanager
def sink(callback: Callable[[str], None]) -> Iterator[None]:
    previous = getattr(_local, "cb", None)
    _local.cb = callback
    try:
        yield
    finally:
        _local.cb = previous


def report(stage: str) -> None:
    cb = getattr(_local, "cb", None)
    if cb is not None:
        try:
            cb(stage)
        except Exception:  # noqa: BLE001 — progress is decoration
            pass
