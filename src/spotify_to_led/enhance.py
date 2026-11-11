"""One image-to-image call to OpenRouter, turning a cover into a frame designed for the panel.

A plain downscale of a 640 px cover keeps whatever was in it, including the
fine detail and lettering a 32x32 panel cannot show. The proof of concept
asked an image model instead to redraw the cover as pixel art for that grid:
one recognisable subject, a handful of saturated colours, thick outlines and
a black background, the colour of an unlit LED. `generate` is that call,
ported onto the service's `httpx2` client. It sends the cover inline as a
base64 data URL, because the bytes are already on hand and some image hosts
refuse the provider's own fetcher, and it returns the decoded image (about
1024 px square) and what OpenRouter says the call cost. Shrinking it to a
board frame is the caller's job.

Every way a call can go wrong ends in one of three exceptions, chosen by
what the caller should do next:

- `EnhanceRefused` for 401, 402 and 403: the key is wrong, the account is out
  of credit, or the key's own credit limit is reached. Asking again soon
  only gets the same answer, so the caller stops asking for a while.
- `EnhanceRetryable` for a transport error, a timeout, 408, 429 and any 5xx:
  the request did not get a fair hearing, and the same request later may
  well succeed.
- `EnhanceFailed` for any other status, or a body that is not one decodable
  image: the request itself was turned down or answered with nothing usable,
  so repeating it is wasted money.

The API key goes into the Authorization header and nowhere else. Exception
messages carry the status and a short, truncated piece of the provider's
error text, which is what makes a log line useful, but never the key or the
request headers; the provider text is scrubbed of the key as well, in case an
error ever echoes it back.

`Enhancer` wraps `generate` in the policy FR-019 sets, and is what the cover
loader calls. It hands back a finished board frame, shrunk with
`render_enhanced`, or `None`, which means "show the plain cover":

- An image gets at most `ENHANCE_ATTEMPTS` calls, waiting 5 s and then 10 s
  between them, and only after `EnhanceRetryable`. A call is cut off after
  90 s; on the Pi a generation took about 9 s, 20 s at worst.
- `EnhanceFailed`, or running out of attempts, means the enhancer gives up on
  that image. It remembers so in memory, and asks for it again only after
  `forget()`, which the owner's switch calls whenever it changes, or after a
  restart. Without that memory every sighting of a cover the model cannot
  draw would cost three more calls.
- `EnhanceRefused` pauses every generation, for every image, until the next
  00:00 UTC, which is when a daily key limit resets. A key that is simply
  wrong is refused the same way, and asked again once a day.

Each image's outcome is logged once, naming the image's URL: INFO with the
cost on success, a WARNING when the enhancer gives up or pauses. A retry is an
INFO line of its own. The key is in none of them.
"""

import asyncio
import base64
import binascii
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from io import BytesIO
from typing import Any

import httpx2
from PIL import Image

from spotify_to_led.rendering import render_enhanced

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/images"

# The proof of concept's prompt, verbatim: it is what the on-screen judgement
# was made with, and the board bake-off judges the same output.
ENHANCE_PROMPT = (
    "Redesign this album cover as a 32x32 pixel-art icon for an LED matrix display.\n"
    "Pick the single most recognizable element of the cover (the main figure, face, "
    "object or symbol) and crop in tight so it fills about 80% of the square canvas, "
    "centered.\n"
    "Style: bold pixel art on a coarse 32x32 grid with large square pixels, flat solid "
    "colors, a limited palette of 6-8 saturated, high-contrast colors taken from the "
    "original cover, thick dark outlines, plain pure black background.\n"
    "No text, letters, logos, gradients, textures or fine details: every shape must stay "
    "readable when the image is shrunk to 32x32 pixels."
)

# The answer is JSON around one base64 PNG, which at 1024 px is 1 to 3 MB
# before the base64 third is added. Twenty megabytes leaves headroom for a
# larger output while still refusing a body that is plainly not one image.
# Checked on the body, and on the image header before the pixels are decoded,
# as `covers.fetch_cover` does for a download.
MAX_RESPONSE_BYTES = 20_000_000
MAX_IMAGE_PIXELS = 4096 * 4096
# Enough of the provider's error text to say what went wrong in one log line.
MAX_DETAIL_CHARS = 200

REFUSED_STATUSES = frozenset({401, 402, 403})
RETRYABLE_STATUSES = frozenset({408, 429})

