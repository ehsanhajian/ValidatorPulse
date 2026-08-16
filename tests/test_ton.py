from __future__ import annotations

import asyncio
import time

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.ton.adapter import TonAdapter
from validator_pulse.chains.ton.api import (
    derive_qos_url,
    extract_ton_metrics,
    parse_elections,
    parse_prometheus_text,
    parse_qos_scoreboard,
    parse_validation_cycles,
)
from validator_pulse.chains.ton.demo import demo_adnl
from validator_pulse.chains.ton.mytonctrl import resolve_mytonctrl_binary
from validator_pulse.chains.ton.state import (
    DOCS_EFFICIENCY_THRESHOLD,
    AdnlHistoryStore,
    AdnlObservation,
    completed_round_below_threshold,
    efficiency_is_actionable,
    is_adnl,
    normalize_adnl,
    parse_complaint,
    resolve_efficiency_threshold,
    ton_effectiveness,
)
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_adnl_helpers_and_thresholds() -> None:
    key = demo_adnl("healthy")
    assert len(key) == 64
    assert is_adnl(key)
    assert normalize_adnl("0x" + key.lower()) == key
    docs = resolve_efficiency_threshold(None)
    assert docs.percent == DOCS_EFFICIENCY_THRESHOLD
    assert docs.source == "docs-efficiency"
    over = resolve_efficiency_threshold(85)
    assert over.source == "config" and over.percent == 85
    assert derive_qos_url("https://elections.toncenter.com", None) == "https://toncenter.com"
    assert derive_qos_url("https://testnet-elections.toncenter.com", None) == (
        "https://testnet.toncenter.com"
    )


def test_efficiency_grace_and_completed_round() -> None:
    now = 1_000_000.0
    assert not efficiency_is_actionable(0.0, utime_since=now - 60, utime_until=now + 10_000, now=now)
    assert not efficiency_is_actionable(None, utime_since=now - 80_000, utime_until=now - 10, now=now)
    assert efficiency_is_actionable(91.0, utime_since=now - 80_000, utime_until=now - 10, now=now)
    threshold = resolve_efficiency_threshold(None)
    assert completed_round_below_threshold(
        81.0, threshold, utime_until=now - 10, now=now
    )
    assert not completed_round_below_threshold(
        81.0, threshold, utime_until=now + 10_000, now=now
    )


def test_adnl_history_survives_rotation() -> None:
    store = AdnlHistoryStore(ttl_seconds=10_000)
    old = "AA" + "11" * 31
    new = "BB" + "22" * 31
    now = time.time()
    store.record(
        AdnlObservation(
            adnl=old,
            cycle_id=1,
            index=4,
            efficiency=99.0,
            in_set=True,
            stake=1,
            seen_at=now,
            utime_until=int(now) + 100,
        )
    )
    tracked = store.tracked_adnls([new], now)
    assert old in tracked and new in tracked
    assert store.history_for(old)
    assert ton_effectiveness(
        in_set=True,
        efficiency=99.0,
        efficiency_actionable=True,
        fined=False,
        missed_election=False,
        severe_lag=False,
        recovering=False,
    ) == 99.0
    assert ton_effectiveness(
        in_set=False,
        efficiency=None,
        efficiency_actionable=False,
        fined=True,
        missed_election=True,
        severe_lag=False,
        recovering=True,
    ) < 15


