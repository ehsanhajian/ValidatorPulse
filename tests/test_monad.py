from __future__ import annotations

import asyncio

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.monad.abi import (
    EXPECTED_CHAIN_ID,
    SELECTOR_GET_CONSENSUS_SET,
    SELECTOR_GET_EPOCH,
    SELECTOR_GET_VALIDATOR,
    decode_epoch,
    decode_proposer_val_id,
    decode_validator,
    decode_validator_set_page,
    encode_selector_call,
)
from validator_pulse.chains.monad.adapter import MonadAdapter
from validator_pulse.chains.monad.demo import demo_validator_id
from validator_pulse.chains.monad.local import parse_ledger_tail_text, parse_status_json
from validator_pulse.chains.monad.rpc import extract_monad_metrics, parse_prometheus_text
from validator_pulse.chains.monad.state import EpochSetStore, monad_effectiveness
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def _word(n: int) -> str:
    return f"{n:064x}"


def test_encode_and_decode_epoch_and_proposer() -> None:
    data = encode_selector_call(SELECTOR_GET_EPOCH)
    assert data.startswith("0x757991a8")
    payload = "0x" + _word(764) + _word(1)
    info = decode_epoch(payload)
    assert info.epoch == 764
    assert info.in_epoch_delay_period is True
    assert decode_proposer_val_id("0x" + _word(42)) == 42
    assert EXPECTED_CHAIN_ID == 143


def test_decode_paginated_consensus_set() -> None:
    # (isDone=false, nextIndex=2, valIds=[101, 202]) then a done page.
    page_hex = (
        "0x"
        + _word(0)
        + _word(2)
        + _word(96)
        + _word(2)
        + _word(101)
        + _word(202)
    )
    page = decode_validator_set_page(page_hex)
    assert page.is_done is False
    assert page.next_index == 2
    assert page.val_ids == [101, 202]
    call = encode_selector_call(SELECTOR_GET_CONSENSUS_SET, 0)
    assert call.startswith("0xfb29b729")


def test_decode_validator_flags_and_stake() -> None:
    auth = int("11" * 20, 16)
    secp_offset = 12 * 32
    bls_offset = secp_offset + 32
    payload = "0x" + "".join(
        [
            _word(auth),
            _word(1),  # stake too low
            _word(10**18),
            _word(0),
            _word(0),
            _word(5),
            _word(9 * 10**17),
            _word(0),
            _word(8 * 10**17),
            _word(0),
            _word(secp_offset),
            _word(bls_offset),
            _word(0),  # empty secp
            _word(0),  # empty bls
        ]
    )
    view = decode_validator(payload)
    assert view.auth_address.endswith("11111111111111111111")
    assert view.stake_too_low is True
    assert view.eligible is False
    assert view.stake == 10**18
    assert encode_selector_call(SELECTOR_GET_VALIDATOR, 123).endswith(f"{123:064x}")


def test_epoch_set_store_tracks_transitions() -> None:
    store = EpochSetStore()
    joined, left, reset = store.observe(1, [1, 2, 3])
    assert reset is True
    joined, left, reset = store.observe(1, [2, 3, 4])
    assert reset is False
    assert joined == frozenset({4})
    assert left == frozenset({1})
    joined, left, reset = store.observe(2, [4])
    assert reset is True
    assert joined == frozenset()


def test_ledger_tail_and_status_parsing() -> None:
    ndjson = """
{"fields":{"message":"proposed_block","author":"aabbcc","round":"10","epoch":"1"}}
{"fields":{"message":"missed_block","author":"aabbcc","round":"11"}}
{"fields":{"message":"proposed_block","author":"ffff","round":"12"}}
"""
    evidence = parse_ledger_tail_text(ndjson, secp_pubkey_hex="aabbcc")
    assert evidence.authored == 1
    assert evidence.missed == 1
    assert evidence.has_duty_history is True
    status = parse_status_json(
        {
            "consensus": {"status": "in-sync", "mode": "live", "blockDifference": 0, "round": 9},
            "services": {"monad-bft": "running", "monad-execution": "running"},
            "peers": {"peersNumber": 12},
        }
    )
    assert status["in_sync"] is True
    assert status["peer_count"] == 12


