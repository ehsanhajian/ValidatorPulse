from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    beacon_api_url: str | None = None
    # Numeric beacon indices, e.g. 123456,789012
    validator_indices: str = "1,2,3"
    # BLS pubkeys (0x + 96 hex). This is the usual "validator address".
    validator_pubkeys: str = ""
    poll_interval_seconds: int = 12
    demo_mode: bool = True

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    webhook_url: str | None = None
    pagerduty_routing_key: str | None = None

    alert_missed_attestations: int = 2
    alert_effectiveness_below: float = 95
    alert_slashing_risk_above: float = 40
    alert_disk_usage_above: float = 85
    alert_clock_drift_ms: float = 500

    host: str = "127.0.0.1"
    port: int = 3000

    def indices(self) -> list[int]:
        values: list[int] = []
        for part in _split_csv(self.validator_indices):
            try:
                values.append(int(part))
            except ValueError:
                continue
        return values

    def pubkeys(self) -> list[str]:
        keys: list[str] = []
        for part in _split_csv(self.validator_pubkeys):
            key = part if part.startswith("0x") else f"0x{part}"
            keys.append(key.lower())
        return keys

    def validator_ids(self) -> list[str]:
        """IDs accepted by the Beacon API `id` query param (index or pubkey)."""
        ids = [str(i) for i in self.indices()] + self.pubkeys()
        if ids:
            return ids
        # Demo fallback when nothing configured
        return ["1", "2", "3"]

    def is_demo(self) -> bool:
        if self.demo_mode:
            return True
        return not bool(self.beacon_api_url and self.beacon_api_url.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