def test_parse_pinned_validation_and_qos_schema() -> None:
    cycles = parse_validation_cycles(
        [
            {
                "cycle_id": 1786793736,
                "cycle_info": {
                    "utime_since": 1786793736,
                    "utime_until": 1786859272,
                    "total_stake": 1,
                    "min_stake": 300000000000000,
                    "max_stake": 10000000000000000,
                    "total_participants": 1,
                    "validators": [
                        {
                            "adnl_addr": demo_adnl("healthy"),
                            "pubkey": "AA" * 32,
                            "weight": 1,
                            "index": 12,
                            "stake": 1200000000000000,
                            "wallet_address": "Ef-demo",
                            "complaints": [{"fine": 101000000000, "passed": True}],
                        }
                    ],
                },
            }
        ]
    )
    assert cycles[0].cycle_id == 1786793736
    row = cycles[0].validators[demo_adnl("healthy")]
    assert row.index == 12
    assert row.complaints[0]["passed"] is True
    qos = parse_qos_scoreboard(
        {
            "scoreboard": [
                {
                    "cycle_id": 1786793736,
                    "utime_since": 1786793736,
                    "utime_until": 1786859272,
                    "adnl_addr": demo_adnl("healthy"),
                    "validator_adnl": demo_adnl("healthy"),
                    "idx": 12,
                    "stake": 1200000000000000,
                    "efficiency": None,
                    "efficiency_mc": None,
                    "efficiency_wc": None,
                }
            ]
        }
    )
    assert qos[0].efficiency is None
    assert qos[0].index == 12
    elections = parse_elections(
        [
            {
                "election_id": 1786793736,
                "finished": True,
                "elect_close": 1786785544,
                "min_stake": 300000000000000,
                "participants_list": [
                    {
                        "adnl_addr": demo_adnl("healthy"),
                        "stake": 1,
                        "index": 12,
                    }
                ],
            }
        ]
    )
    assert demo_adnl("healthy") in elections[0].participants
    parsed = parse_complaint({"fine": 101000000000, "approved": True})
    assert parsed and parsed["passed"] is True


def test_prometheus_and_mytonctrl_safety() -> None:
    metrics = extract_ton_metrics(
        parse_prometheus_text(
            "\n".join(
                [
                    "# HELP validator_masterchain_out_of_sync_seconds lag",
                    "validator_masterchain_out_of_sync_seconds 2",
                    "validator_console_up 1",
                    "validator_index 12",
                    "mytonctrl_synced 1",
                ]
            )
        )
    )
    assert metrics.master_out_of_sync == 2
    assert metrics.console_up is True
    assert resolve_mytonctrl_binary("mytonctrl") == "mytonctrl"
    assert resolve_mytonctrl_binary("mytonctrl; rm -rf /") is None
    assert resolve_mytonctrl_binary("echo hi") is None


def test_ton_demo_collect_and_alerts() -> None:
    settings = Settings(chain="ton", demo_mode=True)
    adapter = TonAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.risk_kind == "operational"
    assert adapter.risk_label == "Fine risk"
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert {"active", "degraded", "fined", "recovering"} <= statuses
    for op in collection.operators:
        assert is_adnl(op.operator_id or "")
    fined = next(op for op in collection.operators if op.status == "fined")
    assert any(e.kind == "fined" for e in fined.protocol_events)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "ton"
    assert snapshot.reward_token_symbol == "GRAM"
    assert snapshot.risk_kind == "operational"
    alerts = evaluate_alerts(snapshot, settings)
    titles = [a.title.lower() for a in alerts]
    assert any("fined" in t for t in titles)
    assert any("efficiency" in t for t in titles)
    assert not any("slashed" in t for t in titles)
    verdict = build_verdict(snapshot)
    assert "fine" in verdict.summary.lower() or "risk" in verdict.summary.lower()


def test_ton_live_requires_adnls() -> None:
    settings = Settings(
        chain="ton",
        demo_mode=False,
        ton_validation_api_url="http://127.0.0.1:1",
        ton_adnl_addresses="",
    )
    adapter = TonAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "TON_ADNL_ADDRESSES" in (collection.consensus.last_error or "")


def test_ton_prometheus_labels() -> None:
    settings = Settings(chain="ton", demo_mode=True)
    adapter = TonAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="ton",
        chain_display_name="TON",
        operator_label="validator",
        risk_kind="operational",
        risk_label="Fine risk",
        primary_duty_label="Validation rounds",
        secondary_duty_label="Catchain efficiency",
        missed_duty_label="Low-efficiency rounds",
        consensus_node_label="TON validator",
        reward_token_symbol="GRAM",
        reward_token_decimals=9,
        reward_token_base_unit="nanoton",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="ton"' in text
    assert demo_adnl("healthy") in text
