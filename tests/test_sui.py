from __future__ import annotations

import asyncio
from pathlib import Path

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.sui.adapter import SuiAdapter
from validator_pulse.chains.sui.counters import MetricsDeltaStore
from validator_pulse.chains.sui.demo import demo_validator_address
from validator_pulse.chains.sui.graphql import parse_report_records, parse_validator_node
from validator_pulse.chains.sui.metrics import (
    extract_sui_metrics,
    parse_prometheus_text,
    sui_effectiveness,
)
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics

_SUI_PKG = Path(__file__).resolve().parents[1] / "validator_pulse" / "chains" / "sui"

_SAMPLE_METRICS = """
# TYPE consensus_proposed_blocks counter
consensus_proposed_blocks{force="false"} 40
consensus_proposed_blocks{force="true"} 2
# TYPE highest_synced_checkpoint gauge
highest_synced_checkpoint 1000
# TYPE last_executed_checkpoint gauge
last_executed_checkpoint 998
# TYPE connected_peers gauge
connected_peers 18
"""


def test_sui_package_has_no_jsonrpc() -> None:
    """Acceptance: prove the Sui adapter never calls deprecated JSON-RPC."""
    for path in _SUI_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert '"jsonrpc"' not in lowered
        assert "'jsonrpc'" not in lowered
        assert "jsonrpc:" not in lowered
        assert "sui_gettotaltransactionblocks" not in lowered
        assert "sui_getlatestcheckpointsequence" not in lowered
        # Must talk to GraphQL, not a generic JSON-RPC path.
        if path.name == "graphql.py":
            assert "graphql" in lowered
            assert "query" in lowered


def test_parse_metrics_and_deltas_no_double_count() -> None:
    parsed = parse_prometheus_text(_SAMPLE_METRICS)
    metrics = extract_sui_metrics(parsed)
    assert metrics.proposed_blocks == 42
    assert metrics.highest_synced_checkpoint == 1000

    store = MetricsDeltaStore()
    d1 = store.observe("v1", proposals=40, checkpoint=900)
    assert d1.reset is True
    d2 = store.observe("v1", proposals=42, checkpoint=1000)
    assert d2.proposals == 2
    assert d2.checkpoint == 100
    # Regression ignored via reset path
    d3 = store.observe("v1", proposals=10, checkpoint=50)
    assert d3.reset is True


def test_parse_validator_and_reports() -> None:
    node = {
        "atRisk": 2,
        "contents": {
            "json": {
                "metadata": {"sui_address": "0xAbc", "name": "Demo"},
                "voting_power": "10",
                "gas_price": "1000",
                "commission_rate": "500",
                "staking_pool": {"sui_balance": "1000"},
            }
        },
    }
    info = parse_validator_node(node)
    assert info is not None
    assert info.address == "0xAbc"
    assert info.at_risk == 2
    reports = parse_report_records(
        {
            "validator_report_records": {
                "contents": [
                    {
                        "key": "0xreporter",
                        "value": {"contents": ["0xabc"]},
                    }
                ]
            }
        }
    )
    assert "0xabc" in reports
    assert "0xreporter" in reports["0xabc"]


def test_sui_effectiveness_distinguishes_risks() -> None:
    healthy = sui_effectiveness(
        in_set=True,
        proposals_delta=5,
        checkpoint_advancing=True,
        at_risk_epochs=0,
        reported=False,
        safe_mode=False,
        metrics_available=True,
    )
    assert healthy == 100.0
    reported = sui_effectiveness(
        in_set=True,
        proposals_delta=5,
        checkpoint_advancing=True,
        at_risk_epochs=0,
        reported=True,
        safe_mode=False,
        metrics_available=True,
    )
    assert reported == 0.0


def test_sui_demo_collect() -> None:
    settings = Settings(chain="sui", demo_mode=True)
    adapter = SuiAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Sui"
    assert adapter.risk_kind == "reward_loss"
    assert len(collection.operators) == 3
    statuses = {op.status for op in collection.operators}
    assert "active" in statuses
    assert "at_risk" in statuses
    assert "reward_slashed" in statuses
    critical = next(op for op in collection.operators if op.status == "reward_slashed")
    assert critical.risk_score == 100
    assert any(e.kind == "slashed" for e in critical.protocol_events)


def test_sui_demo_alerts_distinct() -> None:
    settings = Settings(chain="sui", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "sui"
    assert snapshot.reward_token_symbol == "SUI"
    assert snapshot.reward_token_base_unit == "MIST"
    assert snapshot.risk_kind == "reward_loss"

    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "reward slash" in titles or "reward slashing" in titles
    assert "atrisk" in titles.replace("-", "").replace(" ", "") or "at risk" in titles or "low-stake" in titles

    verdict = build_verdict(snapshot)
    assert "reward" in verdict.summary.lower() or "risk" in verdict.summary.lower()


def test_sui_live_requires_addresses() -> None:
    settings = Settings(
        chain="sui",
        demo_mode=False,
        sui_graphql_url="http://127.0.0.1:1/graphql",
        sui_validator_addresses="",
    )
    adapter = SuiAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "SUI_VALIDATOR_ADDRESSES" in (collection.consensus.last_error or "")


def test_sui_metrics_soft_fail_preserves_path() -> None:
    settings = Settings(
        chain="sui",
        demo_mode=False,
        sui_graphql_url="http://127.0.0.1:1/graphql",
        sui_validator_addresses=demo_validator_address("healthy"),
        sui_metrics_url="http://127.0.0.1:9/metrics",
    )
    adapter = SuiAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert isinstance(collection.operators, list)
    assert collection.consensus.last_error


def test_sui_prometheus_labels() -> None:
    settings = Settings(chain="sui", demo_mode=True)
    adapter = SuiAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="sui",
        chain_display_name="Sui",
        operator_label="validator",
        risk_kind="reward_loss",
        risk_label="Reward risk",
        primary_duty_label="Proposals",
        secondary_duty_label="Checkpoints",
        missed_duty_label="Stalled progress",
        consensus_node_label="Sui GraphQL",
        reward_token_symbol="SUI",
        reward_token_decimals=9,
        reward_token_base_unit="MIST",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="sui"' in text
    assert "operator_risk_score" in text
