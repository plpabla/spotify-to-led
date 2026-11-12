"""Where environment configuration enters the process.

One typed model serves both the Pi, whose env file is named by
`SPOTIFY_TO_LED_ENV_FILE`, and the `.env` on the development machine. Keys
carry no prefix and are matched case-insensitively, so the existing `IDOT_MAC`
key populates `idot_mac`.
"""

import os
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The square sizes `ScreenSize` declares. Repeated here rather than imported so
# that reading configuration never pulls in the board library, and so a bad
# size is rejected with a config error instead of an enum lookup failure.
SUPPORTED_BOARD_SIZES: tuple[int, ...] = (16, 32, 64)

# Prefixed on purpose: the settings keys are unprefixed, so this name can never
# be mistaken for one of them.
ENV_FILE_VARIABLE = "SPOTIFY_TO_LED_ENV_FILE"


class Settings(BaseSettings):
    """Runtime configuration, read from the environment and from `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # The stub is the safe default: a development machine that sets nothing
    # drives a PNG, and only the Pi's env file asks for real hardware.
    board_transport: Literal["stub", "idotmatrix"] = "stub"
    idot_mac: str | None = None
    board_size: int = 32
    # 30 seconds matches FR-018's grace window. The two cadences are the same
    # number on purpose, so they cannot drift apart later.
    board_heartbeat_seconds: float | None = 30.0
    stub_frame_dir: Path = Path("var/frames")
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    # Spotify is optional so that a machine without an account, the laptop and
    # the test suite, still starts and simply runs no reader.
    spotify_client_id: str | None = None
    # Sent verbatim: Spotify matches it character for character against the
    # registered value, so it is never derived from an incoming request.
    spotify_redirect_uri: str | None = None
    # Mutable runtime state written on every token rotation, kept apart from
    # the env file, which stays read-only configuration.
    spotify_token_file: Path = Path("var/spotify-token.json")
    # Rendered covers, kept across restarts; one subdirectory per board size.
    cover_cache_dir: Path = Path("var/covers")
    # Optional like Spotify: without a key no cover is ever sent to OpenRouter.
    # A `SecretStr`, so the key reads as asterisks in a repr, a log line or a
    # validation error, and only `get_secret_value` hands it out.
    openrouter_api_key: SecretStr | None = None
    # About $0.009 an image, and the model the proof of concept was judged on.
    openrouter_model: str = "black-forest-labs/flux-3-image-klein"
    # The owner's choices from the status page, rewritten whenever one changes,
    # so like the token file it is runtime state kept apart from the env file.
    owner_settings_file: Path = Path("var/settings.json")

    @property
    def spotify_enabled(self) -> bool:
        """Whether a Spotify client ID is configured, and so a reader should run."""
        return self.spotify_client_id is not None

    @property
    def enhance_available(self) -> bool:
        """Whether AI enhancement can be offered at all, which needs an OpenRouter key."""
        return self.openrouter_api_key is not None

    @field_validator("spotify_client_id")
    @classmethod
    def _blank_client_id_means_unset(cls, value: str | None) -> str | None:
        """Read an empty client ID as "not configured" rather than as an ID.

        `.env.example` ships `SPOTIFY_CLIENT_ID=` empty next to a placeholder
        redirect URI, and a `.env` copied from it must start without Spotify.
        """
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("openrouter_api_key")
    @classmethod
    def _blank_api_key_means_unset(cls, value: SecretStr | None) -> SecretStr | None:
        """Read an empty OpenRouter key as "not configured", as for the client ID.

        An env file that names the key with no value must start without
        enhancement, not with a key OpenRouter will refuse on every call.
        """
        if value is not None and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("board_size")
    @classmethod
    def _board_size_is_supported(cls, value: int) -> int:
        """Reject a size the board library has no `ScreenSize` member for."""
        if value not in SUPPORTED_BOARD_SIZES:
            raise ValueError(
                f"board_size must be one of {SUPPORTED_BOARD_SIZES}, got {value}"
            )
        return value

    @field_validator("board_heartbeat_seconds")
    @classmethod
    def _heartbeat_is_positive_or_disabled(cls, value: float | None) -> float | None:
        """Reject a non-positive heartbeat, which the library raises on anyway."""
        if value is not None and value <= 0:
            raise ValueError(
                f"board_heartbeat_seconds must be greater than 0, or unset to "
                f"disable the heartbeat; got {value}"
            )
        return value

    @model_validator(mode="after")
    def _require_mac_for_idotmatrix(self) -> Self:
        """Reject a MAC-less hardware configuration at construction.

        Without this the misconfiguration would surface much later as a
        Bluetooth fault, which is a far harder thing to read.
        """
        if self.board_transport == "idotmatrix" and self.idot_mac is None:
            raise ValueError("idot_mac is required when board_transport is 'idotmatrix'")
        return self

    @model_validator(mode="after")
    def _require_redirect_uri_for_spotify(self) -> Self:
        """Reject a client ID without a redirect URI at construction.

        A half-configured Pi would otherwise start and only fail when the
        owner tries to authorize, far from the setting that caused it.
        """
        if self.spotify_enabled and not (self.spotify_redirect_uri or "").strip():
            raise ValueError(
                "spotify_redirect_uri is required when spotify_client_id is set"
            )
        return self


def load_settings() -> Settings:
    """Build `Settings` from the file `SPOTIFY_TO_LED_ENV_FILE` names, or `.env`.

    A named file that is missing raises instead of being skipped. pydantic
    ignores a missing env file silently, and the defaults it would fall back
    to drive the stub, so a typo in the path would look like a dead board.
    """
    path = os.environ.get(ENV_FILE_VARIABLE)
    if path is None:
        return Settings()
    env_file = Path(path)
    if not env_file.is_file():
        raise FileNotFoundError(
            f"{ENV_FILE_VARIABLE} is set to {path!r}, but no file exists there"
        )
    return Settings(_env_file=env_file)
