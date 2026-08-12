from __future__ import annotations

import asyncio

from validator_pulse.alerts import evaluate_alerts
from validator_pulse.chains.tezos.adapter import TezosAdapter
from validator_pulse.chains.tezos.demo import demo_baker_address, tezos_effectiveness
from validator_pulse.chains.tezos.rights import RightsWindow
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_tezos_effectiveness_weights_attestations_and_baking() -> None:
    score = tezos_effectiveness(
        attest_expected=100,
        attest_ok=100,
        bake_expected=10,
        bake_ok=0,
    )
    assert 60 < score < 90


def test_rights_window_reorg_drops_pending() -> None:
    window = RightsWindow()
    window.observe_head(100, "hash100")
    window.ingest_baking_rights(
        [{"level": 101, "round": 0}, {"level": 102, "round": 0}],
        head_level=100,
    )
    assert window.totals()["bake_pending"] == 2
    window.observe_head(99, "hash99reorg")
    totals = window.totals()
    assert totals["bake_pending"] == 0


def test_rights_window_participation_reconciles_misses() -> None:
    window = RightsWindow()
    window.observe_head(105, "hash105")
    window.ingest_attestation_rights(
        [{"level": 100}, {"level": 101}, {"level": 102}, {"level": 103}],
        head_level=105,
    )
    window.apply_participation(missed_slots=2, missed_levels=1, expected_activity=4)
    totals = window.totals()
    assert totals["attest_missed"] == 2
    assert totals["attest_success"] == 2


def test_tezos_demo_collect() -> None:
    settings = Settings(chain="tezos", demo_mode=True)
    adapter = TezosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Tezos"
    assert adapter.operator_label == "baker"
    assert adapter.risk_kind == "slashing"
    assert adapter.primary_duty_label == "Attestations"
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert "forbidden" in statuses
    assert "near_reward_loss" in statuses
    assert "degraded" in statuses
    forbidden = next(op for op in collection.operators if op.status == "forbidden")
    assert forbidden.risk_score == 100
    assert any(e.kind == "slashed" for e in forbidden.protocol_events)


def test_tezos_demo_pulse_alerts_and_token() -> None:
    settings = Settings(chain="tezos", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "tezos"
    assert snapshot.chain_display_name == "Tezos"
    assert snapshot.reward_token_symbol == "XTZ"
    assert snapshot.reward_token_decimals == 6
    assert snapshot.reward_token_base_unit == "mutez"
    assert snapshot.risk_kind == "slashing"

    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "slashed" in titles or "forbidden" in titles
    assert "remaining miss" in titles


def test_tezos_live_requires_baker_addresses() -> None:
    settings = Settings(
        chain="tezos",
        demo_mode=False,
        tezos_rpc_url="http://127.0.0.1:1",
        tezos_baker_addresses="",
    )
    adapter = TezosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "TEZOS_BAKER_ADDRESSES" in (collection.consensus.last_error or "")


def test_tezos_metrics_soft_fail_preserves_rpc_path() -> None:
    settings = Settings(
        chain="tezos",
        demo_mode=False,
        tezos_rpc_url="http://127.0.0.1:1",
        tezos_baker_addresses=demo_baker_address("healthy"),
        tezos_metrics_url="http://127.0.0.1:9/metrics",
    )
    adapter = TezosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert isinstance(collection.operators, list)
    assert collection.consensus.last_error


def test_tezos_prometheus_labels() -> None:
    settings = Settings(chain="tezos", demo_mode=True)
    adapter = TezosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="tezos",
        chain_display_name="Tezos",
        operator_label="baker",
        risk_kind="slashing",
        risk_label="Slashing risk",
        primary_duty_label="Attestations",
        secondary_duty_label="Baking rights",
        missed_duty_label="Missed slots",
        consensus_node_label="Octez node",
        reward_token_symbol="XTZ",
        reward_token_decimals=6,
        reward_token_base_unit="mutez",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="tezos"' in text
    assert "operator_risk_score" in text
    assert demo_baker_address("healthy") in text or any(
        op.operator_id in text for op in collection.operators
    )