# The Enhancer's policy (FR-019). The timeout leaves ample room over the
# calls measured on the Pi in the board bake-off, about 9 s and 20 s at worst.
ENHANCE_TIMEOUT_SECONDS = 90.0
ENHANCE_ATTEMPTS = 3
# The wait after each failed attempt but the last: the start of the cover
# retry's doubling, cut short, since every attempt here is paid for.
ENHANCE_RETRY_DELAYS: tuple[float, ...] = (5.0, 10.0)

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


class EnhanceError(Exception):
    """A generation did not produce an image; the subclass says what to do next."""


class EnhanceRefused(EnhanceError):
    """OpenRouter refused the key or its credit; stop asking for a while."""


class EnhanceRetryable(EnhanceError):
    """The call did not get a fair hearing; the same request may succeed later."""


class EnhanceFailed(EnhanceError):
    """The request was turned down, or answered with no usable image; do not repeat it."""


@dataclass(frozen=True)
class GeneratedImage:
    """The model's image, decoded, and its cost in USD when OpenRouter reports one."""

    image: Image.Image
    cost: float | None


async def generate(
    client: httpx2.AsyncClient,
    api_key: str,
    model: str,
    source: bytes,
    mime: str,
    *,
    timeout: float,
) -> GeneratedImage:
    """Ask `model` to redraw the `source` image (of type `mime`) per `ENHANCE_PROMPT`.

    Raises `EnhanceRefused`, `EnhanceRetryable` or `EnhanceFailed`, as the
    module docstring describes; nothing else escapes for a failed call.
    """
    data_url = f"data:{mime};base64,{base64.b64encode(source).decode('ascii')}"
    body = {
        "model": model,
        "prompt": ENHANCE_PROMPT,
        "n": 1,
        "aspect_ratio": "1:1",
        "input_references": [{"type": "image_url", "image_url": {"url": data_url}}],
    }
    try:
        # As in `fetch_cover`: httpx2's timeout bounds each phase, so this
        # bounds the whole call, and the per-request value keeps the shared
        # client's defaults, which belong to the Spotify reader, out of it.
        async with asyncio.timeout(timeout):
            response = await client.post(
                API_URL,
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
    except TimeoutError:
        raise EnhanceRetryable(f"no answer within {timeout} s") from None
    except httpx2.TransportError as error:
        # The cause is kept for the traceback: a transport error's message is
        # about the connection, and the request headers are not part of it.
        raise EnhanceRetryable(f"{type(error).__name__}: {error}") from error
    except httpx2.RequestError as error:
        # The rest arrive with an answer in hand: a body that does not decode,
        # or redirects without end. Asking again would get the same.
        raise EnhanceFailed(f"{type(error).__name__}: {error}") from error

    status = response.status_code
    if not response.is_success:
        detail = _error_detail(response, api_key)
        message = f"HTTP {status}: {detail}" if detail else f"HTTP {status}"
        if status in REFUSED_STATUSES:
            raise EnhanceRefused(message)
        if status in RETRYABLE_STATUSES or status >= 500:
            raise EnhanceRetryable(message)
        raise EnhanceFailed(message)

    if len(response.content) > MAX_RESPONSE_BYTES:
        raise EnhanceFailed(f"body of {len(response.content)} bytes")
    try:
        payload: Any = response.json()
    except ValueError:
        raise EnhanceFailed("body is not JSON") from None
    if not isinstance(payload, dict):
        raise EnhanceFailed("no image in the answer")
    # Decoding a 1024 px PNG takes a noticeable slice of a Pi's CPU; off the
    # event loop, it cannot hold up the display, the board link or /status.
    image = await asyncio.to_thread(_decode_image, payload)
    return GeneratedImage(image, _cost(payload))


def _decode_image(payload: dict[str, Any]) -> Image.Image:
    """The first image of a successful answer, decoded, or `EnhanceFailed`."""
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise EnhanceFailed("no image in the answer")
    encoded = data[0].get("b64_json")
    if not isinstance(encoded, str):
        raise EnhanceFailed("no image in the answer")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise EnhanceFailed("image is not valid base64") from None
    try:
        image = Image.open(BytesIO(raw))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise EnhanceFailed(f"image of {image.width}x{image.height} pixels")
        # `open` reads only the header; `load` decodes the rest, so a
        # truncated image fails here rather than in the caller's downscale.
        image.load()
    # Pillow reports a damaged PNG chunk as SyntaxError or ValueError, not
    # OSError; `FrameCache.load` catches the same set for the same reason.
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        raise EnhanceFailed(f"image does not decode ({type(error).__name__})") from None
    return image


def _cost(payload: dict[str, Any]) -> float | None:
    """`usage.cost` from a successful answer, or `None` when it is missing."""
    usage = payload.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    return float(cost)


def _error_detail(response: httpx2.Response, api_key: str) -> str:
    """A short piece of the provider's error text, with the key scrubbed out."""
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        detail = error["message"]
    else:
        detail = response.text
    detail = " ".join(detail.split())
    if api_key:
        detail = detail.replace(api_key, "[redacted]")
    if len(detail) > MAX_DETAIL_CHARS:
        detail = detail[:MAX_DETAIL_CHARS] + "..."
    return detail


def _utc_now() -> datetime:
    return datetime.now(UTC)


def next_utc_midnight(now: datetime) -> datetime:
    """The first 00:00 UTC strictly after `now`, when a daily key limit resets."""
    tomorrow = now.astimezone(UTC).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0, 0), tzinfo=UTC)


