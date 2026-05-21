"""Redis-backed sliding-window rate limiter.

Why Redis (not in-process)?
  - Backend runs N replicas behind nginx. An in-process counter would let an
    attacker round-robin replicas to multiply their effective limit.
  - Failure mode is explicit: if Redis is unreachable we FAIL OPEN (allow the
    request and log) rather than locking everyone out. Over-blocking on
    /auth/login would be a self-inflicted DoS.

Algorithm: fixed-window counter using INCR + EXPIRE. Cheap, atomic via
pipeline, and accurate enough for abuse prevention (we don't need
millisecond-level fairness for human-facing endpoints).
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from fastapi import HTTPException, Request

from app.config import settings

log = logging.getLogger("ratelimit")

# Module-level client; lazily created so test suites that mock REDIS_URL still work.
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        import redis as redis_lib
        _client = redis_lib.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
        # ping once at module init time? no — we want lazy init so test envs
        # without a real redis don't blow up at import.
    except Exception as e:
        log.warning("rate limiter could not init redis client: %s", e)
        _client = None
    return _client


def client_ip(request: Request) -> str:
    """X-Forwarded-For aware. Trusts the first hop because we sit behind
    Nginx in our deployments. If you sit behind multiple proxies you should
    parse this differently or use a proper proxy headers middleware."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def hit(key: str, limit: int, window_seconds: int) -> Tuple[bool, int, int]:
    """Increment a counter and tell the caller whether the limit is exceeded.

    Returns:
        (allowed, current_count, retry_after_seconds)

    On Redis failure: (True, 0, 0) — fail-open.
    """
    if settings.RATE_LIMIT_ENABLED is False:
        return True, 0, 0

    cli = _get_client()
    if cli is None:
        return True, 0, 0

    full_key = f"rl:{key}"
    try:
        pipe = cli.pipeline()
        pipe.incr(full_key, 1)
        pipe.expire(full_key, window_seconds, nx=True)  # only set TTL on first INCR
        pipe.ttl(full_key)
        count, _, ttl = pipe.execute()
        # If TTL came back as -1 (no expire set) just set it now.
        if ttl is None or ttl < 0:
            cli.expire(full_key, window_seconds)
            ttl = window_seconds
        if int(count) > limit:
            return False, int(count), int(ttl)
        return True, int(count), int(ttl)
    except Exception as e:
        log.warning("rate limiter degraded (fail-open): %s", e)
        return True, 0, 0


def enforce(
    *,
    request: Request,
    bucket: str,
    limit: int,
    window_seconds: int,
    extra_key: Optional[str] = None,
) -> None:
    """Raise 429 if the caller exceeds the limit.

    `bucket` is a short identifier like "login_ip" / "register_ip" /
    "lookup_ip" / "login_username". `extra_key` is appended to the bucket
    so e.g. login can be limited per-IP AND per-username separately.
    """
    ip = client_ip(request)
    key_suffix = extra_key if extra_key else ip
    key = f"{bucket}:{key_suffix}"
    allowed, count, retry_after = hit(key, limit, window_seconds)
    if not allowed:
        log.info("rate-limited bucket=%s key=%s count=%d", bucket, key_suffix, count)
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(max(retry_after, 1))},
        )


# Helpers used by both auth.py and the audit-log writer
def login_ip_key() -> str: return "login_ip"
def login_user_key() -> str: return "login_user"
def register_ip_key() -> str: return "register_ip"
def lookup_ip_key() -> str: return "lookup_ip"
