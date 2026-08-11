from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

HealthStatus = Literal["healthy", "degraded", "critical", "unknown"]
DutyOutcome = Literal["success", "missed", "late", "pending"]
AlertSeverity = Literal["info", "warning", "critical"]
AlertSource = Literal["validator", "consensus", "infrastructure", "system"]
AlertChannelName = Literal["telegram", "slack", "discord", "webhook", "pagerduty"]

DutyCategory = Literal[
    "attestation",
    "proposal",
    "sync",
    "vote",
    "block",
    "chunk",
    "endorsement",
    "collation",
    "round",
    "checkpoint",
    "poll",
    "leader_slot",
    "other",
]

RiskKind = Literal[
    "slashing",
    "kickout",
    "jail",
    "tombstone",
    "suspension",
    "reward_loss",
    "operational",
]

ProtocolEventKind = Literal[
    "slashed",
    "jailed",
    "kicked",
    "suspended",
    "tombstoned",
    "kes_expired",
    "delinquent",
    "high_skip_rate",
    "rpc_error",
    "other",
]


class DutyStats(BaseModel):
    """Chain-agnostic duty aggregate for one category."""

    category: DutyCategory
    label: str
    expected: int | None = None
    successful: int = 0
    missed: int = 0
    late: int = 0
    weight: float = 1.0


class DutyEvent(BaseModel):
    """Single duty observation for recent-history UI / scoring."""

    operator_id: str
    category: DutyCategory
    label: str
    outcome: DutyOutcome
    epoch: int | None = None
    slot: int | None = None
    inclusion_delay: int | None = None
    reward_base_units: int | None = None


class ProtocolEvent(BaseModel):
    kind: ProtocolEventKind
    severity: AlertSeverity
    message: str
    confirmed: bool = True


class AttestationDuty(BaseModel):
    epoch: int
    slot: int
    validator_index: int
    outcome: DutyOutcome
    inclusion_delay: int | None = None
    reward_gwei: int | None = None


class ProposalDuty(BaseModel):
    epoch: int
    slot: int
    validator_index: int
    outcome: DutyOutcome
    reward_gwei: int | None = None


class AttestationStats(BaseModel):
    expected: int
    successful: int
    missed: int
    late: int


class ProposalStats(BaseModel):
    expected: int
    successful: int
    missed: int


class ValidatorStats(BaseModel):
    """Operator health for one monitored identity.

    Canonical fields are chain-agnostic (`operator_id`, `*_base_units`, `risk_*`,
    `duties`). Ethereum/Polkadot keep legacy aliases (`index`, `*_gwei`,
    `attestations`/`proposals`, `slashing_risk_score`) for API compatibility.
    """

    index: int = 0
    operator_id: str | None = None
    operator_index: int | None = None
    pubkey: str | None = None
    withdrawal_address: str | None = None
    display_name: str | None = None
    display_name_source: str | None = None
    status: str
    balance_gwei: int = 0
    effective_balance_gwei: int = 0
    balance_base_units: int | None = None
    effective_balance_base_units: int | None = None
    attestations: AttestationStats = Field(
        default_factory=lambda: AttestationStats(
            expected=0, successful=0, missed=0, late=0
        )
    )
    proposals: ProposalStats = Field(
        default_factory=lambda: ProposalStats(expected=0, successful=0, missed=0)
    )
    duties: list[DutyStats] = Field(default_factory=list)
    rewards_gwei: int = 0
    rewards_base_units: int | None = None
    attestation_rewards_gwei: int = 0
    proposal_rewards_gwei: int = 0
    sync_committee_rewards_gwei: int = 0
    reward_window_start_epoch: int | None = None
    reward_window_end_epoch: int | None = None
    reward_data_complete: bool = True
    effectiveness_score: float
    slashing_risk_score: float = 0.0
    risk_score: float | None = None
    risk_kind: RiskKind = "slashing"
    protocol_events: list[ProtocolEvent] = Field(default_factory=list)
    recent_attestations: list[AttestationDuty] = Field(default_factory=list)
    recent_proposals: list[ProposalDuty] = Field(default_factory=list)
    recent_duties: list[DutyEvent] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _sync_compat_inputs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)

        if out.get("index") is None and out.get("operator_index") is not None:
            out["index"] = out["operator_index"]
        # Do not invent operator_index from index — account-ID chains leave it unset.

        for legacy, modern in (
            ("balance_gwei", "balance_base_units"),
            ("effective_balance_gwei", "effective_balance_base_units"),
            ("rewards_gwei", "rewards_base_units"),
        ):
            if out.get(modern) is None and out.get(legacy) is not None:
                out[modern] = out[legacy]
            if out.get(legacy) is None and out.get(modern) is not None:
                out[legacy] = out[modern]

        if out.get("risk_score") is None and out.get("slashing_risk_score") is not None:
            out["risk_score"] = out["slashing_risk_score"]
        if (
            out.get("slashing_risk_score") is None
            and out.get("risk_score") is not None
        ):
            out["slashing_risk_score"] = out["risk_score"]

        return out

    @model_validator(mode="after")
    def _fill_derived_fields(self) -> ValidatorStats:
        if not self.operator_id:
            if self.pubkey:
                self.operator_id = self.pubkey
            elif self.operator_index is not None:
                self.operator_id = str(self.operator_index)
            else:
                self.operator_id = str(self.index)
        if self.balance_base_units is None:
            self.balance_base_units = self.balance_gwei
        else:
            self.balance_gwei = self.balance_base_units
        if self.effective_balance_base_units is None:
            self.effective_balance_base_units = self.effective_balance_gwei
        else:
            self.effective_balance_gwei = self.effective_balance_base_units
        if self.rewards_base_units is None:
            self.rewards_base_units = self.rewards_gwei
        else:
            self.rewards_gwei = self.rewards_base_units
        if self.risk_score is None:
            self.risk_score = self.slashing_risk_score
        else:
            self.slashing_risk_score = self.risk_score
        if not self.duties:
            self.duties = [
                DutyStats(
                    category="attestation",
                    label="Attestations",
                    expected=self.attestations.expected,
                    successful=self.attestations.successful,
                    missed=self.attestations.missed,
                    late=self.attestations.late,
                    weight=0.85,
                ),
                DutyStats(
                    category="proposal",
                    label="Proposals",
                    expected=self.proposals.expected,
                    successful=self.proposals.successful,
                    missed=self.proposals.missed,
                    weight=0.15 if self.proposals.expected else 0.0,
                ),
            ]
        elif (
            self.attestations.expected == 0
            and self.attestations.successful == 0
            and self.attestations.missed == 0
            and self.proposals.expected == 0
        ):
            # Duties-first adapters: mirror primary/secondary into Eth-era fields.
            ordered = sorted(self.duties, key=lambda d: d.weight, reverse=True)
            primary = ordered[0]
            self.attestations = AttestationStats(
                expected=primary.expected or 0,
                successful=primary.successful,
                missed=primary.missed,
                late=primary.late,
            )
            if len(ordered) > 1:
                secondary = ordered[1]
                self.proposals = ProposalStats(
                    expected=secondary.expected or 0,
                    successful=secondary.successful,
                    missed=secondary.missed,
                )
        return self

    def primary_duty(self) -> DutyStats:
        if self.duties:
            return max(self.duties, key=lambda d: d.weight)
        return DutyStats(
            category="attestation",
            label="Attestations",
            expected=self.attestations.expected,
            successful=self.attestations.successful,
            missed=self.attestations.missed,
            late=self.attestations.late,
        )