class Enhancer:
    """Turns a cover into an enhanced board frame, within FR-019's retry and pause policy.

    One enhancer serves the whole process, so that its memory of the images it
    gave up on, and its pause, apply to every cover. It is used by the cover
    loader's one task at a time.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        api_key: str,
        model: str,
        size: int,
        *,
        clock: Clock = _utc_now,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._size = size
        self._clock = clock
        self._sleep = sleep
        self._paused_until: datetime | None = None
        self._gave_up: set[str] = set()

    @property
    def paused_until(self) -> datetime | None:
        """When generation may start again after a refusal, or `None` when it is not paused."""
        if self._paused_until is not None and self._clock() < self._paused_until:
            return self._paused_until
        return None

    def gave_up(self, url: str) -> bool:
        """Whether the image at `url` failed for good since the last `forget()`."""
        return url in self._gave_up

    def forget(self) -> None:
        """Forget every image given up on, so each is tried again when next wanted.

        The pause stays: it is about the key and its credit, not the images.
        """
        self._gave_up.clear()

    async def enhance(self, url: str, source: bytes, mime: str) -> Image.Image | None:
        """Return `source`, the image at `url` of type `mime`, as an enhanced board frame.

        `None` means the plain cover is to be shown instead: generation is
        paused, the image was given up on before, or it is given up on now.
        Makes no request in the first two cases.
        """
        if self.paused_until is not None or self.gave_up(url):
            return None
        for attempt in range(1, ENHANCE_ATTEMPTS + 1):
            try:
                generated = await generate(
                    self._client,
                    self._api_key,
                    self._model,
                    source,
                    mime,
                    timeout=ENHANCE_TIMEOUT_SECONDS,
                )
            except EnhanceRefused as error:
                self._paused_until = next_utc_midnight(self._clock())
                logger.warning(
                    "enhancing %s was refused (%s); pausing all generation until %s",
                    url,
                    error,
                    self._paused_until.isoformat(),
                )
                return None
            except EnhanceFailed as error:
                self._gave_up.add(url)
                logger.warning(
                    "enhancing %s failed (%s); showing the plain cover", url, error
                )
                return None
            except EnhanceRetryable as error:
                if attempt == ENHANCE_ATTEMPTS:
                    self._gave_up.add(url)
                    logger.warning(
                        "enhancing %s failed %s times, the last with %s; showing the plain cover",
                        url,
                        attempt,
                        error,
                    )
                    return None
                delay = ENHANCE_RETRY_DELAYS[attempt - 1]
                logger.info(
                    "enhancing %s failed (%s); retrying in %s s", url, error, delay
                )
                await self._sleep(delay)
            except Exception:
                # A bug, or an error nothing above anticipated: give up on the
                # image like a failure, so it is not paid for again, and let
                # the loader show the plain cover instead of dying mid-way.
                self._gave_up.add(url)
                logger.exception(
                    "enhancing %s failed unexpectedly; showing the plain cover", url
                )
                return None
            else:
                cost = f"${generated.cost:.4f}" if generated.cost is not None else "not reported"
                logger.info("cover enhanced: %s (cost %s)", url, cost)
                # Off the event loop for the same reason as the decode.
                return await asyncio.to_thread(render_enhanced, generated.image, self._size)
        # The loop returns on its last attempt whatever the outcome.
        raise AssertionError("unreachable")
