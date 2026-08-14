from __future__ import annotations

import asyncio
from pathlib import Path

from validator_pulse.alerts import build_verdict, evaluate_alerts
from validator_pulse.chains.bsc.abi import (
    EXPECTED_CHAIN_IDS,
    MAINNET_CHAIN_ID,
    SELECTOR_SH_GET_VALIDATORS,
    SELECTOR_SI_THRESHOLDS,
    SLASH_DOUBLE_SIGN,
    SLASH_MALICIOUS_VOTE,
    decode_address_array,
    decode_basic_info,
    decode_stakehub_validator_page,
    decode_two_uints,
    encode_address_call,
    encode_selector_call,
    normalize_address,
    slash_type_label,
)
from validator_pulse.chains.bsc.adapter import BscAdapter
from validator_pulse.chains.bsc.demo import demo_validator_address
from validator_pulse.chains.bsc.state import ValidatorSetStore, bsc_effectiveness
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def _word(n: int) -> str:
    return f"{n:064x}"


def test_selectors_and_threshold_decode() -> None:
    assert MAINNET_CHAIN_ID == 56
    assert 56 in EXPECTED_CHAIN_IDS
    assert encode_selector_call(SELECTOR_SI_THRESHOLDS).startswith("0x8256ace6")
    misdemeanor, felony = decode_two_uints("0x" + _word(333) + _word(1000))
    assert misdemeanor == 333
    assert felony == 1000
    assert slash_type_label(SLASH_DOUBLE_SIGN) == "double-sign"
    assert slash_type_label(SLASH_MALICIOUS_VOTE) == "malicious finality vote"


def test_decode_working_set_and_stakehub_page() -> None:
    page_hex = (
        "0x"
        + _word(32)
        + _word(2)
        + _word(int("11" * 20, 16))
        + _word(int("22" * 20, 16))
    )
    addrs = decode_address_array(page_hex)
    assert addrs[0].endswith("11" * 20)
    assert len(addrs) == 2
    hub = (
        "0x"
        + _word(96)
        + _word(160)
        + _word(7)
        + _word(1)
        + _word(int("aa" * 20, 16))
        + _word(1)
        + _word(int("bb" * 20, 16))
    )
    decoded = decode_stakehub_validator_page(hub)
    assert decoded.total_length == 7
    assert len(decoded.operators) == 1
    call = encode_selector_call(SELECTOR_SH_GET_VALIDATORS, 0, 50)
    assert call.startswith("0xbff02e20")


def test_decode_basic_info_and_address_call() -> None:
    payload = "0x" + _word(1_700_000_000) + _word(1) + _word(1_800_000_000)
    info = decode_basic_info(payload)
    assert info.jailed is True
    assert info.jail_until == 1_800_000_000
    data = encode_address_call("55614fcc", "0xABC")
    assert data.startswith("0x55614fcc")
    assert normalize_address("0xABC").endswith("0000000000000000000000000000000000000abc")


def test_set_store_tracks_changes_without_size() -> None:
    store = ValidatorSetStore()
    joined, left, reset = store.observe(["0x1", "0x2", "0x3"])
    assert reset is True
    joined, left, reset = store.observe(["0x2", "0x3", "0x4"])
    assert reset is False
    assert joined == frozenset({"0x4"})
    assert left == frozenset({"0x1"})
    joined, left, reset = store.observe(["0x9"])
    assert reset is False
    assert joined == frozenset({"0x9"})
    assert left == frozenset({"0x2", "0x3", "0x4"})


def test_effectiveness_and_no_hardcoded_docs_thresholds() -> None:
    healthy = bsc_effectiveness(
        in_working_set=True,
        jailed=False,
        maintaining=False,
        slash_count=0,
        misdemeanor=333,
        double_sign=False,
        malicious_vote=False,
    )
    assert healthy == 100.0
    slashed = bsc_effectiveness(
        in_working_set=True,
        jailed=False,
        maintaining=False,
        slash_count=0,
        misdemeanor=333,
        double_sign=True,
        malicious_vote=False,
    )
    assert slashed == 0.0
    source = Path("validator_pulse/chains/bsc/adapter.py").read_text()
    assert "333" not in source
    assert "1000" not in source
    assert "felony = 200" not in source
    assert "misdemeanor = 50" not in source


def test_bsc_demo_collect() -> None:
    settings = Settings(chain="bsc", demo_mode=True)
    adapter = BscAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.risk_kind == "slashing"
    assert len(collection.operators) == 5
    statuses = {op.status for op in collection.operators}
    assert "active" in statuses
    assert "degraded" in statuses
    assert "maintenance" in statuses
    assert "slashed" in statuses
    assert "jailed" in statuses
    slashed = next(op for op in collection.operators if op.status == "slashed")
    assert any(e.kind == "slashed" for e in slashed.protocol_events)
    jailed = next(op for op in collection.operators if op.status == "jailed")
    assert any(e.kind == "jailed" for e in jailed.protocol_events)


def test_bsc_demo_alerts_double_sign_critical() -> None:
    settings = Settings(chain="bsc", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "bsc"
    assert snapshot.reward_token_symbol == "BNB"
    assert snapshot.risk_kind == "slashing"
    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "slashed" in titles
    assert "jailed" in titles
    assert "maintenance" in titles
    critical = [a for a in alerts if a.severity == "critical"]
    assert any("slash" in a.title.lower() or "jail" in a.title.lower() for a in critical)
    verdict = build_verdict(snapshot)
    assert verdict.status in {"critical", "degraded"}


def test_bsc_live_requires_addresses() -> None:
    settings = Settings(
        chain="bsc",
        demo_mode=False,
        bsc_rpc_url="http://127.0.0.1:1",
        bsc_validator_addresses="",
    )
    adapter = BscAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "BSC_VALIDATOR_ADDRESSES" in (collection.consensus.last_error or "")


def test_bsc_prometheus_labels() -> None:
    settings = Settings(chain="bsc", demo_mode=True)
    adapter = BscAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="bsc",
        chain_display_name="BNB Smart Chain",
        operator_label="validator",
        risk_kind="slashing",
        risk_label="Slashing risk",
        primary_duty_label="Block turns",
        secondary_duty_label="Finality votes",
        missed_duty_label="Missed turns",
        consensus_node_label="BSC RPC",
        reward_token_symbol="BNB",
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
    assert 'chain="bsc"' in text
    assert "operator_risk_score" in text
    assert demo_validator_address("healthy") in text
