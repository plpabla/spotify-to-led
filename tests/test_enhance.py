"""The OpenRouter image call, driven against a scripted OpenRouter.

Every request goes to an `httpx2.MockTransport` whose handler answers as the
test chooses, as in `test_covers.py`, so no network is used and no money is
spent. The tests pin the request's shape, the decoding of a good answer, and
which of the three exceptions each failure becomes, since the retry and pause
logic built on `generate` acts on nothing else. The key is a recognisable
string so that its absence from every exception can be checked.

The `Enhancer` built on it runs against the same scripted OpenRouter, with a
sleep that records each delay and returns at once, and a clock that stands
still until a test moves it, so the retry budget and the pause until 00:00
UTC are checked without waiting.
"""

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone
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
    Enhancer,
    generate,
    next_utc_midnight,
)
from spotify_to_led.rendering import render_enhanced

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


def _png_with_a_damaged_chunk() -> bytes:
    """A PNG whose second IDAT chunk has a broken type, which Pillow reports as SyntaxError."""
    buffer = BytesIO()
    # Noise does not compress, so the data spans several IDAT chunks.
    Image.effect_noise((256, 256), 60).convert("RGB").save(
        buffer, format="PNG", compress_level=0
    )
    data = bytearray(buffer.getvalue())
    second = data.index(b"IDAT", data.index(b"IDAT") + 4)
    data[second + 1 : second + 2] = b"#"
    return bytes(data)


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
        an_answer(
            {"data": [{"b64_json": base64.b64encode(_png_with_a_damaged_chunk()).decode()}]}
        ),
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
        "damaged chunk",
    ],
)
async def test_an_answer_without_a_decodable_image_failed(answer: httpx2.Response) -> None:
    with pytest.raises(EnhanceFailed):
        await call(OpenRouter(answer))


async def test_an_answer_whose_body_does_not_decode_failed() -> None:
    async def undecodable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.DecodingError("broken gzip body")

    with pytest.raises(EnhanceFailed):
        await call(OpenRouter(undecodable))


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


# --- the enhancer: retries, give-up and pause -----------------------------

BOARD_SIZE = 32
A_COVER_URL = "https://i.scdn.co/image/ab67616d0000b273aaaa"
ANOTHER_COVER_URL = "https://i.scdn.co/image/ab67616d0000b273bbbb"
# Mid-afternoon, so the next 00:00 UTC is the start of the following day.
AFTERNOON = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)
NEXT_MIDNIGHT = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)


class ScriptedOpenRouter:
    """Answers each request with the next scripted response, recording what it got."""

    def __init__(self, answers: list[httpx2.Response]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert self.answers, "unscripted request to OpenRouter"
        return self.answers.pop(0)


class RecordingSleep:
    """Records each delay and returns at once."""

    def __init__(self) -> None:
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)


class FakeClock:
    """A clock that stands still at `now` until a test moves it."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class MemoryLedger:
    """Today's spend held in memory, standing in for a `SpendLedger` and its file."""

    def __init__(self, spent: float = 0.0) -> None:
        self.spent = spent
        self.recorded: list[float] = []

    def spent_today(self) -> float:
        return self.spent

    def record(self, cost: float) -> None:
        self.recorded.append(cost)
        self.spent += cost


class Limit:
    """The owner's daily limit, which a test may change between calls."""

    def __init__(self, usd: float = 1.0) -> None:
        self.usd = usd

    def __call__(self) -> float:
        return self.usd


def an_enhancer(
    server: ScriptedOpenRouter,
    sleep: RecordingSleep,
    clock: FakeClock | None = None,
    *,
    ledger: MemoryLedger | None = None,
    limit: Limit | None = None,
) -> Enhancer:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    return Enhancer(
        client,
        AN_API_KEY,
        A_MODEL,
        BOARD_SIZE,
        ledger=ledger or MemoryLedger(),
        daily_limit=limit or Limit(),
        clock=clock or FakeClock(AFTERNOON),
        sleep=sleep,
    )


def retryable() -> httpx2.Response:
    return httpx2.Response(503, text="no provider available")


async def test_an_enhanced_cover_is_the_answer_shrunk_with_lanczos_and_its_cost_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = ScriptedOpenRouter([a_generated_image(cost=0.009)])
    sleep = RecordingSleep()

    with caplog.at_level(logging.INFO, logger="spotify_to_led.enhance"):
        frame = await an_enhancer(server, sleep).enhance(A_COVER_URL, SOURCE, SOURCE_MIME)

    assert frame is not None
    expected = render_enhanced(Image.open(BytesIO(_png())), BOARD_SIZE)
    assert frame.size == (BOARD_SIZE, BOARD_SIZE)
    assert frame.tobytes() == expected.tobytes()
    [request] = server.requests
    assert request.extensions["timeout"]["read"] == 90.0
    assert sleep.durations == []
    assert [r.getMessage() for r in caplog.records] == [
        f"cover enhanced: {A_COVER_URL} (cost $0.0090)"
    ]


