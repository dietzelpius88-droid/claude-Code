"""Konfiguration. Secrets kommen ausschliesslich aus der Umgebung bzw. .env."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


# Base-URLs, belegt aus x10/config.py des offiziellen Extended-SDK und aus
# lighter/endpoint_profiles.py des offiziellen Lighter-SDK.
EXTENDED_REST = {
    Environment.TESTNET: "https://api.starknet.sepolia.extended.exchange/api/v1",
    Environment.MAINNET: "https://api.starknet.extended.exchange/api/v1",
}
EXTENDED_STREAM = {
    Environment.TESTNET: "wss://api.starknet.sepolia.extended.exchange/stream.extended.exchange/v1",
    Environment.MAINNET: "wss://api.starknet.extended.exchange/stream.extended.exchange/v1",
}
LIGHTER_REST = {
    Environment.TESTNET: "https://testnet.zklighter.elliot.ai",
    Environment.MAINNET: "https://mainnet.zklighter.elliot.ai",
}
LIGHTER_STREAM = {
    Environment.TESTNET: "wss://testnet.zklighter.elliot.ai/stream",
    Environment.MAINNET: "wss://mainnet.zklighter.elliot.ai/stream",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
    )

    # --- Ausfuehrung ---
    # Standard ist der Trockenlauf. Live-Handel erst, wenn diese Variable
    # bewusst umgestellt wird.
    dry_run: bool = Field(default=True, alias="DELTAFARM_DRY_RUN")
    environment: Environment = Field(default=Environment.TESTNET, alias="DELTAFARM_ENVIRONMENT")

    # --- Netzwerk ---
    # Die Bindeadresse ist bewusst keine Einstellung. Die Anwendung ist nur
    # lokal erreichbar, und daran soll sich nichts per Konfiguration aendern.
    port: int = Field(default=8787, alias="DELTAFARM_PORT")

    # --- Rate Limiting je Boerse ---
    # Beide Werte sind bewusst konservativ. Weder Extended noch Lighter nennen
    # in SDK oder erreichbarer Doku konkrete Request-Limits (RESEARCH.md,
    # OFFEN-5 und OFFEN-8). Lieber zu langsam als ein IP-Bann.
    extended_rate_per_second: float = Field(default=5.0, alias="EXTENDED_RATE_PER_SECOND")
    extended_burst: int = Field(default=10, alias="EXTENDED_BURST")
    lighter_rate_per_second: float = Field(default=5.0, alias="LIGHTER_RATE_PER_SECOND")
    lighter_burst: int = Field(default=10, alias="LIGHTER_BURST")

    # --- Aktualisierung ---
    poll_seconds: int = Field(default=20, alias="DELTAFARM_POLL_SECONDS")

    # --- Ausfuehrung (Phase 3) ---
    # Wie lange eine Vorschau als Grundlage einer Order taugt. Bewusst kurz:
    # ein alter Browser-Tab soll keine Order ausloesen koennen.
    preview_max_age_seconds: float = Field(default=15.0, alias="DELTAFARM_PREVIEW_MAX_AGE")
    # Anteil der freien Margin, den eine Position hoechstens belegen darf.
    margin_share: Decimal = Field(default=Decimal("0.5"), alias="DELTAFARM_MARGIN_SHARE")
    # Wie lange nach der ersten Fuellung auf das zweite Bein gewartet wird,
    # bevor das Paar als UNGESICHERT gilt.
    hedge_timeout_seconds: float = Field(default=10.0, alias="DELTAFARM_HEDGE_TIMEOUT")

    # --- Schluessel (ab Phase 2) ---
    extended_api_key: Optional[SecretStr] = Field(default=None, alias="EXTENDED_API_KEY")
    extended_public_key: Optional[SecretStr] = Field(default=None, alias="EXTENDED_PUBLIC_KEY")
    extended_private_key: Optional[SecretStr] = Field(default=None, alias="EXTENDED_PRIVATE_KEY")
    extended_vault_id: Optional[str] = Field(default=None, alias="EXTENDED_VAULT_ID")
    lighter_account_index: Optional[int] = Field(default=None, alias="LIGHTER_ACCOUNT_INDEX")
    lighter_api_key_index: Optional[int] = Field(default=None, alias="LIGHTER_API_KEY_INDEX")
    lighter_private_key: Optional[SecretStr] = Field(default=None, alias="LIGHTER_PRIVATE_KEY")

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def extended_rest_url(self) -> str:
        return EXTENDED_REST[self.environment]

    @property
    def lighter_rest_url(self) -> str:
        return LIGHTER_REST[self.environment]

    @property
    def is_mainnet(self) -> bool:
        return self.environment is Environment.MAINNET


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Nur fuer Tests."""
    global _settings
    _settings = None
