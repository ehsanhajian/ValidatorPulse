from __future__ import annotations

import asyncio

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.avalanche.adapter import AvalancheAdapter
from validator_pulse.chains.avalanche.demo import demo_node_id
from validator_pulse.chains.avalanche.rpc import (
    avalanche_endpoints,
    extract_avalanche_metrics,
    normalize_node_id,
    parse_current_validator,
    parse_prometheus_text,
)
from validator_pulse.chains.avalanche.state import (
    recovery_runway,
    resolve_uptime_threshold,
    parse_uptime_percent,
)
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_parse_uptime_ratio_and_percent() -> None:
    assert parse_uptime_percent("0.9876") == 98.76
    assert parse_uptime_percent("99.0000") == 99.0
    assert parse_uptime_percent("99.2665") == 99.2665
    assert parse_uptime_percent(None) is None


def test_node_id_and_endpoints() -> None:
    assert normalize_node_id("abc") == "NodeID-abc"
    assert normalize_node_id("NodeID-abc") == "NodeID-abc"
    p, info, health, metrics = avalanche_endpoints("http://127.0.0.1:9650")
    assert p.endswith("/ext/bc/P")
    assert info.endswith("/ext/info")
    assert health.endswith("/ext/health")
    assert metrics.endswith("/ext/metrics")
    p2, *_ = avalanche_endpoints("https://api.avax.network/ext/bc/P")
    assert p2.endswith("/ext/bc/P")
    assert "api.avax.network" in p2


def test_threshold_source_helicon_and_config() -> None:
    pre = resolve_uptime_threshold(start_time=1, helicon_ts=100, override=None)
    assert pre.percent == 80.0
    assert pre.source == "pre-helicon"
    post = resolve_uptime_threshold(start_time=200, helicon_ts=100, override=None)
    assert post.percent == 90.0
    assert post.source == "helicon-upgrade"
    cfg = resolve_uptime_threshold(start_time=200, helicon_ts=100, override=85)
    assert cfg.percent == 85.0
    assert cfg.source == "config"
    default = resolve_uptime_threshold(start_time=1, helicon_ts=None, override=None)
    assert default.source == "network-default-pre-helicon"


def test_recovery_runway_warns_before_impossible() -> None:
    now = 1_000_000.0
    start = int(now - 80)
    end = int(now + 20)
    # 50% so far, 20 remaining of 100 total → max final 70% < 80%
    dead = recovery_runway(
        uptime_pct=50.0, start_time=start, end_time=end, now=now, requirement_pct=80.0
    )
    assert dead.possible is False
    healthy = recovery_runway(
        uptime_pct=99.0,
        start_time=int(now - 10),
        end_time=int(now + 90),
        now=now,
        requirement_pct=80.0,
    )
    assert healthy.possible is True
    assert healthy.slack_seconds > 0


def test_parse_validator_and_metrics() -> None:
    row = parse_current_validator(
        {
            "nodeID": "NodeID-x",
            "startTime": "1700000000",
            "endTime": "1730000000",
            "stakeAmount": "2000000000000",
            "uptime": "0.95",
            "connected": True,
            "txID": "tx1",
            "potentialReward": "1",
        }
    )
    assert row.node_id == "NodeID-x"
    assert row.uptime_pct == 95.0
    assert row.connected is True
    metrics = extract_avalanche_metrics(
        parse_prometheus_text(
            """
avalanche_network_peers 12
avalanche_snowman_polls_successful 80
avalanche_snowman_polls_failed 20
avalanche_network_connected_percent 97.5
"""
        )
    )
    assert metrics.peers == 12
    assert metrics.poll_success_ratio == 0.8
    assert metrics.connected_stake == 97.5


def test_avalanche_demo_collect() -> None:
    settings = Settings(chain="avalanche", demo_mode=True)
    adapter = AvalancheAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.risk_kind == "reward_loss"
    assert "slash" not in adapter.risk_label.lower()
    assert len(collection.operators) == 3
    statuses = {op.status for op in collection.operators}
    assert "active" in statuses
    assert "degraded" in statuses
    assert "forfeiture" in statuses
    forfeited = next(op for op in collection.operators if op.status == "forfeiture")
    blob = " ".join(e.message.lower() for e in forfeited.protocol_events)
    assert "forfeit" in blob
    assert "not" in blob and "slash" in blob


def test_avalanche_demo_alerts_never_call_slashing() -> None:
    settings = Settings(chain="avalanche", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "avalanche"
    assert snapshot.reward_token_symbol == "AVAX"
    assert snapshot.risk_kind == "reward_loss"
    alerts = evaluate_alerts(snapshot, settings)
    blob = " ".join(f"{a.title} {a.message}".lower() for a in alerts)
    assert "slash" not in blob or "not slashing" in blob or "not principal slash" in blob
    assert "forfeit" in blob or "runway" in blob or "eligibility" in blob
    assert not any("slashing" in a.title.lower() for a in alerts)
    verdict = build_verdict(snapshot)
    assert "slash" not in verdict.summary.lower() or "reward" in verdict.summary.lower()


def test_avalanche_live_requires_node_ids() -> None:
    settings = Settings(
        chain="avalanche",
        demo_mode=False,
        avalanche_rpc_url="http://127.0.0.1:1",
        avalanche_node_ids="",
    )
    adapter = AvalancheAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "AVALANCHE_NODE_IDS" in (collection.consensus.last_error or "")


def test_avalanche_prometheus_labels() -> None:
    settings = Settings(chain="avalanche", demo_mode=True)
    adapter = AvalancheAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="avalanche",
        chain_display_name="Avalanche",
        operator_label="validator",
        risk_kind="reward_loss",
        risk_label="Reward risk",
        primary_duty_label="Uptime",
        secondary_duty_label="Consensus polls",
        missed_duty_label="Failed polls",
        consensus_node_label="Avalanche node",
        reward_token_symbol="AVAX",
        reward_token_decimals=9,
        reward_token_base_unit="nAVAX",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="avalanche"' in text
    assert demo_node_id("healthy") in text
