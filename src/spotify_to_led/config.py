"""Where environment configuration enters the process.

One typed model serves both the systemd `EnvironmentFile` on the Pi and the
`.env` on the development machine. Keys carry no prefix and are matched
case-insensitively, so the existing `IDOT_MAC` key populates `idot_mac`.
"""

from pathlib import Path
from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The square sizes `ScreenSize` declares. Repeated here rather than imported so
# that reading configuration never pulls in the board library, and so a bad
# size is rejected with a config error instead of an enum lookup failure.
SUPPORTED_BOARD_SIZES: tuple[int, ...] = (16, 32, 64)


class Settings(BaseSettings):
    """Runtime configuration, read from the environment and from `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # The stub is the safe default: a development machine that sets nothing
    # drives a PNG, and only the Pi's unit file asks for real hardware.
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