async def test_two_retryable_failures_then_success_wait_5_then_10_s() -> None:
    server = ScriptedOpenRouter([retryable(), retryable(), a_generated_image()])
    sleep = RecordingSleep()
    enhancer = an_enhancer(server, sleep)

    frame = await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME)

    assert frame is not None
    assert len(server.requests) == 3
    assert sleep.durations == [5.0, 10.0]
    assert not enhancer.gave_up(A_COVER_URL)


async def test_three_retryable_failures_give_up_until_forget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = ScriptedOpenRouter([retryable(), retryable(), retryable()])
    sleep = RecordingSleep()
    enhancer = an_enhancer(server, sleep)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.enhance"):
        assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None

    assert len(server.requests) == 3
    assert sleep.durations == [5.0, 10.0]
    assert enhancer.gave_up(A_COVER_URL)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "the outcome is logged once"
    assert A_COVER_URL in warnings[0].getMessage()

    # Wanted again: no request, until the switch is flipped.
    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None
    assert len(server.requests) == 3
    # Another image is not affected.
    assert not enhancer.gave_up(ANOTHER_COVER_URL)

    enhancer.forget()
    server.answers = [a_generated_image()]
    assert not enhancer.gave_up(A_COVER_URL)
    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is not None
    assert len(server.requests) == 4


async def test_a_rejected_request_is_given_up_on_without_a_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    answer = an_answer({"error": {"code": 400, "message": "bad input image"}}, 400)
    server = ScriptedOpenRouter([answer])
    sleep = RecordingSleep()
    enhancer = an_enhancer(server, sleep)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.enhance"):
        assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None

    assert len(server.requests) == 1
    assert sleep.durations == []
    assert enhancer.gave_up(A_COVER_URL)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


async def test_an_unexpected_error_gives_up_on_the_image_instead_of_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def broken_generate(*args: object, **kwargs: object) -> enhance.GeneratedImage:
        nonlocal calls
        calls += 1
        raise RuntimeError("a bug nothing anticipated")

    monkeypatch.setattr(enhance, "generate", broken_generate)
    enhancer = an_enhancer(ScriptedOpenRouter([]), RecordingSleep())

    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None
    assert enhancer.gave_up(A_COVER_URL)
    # Wanted again, it is not paid for again.
    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None
    assert calls == 1


@pytest.mark.parametrize("status", [402, 403])
async def test_a_credit_refusal_pauses_every_image_until_the_next_utc_midnight(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    refusal = an_answer({"error": {"code": status, "message": "Key limit exceeded"}}, status)
    server = ScriptedOpenRouter([refusal])
    sleep = RecordingSleep()
    clock = FakeClock(AFTERNOON)
    enhancer = an_enhancer(server, sleep, clock)

    with caplog.at_level(logging.INFO, logger="spotify_to_led.enhance"):
        assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None

    assert enhancer.paused_until == NEXT_MIDNIGHT
    assert sleep.durations == [], "a refusal is not retried"
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert NEXT_MIDNIGHT.isoformat() in caplog.records[0].getMessage()
    # Not a give-up: the image is fine, the key is not.
    assert not enhancer.gave_up(A_COVER_URL)

    # Until midnight, no request for any image, and `forget` does not lift it.
    enhancer.forget()
    clock.now = NEXT_MIDNIGHT - timedelta(seconds=1)
    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None
    assert await enhancer.enhance(ANOTHER_COVER_URL, SOURCE, SOURCE_MIME) is None
    assert len(server.requests) == 1
    assert enhancer.paused_until == NEXT_MIDNIGHT

    clock.now = NEXT_MIDNIGHT
    server.answers = [a_generated_image()]
    assert enhancer.paused_until is None
    assert await enhancer.enhance(ANOTHER_COVER_URL, SOURCE, SOURCE_MIME) is not None
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (AFTERNOON, NEXT_MIDNIGHT),
        (datetime(2026, 11, 11, 23, 59, 59, tzinfo=UTC), NEXT_MIDNIGHT),
        # Exactly midnight: the limit has just reset, so the next reset is a day on.
        (datetime(2026, 11, 12, 0, 0, tzinfo=UTC), datetime(2026, 11, 13, 0, 0, tzinfo=UTC)),
        (datetime(2026, 11, 12, 0, 0, 1, tzinfo=UTC), datetime(2026, 11, 13, 0, 0, tzinfo=UTC)),
        # An evening west of Greenwich is already the next day in UTC.
        (
            datetime(2026, 11, 11, 20, 0, tzinfo=timezone(timedelta(hours=-5))),
            datetime(2026, 11, 13, 0, 0, tzinfo=UTC),
        ),
    ],
)
def test_the_pause_ends_at_the_first_utc_midnight_after_the_refusal(
    now: datetime, expected: datetime
) -> None:
    assert next_utc_midnight(now) == expected


