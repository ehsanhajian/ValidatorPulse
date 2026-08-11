from __future__ import annotations

import asyncio

from validator_pulse.alerts import evaluate_alerts
from validator_pulse.chains.solana.adapter import SolanaAdapter
from validator_pulse.chains.solana.demo import demo_vote_account
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_solana_demo_collect() -> None:
    settings = Settings(chain="solana", demo_mode=True)
    adapter = SolanaAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Solana"
    assert adapter.primary_duty_label == "Epoch credits"
    assert adapter.missed_duty_label == "Skipped slots"
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert "delinquent" in statuses
    assert "high_skip" in statuses
    assert any(op.operator_id.startswith("Vote") for op in collection.operators)
    delinquent = next(op for op in collection.operators if op.status == "delinquent")
    assert delinquent.risk_score == 100
    assert any(e.kind == "delinquent" for e in delinquent.protocol_events)


def test_solana_demo_pulse_alerts_and_token() -> None:
    settings = Settings(chain="solana", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "solana"
    assert snapshot.chain_display_name == "Solana"
    assert snapshot.reward_token_symbol == "SOL"
    assert snapshot.reward_token_decimals == 9
    assert snapshot.reward_token_base_unit == "lamports"
    assert snapshot.primary_duty_label == "Epoch credits"

    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "delinquent" in titles
    assert "skip rate" in titles


def test_solana_live_requires_vote_or_identity() -> None:
    settings = Settings(
        chain="solana",
        demo_mode=False,
        solana_rpc_url="http://127.0.0.1:1",
        validator_vote_accounts="",
        solana_identity_pubkeys="",
    )
    adapter = SolanaAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "VALIDATOR_VOTE_ACCOUNTS" in (collection.consensus.last_error or "")


def test_solana_metrics_and_skip_duties() -> None:
    settings = Settings(chain="solana", demo_mode=True)
    adapter = SolanaAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    assert metrics.missed_primary_duties_total > 0
    high_skip = next(op for op in collection.operators if op.status == "high_skip")
    leader = next(d for d in high_skip.duties if d.label == "Leader slots")
    assert leader.missed > 0

    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="solana",
        chain_display_name="Solana",
        operator_label="validator",
        risk_kind="slashing",
        risk_label="Slashing risk",
        primary_duty_label="Epoch credits",
        secondary_duty_label="Leader slots",
        missed_duty_label="Skipped slots",
        consensus_node_label="Solana RPC",
        reward_token_symbol="SOL",
        reward_token_decimals=9,
        reward_token_base_unit="lamports",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="solana"' in text
    assert "operator_risk_score" in text
    assert demo_vote_account("healthy") in text or any(
        op.operator_id in text for op in collection.operators
    )
