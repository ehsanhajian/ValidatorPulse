from __future__ import annotations

import asyncio

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.cardano.adapter import CardanoAdapter
from validator_pulse.chains.cardano.counters import CounterSnapshotStore
from validator_pulse.chains.cardano.demo import demo_pool_id
from validator_pulse.chains.cardano.metrics import extract_tracer_metrics, parse_prometheus_text
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics

_SAMPLE_METRICS = """
# TYPE cardano_node_metrics_blocksForged_int counter
cardano_node_metrics_blocksForged_int 12
# TYPE cardano_node_metrics_slotsMissed_int counter
cardano_node_metrics_slotsMissed_int 2
# TYPE cardano_node_metrics_Forge_about_to_lead_counter counter
cardano_node_metrics_Forge_about_to_lead_counter 14
# TYPE cardano_node_metrics_nodeCannotForge_int counter
cardano_node_metrics_nodeCannotForge_int 1
# TYPE remainingKESPeriods_int gauge
remainingKESPeriods_int 8
# TYPE cardano_node_metrics_epoch_int gauge
cardano_node_metrics_epoch_int 450
# TYPE cardano_node_metrics_slotNum_int gauge
cardano_node_metrics_slotNum_int 12345678
# TYPE cardano_node_metrics_peerSelection_ActivePeers_int gauge
cardano_node_metrics_peerSelection_ActivePeers_int 24
"""


def test_parse_tracer_metrics() -> None:
    parsed = parse_prometheus_text(_SAMPLE_METRICS)
    metrics = extract_tracer_metrics(parsed)
    assert metrics.blocks_forged == 12
    assert metrics.slots_missed == 2
    assert metrics.leader_opportunities == 14
    assert metrics.remaining_kes_periods == 8
    assert metrics.epoch == 450


def test_counter_deltas_without_double_counting() -> None:
    store = CounterSnapshotStore()
    d1 = store.observe(
        "pool1",
        blocks_forged=10,
        slots_missed=1,
        leader_opportunities=12,
        cannot_forge=0,
    )
    assert d1.reset is True
    d2 = store.observe(
        "pool1",
        blocks_forged=12,
        slots_missed=2,
        leader_opportunities=14,
        cannot_forge=1,
    )
    assert d2.forged == 2
    assert d2.missed == 1
    assert d2.opportunities == 2
    assert d2.cannot_forge == 1


def test_cardano_demo_collect() -> None:
    settings = Settings(chain="cardano", demo_mode=True)
    adapter = CardanoAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Cardano"
    assert adapter.risk_kind == "suspension"
    assert adapter.risk_label == "Operational risk"
    assert "slashing" not in adapter.risk_label.lower()
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert "kes_expired" in statuses
    assert "missed_slots" not in statuses  # status is degraded
    assert "degraded" in statuses
    expired = next(op for op in collection.operators if op.status == "kes_expired")
    assert expired.risk_score == 100
    assert any(e.kind == "kes_expired" for e in expired.protocol_events)


def test_cardano_demo_alerts_and_no_slashing_wording() -> None:
    settings = Settings(chain="cardano", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "cardano"
    assert snapshot.reward_token_symbol == "ADA"
    assert snapshot.reward_token_base_unit == "lovelace"
    assert snapshot.risk_kind == "suspension"
    assert snapshot.risk_label == "Operational risk"

    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "kes" in titles
    assert "slash" not in titles

    verdict = build_verdict(snapshot)
    assert "operational" in verdict.summary.lower()
    assert "slash" not in verdict.summary.lower()


def test_cardano_live_requires_pool_ids() -> None:
    settings = Settings(
        chain="cardano",
        demo_mode=False,
        cardano_tracer_url="http://127.0.0.1:1",
        cardano_pool_ids="",
    )
    adapter = CardanoAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "CARDANO_POOL_IDS" in (collection.consensus.last_error or "")


def test_cardano_tracer_unavailable_unknown_duty() -> None:
    settings = Settings(
        chain="cardano",
        demo_mode=False,
        cardano_tracer_url="http://127.0.0.1:1",
        cardano_pool_ids="pool1abc",
    )
    adapter = CardanoAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert len(collection.operators) == 1
    op = collection.operators[0]
    assert op.duties[0].expected is None
    assert op.status in {"unknown", "kes_critical", "kes_expired"}


def test_kes_warning_and_critical_thresholds_in_demo() -> None:
    settings = Settings(
        chain="cardano",
        demo_mode=True,
        alert_cardano_kes_warning=5,
        alert_cardano_kes_critical=1,
    )
    adapter = CardanoAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    warning = next(op for op in collection.operators if op.status == "kes_warning")
    assert any(e.kind == "kes_expired" and e.severity == "warning" for e in warning.protocol_events)
    expired = next(op for op in collection.operators if op.status == "kes_expired")
    assert any(e.kind == "kes_expired" and e.severity == "critical" for e in expired.protocol_events)


def test_cardano_prometheus_labels() -> None:
    settings = Settings(chain="cardano", demo_mode=True)
    adapter = CardanoAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="cardano",
        chain_display_name="Cardano",
        operator_label="stake pool",
        risk_kind="suspension",
        risk_label="Operational risk",
        primary_duty_label="Leader slots",
        secondary_duty_label="Blocks forged",
        missed_duty_label="Missed slots",
        consensus_node_label="Block producer",
        reward_token_symbol="ADA",
        reward_token_decimals=6,
        reward_token_base_unit="lovelace",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="cardano"' in text
    assert "operator_risk_score" in text
    assert demo_pool_id("healthy") in text or any(
        op.operator_id in text for op in collection.operators
    )
