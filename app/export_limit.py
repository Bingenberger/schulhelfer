"""One PDF slot in the declared single web process; no queued exporters."""
from contextlib import contextmanager
import threading
from fastapi import HTTPException

_slot = threading.BoundedSemaphore(1)


def pdf_slot():
    if not _slot.acquire(blocking=False):
        raise HTTPException(503, "PDF-Export ist belegt. Bitte später erneut versuchen.", headers={"Retry-After": "5"})
    try:
        yield
    finally:
        _slot.release()
