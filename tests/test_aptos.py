from __future__ import annotations

import asyncio

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.aptos.adapter import AptosAdapter
from validator_pulse.chains.aptos.demo import demo_pool_address
from validator_pulse.chains.aptos.epoch import EpochProposalStore
from validator_pulse.chains.aptos.scoring import (
    aptos_effectiveness,
    index_active_validators,
    parse_view_u64_pair,
    status_from_code,
)
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_aptos_effectiveness_weights_proposals() -> None:
    perfect = aptos_effectiveness(
        successful=10, failed=0, in_set=True, syncing=False
    )
    assert perfect == 100.0
    failed_heavy = aptos_effectiveness(
        successful=0, failed=10, in_set=True, syncing=False
    )
    assert failed_heavy < 40
    inactive = aptos_effectiveness(
        successful=10, failed=0, in_set=False, syncing=False
    )
    assert inactive == 0.0


def test_epoch_store_no_negative_deltas() -> None:
    store = EpochProposalStore()
    first, reset = store.observe(
        "0xabc", epoch=10, successful=5, failed=1, validator_index=3
    )
    assert reset is True
    assert first.successful == 5

    same, reset2 = store.observe(
        "0xabc", epoch=10, successful=4, failed=0, validator_index=3
    )
    assert reset2 is False
    assert same.successful == 5
    assert same.failed == 1

    nxt, reset3 = store.observe(
        "0xabc", epoch=11, successful=1, failed=0, validator_index=7
    )
    assert reset3 is True
    assert nxt.successful == 1
    assert nxt.validator_index == 7


def test_status_and_index_helpers() -> None:
    assert status_from_code(2) == "active"
    assert status_from_code(4) == "inactive"
    assert parse_view_u64_pair(["22", "3"]) == (22, 3)
    mapped = index_active_validators(
        {
            "active_validators": [
                {
                    "addr": "0xAa",
                    "config": {"validator_index": "5"},
                }
            ]
        }
    )
    assert mapped["0xaa"] == 5


def test_aptos_demo_collect() -> None:
    settings = Settings(chain="aptos", demo_mode=True)
    adapter = AptosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Aptos"
    assert adapter.risk_kind == "reward_loss"
    assert "slash" not in adapter.risk_label.lower()
    assert len(collection.operators) == 3
    statuses = {op.status for op in collection.operators}
    assert "active" in statuses
    assert "degraded" in statuses
    assert "inactive" in statuses
    inactive = next(op for op in collection.operators if op.status == "inactive")
    assert inactive.risk_score == 100
    assert any(d.label == "Proposals" for d in collection.operators[0].duties)


def test_aptos_demo_alerts_no_slashing_wording() -> None:
    settings = Settings(chain="aptos", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "aptos"
    assert snapshot.reward_token_symbol == "APT"
    assert snapshot.reward_token_decimals == 8
    assert snapshot.reward_token_base_unit == "octas"
    assert snapshot.risk_kind == "reward_loss"
    assert snapshot.risk_label == "Reward risk"

    alerts = evaluate_alerts(snapshot, settings)
    blob = " ".join(f"{a.title} {a.message}".lower() for a in alerts)
    assert "reward" in blob or "inactive" in blob or "failed" in blob
    assert "principal slash" not in blob
    # Generic risk alerts may say "slashing risk" via shared thresholds — ensure
    # Aptos-specific events and labels do not claim principal slashing.
    assert snapshot.risk_label.lower() != "slashing risk"

    verdict = build_verdict(snapshot)
    assert "reward" in verdict.summary.lower() or "risk" in verdict.summary.lower()


def test_aptos_live_requires_pools() -> None:
    settings = Settings(
        chain="aptos",
        demo_mode=False,
        aptos_rest_url="http://127.0.0.1:1/v1",
        aptos_pool_addresses="",
    )
    adapter = AptosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "APTOS_POOL_ADDRESSES" in (collection.consensus.last_error or "")


def test_aptos_metrics_soft_fail() -> None:
    settings = Settings(
        chain="aptos",
        demo_mode=False,
        aptos_rest_url="http://127.0.0.1:1/v1",
        aptos_pool_addresses=demo_pool_address("active"),
        aptos_metrics_url="http://127.0.0.1:9/metrics",
    )
    adapter = AptosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert isinstance(collection.operators, list)
    assert collection.consensus.last_error


def test_aptos_prometheus_labels() -> None:
    settings = Settings(chain="aptos", demo_mode=True)
    adapter = AptosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="aptos",
        chain_display_name="Aptos",
        operator_label="validator",
        risk_kind="reward_loss",
        risk_label="Reward risk",
        primary_duty_label="Proposals",
        secondary_duty_label="Set membership",
        missed_duty_label="Failed proposals",
        consensus_node_label="Aptos fullnode",
        reward_token_symbol="APT",
        reward_token_decimals=8,
        reward_token_base_unit="octas",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="aptos"' in text
    assert "operator_risk_score" in text
    assert demo_pool_address("active") in text or any(
        op.operator_id in text for op in collection.operators
    )
