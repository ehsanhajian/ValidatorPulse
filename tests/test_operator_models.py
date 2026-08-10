from __future__ import annotations

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.config import Settings
from validator_pulse.metrics import to_prometheus
from validator_pulse.models import (
    DutyEvent,
    DutyStats,
    OperatorStats,
    ProtocolEvent,
    PulseSnapshot,
    ValidatorStats,
)
from validator_pulse.scoring import aggregate_fleet_metrics


def _consensus(**overrides):
    base = {
        "beacon_reachable": True,
        "syncing": False,
        "sync_distance": 0,
        "head_slot": 100,
        "finalized_epoch": 1,
        "justified_epoch": 2,
        "peer_count": 10,
        "connected_peers": 10,
        "status": "healthy",
    }
    base.update(overrides)
    return base


def _infra(**overrides):
    base = {
        "cpu_usage_percent": 10,
        "memory_usage_percent": 20,
        "memory_used_bytes": 1,
        "memory_total_bytes": 2,
        "disk_usage_percent": 30,
        "disk_used_bytes": 1,
        "disk_total_bytes": 2,
        "disk_latency_ms": 1,
        "network_healthy": True,
        "network_rx_bytes_per_sec": 0,
        "network_tx_bytes_per_sec": 0,
        "clock_drift_ms": 1,
        "status": "healthy",
    }
    base.update(overrides)
    return base


def near_like_operator() -> OperatorStats:
    """Account-ID chain fixture (NEAR-style): string identity, chunk/block duties, kickout risk."""
    return OperatorStats(
        operator_id="validatorpulse.near",
        status="active",
        balance_base_units=50_000_000_000_000_000_000_000_000,
        effective_balance_base_units=50_000_000_000_000_000_000_000_000,
        duties=[
            DutyStats(
                category="chunk",
                label="Chunks",
                expected=100,
                successful=97,
                missed=3,
                late=0,
                weight=0.7,
            ),
            DutyStats(
                category="block",
                label="Blocks",
                expected=10,
                successful=9,
                missed=1,
                weight=0.3,
            ),
        ],
        rewards_base_units=1_250_000_000_000_000_000_000_000,
        effectiveness_score=96.5,
        risk_score=22.0,
        risk_kind="kickout",
        recent_duties=[
            DutyEvent(
                operator_id="validatorpulse.near",
                category="chunk",
                label="Chunks",
                outcome="success",
            ),
            DutyEvent(
                operator_id="validatorpulse.near",
                category="chunk",
                label="Chunks",
                outcome="missed",
            ),
        ],
    )


def cardano_like_operator() -> OperatorStats:
    """Local-metrics chain fixture (Cardano-style): pool id, blocks/polls, KES suspension risk."""
    return OperatorStats(
        operator_id="pool1xyza…cdef",
        status="registered",
        balance_base_units=0,
        effective_balance_base_units=0,
        duties=[
            DutyStats(
                category="block",
                label="Blocks",
                expected=12,
                successful=11,
                missed=1,
                weight=0.8,
            ),
            DutyStats(
                category="poll",
                label="Governance polls",
                expected=2,
                successful=2,
                missed=0,
                weight=0.2,
            ),
        ],
        rewards_base_units=-150_000,  # signed lovelace window can be negative
        effectiveness_score=91.0,
        risk_score=48.0,
        risk_kind="suspension",
        protocol_events=[
            ProtocolEvent(
                kind="kes_expired",
                severity="warning",
                message="KES keys expire within 2 days",
                confirmed=False,
            )
        ],
    )


def test_operator_stats_alias_and_string_id_without_index() -> None:
    op = near_like_operator()
    assert isinstance(op, ValidatorStats)
    assert op.operator_id == "validatorpulse.near"
    assert op.operator_index is None
    assert op.balance_base_units == op.balance_gwei
    assert op.risk_score == op.slashing_risk_score
    assert op.primary_duty().category == "chunk"
    assert op.attestations.missed == 3
    assert op.proposals.missed == 1


def test_cardano_like_signed_rewards_and_risk_kind() -> None:
    op = cardano_like_operator()
    assert op.rewards_base_units == -150_000
    assert op.rewards_gwei == -150_000
    assert op.risk_kind == "suspension"
    assert op.protocol_events[0].kind == "kes_expired"


def test_heterogeneous_snapshot_avoids_slashing_wording() -> None:
    operators = [near_like_operator()]
    metrics = aggregate_fleet_metrics(operators)
    assert metrics.missed_primary_duties_total == 3
    assert metrics.risk_score == 22.0

    snapshot = PulseSnapshot(
        collected_at="2026-08-10T00:00:00+00:00",
        demo_mode=True,
        schema_version=2,
        chain="near",
        chain_display_name="NEAR",
        operator_label="validator",
        risk_kind="kickout",
        risk_label="Kickout risk",
        primary_duty_label="Chunks",
        secondary_duty_label="Blocks",
        missed_duty_label="Missed chunks",
        consensus_node_label="NEAR node",
        reward_token_symbol="NEAR",
        reward_token_decimals=24,
        reward_token_base_unit="yocto",
        verdict={"status": "healthy", "answer": "Yes", "summary": "ok"},
        validators=operators,
        consensus=_consensus(),
        infrastructure=_infra(),
        metrics=metrics,
    )
    verdict = build_verdict(snapshot)
    assert "kickout risk" in verdict.summary.lower()
    assert "slashing" not in verdict.summary.lower()

    settings = Settings(alert_missed_attestations=2, alert_slashing_risk_above=40)
    alerts = evaluate_alerts(snapshot, settings)
    assert any("chunk" in a.title.lower() for a in alerts)
    assert not any("slash" in a.title.lower() for a in alerts)

    text = to_prometheus(snapshot)
    assert 'operator_id="validatorpulse.near"' in text
    assert 'chain="near"' in text
    assert "operator_risk_score" in text
    assert "operator_rewards_base_units" in text
    # Legacy series remain for scrapers mid-migration.
    assert "validator_slashing_risk_score" in text
    assert "validator_rewards_gwei" in text


def test_legacy_gwei_construction_still_works() -> None:
    op = ValidatorStats(
        index=7,
        operator_index=7,
        status="active_ongoing",
        balance_gwei=32_000_000_000,
        effective_balance_gwei=32_000_000_000,
        attestations={
            "expected": 32,
            "successful": 32,
            "missed": 0,
            "late": 0,
        },
        proposals={"expected": 0, "successful": 0, "missed": 0},
        rewards_gwei=12,
        effectiveness_score=100,
        slashing_risk_score=5,
    )
    assert op.operator_id == "7"
    assert op.balance_base_units == 32_000_000_000
    assert op.risk_score == 5
    assert op.duties[0].category == "attestation"
