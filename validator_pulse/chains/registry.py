from __future__ import annotations

from collections.abc import Callable

from validator_pulse.chains.base import ChainAdapter, UnsupportedChainError

AdapterFactory = Callable[[], ChainAdapter]

_REGISTRY: dict[str, AdapterFactory] = {}
_KNOWN_UNIMPLEMENTED: dict[str, str] = {}


def register_adapter(name: str, factory: AdapterFactory) -> None:
    key = name.strip().lower()
    _REGISTRY[key] = factory


def list_implemented_chains() -> list[str]:
    return sorted(_REGISTRY)


def list_known_chains() -> list[str]:
    return sorted({*_REGISTRY, *_KNOWN_UNIMPLEMENTED})


def get_adapter(chain: str) -> ChainAdapter:
    key = (chain or "").strip().lower()
    if not key:
        key = "ethereum"
    if key in {"polkadot-relay", "polkadot_relay"}:
        key = "polkadot"

    if key in _KNOWN_UNIMPLEMENTED:
        raise UnsupportedChainError(_KNOWN_UNIMPLEMENTED[key])

    factory = _REGISTRY.get(key)
    if factory is None:
        known = ", ".join(list_known_chains() + ["polkadot-relay"])
        raise UnsupportedChainError(
            f"Unknown chain '{chain}'. Known values: {known}. "
            "Implemented today: " + ", ".join(list_implemented_chains())
        )
    return factory()


def _load_builtin_adapters() -> None:
    # Local imports avoid circular deps at module import time.
    from validator_pulse.chains.algorand.adapter import AlgorandAdapter
    from validator_pulse.chains.aptos.adapter import AptosAdapter
    from validator_pulse.chains.avalanche.adapter import AvalancheAdapter
    from validator_pulse.chains.bsc.adapter import BscAdapter
    from validator_pulse.chains.cardano.adapter import CardanoAdapter
    from validator_pulse.chains.cosmos.adapter import CosmosAdapter
    from validator_pulse.chains.ethereum.adapter import EthereumAdapter
    from validator_pulse.chains.mina.adapter import MinaAdapter
    from validator_pulse.chains.monad.adapter import MonadAdapter
    from validator_pulse.chains.near.adapter import NearAdapter
    from validator_pulse.chains.polkadot.adapter import PolkadotAdapter
    from validator_pulse.chains.solana.adapter import SolanaAdapter
    from validator_pulse.chains.sui.adapter import SuiAdapter
    from validator_pulse.chains.tezos.adapter import TezosAdapter

    register_adapter("ethereum", EthereumAdapter)
    register_adapter("polkadot", PolkadotAdapter)
    register_adapter("cosmos", CosmosAdapter)
    register_adapter("solana", SolanaAdapter)
    register_adapter("near", NearAdapter)
    register_adapter("cardano", CardanoAdapter)
    register_adapter("tezos", TezosAdapter)
    register_adapter("algorand", AlgorandAdapter)
    register_adapter("bsc", BscAdapter)
    register_adapter("aptos", AptosAdapter)
    register_adapter("sui", SuiAdapter)
    register_adapter("monad", MonadAdapter)
    register_adapter("avalanche", AvalancheAdapter)
    register_adapter("mina", MinaAdapter)


_load_builtin_adapters()
