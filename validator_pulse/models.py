from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HealthStatus = Literal["healthy", "degraded", "critical", "unknown"]
DutyOutcome = Literal["success", "missed", "late", "pending"]
AlertSeverity = Literal["info", "warning", "critical"]
AlertSource = Literal["validator", "consensus", "infrastructure", "system"]
AlertChannelName = Literal["telegram", "slack", "discord", "webhook", "pagerduty"]


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
    index: int
    pubkey: str | None = None
    display_name: str | None = None
    status: str
    balance_gwei: int
    effective_balance_gwei: int
    attestations: AttestationStats
    proposals: ProposalStats
    rewards_gwei: int
    effectiveness_score: float
    slashing_risk_score: float
    recent_attestations: list[AttestationDuty] = Field(default_factory=list)
    recent_proposals: list[ProposalDuty] = Field(default_factory=list)


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


class Verdict(BaseModel):
    status: HealthStatus
    answer: str
    summary: str


class PulseSnapshot(BaseModel):
    collected_at: str
    demo_mode: bool
    chain: str = "ethereum"
    chain_display_name: str = "Ethereum"
    operator_label: str = "validator"
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
