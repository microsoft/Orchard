"""Capability tokens for sandbox service endpoints.

A service endpoint URL is handed to programs that cannot attach an
``X-API-Key`` header — an OpenEnv ``EnvClient`` opens a raw WebSocket, a
browser follows a link, a curl one-liner is pasted into a notebook. The URL
therefore has to authenticate itself.

A token is a signed, expiring capability naming exactly one ``(sandbox, port)``
pair::

    base64url(payload) "." base64url(HMAC-SHA256(secret, payload))

where ``payload`` is ``"<sandbox_id>:<port>:<expires_at>"``. Verification is
pure computation, so any orchestrator replica can validate a token minted by
any other without shared state. Possession of a valid token grants access to
that one port on that one sandbox, until it expires or the port is revoked.

Signing key resolution, in order:

1. ``SERVICE_TOKEN_SECRET`` — set this in multi-replica deployments.
2. A digest of the configured API keys, which every replica already shares.
3. A per-process random key, with a warning. Tokens then stop working when a
   replica restarts or a request lands on a different one.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time

from orchard_env.orchestrator.settings import settings

logger = logging.getLogger(__name__)

_SEPARATOR = "."
_PROCESS_SECRET: bytes | None = None


class ServiceTokenError(Exception):
    """Raised when a token is malformed, forged, or expired."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signing_secret() -> bytes:
    """Return the HMAC key, preferring the explicitly configured one."""
    configured = settings.service_token_secret
    if configured:
        return configured.encode("utf-8")

    api_keys = settings.get_api_keys_set()
    if api_keys:
        # Deterministic across replicas: they are configured with the same keys.
        # Domain-separated so the derived value is not itself a usable API key.
        joined = "\x00".join(sorted(api_keys))
        return hashlib.sha256(f"orchard-service-token\x00{joined}".encode()).digest()

    global _PROCESS_SECRET
    if _PROCESS_SECRET is None:
        _PROCESS_SECRET = secrets.token_bytes(32)
        logger.warning(
            "No SERVICE_TOKEN_SECRET and no API keys configured; service tokens "
            "are signed with a per-process key. They will not validate across "
            "orchestrator replicas or restarts. Set SERVICE_TOKEN_SECRET."
        )
    return _PROCESS_SECRET


def mint_token(
    sandbox_id: str, port: int, ttl_seconds: int | None = None
) -> tuple[str, float]:
    """Mint a capability token for one ``(sandbox_id, port)`` pair.

    Args:
        sandbox_id: Sandbox the token grants access to.
        port: Sandbox port the token grants access to.
        ttl_seconds: Lifetime in seconds. Defaults to
            ``settings.service_token_ttl_seconds``.

    Returns:
        A ``(token, expires_at)`` pair, where ``expires_at`` is a Unix timestamp.
    """
    if ttl_seconds is None:
        ttl_seconds = settings.service_token_ttl_seconds
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    expires_at = int(time.time()) + int(ttl_seconds)
    payload = f"{sandbox_id}:{port}:{expires_at}".encode()
    signature = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
    token = f"{_b64encode(payload)}{_SEPARATOR}{_b64encode(signature)}"
    return token, float(expires_at)


def verify_token(token: str) -> tuple[str, int]:
    """Verify a capability token and return the ``(sandbox_id, port)`` it names.

    Raises:
        ServiceTokenError: If the token is malformed, has an invalid signature,
            or has expired.
    """
    if not token or _SEPARATOR not in token:
        raise ServiceTokenError("Malformed service token")

    encoded_payload, _, encoded_signature = token.partition(_SEPARATOR)
    try:
        payload = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
    except Exception as exc:
        raise ServiceTokenError("Malformed service token") from exc

    expected = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
    # Constant-time comparison: a fast reject would leak the signature bytewise.
    if not hmac.compare_digest(signature, expected):
        raise ServiceTokenError("Invalid service token signature")

    try:
        sandbox_id, port_text, expires_text = payload.decode("utf-8").rsplit(":", 2)
        port = int(port_text)
        expires_at = int(expires_text)
    except Exception as exc:
        raise ServiceTokenError("Malformed service token payload") from exc

    if not sandbox_id:
        raise ServiceTokenError("Malformed service token payload")

    if time.time() > expires_at:
        raise ServiceTokenError("Service token has expired")

    return sandbox_id, port
