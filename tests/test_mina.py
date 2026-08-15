from __future__ import annotations

import asyncio
from pathlib import Path

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.mina.adapter import MinaAdapter
from validator_pulse.chains.mina.archive import parse_archive_rows
from validator_pulse.chains.mina.demo import demo_producer_key
from validator_pulse.chains.mina.graphql import graphql_host_is_local, parse_daemon_snapshot
from validator_pulse.chains.mina.local import parse_client_status, parse_mina_log_text
from validator_pulse.chains.mina.state import (
    CanonicalBlock,
    WonSlot,
    classify_won_slots,
    mina_effectiveness,
    normalize_public_key,
)
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics

_MINA_PKG = Path(__file__).resolve().parents[1] / "validator_pulse" / "chains" / "mina"

_STATUS_SAMPLE = """
Mina daemon status
-----------------------------------
Block height:                                  297084
Max observed block height:                     297084
Peers:                                         12
Block producers running:                       1 (B62qmFmLLU58mtPRW963iMAUSH1KHA9teCAperWnhzH53AtxHWMUSkG)
Next block will be produced in:                in 5.404h for slot: 2004 slot-since-genesis: 447864 (Generated from consensus at slot: 1830 slot-since-genesis: 447690)
Consensus time now:                            epoch=0, slot=1895
Sync Status:                                   Synced
"""


def test_mina_package_is_query_only() -> None:
    for path in _MINA_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "mutation" not in text
        if path.name == "graphql.py":
            assert "query" in text
            assert "bestchain" in text


def test_normalize_and_local_graphql() -> None:
    assert normalize_public_key("b62qabc").startswith("B62")
    assert graphql_host_is_local("http://127.0.0.1:3085/graphql")
    assert graphql_host_is_local("http://localhost:3085/graphql")
    assert not graphql_host_is_local("https://graphql.example.com/graphql")


def test_parse_client_status_and_logs() -> None:
    status = parse_client_status(_STATUS_SAMPLE)
    assert status.sync_status == "SYNCED"
    assert status.block_height == 297084
    assert status.peers == 12
    assert status.producers[0].startswith("B62q")
    assert status.next_global_slot == 447864
    key = status.producers[0]
    logs = parse_mina_log_text(
        "\n".join(
            [
                '{"message":"Won slot","metadata":{"global_slot":"100","block_creator":"%s"}}' % key,
                '{"message":"Successfully produced a new block","metadata":{"global_slot":"100","state_hash":"3Nabc"}}',
                "Producing block in slot 110",
            ]
        ),
        pubkey=key,
    )
    by_slot = {w.slot: w for w in logs}
    assert by_slot[100].produced is True
    assert by_slot[100].state_hash == "3Nabc"
    assert 110 in by_slot


def test_classify_slots_and_effectiveness_requires_evidence() -> None:
    key = demo_producer_key("schedule")
    won = [
        WonSlot(pubkey=key, slot=10, produced=True, source="log"),
        WonSlot(pubkey=key, slot=11, produced=True, source="log"),
        WonSlot(pubkey=key, slot=12, produced=False, source="log"),
        WonSlot(pubkey=key, slot=20, produced=False, source="cli"),
    ]
    canonical = {
        10: CanonicalBlock(slot=10, height=1, creator=key, state_hash="a"),
        11: CanonicalBlock(slot=11, height=2, creator="B62qOther111111111111111111111111111111111", state_hash="b"),
    }
    outcomes = classify_won_slots(
        won=won, canonical_by_slot=canonical, current_slot=15, miss_grace_slots=2
    )
    kinds = {o.slot: o.kind for o in outcomes}
    assert kinds[10] == "canonical"
    assert kinds[11] == "orphaned"
    assert kinds[12] == "missed"
    assert kinds[20] == "pending"
    assert mina_effectiveness(synced=True, activated=True, outcomes=outcomes) < 80
    # No local evidence → do not treat as 0% from invented misses.
    none = mina_effectiveness(synced=True, activated=True, outcomes=None)
    assert none >= 50


