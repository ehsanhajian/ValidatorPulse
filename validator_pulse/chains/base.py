from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from validator_pulse.config import Settings
from validator_pulse.models import (
    ConsensusHealth,
    InfrastructureHealth,
    RiskKind,
    ValidatorStats,
)


class UnsupportedChainError(ValueError):
    """Raised when CHAIN is unknown or not yet implemented."""


@dataclass(frozen=True)
class ChainCollection:
    """Chain-specific health payload returned by an adapter."""

    consensus: ConsensusHealth
    operators: list[ValidatorStats]
    infrastructure: InfrastructureHealth


class ChainAdapter(Protocol):
    """Plugin interface for a consensus network.

    Adapters own chain-specific collection (consensus + operators/duties).
    Shared concerns stay outside: infrastructure, alerting, metrics, dashboard.
    """

    name: str
    display_name: str
    operator_label: str  # e.g. "validator", "collator"
    risk_kind: RiskKind
    risk_label: str
    primary_duty_label: str
    secondary_duty_label: str
    missed_duty_label: str
    consensus_node_label: str

    def is_demo(self, settings: Settings) -> bool:
        """Whether this adapter should run in demo mode for the given settings."""

    async def collect(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        """Collect consensus + operator health for this chain."""
