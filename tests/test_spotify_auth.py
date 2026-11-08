"""PKCE and the two token calls, against a mocked `accounts.spotify.com`.

No test here touches the network or needs an account: every POST goes to an
`httpx2.MockTransport` that records the request and returns a canned answer.
The secret-free error messages matter as much as the happy path, because an
exception message is one `logger.exception` away from the pm2 log.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from spotify_to_led.spotify.auth import (
    SCOPE,
    TOKEN_URL,
    AuthorizationRevoked,
    PendingAuthorization,
    SpotifyAuthError,
    TokenSet,
    authorize_url,
    code_challenge,
    exchange_code,
    refresh,
)

A_CLIENT_ID = "0123456789abcdef0123456789abcdef"
A_REDIRECT_URI = "https://led.example.com/callback"
A_CODE = "AQD-the-authorization-code"
A_VERIFIER = "the-code-verifier-" + "v" * 40
AN_ACCESS_TOKEN = "BQ-the-access-token"
A_REFRESH_TOKEN = "AQ-the-refresh-token"

# RFC 7636 Appendix B.
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

Handler = Callable[[httpx2.Request], httpx2.Response]


def mock_client(handler: Handler, requests: list[httpx2.Request]) -> httpx2.AsyncClient:
    """A client whose every request is recorded in `requests`, then answered."""

    def record(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(record))


def answer(status_code: int, body: object) -> Handler:
    return lambda request: httpx2.Response(status_code, json=body)


def form_of(request: httpx2.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def a_token_set(**overrides: object) -> TokenSet:
    values: dict[str, object] = {
        "access_token": AN_ACCESS_TOKEN,
        "refresh_token": A_REFRESH_TOKEN,
        "expires_at": datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        "authorized_at": datetime(2026, 5, 1, 9, 30, tzinfo=UTC),
        "scope": SCOPE,
    }
    values.update(overrides)
    return TokenSet(**values)  # type: ignore[arg-type]


def test_the_challenge_matches_the_rfc_7636_test_vector() -> None:
    assert code_challenge(RFC_VERIFIER) == RFC_CHALLENGE


def test_a_pending_authorization_has_a_valid_verifier_and_a_fresh_state() -> None:
    first = PendingAuthorization.create()
    second = PendingAuthorization.create()

    unreserved = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert 43 <= len(first.code_verifier) <= 128
    assert set(first.code_verifier) <= unreserved
    assert first.state != second.state
    assert first.code_verifier != second.code_verifier
    # The verifier is a secret until the exchange; a stray log of the object
    # must not print it.
    assert first.code_verifier not in repr(first)


def test_the_authorize_url_carries_exactly_the_seven_parameters() -> None:
    pending = PendingAuthorization(state="the-state", code_verifier=RFC_VERIFIER)

    url = urlsplit(authorize_url(A_CLIENT_ID, A_REDIRECT_URI, pending))
    query = parse_qs(url.query)

    assert (url.scheme, url.netloc, url.path) == ("https", "accounts.spotify.com", "/authorize")
    assert query == {
        "client_id": [A_CLIENT_ID],
        "response_type": ["code"],
        "redirect_uri": [A_REDIRECT_URI],
        "code_challenge_method": ["S256"],
        "code_challenge": [RFC_CHALLENGE],
        "state": ["the-state"],
        "scope": ["user-read-currently-playing"],
    }
    # Read-only is a hard rule: one scope, and never a write one.
    assert SCOPE == "user-read-currently-playing"


async def test_the_code_exchange_sends_a_form_body_and_no_authorization_header() -> None:
    requests: list[httpx2.Request] = []
    client = mock_client(
        answer(
            200,
            {
                "access_token": AN_ACCESS_TOKEN,
                "token_type": "Bearer",
                "scope": SCOPE,
                "expires_in": 3600,
                "refresh_token": A_REFRESH_TOKEN,
            },
        ),
        requests,
    )
    before = datetime.now(UTC)

    token_set = await exchange_code(client, A_CLIENT_ID, A_REDIRECT_URI, A_CODE, A_VERIFIER)

    after = datetime.now(UTC)
    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == TOKEN_URL
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    # PKCE has no secret, so there is nothing to put in a Basic header.
    assert "authorization" not in request.headers
    assert form_of(request) == {
        "grant_type": "authorization_code",
        "code": A_CODE,
        "redirect_uri": A_REDIRECT_URI,
        "client_id": A_CLIENT_ID,
        "code_verifier": A_VERIFIER,
    }
    assert token_set.access_token == AN_ACCESS_TOKEN
    assert token_set.refresh_token == A_REFRESH_TOKEN
    assert token_set.scope == SCOPE
    assert before <= token_set.authorized_at <= after
    assert token_set.expires_at == token_set.authorized_at + timedelta(seconds=3600)


async def test_a_refresh_sends_a_form_body_and_no_authorization_header() -> None:
    requests: list[httpx2.Request] = []
    client = mock_client(
        answer(
            200,
            {
                "access_token": "BQ-the-new-access-token",
                "token_type": "Bearer",
                "scope": SCOPE,
                "expires_in": 3600,
                "refresh_token": "AQ-the-new-refresh-token",
            },
        ),
        requests,
    )
    old = a_token_set()
    before = datetime.now(UTC)

    new = await refresh(client, A_CLIENT_ID, old)

    after = datetime.now(UTC)
    [request] = requests
    assert str(request.url) == TOKEN_URL
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert "authorization" not in request.headers
    assert form_of(request) == {
        "grant_type": "refresh_token",
        "refresh_token": A_REFRESH_TOKEN,
        "client_id": A_CLIENT_ID,
    }
    assert new.access_token == "BQ-the-new-access-token"
    assert new.refresh_token == "AQ-the-new-refresh-token"
    assert before + timedelta(seconds=3600) <= new.expires_at <= after + timedelta(seconds=3600)


async def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one_and_the_grant_age() -> None:
    # Spotify may leave the refresh token out; the old one stays valid. The
    # grant time must survive too, because refreshing does not extend the
    # six-month lifetime it is the only record of.
    requests: list[httpx2.Request] = []
    client = mock_client(
        answer(200, {"access_token": "BQ-the-new-access-token", "expires_in": 3600}), requests
    )
    old = a_token_set()

    new = await refresh(client, A_CLIENT_ID, old)

    assert new.refresh_token == A_REFRESH_TOKEN
    assert new.authorized_at == old.authorized_at
    assert new.scope == old.scope


async def test_invalid_grant_on_refresh_raises_authorization_revoked() -> None:
    client = mock_client(
        answer(400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}), []
    )

    with pytest.raises(AuthorizationRevoked):
        await refresh(client, A_CLIENT_ID, a_token_set())


async def test_invalid_grant_on_the_exchange_raises_authorization_revoked() -> None:
    client = mock_client(answer(400, {"error": "invalid_grant"}), [])

    with pytest.raises(AuthorizationRevoked):
        await exchange_code(client, A_CLIENT_ID, A_REDIRECT_URI, A_CODE, A_VERIFIER)


@pytest.mark.parametrize(
    ("status_code", "body", "error"),
    [
        (400, {"error": "invalid_request"}, "invalid_request"),
        (401, {"error": "invalid_client"}, "invalid_client"),
        (500, {"error": "server_error"}, "server_error"),
        (503, "Service Unavailable", None),
    ],
)
async def test_other_failures_raise_the_retryable_error_with_status_and_error_only(
    status_code: int, body: object, error: str | None
) -> None:
    client = mock_client(answer(status_code, body), [])

    with pytest.raises(SpotifyAuthError) as raised:
        await refresh(client, A_CLIENT_ID, a_token_set())

    assert not isinstance(raised.value, AuthorizationRevoked)
    assert raised.value.status_code == status_code
    assert raised.value.error == error


async def test_a_non_json_failure_raises_the_retryable_error() -> None:
    client = mock_client(lambda request: httpx2.Response(502, text="<html>Bad Gateway</html>"), [])

    with pytest.raises(SpotifyAuthError) as raised:
        await exchange_code(client, A_CLIENT_ID, A_REDIRECT_URI, A_CODE, A_VERIFIER)

    assert raised.value.status_code == 502
    assert raised.value.error is None


@pytest.mark.parametrize("status_code", [400, 500])
async def test_error_messages_carry_no_code_verifier_or_token(status_code: int) -> None:
    # The response deliberately echoes every secret, so a message built from
    # the request or the response text would be caught here.
    def echo(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code,
            json={"error": "invalid_request", "error_description": request.content.decode()},
        )

    secrets_sent = (A_CODE, A_VERIFIER, AN_ACCESS_TOKEN, A_REFRESH_TOKEN)

    with pytest.raises(SpotifyAuthError) as raised_exchange:
        await exchange_code(
            mock_client(echo, []), A_CLIENT_ID, A_REDIRECT_URI, A_CODE, A_VERIFIER
        )
    with pytest.raises(SpotifyAuthError) as raised_refresh:
        await refresh(mock_client(echo, []), A_CLIENT_ID, a_token_set())

    for raised in (raised_exchange, raised_refresh):
        message = str(raised.value) + repr(raised.value)
        assert str(status_code) in message
        assert "invalid_request" in message
        for secret in secrets_sent:
            assert secret not in message
        # Nothing chained, so a traceback cannot print the response either.
        assert raised.value.__cause__ is None
        assert raised.value.__suppress_context__


async def test_a_success_without_an_access_token_is_an_error_not_a_crash() -> None:
    client = mock_client(answer(200, {"token_type": "Bearer"}), [])

    with pytest.raises(SpotifyAuthError):
        await refresh(client, A_CLIENT_ID, a_token_set())


async def test_an_exchange_without_a_refresh_token_is_an_error() -> None:
    # Without one the grant would quietly end an hour later, unattended.
    client = mock_client(answer(200, {"access_token": AN_ACCESS_TOKEN, "expires_in": 3600}), [])

    with pytest.raises(SpotifyAuthError):
        await exchange_code(client, A_CLIENT_ID, A_REDIRECT_URI, A_CODE, A_VERIFIER)


async def test_a_transport_error_propagates_as_the_httpx2_error() -> None:
    # Left unwrapped on purpose: the reader tells a network fault from an
    # answer Spotify gave by this type.
    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(httpx2.TransportError):
        await refresh(mock_client(unreachable, []), A_CLIENT_ID, a_token_set())


def test_a_token_set_keeps_its_tokens_out_of_its_repr() -> None:
    text = repr(a_token_set())

    assert AN_ACCESS_TOKEN not in text
    assert A_REFRESH_TOKEN not in text
