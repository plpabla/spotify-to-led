"""The one place environment configuration enters the process.

Every test here is hermetic: the `clean_env` fixture removes the keys
`Settings` reads and each construction either passes `_env_file=None` or names
its own file, so the repository's real `.env` cannot change a result on a
development machine.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from spotify_to_led.config import ENV_FILE_VARIABLE, Settings, load_settings

SETTING_KEYS = (
    "BOARD_TRANSPORT",
    "IDOT_MAC",
    "BOARD_SIZE",
    "BOARD_HEARTBEAT_SECONDS",
    "STUB_FRAME_DIR",
    "HOST",
    "PORT",
    "LOG_LEVEL",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_REDIRECT_URI",
    "SPOTIFY_TOKEN_FILE",
    ENV_FILE_VARIABLE,
)

A_MAC = "11:22:33:44:55:66"
A_CLIENT_ID = "0123456789abcdef0123456789abcdef"
A_REDIRECT_URI = "https://led.example.com/callback"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every key `Settings` reads, so defaults are really defaults."""
    for key in SETTING_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_are_the_safe_stub_configuration(clean_env: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.board_transport == "stub"
    assert settings.idot_mac is None
    assert settings.board_size == 32
    assert settings.board_heartbeat_seconds == 30.0
    assert settings.stub_frame_dir == Path("var/frames")
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.log_level == "INFO"


def test_idot_mac_is_read_from_the_environment(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Upper case key, lower case field: the match has to be case-insensitive,
    # because the key already exists in that spelling.
    monkeypatch.setenv("IDOT_MAC", A_MAC)

    assert Settings(_env_file=None).idot_mac == A_MAC


def test_idot_mac_is_read_from_a_dotenv_file(clean_env: None, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"IDOT_MAC={A_MAC}\n", encoding="utf-8")

    assert Settings(_env_file=env_file).idot_mac == A_MAC


def test_a_dotenv_file_may_carry_keys_that_are_not_settings(
    clean_env: None, tmp_path: Path
) -> None:
    # A stray key must not take the whole process down at startup.
    env_file = tmp_path / ".env"
    env_file.write_text(f"IDOT_MAC={A_MAC}\nSOMETHING_ELSE=1\n", encoding="utf-8")

    assert Settings(_env_file=env_file).idot_mac == A_MAC


def test_idotmatrix_without_a_mac_is_rejected_at_construction(clean_env: None) -> None:
    with pytest.raises(ValidationError, match="idot_mac"):
        Settings(_env_file=None, board_transport="idotmatrix")


def test_idotmatrix_with_a_mac_is_accepted(clean_env: None) -> None:
    settings = Settings(_env_file=None, board_transport="idotmatrix", idot_mac=A_MAC)

    assert settings.board_transport == "idotmatrix"
    assert settings.idot_mac == A_MAC


def test_an_unknown_transport_is_rejected(clean_env: None) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, board_transport="carrier-pigeon")


def test_a_board_size_the_library_has_no_screensize_for_is_rejected(
    clean_env: None,
) -> None:
    # Without this the value reaches `ScreenSize((17, 17))` and the process dies
    # on an enum lookup, which reads as a library fault rather than a typo.
    with pytest.raises(ValidationError, match="board_size"):
        Settings(_env_file=None, board_size=17)


@pytest.mark.parametrize("heartbeat", [0, -1.5])
def test_a_non_positive_heartbeat_is_rejected(
    clean_env: None, heartbeat: float
) -> None:
    with pytest.raises(ValidationError, match="board_heartbeat_seconds"):
        Settings(_env_file=None, board_heartbeat_seconds=heartbeat)


def test_an_unset_heartbeat_disables_it_rather_than_failing(clean_env: None) -> None:
    assert Settings(_env_file=None, board_heartbeat_seconds=None).board_heartbeat_seconds is None


def test_without_the_selector_the_working_directory_dotenv_is_read(
    clean_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(f"IDOT_MAC={A_MAC}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert load_settings().idot_mac == A_MAC


def test_the_selector_reads_the_named_file_instead_of_dotenv(
    clean_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("IDOT_MAC=AA:AA:AA:AA:AA:AA\n", encoding="utf-8")
    named = tmp_path / "env"
    named.write_text(f"IDOT_MAC={A_MAC}\nBOARD_SIZE=64\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(named))

    settings = load_settings()

    assert settings.idot_mac == A_MAC
    assert settings.board_size == 64


def test_a_selector_naming_a_missing_file_fails_with_the_path(
    clean_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Falling back to defaults here would quietly drive the stub instead of
    # the board, so the process has to refuse to start.
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(missing))

    with pytest.raises(FileNotFoundError, match="does-not-exist"):
        load_settings()


def test_the_process_environment_beats_the_selected_file(
    clean_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    named = tmp_path / "env"
    named.write_text("IDOT_MAC=AA:AA:AA:AA:AA:AA\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(named))
    monkeypatch.setenv("IDOT_MAC", A_MAC)

    assert load_settings().idot_mac == A_MAC


def test_without_a_client_id_spotify_is_disabled(clean_env: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.spotify_client_id is None
    assert settings.spotify_redirect_uri is None
    assert not settings.spotify_enabled


def test_the_token_file_defaults_to_the_checkout_var_directory(clean_env: None) -> None:
    # Relative on purpose: pm2 runs the service from the checkout, which the
    # daemon user owns, so no extra setup step is needed on the Pi.
    assert Settings(_env_file=None).spotify_token_file == Path("var/spotify-token.json")


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_env_file_copied_from_the_example_leaves_spotify_disabled(
    clean_env: None, tmp_path: Path, blank: str
) -> None:
    # `.env.example` ships an empty client ID next to a placeholder redirect
    # URI. A laptop `.env` copied from it has to start, not fail validation.
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SPOTIFY_CLIENT_ID={blank}\nSPOTIFY_REDIRECT_URI={A_REDIRECT_URI}\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.spotify_client_id is None
    assert not settings.spotify_enabled


def test_a_client_id_with_a_redirect_uri_enables_spotify(clean_env: None) -> None:
    settings = Settings(
        _env_file=None, spotify_client_id=A_CLIENT_ID, spotify_redirect_uri=A_REDIRECT_URI
    )

    assert settings.spotify_enabled
    assert settings.spotify_client_id == A_CLIENT_ID
    # Verbatim: Spotify compares it character for character.
    assert settings.spotify_redirect_uri == A_REDIRECT_URI


@pytest.mark.parametrize("redirect_uri", [None, "", "  "])
def test_a_client_id_without_a_redirect_uri_is_rejected_at_construction(
    clean_env: None, redirect_uri: str | None
) -> None:
    # A half-configured Pi must fail at startup, not when the owner first
    # tries to authorize.
    with pytest.raises(ValidationError, match="spotify_redirect_uri"):
        Settings(
            _env_file=None, spotify_client_id=A_CLIENT_ID, spotify_redirect_uri=redirect_uri
        )
