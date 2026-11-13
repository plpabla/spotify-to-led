"""The list of models the owner can pick from, and which one is in use.

The list is a hand-edited file, so what matters is that comments, blank lines
and stray whitespace never become model ids, and that a list the service
cannot use still leaves it a model to ask for, with a warning.
"""

import logging
from pathlib import Path

import pytest

from spotify_to_led.models import FALLBACK_MODEL, load_models, model_in_use


def a_list(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "models.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_ids_are_read_in_order_without_comments_blanks_or_whitespace(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = a_list(
        tmp_path,
        "# the first id is the default\n"
        "\n"
        "  b/second-listed-first  \n"
        "\t# an indented comment\n"
        "a/listed-second\n"
        "   \n",
    )

    with caplog.at_level(logging.WARNING):
        assert load_models(path) == ("b/second-listed-first", "a/listed-second")

    assert caplog.records == []


def test_a_repeated_id_is_kept_once_where_it_first_appears(tmp_path: Path) -> None:
    path = a_list(tmp_path, "a/one\nb/two\na/one\nc/three\nb/two\n")

    assert load_models(path) == ("a/one", "b/two", "c/three")


def test_the_shipped_list_names_the_fallback_first() -> None:
    # The repository's own file: behaviour is unchanged until the owner picks.
    shipped = Path(__file__).parent.parent / "models.txt"

    assert load_models(shipped)[0] == FALLBACK_MODEL


@pytest.mark.parametrize(
    "text", ["", "\n\n", "# only a comment\n  # and another\n"], ids=["empty", "blank", "comments"]
)
def test_a_list_naming_no_model_falls_back_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, text: str
) -> None:
    path = a_list(tmp_path, text)

    with caplog.at_level(logging.WARNING):
        assert load_models(path) == (FALLBACK_MODEL,)

    [record] = caplog.records
    assert record.levelno == logging.WARNING


def test_a_missing_list_falls_back_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Unlike the owner's settings, the list is expected to exist.
    with caplog.at_level(logging.WARNING):
        assert load_models(tmp_path / "missing.txt") == (FALLBACK_MODEL,)

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "missing.txt" in record.getMessage()


def test_a_list_still_missing_is_warned_about_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # `/status` reads the list every few seconds; one line says it all.
    path = tmp_path / "missing.txt"

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert load_models(path) == (FALLBACK_MODEL,)

    assert len(caplog.records) == 1


def test_a_list_fixed_and_broken_again_is_warned_about_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "models.txt"

    with caplog.at_level(logging.WARNING):
        load_models(path)
        a_list(tmp_path, "a/one\n")
        assert load_models(path) == ("a/one",)
        path.unlink()
        load_models(path)

    assert len(caplog.records) == 2


def test_a_list_whose_problem_changes_is_warned_about_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "models.txt"

    with caplog.at_level(logging.WARNING):
        load_models(path)
        a_list(tmp_path, "# no model yet\n")
        load_models(path)
        load_models(path)

    assert len(caplog.records) == 2
    assert "names no model" in caplog.records[1].getMessage()


def test_a_list_that_is_not_text_falls_back_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "models.txt"
    path.write_bytes(b"\xff\xfe\x00not utf-8\xc3")

    with caplog.at_level(logging.WARNING):
        assert load_models(path) == (FALLBACK_MODEL,)

    assert caplog.records


def test_a_directory_in_place_of_the_list_falls_back(tmp_path: Path) -> None:
    assert load_models(tmp_path) == (FALLBACK_MODEL,)


MODELS = ("a/first", "b/second")


def test_a_listed_pick_is_in_use() -> None:
    assert model_in_use(MODELS, "b/second") == "b/second"


def test_without_a_pick_the_first_id_is_in_use() -> None:
    assert model_in_use(MODELS, None) == "a/first"


def test_a_pick_no_longer_listed_gives_way_to_the_first_id() -> None:
    assert model_in_use(MODELS, "c/removed") == "a/first"
