"""The OpenRouter image call, driven against a scripted OpenRouter.

Every request goes to an `httpx2.MockTransport` whose handler answers as the
test chooses, as in `test_covers.py`, so no network is used and no money is
spent. The tests pin the request's shape, the decoding of a good answer, and
which of the three exceptions each failure becomes, since the retry and pause
logic built on `generate` acts on nothing else. The key is a recognisable
string so that its absence from every exception can be checked.
"""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from io import BytesIO

import httpx2
import pytest
from PIL import Image

from spotify_to_led import enhance
from spotify_to_led.enhance import (
    ENHANCE_PROMPT,
    EnhanceError,
    EnhanceFailed,
    EnhanceRefused,
    EnhanceRetryable,
    generate,
)

AN_API_KEY = "sk-or-v1-test-key-that-must-never-leak"
A_MODEL = "black-forest-labs/flux-3-image-klein"
SOURCE = b"\xff\xd8\xff\xe0 pretend jpeg bytes"
SOURCE_MIME = "image/jpeg"
TIMEOUT = 5.0
GENERATED_COLOUR: tuple[int, int, int] = (10, 200, 30)

Handler = Callable[[httpx2.Request], Awaitable[httpx2.Response]]


def _png(size: int = 64) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (size, size), GENERATED_COLOUR).save(buffer, format="PNG")
    return buffer.getvalue()


def an_answer(body: object, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json=body)


def a_generated_image(cost: object = 0.009) -> httpx2.Response:
    body: dict[str, object] = {
        "data": [{"b64_json": base64.b64encode(_png()).decode("ascii")}]
    }
    if cost is not None:
        body["usage"] = {"cost": cost}
    return an_answer(body)


class OpenRouter:
    """Answers every request with one scripted response, recording what it got."""

    def __init__(self, answer: httpx2.Response | Handler) -> None:
        self.answer = answer
        self.requests: list[httpx2.Request] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.answer, httpx2.Response):
            return self.answer
        return await self.answer(request)


async def call(server: OpenRouter, *, timeout: float = TIMEOUT) -> enhance.GeneratedImage:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server)) as client:
        return await generate(
            client, AN_API_KEY, A_MODEL, SOURCE, SOURCE_MIME, timeout=timeout
        )


# --- the request ----------------------------------------------------------


async def test_the_request_carries_the_cover_inline_with_the_prompt_and_the_key() -> None:
    server = OpenRouter(a_generated_image())

    await call(server)

    [request] = server.requests
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/v1/images"
    assert request.headers["authorization"] == f"Bearer {AN_API_KEY}"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body == {
        "model": A_MODEL,
        "prompt": ENHANCE_PROMPT,
        "n": 1,
        "aspect_ratio": "1:1",
        "input_references": [
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,"
                    + base64.b64encode(SOURCE).decode("ascii")
                },
            }
        ],
    }
    # Per request, so the shared client's defaults stay the Spotify reader's.
    assert request.extensions["timeout"]["read"] == TIMEOUT


def test_the_prompt_is_the_proof_of_concepts_verbatim() -> None:
    assert ENHANCE_PROMPT == (
        "Redesign this album cover as a 32x32 pixel-art icon for an LED matrix display.\n"
        "Pick the single most recognizable element of the cover (the main figure, face,"
        " object or symbol) and crop in tight so it fills about 80% of the square canvas,"
        " centered.\n"
        "Style: bold pixel art on a coarse 32x32 grid with large square pixels, flat solid"
        " colors, a limited palette of 6-8 saturated, high-contrast colors taken from the"
        " original cover, thick dark outlines, plain pure black background.\n"
        "No text, letters, logos, gradients, textures or fine details: every shape must"
        " stay readable when the image is shrunk to 32x32 pixels."
    )


# --- a good answer --------------------------------------------------------


async def test_a_good_answer_decodes_to_the_image_and_its_cost() -> None:
    result = await call(OpenRouter(a_generated_image(cost=0.009)))

    assert result.image.size == (64, 64)
    assert result.image.getpixel((0, 0)) == GENERATED_COLOUR
    assert result.cost == 0.009


@pytest.mark.parametrize(
    "answer",
    [
        a_generated_image(cost=None),
        a_generated_image(cost="0.009"),
        a_generated_image(cost=True),
    ],
    ids=["no usage", "cost not a number", "cost a boolean"],
)
async def test_a_good_answer_without_a_usable_cost_has_none(
    answer: httpx2.Response,
) -> None:
    result = await call(OpenRouter(answer))

    assert result.cost is None
    assert result.image.size == (64, 64)


