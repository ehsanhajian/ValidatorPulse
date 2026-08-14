from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenInfo:
    symbol: str
    decimals: int
    base_unit: str  # e.g. planck, wei


# Common Polkadot + Kusama parachain native tokens.
# IDs can overlap across networks; prefer REWARD_TOKEN_SYMBOL when ambiguous.
_PARACHAIN_TOKENS: dict[int, TokenInfo] = {
    # Polkadot system / common
    1000: TokenInfo("DOT", 10, "planck"),  # Asset Hub
    # Polkadot parachains
    2000: TokenInfo("ACA", 12, "planck"),  # Acala
    2004: TokenInfo("GLMR", 18, "wei"),  # Moonbeam
    2006: TokenInfo("ASTR", 18, "planck"),  # Astar
    2030: TokenInfo("BNC", 12, "planck"),  # Bifrost
    2034: TokenInfo("HDX", 12, "planck"),  # Hydration
    2035: TokenInfo("PHA", 12, "planck"),  # Phala
    2046: TokenInfo("MANTA", 18, "planck"),  # Manta
    # Kusama parachains (IDs reused carefully — override via env if needed)
    2001: TokenInfo("BNC", 12, "planck"),  # Bifrost Kusama
    2007: TokenInfo("SDN", 18, "planck"),  # Shiden
    2023: TokenInfo("MOVR", 18, "wei"),  # Moonriver
}


def resolve_reward_token(
    *,
    chain: str,
    parachain_id: int | None = None,
    symbol_override: str | None = None,
    decimals_override: int | None = None,
    cosmos_profile: str | None = None,
) -> TokenInfo:
    chain_key = (chain or "").strip().lower()

    if chain_key == "ethereum":
        return TokenInfo(
            symbol=(symbol_override or "ETH").upper(),
            decimals=decimals_override if decimals_override is not None else 9,
            base_unit="gwei",
        )

    if chain_key == "polkadot":
        if symbol_override and symbol_override.strip():
            mapped = _PARACHAIN_TOKENS.get(parachain_id) if parachain_id is not None else None
            return TokenInfo(
                symbol=symbol_override.strip().upper(),
                decimals=(
                    decimals_override
                    if decimals_override is not None
                    else (mapped.decimals if mapped else 10)
                ),
                base_unit=mapped.base_unit if mapped else "planck",
            )

        if parachain_id is None:
            return TokenInfo(
                symbol="DOT",
                decimals=decimals_override if decimals_override is not None else 10,
                base_unit="planck",
            )

        mapped = _PARACHAIN_TOKENS.get(parachain_id)
        if mapped:
            return TokenInfo(
                symbol=mapped.symbol,
                decimals=(
                    decimals_override
                    if decimals_override is not None
                    else mapped.decimals
                ),
                base_unit=mapped.base_unit,
            )

        return TokenInfo(
            symbol=f"PARA{parachain_id}",
            decimals=decimals_override if decimals_override is not None else 10,
            base_unit="planck",
        )

    if chain_key == "cosmos":
        from validator_pulse.chains.cosmos.profiles import get_profile

        try:
            profile = get_profile(cosmos_profile)
        except ValueError:
            profile = get_profile("cosmoshub")
        return TokenInfo(
            symbol=(symbol_override or profile.token_symbol).upper(),
            decimals=(
                decimals_override
                if decimals_override is not None
                else profile.token_decimals
            ),
            base_unit=profile.token_base_unit,
        )

    if chain_key == "solana":
        return TokenInfo(
            symbol=(symbol_override or "SOL").upper(),
            decimals=decimals_override if decimals_override is not None else 9,
            base_unit="lamports",
        )

    if chain_key == "near":
        return TokenInfo(
            symbol=(symbol_override or "NEAR").upper(),
            decimals=decimals_override if decimals_override is not None else 24,
            base_unit="yoctoNEAR",
        )

    if chain_key == "cardano":
        return TokenInfo(
            symbol=(symbol_override or "ADA").upper(),
            decimals=decimals_override if decimals_override is not None else 6,
            base_unit="lovelace",
        )

    if chain_key == "tezos":
        return TokenInfo(
            symbol=(symbol_override or "XTZ").upper(),
            decimals=decimals_override if decimals_override is not None else 6,
            base_unit="mutez",
        )

    if chain_key == "algorand":
        return TokenInfo(
            symbol=(symbol_override or "ALGO").upper(),
            decimals=decimals_override if decimals_override is not None else 6,
            base_unit="microAlgos",
        )

    if chain_key == "aptos":
        return TokenInfo(
            symbol=(symbol_override or "APT").upper(),
            decimals=decimals_override if decimals_override is not None else 8,
            base_unit="octas",
        )

    if chain_key == "sui":
        return TokenInfo(
            symbol=(symbol_override or "SUI").upper(),
            decimals=decimals_override if decimals_override is not None else 9,
            base_unit="MIST",
        )

    if chain_key == "monad":
        return TokenInfo(
            symbol=(symbol_override or "MON").upper(),
            decimals=decimals_override if decimals_override is not None else 18,
            base_unit="wei",
        )

    return TokenInfo(
        symbol=(symbol_override or "TOKEN").upper(),
        decimals=decimals_override if decimals_override is not None else 0,
        base_unit="unit",
    )


def format_token_amount(amount: int, token: TokenInfo) -> str:
    if token.decimals <= 0:
        return f"{amount:,} {token.symbol}"

    whole = amount / (10**token.decimals)
    # Ethereum path historically treated stored values as gwei (1e9 wei).
    if token.base_unit == "gwei" and token.symbol == "ETH":
        return f"{amount / 1e9:.3f} ETH"

    if amount >= 10**token.decimals:
        return f"{whole:.4f} {token.symbol}"
    return f"{amount:,} {token.base_unit}"
