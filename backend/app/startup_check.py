"""Production startup safety validator.

Called from `app.main` before the FastAPI app is constructed. When DEBUG is
False, refuses to start the process if any obvious foot-gun is present:
default SECRET_KEY, weak SECRET_KEY, wildcard CORS, etc.

The point is to make "bring up to production" a hard fail with a loud
error, instead of silently shipping a development config to the public
internet.
"""

from __future__ import annotations

from app.config import settings


# Anything containing one of these substrings is treated as obviously default.
_BAD_SECRET_PATTERNS = (
    "change-me",
    "please-change",
    "please-change-me",
    "please-change-me-in-production",
    "please-change-me-to-a-random-string",
    "default-secret",
    "test-secret",
    "your-secret-here",
    "insecure",
    "example",
)
_MIN_SECRET_LEN = 48


def _looks_default(secret: str) -> bool:
    s = (secret or "").lower()
    return any(p in s for p in _BAD_SECRET_PATTERNS)


def validate_production_safety() -> None:
    """Raise RuntimeError if the current configuration is unsafe for prod.

    Only enforced when DEBUG is False — dev mode is intentionally lenient.
    """
    if settings.DEBUG:
        return

    errors: list[str] = []

    # SECRET_KEY checks
    sk = settings.SECRET_KEY or ""
    if not sk:
        errors.append("SECRET_KEY is empty")
    else:
        if _looks_default(sk):
            errors.append(
                "SECRET_KEY appears to be a default placeholder. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        if len(sk) < _MIN_SECRET_LEN:
            errors.append(
                f"SECRET_KEY is too short ({len(sk)} chars); "
                f"need at least {_MIN_SECRET_LEN}."
            )

    # CORS check
    co = (settings.CORS_ORIGINS or "").strip()
    if co == "*" or co == "":
        errors.append(
            "CORS_ORIGINS must be an explicit list of origins in production "
            "(e.g. \"https://app.example.com\"). Wildcard '*' is not allowed."
        )

    # AI_API_KEY: not enforced when AI_PROVIDER=mock; we trust mock to not
    # need a key. If you switch the provider you must set the key yourself.
    if settings.AI_PROVIDER not in ("mock", "anthropic", "openai"):
        # Unknown provider — soft warn via error, since unrecognized providers
        # would also break runtime.
        errors.append(f"AI_PROVIDER={settings.AI_PROVIDER!r} is not a known value")

    if errors:
        raise RuntimeError(
            "Production safety check failed (DEBUG=false). "
            "Refusing to start with these issues:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\nFix .env / your compose env, then restart."
        )