# --- failures -------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 402, 403])
async def test_a_refused_key_or_credit_is_refused(status: int) -> None:
    answer = an_answer({"error": {"code": status, "message": "Key limit exceeded"}}, status)

    with pytest.raises(EnhanceRefused, match=f"HTTP {status}: Key limit exceeded"):
        await call(OpenRouter(answer))


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
async def test_a_status_worth_asking_again_is_retryable(status: int) -> None:
    with pytest.raises(EnhanceRetryable, match=f"HTTP {status}"):
        await call(OpenRouter(httpx2.Response(status, text="try later")))


async def test_a_transport_error_is_retryable() -> None:
    async def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(EnhanceRetryable, match="ConnectError"):
        await call(OpenRouter(unreachable))


async def test_a_call_that_outlasts_its_timeout_is_retryable() -> None:
    async def slow(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(0.5)
        return a_generated_image()

    with pytest.raises(EnhanceRetryable, match="no answer within"):
        await call(OpenRouter(slow), timeout=0.05)


async def test_a_rejected_request_failed() -> None:
    answer = an_answer({"error": {"code": 400, "message": "bad input image"}}, 400)

    with pytest.raises(EnhanceFailed, match="HTTP 400: bad input image"):
        await call(OpenRouter(answer))


async def test_a_long_provider_message_is_cut_short() -> None:
    answer = an_answer({"error": {"message": "x" * 5000}}, 400)

    with pytest.raises(EnhanceFailed) as raised:
        await call(OpenRouter(answer))

    assert len(str(raised.value)) < 300


@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(200, text="<html>not json</html>"),
        an_answer(["not", "an", "object"]),
        an_answer({"usage": {"cost": 0.009}}),
        an_answer({"data": []}),
        an_answer({"data": [{"url": "https://example.com/a.png"}]}),
        an_answer({"data": [{"b64_json": "not base64 at all!"}]}),
        an_answer({"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]}),
        an_answer({"data": [{"b64_json": base64.b64encode(_png()[:60]).decode()}]}),
    ],
    ids=[
        "not json",
        "not an object",
        "no data",
        "empty data",
        "no b64_json",
        "bad base64",
        "not an image",
        "truncated image",
    ],
)
async def test_an_answer_without_a_decodable_image_failed(answer: httpx2.Response) -> None:
    with pytest.raises(EnhanceFailed):
        await call(OpenRouter(answer))


@pytest.mark.parametrize("limit", ["MAX_RESPONSE_BYTES", "MAX_IMAGE_PIXELS"])
async def test_an_answer_too_large_to_be_one_image_failed(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    # The 64x64 test image is a few hundred bytes and 4096 pixels.
    monkeypatch.setattr(enhance, limit, 100)

    with pytest.raises(EnhanceFailed):
        await call(OpenRouter(a_generated_image()))


# --- the key stays secret -------------------------------------------------


async def _raise_connect_error(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


async def _never_answer(request: httpx2.Request) -> httpx2.Response:
    await asyncio.sleep(0.5)
    return a_generated_image()


@pytest.mark.parametrize(
    "answer",
    [
        # A provider that echoes the key back must not get it into the message.
        an_answer({"error": {"message": f"invalid key {AN_API_KEY}"}}, 401),
        httpx2.Response(402, text=f"no credit for {AN_API_KEY}"),
        an_answer({"error": {"message": "Key limit exceeded"}}, 403),
        httpx2.Response(429, text=AN_API_KEY),
        httpx2.Response(500),
        an_answer({"error": {"message": f"bad request {AN_API_KEY}"}}, 400),
        httpx2.Response(200, text="not json"),
        _raise_connect_error,
        _never_answer,
    ],
    ids=["401", "402", "403", "429", "500", "400", "bad body", "transport", "timeout"],
)
async def test_the_key_never_appears_in_an_exception(
    answer: httpx2.Response | Handler,
) -> None:
    with pytest.raises(EnhanceError) as raised:
        await call(OpenRouter(answer), timeout=0.05)

    assert AN_API_KEY not in str(raised.value)
    assert AN_API_KEY not in repr(raised.value)
    cause = raised.value.__cause__
    if cause is not None:
        assert AN_API_KEY not in str(cause)
