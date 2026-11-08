"""Authorization Code with PKCE against `accounts.spotify.com`, as raw HTTP.

PKCE needs no client secret, so the only credential the service holds is the
token set Spotify hands back. The rest of the service sees either a typed
`TokenSet` or one of two errors: `AuthorizationRevoked`, the routine end of a
credential (refresh tokens expire six months after authorization), and
`SpotifyAuthError`, a failure worth trying again later.

Nothing here may leak the authorization code, the verifier or a token: error
messages carry the HTTP status and Spotify's `error` field only, and the
secret fields are kept out of every `repr`.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from urllib.parse import urlencode

import httpx2

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

# The only scope ever requested. The service is read-only against Spotify, so
# adding a scope here is a hard-rule change, not a tweak.
SCOPE = "user-read-currently-playing"


class AuthorizationRevoked(Exception):
    """Spotify answered `invalid_grant`: discard the token set and re-authorize.

    Spotify's guidance is not to retry, so this is kept apart from
    `SpotifyAuthError`, which a caller may retry.
    """


class SpotifyAuthError(Exception):
    """A token request failed in a way that may succeed on a later attempt."""

    def __init__(self, status_code: int, error: str | None) -> None:
        # Only the status and Spotify's error code: the request body holds the
        # code, verifier or refresh token, and must never reach a log line.
        super().__init__(f"Spotify token request failed: HTTP {status_code}, error={error!r}")
        self.status_code = status_code
        self.error = error


def code_challenge(code_verifier: str) -> str:
    """The S256 challenge: base64url of the verifier's SHA-256, unpadded."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class PendingAuthorization:
    """One authorization the owner has started but not yet completed."""

    state: str
    code_verifier: str = field(repr=False)

    @classmethod
    def create(cls) -> Self:
        """Start a fresh authorization with an unguessable state and verifier."""
        # token_urlsafe draws from [A-Za-z0-9_-], a subset of RFC 7636's
        # unreserved set; 64 bytes encode to 86 characters, inside 43-128.
        return cls(state=secrets.token_urlsafe(32), code_verifier=secrets.token_urlsafe(64))

    @property
    def code_challenge(self) -> str:
        return code_challenge(self.code_verifier)


@dataclass(frozen=True)
class TokenSet:
    """The credential Spotify issued, plus when the owner granted it.

    `authorized_at` is the time of the code exchange and is carried through
    every refresh: refreshing does not extend the refresh token's six-month
    lifetime, so this is the only way to know how old the grant really is.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime
    authorized_at: datetime
    scope: str


def authorize_url(client_id: str, redirect_uri: str, pending: PendingAuthorization) -> str:
    """The URL that sends the owner to Spotify's consent screen."""
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": pending.code_challenge,
            "state": pending.state,
            "scope": SCOPE,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def exchange_code(
    client: httpx2.AsyncClient,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> TokenSet:
    """Trade the code from `/callback` for the first token set of a grant."""
    body = await _post_token(
        client,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
    )
    now = datetime.now(UTC)
    refresh_token = body.get("refresh_token")
    if not isinstance(refresh_token, str):
        # A grant without a refresh token would end in an hour, unattended.
        raise SpotifyAuthError(200, "missing_refresh_token")
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=refresh_token,
        expires_at=now + timedelta(seconds=body["expires_in"]),
        authorized_at=now,
        scope=str(body.get("scope", "")),
    )


async def refresh(client: httpx2.AsyncClient, client_id: str, token_set: TokenSet) -> TokenSet:
    """Get a new access token, keeping the grant's refresh token and age."""
    body = await _post_token(
        client,
        {
            "grant_type": "refresh_token",
            "refresh_token": token_set.refresh_token,
            "client_id": client_id,
        },
    )
    now = datetime.now(UTC)
    # Spotify may omit a new refresh token; the documented answer is to keep
    # using the one already held.
    refresh_token = body.get("refresh_token")
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=refresh_token if isinstance(refresh_token, str) else token_set.refresh_token,
        expires_at=now + timedelta(seconds=body["expires_in"]),
        authorized_at=token_set.authorized_at,
        scope=str(body.get("scope", token_set.scope)),
    )


async def _post_token(client: httpx2.AsyncClient, form: dict[str, str]) -> dict[str, Any]:
    """POST `form` to the token endpoint and return a validated success body.

    Transport errors from `httpx2` propagate unchanged, so a caller can tell a
    network fault from an answer Spotify gave. Every error raised here is
    raised `from None`, so no chained exception can carry response text into
    a traceback.
    """
    # Form-encoded with no Authorization header: PKCE has no secret to send.
    response = await client.post(TOKEN_URL, data=form)
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise SpotifyAuthError(response.status_code, None) from None

    if response.status_code != 200:
        error = body.get("error")
        error = error if isinstance(error, str) else None
        if response.status_code == 400 and error == "invalid_grant":
            raise AuthorizationRevoked("Spotify answered invalid_grant") from None
        raise SpotifyAuthError(response.status_code, error) from None

    if not isinstance(body.get("access_token"), str) or not isinstance(
        body.get("expires_in"), int
    ):
        raise SpotifyAuthError(response.status_code, "malformed_token_response") from None
    return body
