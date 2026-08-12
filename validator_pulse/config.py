from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import field_validator
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
    # Active chain plugin: ethereum | polkadot | cosmos | solana | near (implemented).
    chain: str = "ethereum"
    # Numeric beacon indices, e.g. 123456,789012
    validator_indices: str = "1,2,3"
    # BLS pubkeys (0x + 96 hex). This is the usual "validator address".
    validator_pubkeys: str = ""
    # Polkadot / parachain collators & relay validators
    substrate_rpc_url: str | None = None
    # collator (parachain) | validator (relay NPoS). CHAIN=polkadot-relay forces validator.
    polkadot_role: str = "collator"
    collator_addresses: str = ""
    # Relay validator stash accounts (SS58); controller/session keys optional later
    validator_stash_addresses: str = ""
    parachain_id: int | None = None
    # Cosmos SDK / CometBFT
    cosmos_rest_url: str | None = None
    cosmos_rpc_url: str | None = None
    cosmos_grpc_url: str | None = None
    cosmos_validator_operator_addresses: str = ""
    cosmos_chain_id: str | None = None
    cosmos_profile: str = "cosmoshub"
    # Solana
    solana_rpc_url: str | None = None
    # Comma-separated vote account pubkeys (preferred operator IDs)
    validator_vote_accounts: str = ""
    # Optional identity pubkeys — used when vote accounts are unset
    solana_identity_pubkeys: str = ""
    # NEAR
    near_rpc_url: str | None = None
    near_validator_account_ids: str = ""
    near_metrics_url: str | None = None
    # Optional overrides when parachain token isn't in the built-in map
    reward_token_symbol: str | None = None
    reward_token_decimals: int | None = None
    # Fetch operator display names from explorers and optional identity sources
    fetch_operator_names: bool = True
    subscan_api_key: str | None = None
    beaconcha_base_url: str = "https://beaconcha.in"
    beaconcha_api_key: str | None = None
    rated_api_key: str | None = None
    rated_api_base_url: str = "https://api.rated.network"
    rated_network: str = "mainnet"
    ens_lookup_enabled: bool = False
    ens_api_key: str | None = None
    ens_api_base_url: str = "https://api.enswhois.com"
    poll_interval_seconds: int = 12
    demo_mode: bool = True

    # Shared RPC HTTP(S) / TLS policy (Beacon, Substrate, future adapters)
    rpc_tls_verify: bool = True
    rpc_tls_ca_bundle: str | None = None
    rpc_tls_insecure: bool = False
    rpc_connect_timeout_seconds: float = 8.0

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    webhook_url: str | None = None
    pagerduty_routing_key: str | None = None

    alert_missed_attestations: int = 2
    alert_effectiveness_below: float = 95
    alert_slashing_risk_above: float = 40
    alert_low_era_points_below: int = 40
    alert_skip_rate_above: float = 10.0
    alert_disk_usage_above: float = 85
    alert_clock_drift_ms: float = 500

    host: str = "127.0.0.1"
    port: int = 3000

    # Optional web panel / API credentials (both required to enable auth)
    web_auth_username: str | None = None
    web_auth_password: str | None = None
    # Optional dedicated token for GET /api/metrics (Bearer / X-Metrics-Token / ?token=)
    web_metrics_token: str | None = None

    @field_validator("parachain_id", "reward_token_decimals", mode="before")
    @classmethod
    def _empty_optional_int(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator(
        "reward_token_symbol",
        "rpc_tls_ca_bundle",
        "web_auth_username",
        "web_auth_password",
        "web_metrics_token",
        "cosmos_rest_url",
        "cosmos_rpc_url",
        "cosmos_grpc_url",
        "cosmos_chain_id",
        "solana_rpc_url",
        "near_rpc_url",
        "near_metrics_url",
        mode="before",
    )
    @classmethod
    def _empty_optional_str(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("polkadot_role", mode="before")
    @classmethod
    def _normalize_polkadot_role(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "collator"
        return str(value).strip().lower()

    @field_validator("cosmos_profile", mode="before")
    @classmethod
    def _normalize_cosmos_profile(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "cosmoshub"
        return str(value).strip().lower()

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

    def collator_address_list(self) -> list[str]:
        return _split_csv(self.collator_addresses)

    def validator_stash_address_list(self) -> list[str]:
        return _split_csv(self.validator_stash_addresses)

    def cosmos_validator_address_list(self) -> list[str]:
        return _split_csv(self.cosmos_validator_operator_addresses)

    def solana_vote_account_list(self) -> list[str]:
        return _split_csv(self.validator_vote_accounts)

    def solana_identity_pubkey_list(self) -> list[str]:
        return _split_csv(self.solana_identity_pubkeys)

    def near_validator_account_list(self) -> list[str]:
        return _split_csv(self.near_validator_account_ids)

    def resolved_chain(self) -> str:
        """Registry key for the active adapter (`polkadot-relay` → `polkadot`)."""
        key = (self.chain or "ethereum").strip().lower()
        if key in {"polkadot-relay", "polkadot_relay"}:
            return "polkadot"
        return key or "ethereum"

    def resolved_polkadot_role(self) -> str:
        key = (self.chain or "").strip().lower()
        if key in {"polkadot-relay", "polkadot_relay"}:
            return "validator"
        role = (self.polkadot_role or "collator").strip().lower()
        if role not in {"collator", "validator"}:
            return "collator"
        return role

    def is_demo(self) -> bool:
        """Deprecated helper — prefer adapter.is_demo(settings)."""
        if self.demo_mode:
            return True
        chain = self.resolved_chain()
        if chain == "ethereum":
            return not bool(self.beacon_api_url and self.beacon_api_url.strip())
        if chain == "polkadot":
            return not bool(self.substrate_rpc_url and self.substrate_rpc_url.strip())
        if chain == "cosmos":
            has_rest = bool(self.cosmos_rest_url and self.cosmos_rest_url.strip())
            has_rpc = bool(self.cosmos_rpc_url and self.cosmos_rpc_url.strip())
            return not (has_rest or has_rpc)
        if chain == "solana":
            return not bool(self.solana_rpc_url and self.solana_rpc_url.strip())
        if chain == "near":
            return not bool(self.near_rpc_url and self.near_rpc_url.strip())
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
