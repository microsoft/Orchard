"""Unit tests for sandbox service capability tokens.

The token *is* the credential in a service URL, so these tests concentrate on
the ways a token could wrongly be accepted: a forged signature, a tampered
payload, a stale expiry, or a token minted for a different sandbox or port.
"""

import time
from unittest.mock import patch

import pytest

from orchard_env.orchestrator import service_tokens
from orchard_env.orchestrator.service_tokens import (
    ServiceTokenError,
    mint_token,
    verify_token,
)


@pytest.fixture(autouse=True)
def fixed_secret():
    """Pin the signing key so tests do not depend on ambient configuration."""
    with patch.object(
        service_tokens.settings, "service_token_secret", "test-secret-key"
    ):
        yield


class TestRoundTrip:
    def test_mint_and_verify_returns_sandbox_and_port(self):
        token, expires_at = mint_token("sandbox-abc", 8000)
        assert verify_token(token) == ("sandbox-abc", 8000)
        assert expires_at > time.time()

    def test_token_has_two_base64url_segments(self):
        token, _ = mint_token("sandbox-abc", 8000)
        payload, separator, signature = token.partition(".")
        assert separator == "."
        assert payload and signature
        # base64url alphabet only: safe to embed in a URL path unescaped.
        assert all(c.isalnum() or c in "-_" for c in payload + signature)

    def test_sandbox_ids_containing_colons_survive(self):
        # The payload is colon-delimited and parsed right-to-left, so a colon
        # inside the sandbox id must not shift the port or expiry fields.
        token, _ = mint_token("weird:id:with:colons", 8000)
        assert verify_token(token) == ("weird:id:with:colons", 8000)

    def test_explicit_ttl_is_honoured(self):
        _token, expires_at = mint_token("sandbox-abc", 8000, ttl_seconds=120)
        assert 110 < expires_at - time.time() <= 120

    def test_non_positive_ttl_rejected(self):
        with pytest.raises(ValueError):
            mint_token("sandbox-abc", 8000, ttl_seconds=0)


class TestRejection:
    def test_expired_token_rejected(self):
        token, _ = mint_token("sandbox-abc", 8000, ttl_seconds=1)
        future = time.time() + 3600
        with patch(
            "orchard_env.orchestrator.service_tokens.time.time", return_value=future
        ):
            with pytest.raises(ServiceTokenError, match="expired"):
                verify_token(token)

    def test_tampered_payload_rejected(self):
        token, _ = mint_token("sandbox-abc", 8000)
        payload, _, signature = token.partition(".")
        forged_payload, _ = mint_token("sandbox-victim", 9999)
        tampered = f"{forged_payload.partition('.')[0]}.{signature}"
        assert tampered != token
        with pytest.raises(ServiceTokenError, match="signature"):
            verify_token(tampered)

    def test_token_signed_with_another_secret_rejected(self):
        with patch.object(service_tokens.settings, "service_token_secret", "other-key"):
            token, _ = mint_token("sandbox-abc", 8000)
        with pytest.raises(ServiceTokenError, match="signature"):
            verify_token(token)

    @pytest.mark.parametrize(
        "bad", ["", "no-separator", ".", "abc.", ".abc", "!!!.???"]
    )
    def test_malformed_tokens_rejected(self, bad):
        with pytest.raises(ServiceTokenError):
            verify_token(bad)

    def test_empty_sandbox_id_rejected(self):
        # Guards against a token whose payload decodes but names nothing.
        token, _ = mint_token("", 8000)
        with pytest.raises(ServiceTokenError):
            verify_token(token)


class TestSecretDerivation:
    def test_api_keys_derive_a_stable_secret(self):
        """Replicas sharing API keys must validate each other's tokens."""
        with patch.object(service_tokens.settings, "service_token_secret", None):
            with patch.object(service_tokens.settings, "api_keys", "k1,k2"):
                token, _ = mint_token("sandbox-abc", 8000)
                assert verify_token(token) == ("sandbox-abc", 8000)

    def test_derived_secret_is_not_the_api_key_itself(self):
        with patch.object(service_tokens.settings, "service_token_secret", None):
            with patch.object(service_tokens.settings, "api_keys", "k1"):
                derived = service_tokens._signing_secret()
        assert derived != b"k1"

    def test_key_order_does_not_change_the_secret(self):
        with patch.object(service_tokens.settings, "service_token_secret", None):
            with patch.object(service_tokens.settings, "api_keys", "a,b"):
                first = service_tokens._signing_secret()
            with patch.object(service_tokens.settings, "api_keys", "b,a"):
                second = service_tokens._signing_secret()
        assert first == second

    def test_different_api_keys_produce_different_secrets(self):
        with patch.object(service_tokens.settings, "service_token_secret", None):
            with patch.object(service_tokens.settings, "api_keys", "a"):
                first = service_tokens._signing_secret()
            with patch.object(service_tokens.settings, "api_keys", "b"):
                second = service_tokens._signing_secret()
        assert first != second

    def test_process_secret_used_as_last_resort(self):
        service_tokens._PROCESS_SECRET = None
        with patch.object(service_tokens.settings, "service_token_secret", None):
            with patch.object(service_tokens.settings, "api_keys", None):
                token, _ = mint_token("sandbox-abc", 8000)
                assert verify_token(token) == ("sandbox-abc", 8000)
        service_tokens._PROCESS_SECRET = None
