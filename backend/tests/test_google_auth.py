"""Google OAuth2 helpers.

The consent flow itself needs a browser and a real Google account, so what is
covered here is everything around it: the URL that flow starts from, the token
exchange, and the refresh caching the adapter depends on.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from app.calendars.google_auth import (
    SCOPE,
    GoogleAuthError,
    GoogleCredentials,
    authorization_url,
    code_challenge_for,
    exchange_code,
    make_code_verifier,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def recording_post(response):
    calls = []

    def _post(url, data):
        calls.append((url, data))
        return response

    _post.calls = calls
    return _post


class TestAuthorizationUrl:
    def params(self) -> dict:
        url = authorization_url("cid", "http://127.0.0.1:9000/", "verifier" * 6, "state123")
        return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}

    def test_requests_read_only_access(self):
        """HomeDash never writes, so a bug here must not be able to damage
        a family's schedule."""
        assert self.params()["scope"] == SCOPE
        assert self.params()["scope"].endswith("calendar.readonly")

    def test_asks_for_a_refresh_token(self):
        # Without both of these Google silently returns no refresh token on a
        # repeat authorization, and the panel dies an hour later.
        params = self.params()
        assert params["access_type"] == "offline"
        assert params["prompt"] == "consent"

    def test_uses_pkce(self):
        params = self.params()
        assert params["code_challenge_method"] == "S256"
        assert params["code_challenge"] == code_challenge_for("verifier" * 6)

    def test_carries_state_and_redirect(self):
        params = self.params()
        assert params["state"] == "state123"
        assert params["redirect_uri"] == "http://127.0.0.1:9000/"


class TestPkce:
    def test_challenge_is_unpadded_base64url_sha256(self):
        verifier = "abc123"
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert code_challenge_for(verifier) == expected
        assert "=" not in code_challenge_for(verifier)

    def test_verifiers_are_unique_and_url_safe(self):
        verifiers = {make_code_verifier() for _ in range(20)}
        assert len(verifiers) == 20
        assert all(set(v) <= set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        ) for v in verifiers)


class TestExchange:
    def test_sends_the_verifier_and_returns_tokens(self):
        post = recording_post(FakeResponse(payload={"refresh_token": "rt", "access_token": "at"}))
        tokens = exchange_code("cid", "secret", "code", "http://127.0.0.1:1/", "verifier", post=post)

        assert tokens["refresh_token"] == "rt"
        sent = post.calls[0][1]
        assert sent["code_verifier"] == "verifier"
        assert sent["grant_type"] == "authorization_code"

    def test_invalid_grant_explains_the_seven_day_trap(self):
        """Testing-mode refresh tokens expire after a week, which otherwise
        looks like the panel randomly breaking."""
        post = recording_post(
            FakeResponse(status_code=400, payload={"error": "invalid_grant", "error_description": "Bad Request"})
        )
        with pytest.raises(GoogleAuthError, match="Testing mode"):
            exchange_code("cid", "secret", "code", "http://x/", "v", post=post)

    def test_invalid_client_points_at_the_client_settings(self):
        post = recording_post(FakeResponse(status_code=401, payload={"error": "invalid_client"}))
        with pytest.raises(GoogleAuthError, match="client_id and client_secret"):
            exchange_code("cid", "secret", "code", "http://x/", "v", post=post)

    def test_non_json_error_body_does_not_mask_the_failure(self):
        post = recording_post(FakeResponse(status_code=500, text="<html>oops</html>"))
        with pytest.raises(GoogleAuthError, match="500"):
            exchange_code("cid", "secret", "code", "http://x/", "v", post=post)


class TestCredentials:
    def creds(self, post) -> GoogleCredentials:
        return GoogleCredentials("cid", "secret", "rt", post=post)

    def test_caches_the_access_token(self):
        post = recording_post(FakeResponse(payload={"access_token": "at", "expires_in": 3600}))
        creds = self.creds(post)

        assert creds.access_token(now=1000) == "at"
        assert creds.access_token(now=2000) == "at"
        # A one-minute poll must not mint a token every single minute.
        assert len(post.calls) == 1

    def test_refreshes_before_expiry(self):
        post = recording_post(FakeResponse(payload={"access_token": "at", "expires_in": 3600}))
        creds = self.creds(post)
        creds.access_token(now=1000)

        # Inside the safety margin: a token expiring mid-request would look
        # like a spurious 401 on an otherwise fine poll.
        creds.access_token(now=1000 + 3600 - 30)
        assert len(post.calls) == 2

    def test_invalidate_forces_a_refresh(self):
        post = recording_post(FakeResponse(payload={"access_token": "at", "expires_in": 3600}))
        creds = self.creds(post)
        creds.access_token(now=1000)
        creds.invalidate()
        creds.access_token(now=1001)

        assert len(post.calls) == 2

    def test_missing_fields_are_named(self):
        with pytest.raises(ValueError, match="refresh_token"):
            GoogleCredentials.from_blob({"client_id": "a", "client_secret": "b"}, "Family")

    def test_no_secret_reaches_the_repr(self):
        """A dataclass repr ends up in logs and traceback frames."""
        creds = GoogleCredentials("cid", "sekrit", "refresh-tok")
        creds._access_token = "access-tok"
        rendered = repr(creds)
        assert "cid" in rendered  # the non-secret half stays useful
        for secret in ("sekrit", "refresh-tok", "access-tok"):
            assert secret not in rendered
