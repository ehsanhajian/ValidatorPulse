from __future__ import annotations

import asyncio

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.multiversx.adapter import MultiversXAdapter
from validator_pulse.chains.multiversx.api import parse_heartbeats, parse_validator_statistics
from validator_pulse.chains.multiversx.demo import demo_bls_key
from validator_pulse.chains.multiversx.state import (
    DOCS_JAIL_RATING,
    is_bls_key,
    is_jailed,
    is_passive_recovery,
    is_slashed,
    jail_threshold_from_ratings,
    mx_effectiveness,
    normalize_bls_key,
    resolve_jail_threshold,
    shard_label,
)
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_bls_and_shard_helpers() -> None:
    key = demo_bls_key("healthy")
    assert len(key) == 192
    assert is_bls_key(key)
    assert normalize_bls_key("0x" + key.upper()) == key
    assert shard_label(0) == "0"
    assert shard_label(4_294_967_295) == "metachain"
    assert is_jailed("jailed", None)
    assert is_slashed("eligible", "slashed")
    assert is_passive_recovery("waiting", None)
    assert not is_passive_recovery("jailed", None)


def test_jail_threshold_from_network_ratings() -> None:
    cfg = {
        "erd_ratings_general_max_rating": 10_000_000,
        "erd_ratings_general_selection_chances": [
            {"erd_chance_percent": 5, "erd_max_threshold": 0},
            {"erd_chance_percent": 0, "erd_max_threshold": 1_000_000},
            {"erd_chance_percent": 16, "erd_max_threshold": 2_000_000},
        ],
    }
    derived = jail_threshold_from_ratings(cfg)
    assert derived == 10.0
    docs = resolve_jail_threshold(override=None, ratings_config=None)
    assert docs.rating == DOCS_JAIL_RATING
    assert docs.source == "docs-rating"
    net = resolve_jail_threshold(override=None, ratings_config=cfg)
    assert net.source == "network-ratings"
    over = resolve_jail_threshold(override=12, ratings_config=cfg)
    assert over.source == "config" and over.rating == 12


def test_parse_heartbeat_and_statistics() -> None:
    key = demo_bls_key("healthy")
    hbs = parse_heartbeats(
        {
            "heartbeats": [
                {
                    "publicKey": key.upper(),
                    "peerType": "eligible",
                    "isActive": True,
                    "receivedShardID": 0,
                    "computedShardID": 0,
                    "versionNumber": "v1.11.11",
                    "nodeDisplayName": "demo",
                    "nonce": 100,
                    "numInstances": 2,
                }
            ]
        }
    )
    assert hbs[key].is_active is True
    assert hbs[key].num_instances == 2
    stats = parse_validator_statistics(
        {
            "statistics": {
                key: {
                    "rating": 96.5,
                    "tempRating": 97,
                    "numLeaderSuccess": 10,
                    "numLeaderFailure": 1,
                    "numValidatorSuccess": 100,
                    "numValidatorFailure": 2,
                    "shardId": 0,
                    "validatorStatus": "eligible",
                }
            }
        }
    )
    assert stats[key].rating == 96.5
    assert stats[key].leader_failure == 1


def test_effectiveness_jail_and_counters() -> None:
    healthy = mx_effectiveness(
        heartbeat_active=True,
        jailed=False,
        slashed=False,
        rating=96,
        jail_threshold=10,
        proposal_ratio=1.0,
        signature_ratio=0.99,
        passive=False,
    )
    jailed = mx_effectiveness(
        heartbeat_active=False,
        jailed=True,
        slashed=False,
        rating=6,
        jail_threshold=10,
        proposal_ratio=None,
        signature_ratio=None,
        passive=False,
    )
    slashed = mx_effectiveness(
        heartbeat_active=False,
        jailed=False,
        slashed=True,
        rating=0,
        jail_threshold=10,
        proposal_ratio=0,
        signature_ratio=0,
        passive=False,
    )
    assert healthy > 80
    assert jailed < 15
    assert slashed == 0.0


def test_mx_demo_collect() -> None:
    settings = Settings(chain="multiversx", demo_mode=True)
    adapter = MultiversXAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.risk_kind == "jail"
    assert adapter.risk_label == "Jail risk"
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert {"active", "degraded", "jailed", "recovering"} <= statuses
    for op in collection.operators:
        assert is_bls_key(op.operator_id)
    jailed = next(op for op in collection.operators if op.status == "jailed")
    assert any(e.kind == "jailed" for e in jailed.protocol_events)
    blob = " ".join(e.message.lower() for e in jailed.protocol_events)
    assert "slash" in blob and "not" in blob


def test_mx_demo_alerts_distinguish_jail_and_slash() -> None:
    settings = Settings(chain="multiversx", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "multiversx"
    assert snapshot.reward_token_symbol == "EGLD"
    assert snapshot.reward_token_decimals == 18
    assert snapshot.risk_kind == "jail"
    alerts = evaluate_alerts(snapshot, settings)
    titles = [a.title.lower() for a in alerts]
    assert any("jailed" in t for t in titles)
    assert not any("slashed" in t for t in titles)
    blob = " ".join(f"{a.title} {a.message}".lower() for a in alerts)
    assert "jail" in blob
    verdict = build_verdict(snapshot)
    assert "jail" in verdict.summary.lower() or "risk" in verdict.summary.lower()


def test_mx_live_requires_keys() -> None:
    settings = Settings(
        chain="multiversx",
        demo_mode=False,
        multiversx_gateway_url="http://127.0.0.1:1",
        multiversx_validator_bls_keys="",
    )
    adapter = MultiversXAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "MULTIVERSX_VALIDATOR_BLS_KEYS" in (collection.consensus.last_error or "")


def test_mx_prometheus_labels() -> None:
    settings = Settings(chain="multiversx", demo_mode=True)
    adapter = MultiversXAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="multiversx",
        chain_display_name="MultiversX",
        operator_label="validator",
        risk_kind="jail",
        risk_label="Jail risk",
        primary_duty_label="Leader proposals",
        secondary_duty_label="Consensus signatures",
        missed_duty_label="Failed signatures",
        consensus_node_label="MultiversX node",
        reward_token_symbol="EGLD",
        reward_token_decimals=18,
        reward_token_base_unit="wei",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="multiversx"' in text
    assert demo_bls_key("healthy") in text
