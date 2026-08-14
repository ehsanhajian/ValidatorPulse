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


def test_near_adapter_is_registered() -> None:
    adapter = get_adapter("near")
    assert adapter.name == "near"
    assert adapter.operator_label == "validator"
    assert adapter.risk_kind == "kickout"
    assert "near" in list_implemented_chains()


def test_cardano_adapter_is_registered() -> None:
    adapter = get_adapter("cardano")
    assert adapter.name == "cardano"
    assert adapter.operator_label == "stake pool"
    assert adapter.risk_kind == "suspension"
    assert "cardano" in list_implemented_chains()


def test_tezos_adapter_is_registered() -> None:
    adapter = get_adapter("tezos")
    assert adapter.name == "tezos"
    assert adapter.operator_label == "baker"
    assert adapter.risk_kind == "slashing"
    assert "tezos" in list_implemented_chains()


def test_algorand_adapter_is_registered() -> None:
    adapter = get_adapter("algorand")
    assert adapter.name == "algorand"
    assert adapter.operator_label == "participation node"
    assert adapter.risk_kind == "suspension"
    assert "algorand" in list_implemented_chains()


def test_aptos_adapter_is_registered() -> None:
    adapter = get_adapter("aptos")
    assert adapter.name == "aptos"
    assert adapter.operator_label == "validator"
    assert adapter.risk_kind == "reward_loss"
    assert "aptos" in list_implemented_chains()


def test_sui_adapter_is_registered() -> None:
    adapter = get_adapter("sui")
    assert adapter.name == "sui"
    assert adapter.operator_label == "validator"
    assert adapter.risk_kind == "reward_loss"
    assert "sui" in list_implemented_chains()


def test_monad_adapter_is_registered() -> None:
    adapter = get_adapter("monad")
    assert adapter.name == "monad"
    assert adapter.operator_label == "validator"
    assert adapter.risk_kind == "reward_loss"
    assert "monad" in list_implemented_chains()


def test_unknown_chain_lists_known_values() -> None:
    with pytest.raises(UnsupportedChainError, match="Known values"):
        get_adapter("bitcoin")
    known = list_known_chains()
    assert "ethereum" in known
    assert "polkadot" in known
    assert "cosmos" in known
    assert "solana" in known
    assert "near" in known
    assert "cardano" in known
    assert "tezos" in known
    assert "algorand" in known
    assert "aptos" in known
    assert "sui" in known
    assert "monad" in known
    assert "sui" in list_implemented_chains()
    assert "monad" in list_implemented_chains()


def test_unimplemented_reserved_chains_cleared() -> None:
    for name in (
        "ethereum",
        "polkadot",
        "cosmos",
        "solana",
        "near",
        "cardano",
        "tezos",
        "algorand",
        "aptos",
        "sui",
        "monad",
    ):
        assert get_adapter(name).name == name
        assert name in list_implemented_chains()
