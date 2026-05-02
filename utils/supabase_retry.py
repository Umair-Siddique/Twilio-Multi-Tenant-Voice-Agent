"""
Small resilience helpers around Supabase/PostgREST calls.

We occasionally see transient disconnects from the underlying httpx/httpcore
transport (e.g. RemoteProtocolError: Server disconnected). These should not
require a manual browser refresh; a short retry typically succeeds.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Optional


def _is_transient_disconnect(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    if "server disconnected" in msg:
        return True

    # Prefer type checks when available.
    try:
        import httpx  # type: ignore

        if isinstance(exc, httpx.RemoteProtocolError):
            return True
    except Exception:
        pass

    try:
        import httpcore  # type: ignore

        if isinstance(exc, httpcore.RemoteProtocolError):
            return True
    except Exception:
        pass

    return False


def supabase_call_with_retry(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay_s: float = 0.15,
    max_delay_s: float = 1.25,
    jitter_s: float = 0.05,
) -> Any:
    """
    Run a Supabase call and retry on transient disconnects.

    Intended usage:
      supabase_call_with_retry(lambda: supabase.table("x").select("*").execute())
    """
    last_exc: Optional[BaseException] = None
    attempts = max(int(attempts or 1), 1)

    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not _is_transient_disconnect(exc) or i >= attempts - 1:
                raise

            delay = min(max_delay_s, base_delay_s * (2**i))
            delay = max(0.0, delay + random.uniform(0.0, jitter_s))
            time.sleep(delay)

    # Should be unreachable, but keep mypy/happy path sane.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("supabase_call_with_retry failed without exception")

