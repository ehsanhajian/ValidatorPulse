from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CosmosProfile:
    name: str
    display_name: str
    default_chain_id: str
    account_prefix: str
    valoper_prefix: str
    valcons_prefix: str
    token_symbol: str
    token_decimals: int
    token_base_unit: str


_PROFILES: dict[str, CosmosProfile] = {
    "cosmoshub": CosmosProfile(
        name="cosmoshub",
        display_name="Cosmos Hub",
        default_chain_id="cosmoshub-4",
        account_prefix="cosmos",
        valoper_prefix="cosmosvaloper",
        valcons_prefix="cosmosvalcons",
        token_symbol="ATOM",
        token_decimals=6,
        token_base_unit="uatom",
    ),
    "celestia": CosmosProfile(
        name="celestia",
        display_name="Celestia",
        default_chain_id="celestia",
        account_prefix="celestia",
        valoper_prefix="celestiavaloper",
        valcons_prefix="celestiavalcons",
        token_symbol="TIA",
        token_decimals=6,
        token_base_unit="utia",
    ),
}


def list_profiles() -> list[str]:
    return sorted(_PROFILES)


def get_profile(name: str | None) -> CosmosProfile:
    key = (name or "cosmoshub").strip().lower() or "cosmoshub"
    if key not in _PROFILES:
        known = ", ".join(list_profiles())
        raise ValueError(f"Unknown COSMOS_PROFILE '{name}'. Known: {known}")
    return _PROFILES[key]
