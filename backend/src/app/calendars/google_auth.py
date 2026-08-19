"""OAuth2 for Google Calendar, read-only.

Google removed the out-of-band flow in 2022 and rejects non-loopback plaintext
redirect URIs, so a LAN address like http://192.168.1.5:8000 cannot be used.
What is left for a self-hosted app is the native-app loopback flow: register a
**Desktop app** client, run the consent flow once on a machine with a browser,
and keep the refresh token it returns.

That is why authorization is a one-time CLI (`homedash-google-auth`) rather
than something the panel does: the panel has no browser session and no public
hostname, but it can hold a refresh token indefinitely.

Only `calendar.readonly` is requested. HomeDash never writes to a calendar, so
a bug here cannot damage a family's schedule.
"""

import base64
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# Refresh a little early: a token that expires mid-request would surface as a
# spurious 401 on a poll that was otherwise fine.
EXPIRY_MARGIN_SECONDS = 60


def make_code_verifier() -> str:
    """A PKCE verifier. Required for native-app clients."""
    return base64.urlsafe_b64encode(os.urandom(64)).decode("ascii").rstrip("=")


def code_challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorization_url(client_id: str, redirect_uri: str, code_verifier: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": code_challenge_for(code_verifier),
        "code_challenge_method": "S256",
        # Without both of these Google returns no refresh token on a repeat
        # authorization, and the setup appears to succeed while leaving the
        # panel unable to sync once the first access token expires.
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    post=None,
) -> dict[str, Any]:
    """Trade an authorization code for tokens."""
    return _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
        },
        post=post,
    )


def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str, post=None
) -> dict[str, Any]:
    return _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        post=post,
    )


def _token_request(data: dict[str, str], post=None) -> dict[str, Any]:
    post = post or _default_post
    response = post(TOKEN_ENDPOINT, data)
    if response.status_code != 200:
        raise GoogleAuthError(_describe_failure(response))
    return response.json()


def _default_post(url: str, data: dict[str, str]):
    return httpx.post(url, data=data, timeout=30.0)


def _describe_failure(response) -> str:
    """Turn Google's error body into something actionable.

    The generic "invalid_grant" is the one people actually hit, and it means
    something specific and fixable.
    """
    try:
        body = response.json()
    except Exception:
        body = {}
    error = body.get("error", "")
    description = body.get("error_description", response.text[:200])
    hint = ""
    if error == "invalid_grant":
        hint = (
            "\nThis usually means the refresh token was revoked, the Google "
            "account's password changed, or the OAuth consent screen is still "
            "in Testing mode - test-mode refresh tokens expire after 7 days. "
            "Publish the app or re-run `homedash-google-auth`."
        )
    elif error == "invalid_client":
        hint = "\nCheck client_id and client_secret against the Desktop app client in Google Cloud."
    return f"Google token request failed ({response.status_code} {error}): {description}{hint}"


class GoogleAuthError(RuntimeError):
    pass


@dataclass
class GoogleCredentials:
    """Holds a refresh token and mints access tokens from it.

    Access tokens are cached in memory only. Persisting them would buy
    nothing - they last an hour, and the refresh token in the environment
    regenerates them - while adding a second secret to the SQLite volume.
    """

    client_id: str
    # repr=False on every secret: a dataclass repr reaches logs and traceback
    # frames, and a leaked refresh token is a standing grant on the calendar.
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    post: Any = None
    _access_token: str | None = field(default=None, repr=False)
    _expires_at: float = field(default=0.0, repr=False)

    def access_token(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        if self._access_token and now < self._expires_at - EXPIRY_MARGIN_SECONDS:
            return self._access_token
        payload = refresh_access_token(
            self.client_id, self.client_secret, self.refresh_token, post=self.post
        )
        self._access_token = payload["access_token"]
        self._expires_at = now + float(payload.get("expires_in", 3600))
        return self._access_token

    def invalidate(self) -> None:
        """Drop the cached token so the next call re-refreshes.

        Used when the API answers 401 despite a token we believed current -
        a revoked grant or a clock skew, both of which a refresh resolves.
        """
        self._access_token = None
        self._expires_at = 0.0

    @classmethod
    def from_blob(cls, blob: dict[str, Any], calendar_name: str) -> "GoogleCredentials":
        missing = [
            key for key in ("client_id", "client_secret", "refresh_token") if not blob.get(key)
        ]
        if missing:
            raise ValueError(
                f"Google credentials for calendar {calendar_name!r} are missing "
                f"{', '.join(missing)}. Run `uv run homedash-google-auth` to obtain them."
            )
        return cls(
            client_id=blob["client_id"],
            client_secret=blob["client_secret"],
            refresh_token=blob["refresh_token"],
        )