def test_monad_effectiveness() -> None:
    healthy = monad_effectiveness(
        in_consensus_set=True,
        eligible=True,
        local_evidence=True,
        authored=10,
        missed=0,
        lagging=False,
    )
    assert healthy == 100.0
    out = monad_effectiveness(
        in_consensus_set=False,
        eligible=True,
        local_evidence=False,
        authored=0,
        missed=0,
        lagging=False,
    )
    assert out == 0.0


def test_prometheus_extract() -> None:
    text = """
# TYPE consensus_proposed_blocks counter
consensus_proposed_blocks 7
monad_consensus_round 99
connected_peers 3
"""
    metrics = extract_monad_metrics(parse_prometheus_text(text))
    assert metrics.proposed_blocks == 7
    assert metrics.round == 99
    assert metrics.connected_peers == 3


def test_monad_demo_collect() -> None:
    settings = Settings(chain="monad", demo_mode=True)
    adapter = MonadAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Monad"
    assert adapter.risk_kind == "reward_loss"
    assert "slash" not in adapter.risk_label.lower()
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert "active" in statuses
    assert "lagging" in statuses
    assert "degraded" in statuses
    assert "pending_set" in statuses
    failed = next(op for op in collection.operators if op.status == "degraded")
    assert failed.proposals.missed > 0
    assert any("not slashing" in e.message.lower() for e in failed.protocol_events)


def test_monad_demo_alerts_no_slashing_claim() -> None:
    settings = Settings(chain="monad", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "monad"
    assert snapshot.reward_token_symbol == "MON"
    assert snapshot.reward_token_decimals == 18
    assert snapshot.reward_token_base_unit == "wei"
    assert snapshot.risk_kind == "reward_loss"
    alerts = evaluate_alerts(snapshot, settings)
    blob = " ".join(f"{a.title} {a.message}".lower() for a in alerts)
    assert "slash" not in blob or "not slashing" in blob
    assert "reward" in blob or "lag" in blob or "set" in blob
    verdict = build_verdict(snapshot)
    assert "slash" not in verdict.summary.lower() or "reward" in verdict.summary.lower()


def test_monad_live_requires_ids() -> None:
    settings = Settings(
        chain="monad",
        demo_mode=False,
        monad_rpc_url="http://127.0.0.1:1",
        monad_validator_ids="",
    )
    adapter = MonadAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "MONAD_VALIDATOR_IDS" in (collection.consensus.last_error or "")


def test_monad_rpc_only_marks_duties_unknown() -> None:
    settings = Settings(
        chain="monad",
        demo_mode=False,
        monad_rpc_url="http://127.0.0.1:1",
        monad_validator_ids=str(demo_validator_id("healthy")),
        monad_metrics_url="http://127.0.0.1:9/metrics",
    )
    adapter = MonadAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert isinstance(collection.operators, list)
    assert collection.consensus.last_error
    # Unreachable RPC → failed operators still must not invent missed duties.
    for op in collection.operators:
        assert all(d.expected is None or d.missed == 0 for d in op.duties)


def test_monad_prometheus_labels() -> None:
    settings = Settings(chain="monad", demo_mode=True)
    adapter = MonadAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="monad",
        chain_display_name="Monad",
        operator_label="validator",
        risk_kind="reward_loss",
        risk_label="Reward risk",
        primary_duty_label="Proposals",
        secondary_duty_label="Set membership",
        missed_duty_label="Missed proposals",
        consensus_node_label="Monad RPC",
        reward_token_symbol="MON",
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
    assert 'chain="monad"' in text
    assert "operator_risk_score" in text
    assert str(demo_validator_id("healthy")) in text
