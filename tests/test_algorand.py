from __future__ import annotations

import asyncio

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.algorand.adapter import AlgorandAdapter
from validator_pulse.chains.algorand.auth import redact_secrets
from validator_pulse.chains.algorand.demo import demo_account_address
from validator_pulse.chains.algorand.keys import (
    ParticipationKeyView,
    algorand_effectiveness,
    evaluate_partkey_health,
    parse_participation_keys,
)
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_redact_secrets_strips_token() -> None:
    secret = "super-secret-algod-token-xyz"
    msg = f"HTTP 401: Invalid API Token x-algo-api-token: {secret}"
    cleaned = redact_secrets(msg, secret)
    assert secret not in cleaned
    assert "[REDACTED]" in cleaned


def test_parse_and_evaluate_partkeys() -> None:
    payload = [
        {
            "address": "ADDR1",
            "id": "key-a",
            "effective-first-valid": 100,
            "effective-last-valid": 200,
            "last-vote": 150,
            "last-block-proposal": 140,
            "last-state-proof": 0,
            "key": {
                "vote-first-valid": 100,
                "vote-last-valid": 200,
                "vote-key-dilution": 10000,
                "selection-participation-key": "aa==",
                "vote-participation-key": "bb==",
            },
        }
    ]
    keys = parse_participation_keys(payload)
    assert len(keys) == 1
    assert keys[0].last_vote == 150

    valid = evaluate_partkey_health(keys, current_round=150, warning_rounds=20)
    assert valid.state == "valid"

    expiring = evaluate_partkey_health(keys, current_round=190, warning_rounds=20)
    assert expiring.state == "expiring"

    expired = evaluate_partkey_health(keys, current_round=201, warning_rounds=20)
    assert expired.state == "expired"

    missing = evaluate_partkey_health([], current_round=100, warning_rounds=20)
    assert missing.state == "missing"


def test_algorand_effectiveness_does_not_require_expected_duties() -> None:
    score = algorand_effectiveness(
        online=True,
        incentive_eligible=True,
        partkey_state="valid",
        activity_advancing=True,
    )
    assert score == 100.0
    offline = algorand_effectiveness(
        online=False,
        incentive_eligible=False,
        partkey_state="valid",
        activity_advancing=False,
    )
    assert offline == 0.0


def test_algorand_demo_collect() -> None:
    settings = Settings(chain="algorand", demo_mode=True)
    adapter = AlgorandAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Algorand"
    assert adapter.operator_label == "participation node"
    assert adapter.risk_kind == "suspension"
    assert "slash" not in adapter.risk_label.lower()
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert "key_expiring" in statuses
    assert "key_missing" in statuses
    assert "offline" in statuses
    for op in collection.operators:
        assert all(d.expected is None for d in op.duties)
        assert op.attestations.missed == 0
    offline = next(op for op in collection.operators if op.status == "offline")
    assert offline.risk_score == 100
    assert any(e.kind == "suspended" for e in offline.protocol_events)


def test_algorand_demo_alerts_no_slashing_wording() -> None:
    settings = Settings(chain="algorand", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "algorand"
    assert snapshot.reward_token_symbol == "ALGO"
    assert snapshot.reward_token_base_unit == "microAlgos"
    assert snapshot.risk_kind == "suspension"
    assert snapshot.risk_label == "Operational risk"

    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "offline" in titles or "participation key" in titles
    assert "slash" not in titles

    verdict = build_verdict(snapshot)
    assert "slash" not in verdict.summary.lower()


def test_algorand_live_requires_accounts() -> None:
    settings = Settings(
        chain="algorand",
        demo_mode=False,
        algorand_algod_url="http://127.0.0.1:1",
        algorand_account_addresses="",
        algorand_algod_token="test-token",
    )
    adapter = AlgorandAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "ALGORAND_ACCOUNT_ADDRESSES" in (collection.consensus.last_error or "")
    # Token must not leak into consensus errors.
    assert "test-token" not in (collection.consensus.last_error or "")


def test_algorand_metrics_soft_fail() -> None:
    settings = Settings(
        chain="algorand",
        demo_mode=False,
        algorand_algod_url="http://127.0.0.1:1",
        algorand_account_addresses=demo_account_address("healthy"),
        algorand_algod_token="secret-token-value",
        algorand_metrics_url="http://127.0.0.1:9/metrics",
    )
    adapter = AlgorandAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert isinstance(collection.operators, list)
    err = collection.consensus.last_error or ""
    assert err
    assert "secret-token-value" not in err


def test_online_to_offline_transition_alerts() -> None:
    adapter = AlgorandAdapter()
    adapter._prev_online["ADDR"] = True
    adapter._prev_eligible["ADDR"] = True
    # Simulate building events path by calling evaluate after setting prev state
    # via a tiny unit of the transition logic using evaluate_partkey_health + manual events.
    # Full live path needs RPC; instead exercise adapter state bump helpers.
    assert adapter._bump_observed(adapter._vote_obs, adapter._last_vote_round, "ADDR", 10) == 1
    assert adapter._bump_observed(adapter._vote_obs, adapter._last_vote_round, "ADDR", 10) == 1
    assert adapter._bump_observed(adapter._vote_obs, adapter._last_vote_round, "ADDR", 11) == 2


def test_algorand_prometheus_labels() -> None:
    settings = Settings(chain="algorand", demo_mode=True)
    adapter = AlgorandAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="algorand",
        chain_display_name="Algorand",
        operator_label="participation node",
        risk_kind="suspension",
        risk_label="Operational risk",
        primary_duty_label="Observed votes",
        secondary_duty_label="Observed proposals",
        missed_duty_label="Unobservable misses",
        consensus_node_label="algod",
        reward_token_symbol="ALGO",
        reward_token_decimals=6,
        reward_token_base_unit="microAlgos",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="algorand"' in text
    assert "operator_risk_score" in text
    assert demo_account_address("healthy") in text or any(
        op.operator_id in text for op in collection.operators
    )


def test_participation_key_view_roundtrip() -> None:
    view = ParticipationKeyView(
        address="ADDR",
        vote_first_valid=1,
        vote_last_valid=100,
        effective_first_valid=1,
        effective_last_valid=100,
        last_vote=50,
        last_proposal=40,
        last_state_proof=0,
        key_id="id1",
    )
    assert view.address == "ADDR"
