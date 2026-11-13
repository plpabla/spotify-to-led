"""Which OpenRouter image models the owner can pick from, and which one is in use.

The list is `models.txt`, a hand-edited file tracked with the checkout, one
model id per line. It is read on demand rather than once at startup, so an
edit shows on the status page and reaches the next generation without a
restart. The owner's pick is saved with their other settings; the model in
use is that pick while it is still in the list, and the list's first id
otherwise, which is also what a fresh install uses.

Like the other runtime files, a list that cannot be used does not stop the
service: a missing, unreadable or id-less file reads as `FALLBACK_MODEL`
alone. Unlike the owner's settings file, the list is expected to exist, so a
missing one is warned about too.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# About $0.009 an image, and the model the proof of concept was judged on.
FALLBACK_MODEL = "black-forest-labs/flux-3-image-klein"


def load_models(path: Path) -> tuple[str, ...]:
    """The model ids listed at `path`, in file order, each once; never raises.

    Whitespace around a line is stripped, and blank lines and lines starting
    with `#` are skipped. A file that cannot be read, or lists no id, gives
    `(FALLBACK_MODEL,)` and a WARNING.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        # ValueError covers a file that is not UTF-8.
        logger.warning(
            "cannot read the model list %s (%s); using %s",
            path,
            type(error).__name__,
            FALLBACK_MODEL,
        )
        return (FALLBACK_MODEL,)
    # A dict keeps the first sighting of each id, in order.
    ids = dict.fromkeys(
        line
        for line in (raw.strip() for raw in text.splitlines())
        if line and not line.startswith("#")
    )
    if not ids:
        logger.warning("the model list %s names no model; using %s", path, FALLBACK_MODEL)
        return (FALLBACK_MODEL,)
    return tuple(ids)


def model_in_use(models: tuple[str, ...], saved: str | None) -> str:
    """The owner's `saved` pick while `models` lists it, else the list's first id."""
    if saved is not None and saved in models:
        return saved
    return models[0]
