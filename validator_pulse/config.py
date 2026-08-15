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
    # Active chain plugin: ethereum | polkadot | cosmos | solana | near | cardano | tezos | algorand | bsc | aptos | sui | monad | avalanche | mina | multiversx (implemented).
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
    # Cardano stake pools
    cardano_pool_ids: str = ""
    cardano_tracer_url: str | None = None
    cardano_node_name: str = "block-producer"
    cardano_network: str = "mainnet"
    cardano_node_socket_path: str | None = None
    # Tezos bakers (Octez protocol RPC)
    tezos_rpc_url: str | None = None
    tezos_baker_addresses: str = ""
    tezos_metrics_url: str | None = None
    tezos_baker_log_path: str | None = None
    # Algorand participation nodes (local authenticated algod)
    algorand_algod_url: str | None = None
    algorand_algod_token: str | None = None
    algorand_algod_token_file: str | None = None
    algorand_account_addresses: str = ""
    algorand_metrics_url: str | None = None
    # Aptos validators (fullnode REST + stake view)
    aptos_rest_url: str | None = None
    aptos_pool_addresses: str = ""
    aptos_metrics_url: str | None = None
    aptos_api_key: str | None = None
    # Sui validators (GraphQL + optional local Prometheus; no JSON-RPC)
    sui_graphql_url: str | None = None
    sui_validator_addresses: str = ""
    sui_metrics_url: str | None = None
    # BNB Smart Chain validators (SlashIndicator + StakeHub)
    bsc_rpc_url: str | None = None
    bsc_validator_addresses: str = ""
    bsc_metrics_url: str | None = None
    bsc_slash_contract: str | None = None
    bsc_stake_hub_contract: str | None = None
    bsc_misdemeanor_threshold: int | None = None
    bsc_felony_threshold: int | None = None
    # Avalanche Primary Network validators (P-Chain + local info/metrics)
    avalanche_rpc_url: str | None = None
    avalanche_node_ids: str = ""
    avalanche_network: str = "mainnet"
    avalanche_metrics_url: str | None = None
    avalanche_uptime_threshold: float | None = None
    # Mina block producers (local GraphQL + CLI/logs; optional archive)
    mina_graphql_url: str | None = None
    mina_producer_public_keys: str = ""
    mina_client_command: str = "mina"
    mina_archive_database_url: str | None = None
    mina_log_path: str | None = None
    # MultiversX validators (local node APIs + gateway heartbeat/statistics)
    multiversx_node_api_url: str | None = None
    multiversx_gateway_url: str | None = None
    multiversx_validator_bls_keys: str = ""
    multiversx_shard_id: int | None = None
    multiversx_jail_rating_threshold: float | None = None
    # Monad validators (EVM RPC staking precompile 0x1000 + optional local evidence)
    monad_rpc_url: str | None = None
    monad_validator_ids: str = ""
    monad_metrics_url: str | None = None
    monad_ledger_tail_path: str | None = None
    monad_status_path: str | None = None
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
    alert_cardano_kes_warning: int = 5
    alert_cardano_kes_critical: int = 1
    alert_tezos_remaining_misses_below: int = 2
    alert_algorand_partkey_warning_rounds: int = 50_000
    alert_algorand_heartbeat_gap_rounds: int = 10_000
    alert_aptos_failed_proposals: int = 3
    alert_sui_at_risk_epochs: int = 3
    alert_avalanche_runway_hours: float = 24
    alert_mina_near_slot_slots: int = 2
    alert_multiversx_rating_below: float = 20
    alert_disk_usage_above: float = 85
    alert_clock_drift_ms: float = 500

    host: str = "127.0.0.1"
    port: int = 3000

    # Optional web panel / API credentials (both required to enable auth)
    web_auth_username: str | None = None
    web_auth_password: str | None = None
    # Optional dedicated token for GET /api/metrics (Bearer / X-Metrics-Token / ?token=)
    web_metrics_token: str | None = None

    @field_validator(
        "parachain_id",
        "reward_token_decimals",
        "bsc_misdemeanor_threshold",
        "bsc_felony_threshold",
        "avalanche_uptime_threshold",
        "multiversx_shard_id",
        "multiversx_jail_rating_threshold",
        mode="before",
    )
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
        "cardano_tracer_url",
        "cardano_node_socket_path",
        "tezos_rpc_url",
        "tezos_metrics_url",
        "tezos_baker_log_path",
        "algorand_algod_url",
        "algorand_algod_token",
        "algorand_algod_token_file",
        "algorand_metrics_url",
        "aptos_rest_url",
        "aptos_metrics_url",
        "aptos_api_key",
        "sui_graphql_url",
        "sui_metrics_url",
        "bsc_rpc_url",
        "bsc_metrics_url",
        "bsc_slash_contract",
        "bsc_stake_hub_contract",
        "avalanche_rpc_url",
        "avalanche_metrics_url",
        "mina_graphql_url",
        "mina_archive_database_url",
        "mina_log_path",
        "multiversx_node_api_url",
        "multiversx_gateway_url",
        "monad_rpc_url",
        "monad_metrics_url",
        "monad_ledger_tail_path",
        "monad_status_path",
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

    def cardano_pool_id_list(self) -> list[str]:
        return _split_csv(self.cardano_pool_ids)

    def tezos_baker_address_list(self) -> list[str]:
        return _split_csv(self.tezos_baker_addresses)

    def algorand_account_address_list(self) -> list[str]:
        return _split_csv(self.algorand_account_addresses)

    def aptos_pool_address_list(self) -> list[str]:
        return _split_csv(self.aptos_pool_addresses)

    def sui_validator_address_list(self) -> list[str]:
        return _split_csv(self.sui_validator_addresses)

    def bsc_validator_address_list(self) -> list[str]:
        return _split_csv(self.bsc_validator_addresses)

    def avalanche_node_id_list(self) -> list[str]:
        return _split_csv(self.avalanche_node_ids)

    def mina_producer_public_key_list(self) -> list[str]:
        return _split_csv(self.mina_producer_public_keys)

    def multiversx_bls_key_list(self) -> list[str]:
        return _split_csv(self.multiversx_validator_bls_keys)

    def monad_validator_id_list(self) -> list[int]:
        values: list[int] = []
        for part in _split_csv(self.monad_validator_ids):
            try:
                values.append(int(part, 0) if part.lower().startswith("0x") else int(part))
            except ValueError:
                continue
        return values

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
        if chain == "cardano":
            return not bool(self.cardano_tracer_url and self.cardano_tracer_url.strip())
        if chain == "tezos":
            return not bool(self.tezos_rpc_url and self.tezos_rpc_url.strip())
        if chain == "algorand":
            return not bool(self.algorand_algod_url and self.algorand_algod_url.strip())
        if chain == "aptos":
            return not bool(self.aptos_rest_url and self.aptos_rest_url.strip())
        if chain == "sui":
            return not bool(self.sui_graphql_url and self.sui_graphql_url.strip())
        if chain == "bsc":
            return not bool(self.bsc_rpc_url and self.bsc_rpc_url.strip())
        if chain == "avalanche":
            return not bool(self.avalanche_rpc_url and self.avalanche_rpc_url.strip())
        if chain == "mina":
            return not bool(self.mina_graphql_url and self.mina_graphql_url.strip())
        if chain == "multiversx":
            node = bool(self.multiversx_node_api_url and self.multiversx_node_api_url.strip())
            gateway = bool(self.multiversx_gateway_url and self.multiversx_gateway_url.strip())
            return not (node or gateway)
        if chain == "monad":
            return not bool(self.monad_rpc_url and self.monad_rpc_url.strip())
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