# Preferred name for heterogeneous chains; same schema as ValidatorStats.
OperatorStats = ValidatorStats


class ConsensusHealth(BaseModel):
    beacon_reachable: bool
    syncing: bool
    sync_distance: int
    head_slot: int
    finalized_epoch: int
    justified_epoch: int
    peer_count: int
    connected_peers: int
    status: HealthStatus
    last_error: str | None = None


class InfrastructureHealth(BaseModel):
    cpu_usage_percent: float
    memory_usage_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    disk_usage_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_latency_ms: float
    network_healthy: bool
    network_rx_bytes_per_sec: float
    network_tx_bytes_per_sec: float
    clock_drift_ms: float
    status: HealthStatus


class AlertEvent(BaseModel):
    id: str
    severity: AlertSeverity
    title: str
    message: str
    source: AlertSource
    created_at: str
    channels: list[AlertChannelName]
    delivered: bool = False


class FleetMetrics(BaseModel):
    validator_effectiveness_score: float
    validator_missed_attestations_total: int
    validator_slashing_risk_score: float
    effectiveness_score: float | None = None
    missed_primary_duties_total: int | None = None
    risk_score: float | None = None

    @model_validator(mode="after")
    def _sync_aliases(self) -> FleetMetrics:
        if self.effectiveness_score is None:
            self.effectiveness_score = self.validator_effectiveness_score
        else:
            self.validator_effectiveness_score = self.effectiveness_score
        if self.missed_primary_duties_total is None:
            self.missed_primary_duties_total = (
                self.validator_missed_attestations_total
            )
        else:
            self.validator_missed_attestations_total = (
                self.missed_primary_duties_total
            )
        if self.risk_score is None:
            self.risk_score = self.validator_slashing_risk_score
        else:
            self.validator_slashing_risk_score = self.risk_score
        return self


class Verdict(BaseModel):
    status: HealthStatus
    answer: str
    summary: str


class PulseSnapshot(BaseModel):
    collected_at: str
    demo_mode: bool
    schema_version: int = 2
    chain: str = "ethereum"
    chain_display_name: str = "Ethereum"
    operator_label: str = "validator"
    risk_kind: RiskKind = "slashing"
    risk_label: str = "Slashing risk"
    primary_duty_label: str = "Attestations"
    secondary_duty_label: str = "Proposals"
    missed_duty_label: str = "Missed attestations"
    consensus_node_label: str = "Beacon"
    parachain_id: int | None = None
    reward_token_symbol: str = "ETH"
    reward_token_decimals: int = 9
    reward_token_base_unit: str = "gwei"
    verdict: Verdict
    validators: list[ValidatorStats]
    consensus: ConsensusHealth
    infrastructure: InfrastructureHealth
    metrics: FleetMetrics
    recent_alerts: list[AlertEvent] = Field(default_factory=list)
    configured_channels: list[AlertChannelName] = Field(default_factory=list)

    @property
    def operators(self) -> list[ValidatorStats]:
        """Alias for validators — preferred name for heterogeneous chains."""
        return self.validators
