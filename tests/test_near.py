from __future__ import annotations

import asyncio

from validator_pulse.alerts import evaluate_alerts
from validator_pulse.chains.near.adapter import NearAdapter
from validator_pulse.chains.near.demo import demo_account_id, near_effectiveness
from validator_pulse.chains.near.epoch import EpochSnapshotStore
from validator_pulse.chains.near.rpc import format_kickout_reason
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_near_effectiveness_weights_blocks_highest() -> None:
    # Perfect chunks/endorsements but missing all blocks → well below 100.
    score = near_effectiveness(
        expected_blocks=10,
        produced_blocks=0,
        expected_chunks=100,
        produced_chunks=100,
        expected_endorsements=100,
        produced_endorsements=100,
    )
    assert score < 60


def test_epoch_snapshot_resets_without_negative_deltas() -> None:
    store = EpochSnapshotStore()
    first, reset = store.observe(
        "pool.near",
        epoch_height=10,
        expected_blocks=10,
        produced_blocks=8,
        expected_chunks=100,
        produced_chunks=90,
        expected_endorsements=200,
        produced_endorsements=180,
    )
    assert reset is True
    assert first.produced_blocks == 8

    # Regression within epoch is ignored (no negative delta).
    same, reset2 = store.observe(
        "pool.near",
        epoch_height=10,
        expected_blocks=10,
        produced_blocks=7,
        expected_chunks=100,
        produced_chunks=80,
        expected_endorsements=200,
        produced_endorsements=170,
    )
    assert reset2 is False
    assert same.produced_blocks == 8
    assert same.produced_chunks == 90

    # New epoch replaces counters wholesale.
    nxt, reset3 = store.observe(
        "pool.near",
        epoch_height=11,
        expected_blocks=2,
        produced_blocks=1,
        expected_chunks=20,
        produced_chunks=18,
        expected_endorsements=40,
        produced_endorsements=39,
    )
    assert reset3 is True
    assert nxt.produced_blocks == 1
    assert nxt.expected_chunks == 20


def test_format_kickout_reason() -> None:
    assert "NotEnoughChunks" in format_kickout_reason({"NotEnoughChunks": {"produced": 1}})
    assert format_kickout_reason("NotEnoughBlocks") == "NotEnoughBlocks"


def test_near_demo_collect() -> None:
    settings = Settings(chain="near", demo_mode=True)
    adapter = NearAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "NEAR"
    assert adapter.risk_kind == "kickout"
    assert adapter.primary_duty_label == "Blocks"
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert "near_kickout" in statuses
    assert "set_transition" in statuses
    assert "slashed" in statuses
    slashed = next(op for op in collection.operators if op.status == "slashed")
    assert slashed.risk_score == 100
    assert any(e.kind == "slashed" for e in slashed.protocol_events)
    assert any(d.label == "Chunks" for d in collection.operators[0].duties)


def test_near_demo_pulse_alerts_and_token() -> None:
    settings = Settings(chain="near", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "near"
    assert snapshot.chain_display_name == "NEAR"
    assert snapshot.reward_token_symbol == "NEAR"
    assert snapshot.reward_token_decimals == 24
    assert snapshot.reward_token_base_unit == "yoctoNEAR"
    assert snapshot.risk_kind == "kickout"

    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "slashed" in titles
    assert "kickout" in titles


def test_near_live_requires_account_ids() -> None:
    settings = Settings(
        chain="near",
        demo_mode=False,
        near_rpc_url="http://127.0.0.1:1",
        near_validator_account_ids="",
    )
    adapter = NearAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "NEAR_VALIDATOR_ACCOUNT_IDS" in (collection.consensus.last_error or "")


def test_near_metrics_soft_fail_preserves_rpc_path() -> None:
    """Missing metrics URL is fine; empty accounts still reported via RPC path."""
    settings = Settings(
        chain="near",
        demo_mode=False,
        near_rpc_url="http://127.0.0.1:1",
        near_validator_account_ids="pool.near",
        near_metrics_url="http://127.0.0.1:9/metrics",
    )
    adapter = NearAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    # RPC is unreachable → operators may be failed, but collect must not raise.
    assert isinstance(collection.operators, list)
    err = collection.consensus.last_error or ""
    # Either metrics note and/or validators/transport error — soft path.
    assert err


def test_near_prometheus_labels() -> None:
    settings = Settings(chain="near", demo_mode=True)
    adapter = NearAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="near",
        chain_display_name="NEAR",
        operator_label="validator",
        risk_kind="kickout",
        risk_label="Kickout risk",
        primary_duty_label="Blocks",
        secondary_duty_label="Chunks",
        missed_duty_label="Missed production",
        consensus_node_label="NEAR RPC",
        reward_token_symbol="NEAR",
        reward_token_decimals=24,
        reward_token_base_unit="yoctoNEAR",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="near"' in text
    assert "operator_risk_score" in text
    assert demo_account_id("healthy") in text or any(
        op.operator_id in text for op in collection.operators
    )