async def test_the_key_never_appears_in_the_enhancers_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    answers = [
        httpx2.Response(429, text=AN_API_KEY),
        an_answer({"error": {"message": f"bad {AN_API_KEY}"}}, 400),
    ]
    server = ScriptedOpenRouter(answers)
    enhancer = an_enhancer(server, RecordingSleep())

    with caplog.at_level(logging.DEBUG):
        await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME)
        server.answers = [an_answer({"error": {"message": f"no {AN_API_KEY}"}}, 401)]
        await enhancer.enhance(ANOTHER_COVER_URL, SOURCE, SOURCE_MIME)

    assert len(server.requests) == 3
    assert caplog.records, "the outcomes are logged"
    assert AN_API_KEY not in caplog.text


async def test_a_success_records_its_reported_cost() -> None:
    ledger = MemoryLedger()
    server = ScriptedOpenRouter([a_generated_image(cost=0.009)])
    enhancer = an_enhancer(server, RecordingSleep(), ledger=ledger)

    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is not None

    assert ledger.recorded == [0.009]
    assert enhancer.spent_today == pytest.approx(0.009)


async def test_a_success_without_a_reported_cost_records_nothing() -> None:
    ledger = MemoryLedger()
    server = ScriptedOpenRouter([a_generated_image(cost=None)])
    enhancer = an_enhancer(server, RecordingSleep(), ledger=ledger)

    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is not None

    assert ledger.recorded == []


async def test_a_failure_records_nothing() -> None:
    ledger = MemoryLedger()
    server = ScriptedOpenRouter([an_answer({"error": {"message": "bad input image"}}, 400)])
    enhancer = an_enhancer(server, RecordingSleep(), ledger=ledger)

    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None

    assert ledger.recorded == []


@pytest.mark.parametrize("spent", [1.0, 1.5], ids=["at the limit", "over it"])
async def test_at_or_over_the_limit_no_request_is_made(
    spent: float, caplog: pytest.LogCaptureFixture
) -> None:
    server = ScriptedOpenRouter([])
    enhancer = an_enhancer(server, RecordingSleep(), ledger=MemoryLedger(spent), limit=Limit(1.0))

    assert enhancer.limit_reached
    with caplog.at_level(logging.INFO, logger="spotify_to_led.enhance"):
        assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None

    assert server.requests == []
    # Not a give-up: the image is fine, today's money is spent.
    assert not enhancer.gave_up(A_COVER_URL)
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert A_COVER_URL in caplog.records[0].getMessage()


async def test_just_under_the_limit_one_call_is_made_and_may_cross_it() -> None:
    ledger = MemoryLedger(0.995)
    server = ScriptedOpenRouter([a_generated_image(cost=0.009), a_generated_image()])
    enhancer = an_enhancer(server, RecordingSleep(), ledger=ledger, limit=Limit(1.0))

    assert not enhancer.limit_reached
    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is not None

    assert len(server.requests) == 1
    assert enhancer.spent_today == pytest.approx(1.004)
    assert enhancer.limit_reached
    # The next image is not started.
    assert await enhancer.enhance(ANOTHER_COVER_URL, SOURCE, SOURCE_MIME) is None
    assert len(server.requests) == 1


async def test_the_limit_is_checked_once_so_retries_are_not_cut_short() -> None:
    ledger = MemoryLedger(0.5)
    limit = Limit(1.0)
    server = ScriptedOpenRouter([retryable(), a_generated_image()])

    class LowerTheLimit(RecordingSleep):
        async def __call__(self, seconds: float) -> None:
            # The owner lowers the limit below today's spend between attempts.
            limit.usd = 0.1

    enhancer = an_enhancer(server, LowerTheLimit(), ledger=ledger, limit=limit)

    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is not None
    assert len(server.requests) == 2


async def test_the_limit_is_read_live_so_raising_it_lets_the_next_cover_generate() -> None:
    limit = Limit(0.5)
    server = ScriptedOpenRouter([a_generated_image()])
    enhancer = an_enhancer(server, RecordingSleep(), ledger=MemoryLedger(0.5), limit=limit)

    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is None
    assert server.requests == []

    limit.usd = 2.0

    assert not enhancer.limit_reached
    assert await enhancer.enhance(A_COVER_URL, SOURCE, SOURCE_MIME) is not None
    assert len(server.requests) == 1