def test_parse_graphql_snapshot_and_archive() -> None:
    data = {
        "syncStatus": "SYNCED",
        "daemonStatus": {
            "blockchainLength": 100,
            "highestBlockLengthReceived": 100,
            "peers": [{"peerId": "p1"}, {"peerId": "p2"}],
            "syncStatus": "SYNCED",
            "blockProductionKeys": ["B62qSchedule1111111111111111111111111111111"],
            "consensusTimeNow": {"epoch": "3", "slot": "10"},
            "globalSlotSinceGenesisBestTip": 400,
            "nextBlockProduction": {
                "times": [{"epoch": "3", "slot": "20"}],
                "globalSlotSinceGenesis": [410],
            },
        },
        "bestChain": [
            {
                "creator": "B62qSchedule1111111111111111111111111111111",
                "stateHash": "3Ntip",
                "protocolState": {
                    "consensusState": {
                        "blockHeight": "100",
                        "slotSinceGenesis": "400",
                        "slot": "10",
                        "epoch": "3",
                    }
                },
                "transactions": {"coinbase": "72000000000"},
            }
        ],
    }
    snap = parse_daemon_snapshot(data, graphql_url="http://127.0.0.1:3085/graphql")
    assert snap.sync_status == "SYNCED"
    assert snap.peers == 2
    assert snap.local_graphql is True
    assert snap.blocks[0].coinbase == 72_000_000_000
    assert snap.next_slots[0].slot == 410
    rows = parse_archive_rows(
        [{"creator": snap.production_keys[0], "slot": 50, "height": 40, "state_hash": "3Nold"}],
        pubkey=snap.production_keys[0],
    )
    assert rows[0].slot == 50


def test_mina_demo_collect() -> None:
    settings = Settings(chain="mina", demo_mode=True)
    adapter = MinaAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.risk_kind == "reward_loss"
    assert "slash" not in adapter.risk_label.lower()
    assert len(collection.operators) == 5
    statuses = {op.status for op in collection.operators}
    assert {"active", "orphaned", "missed", "unsynced", "recovering"} <= statuses
    for op in collection.operators:
        assert op.operator_id.startswith("B62")
        assert op.duties[0].expected is not None


def test_mina_demo_alerts_never_claim_slashing() -> None:
    settings = Settings(chain="mina", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "mina"
    assert snapshot.reward_token_symbol == "MINA"
    assert snapshot.reward_token_decimals == 9
    assert snapshot.reward_token_base_unit == "nanomina"
    assert snapshot.risk_kind == "reward_loss"
    alerts = evaluate_alerts(snapshot, settings)
    blob = " ".join(f"{a.title} {a.message}".lower() for a in alerts)
    assert "slash" not in blob or "not slashing" in blob or "does not slash" in blob
    assert not any("slashing" in a.title.lower() for a in alerts)
    assert any("unsynced" in a.title.lower() or "missed" in a.title.lower() or "orphan" in a.title.lower() for a in alerts)
    verdict = build_verdict(snapshot)
    assert "slash" not in verdict.summary.lower() or "reward" in verdict.summary.lower()


def test_mina_live_requires_keys() -> None:
    settings = Settings(
        chain="mina",
        demo_mode=False,
        mina_graphql_url="http://127.0.0.1:1/graphql",
        mina_producer_public_keys="",
    )
    adapter = MinaAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "MINA_PRODUCER_PUBLIC_KEYS" in (collection.consensus.last_error or "")


def test_mina_graphql_only_does_not_invent_duties() -> None:
    settings = Settings(
        chain="mina",
        demo_mode=False,
        mina_graphql_url="http://127.0.0.1:1/graphql",
        mina_producer_public_keys=demo_producer_key("schedule"),
        mina_client_command="mina-missing-binary",
    )
    adapter = MinaAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators
    for op in collection.operators:
        assert all(d.expected is None for d in op.duties)
        blob = " ".join(e.message.lower() for e in op.protocol_events)
        assert "not invented" in blob or "unavailable" in blob


def test_mina_prometheus_labels() -> None:
    settings = Settings(chain="mina", demo_mode=True)
    adapter = MinaAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="mina",
        chain_display_name="Mina",
        operator_label="block producer",
        risk_kind="reward_loss",
        risk_label="Reward risk",
        primary_duty_label="Won slots",
        secondary_duty_label="Canonical blocks",
        missed_duty_label="Missed slots",
        consensus_node_label="Mina daemon",
        reward_token_symbol="MINA",
        reward_token_decimals=9,
        reward_token_base_unit="nanomina",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="mina"' in text
    assert demo_producer_key("schedule") in text
