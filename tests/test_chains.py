from __future__ import annotations

import pytest

from validator_pulse.chains import (
    UnsupportedChainError,
    get_adapter,
    list_implemented_chains,
    list_known_chains,
)


def test_ethereum_adapter_is_registered() -> None:
    adapter = get_adapter("ethereum")
    assert adapter.name == "ethereum"
    assert adapter.operator_label == "validator"
    assert "ethereum" in list_implemented_chains()


def test_polkadot_adapter_is_registered() -> None:
    adapter = get_adapter("polkadot")
    assert adapter.name == "polkadot"
    assert adapter.operator_label == "collator"
    assert "polkadot" in list_implemented_chains()


def test_polkadot_relay_alias_resolves() -> None:
    from validator_pulse.config import Settings

    adapter = get_adapter("polkadot-relay")
    assert adapter.name == "polkadot"
    adapter.configure(Settings(chain="polkadot-relay"))
    assert adapter.operator_label == "validator"
    assert adapter.risk_kind == "slashing"


def test_cosmos_adapter_is_registered() -> None:
    adapter = get_adapter("cosmos")
    assert adapter.name == "cosmos"
    assert adapter.operator_label == "validator"
    assert "cosmos" in list_implemented_chains()


def test_solana_adapter_is_registered() -> None:
    adapter = get_adapter("solana")
    assert adapter.name == "solana"
    assert adapter.operator_label == "validator"
    assert "solana" in list_implemented_chains()


def test_unknown_chain_lists_known_values() -> None:
    with pytest.raises(UnsupportedChainError, match="Known values"):
        get_adapter("bitcoin")
    known = list_known_chains()
    assert "ethereum" in known
    assert "polkadot" in known
    assert "cosmos" in known
    assert "solana" in known
    assert "solana" in list_implemented_chains()


def test_unimplemented_reserved_chains_cleared() -> None:
    # All previously reserved chains that still appear in docs are implemented.
    for name in ("ethereum", "polkadot", "cosmos", "solana"):
        assert get_adapter(name).name in {"ethereum", "polkadot", "cosmos", "solana"}
        assert name in list_implemented_chains()