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


def test_unimplemented_chain_has_clear_error() -> None:
    with pytest.raises(UnsupportedChainError, match="issues/5"):
        get_adapter("cosmos")
    with pytest.raises(UnsupportedChainError, match="issues/6"):
        get_adapter("solana")


def test_unknown_chain_lists_known_values() -> None:
    with pytest.raises(UnsupportedChainError, match="Known values"):
        get_adapter("bitcoin")
    known = list_known_chains()
    assert "ethereum" in known
    assert "polkadot" in known
