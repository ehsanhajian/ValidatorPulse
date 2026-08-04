from __future__ import annotations

from collections.abc import Callable

from validator_pulse.chains.base import ChainAdapter, UnsupportedChainError

AdapterFactory = Callable[[], ChainAdapter]

_REGISTRY: dict[str, AdapterFactory] = {}
_KNOWN_UNIMPLEMENTED: dict[str, str] = {
    "polkadot": (
        "Polkadot / parachain collator monitoring is not implemented yet. "
        "See https://github.com/ehsanhajian/ValidatorPulse/issues/4"
    ),
    "cosmos": (
        "Cosmos SDK validator monitoring is not implemented yet. "
        "See https://github.com/ehsanhajian/ValidatorPulse/issues/5"
    ),
    "solana": (
        "Solana validator monitoring is not implemented yet. "
        "See https://github.com/ehsanhajian/ValidatorPulse/issues/6"
    ),
}


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

    if key in _KNOWN_UNIMPLEMENTED:
        raise UnsupportedChainError(_KNOWN_UNIMPLEMENTED[key])

    factory = _REGISTRY.get(key)
    if factory is None:
        known = ", ".join(list_known_chains())
        raise UnsupportedChainError(
            f"Unknown chain '{chain}'. Known values: {known}. "
            "Implemented today: " + ", ".join(list_implemented_chains())
        )
    return factory()


def _load_builtin_adapters() -> None:
    # Local imports avoid circular deps at module import time.
    from validator_pulse.chains.ethereum.adapter import EthereumAdapter

    register_adapter("ethereum", EthereumAdapter)


_load_builtin_adapters()
