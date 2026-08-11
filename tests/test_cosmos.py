from __future__ import annotations

import asyncio

from validator_pulse.alerts import evaluate_alerts
from validator_pulse.chains.cosmos.adapter import CosmosAdapter
from validator_pulse.chains.cosmos.bech32 import bech32_decode, retarget_bech32
from validator_pulse.chains.cosmos.demo import demo_consensus_address, demo_operator_address
from validator_pulse.chains.cosmos.lcd import consensus_address_from_pubkey
from validator_pulse.chains.cosmos.profiles import get_profile
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_bech32_profile_retarget_joins_operator_and_consensus() -> None:
    hub = get_profile("cosmoshub")
    celestia = get_profile("celestia")
    op = demo_operator_address("healthy", hub)
    cons = retarget_bech32(op, hub.valcons_prefix)
    assert cons == demo_consensus_address("healthy", hub)
    assert bech32_decode(op)[1] == bech32_decode(cons)[1]

    cel_op = demo_operator_address("healthy", celestia)
    assert cel_op.startswith("celestiavaloper")
    assert demo_consensus_address("healthy", celestia).startswith("celestiavalcons")


def test_consensus_address_from_ed25519_pubkey() -> None:
    # Deterministic ed25519-looking 32-byte key (base64).
    import base64

    raw = bytes(range(32))
    pubkey = base64.b64encode(raw).decode()
    profile = get_profile("cosmoshub")
    addr = consensus_address_from_pubkey(pubkey, profile)
    assert addr.startswith("cosmosvalcons1")
    assert len(bech32_decode(addr)[1]) == 20


def test_cosmos_hub_demo_collect() -> None:
    settings = Settings(chain="cosmos", cosmos_profile="cosmoshub", demo_mode=True)
    adapter = CosmosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert adapter.display_name == "Cosmos Hub"
    assert adapter.primary_duty_label == "Signed blocks"
    assert len(collection.operators) == 4
    statuses = {op.status for op in collection.operators}
    assert "jailed" in statuses
    assert "tombstoned" in statuses
    assert any(op.operator_id.startswith("cosmosvaloper") for op in collection.operators)
    tomb = next(op for op in collection.operators if op.status == "tombstoned")
    assert tomb.risk_score == 100
    assert any(e.kind == "tombstoned" for e in tomb.protocol_events)


def test_celestia_demo_profile_and_alerts() -> None:
    settings = Settings(chain="cosmos", cosmos_profile="celestia", demo_mode=True)
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "cosmos"
    assert snapshot.chain_display_name == "Celestia"
    assert snapshot.reward_token_symbol == "TIA"
    assert snapshot.primary_duty_label == "Signed blocks"
    assert any(v.operator_id.startswith("celestiavaloper") for v in snapshot.validators)

    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "jailed" in titles
    assert "tombstoned" in titles


def test_cosmos_live_requires_operator_addresses() -> None:
    settings = Settings(
        chain="cosmos",
        demo_mode=False,
        cosmos_rpc_url="http://127.0.0.1:1",
        cosmos_validator_operator_addresses="",
    )
    adapter = CosmosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    assert collection.operators == []
    assert "COSMOS_VALIDATOR_OPERATOR_ADDRESSES" in (collection.consensus.last_error or "")


def test_cosmos_metrics_and_missed_blocks() -> None:
    settings = Settings(chain="cosmos", cosmos_profile="cosmoshub", demo_mode=True)
    adapter = CosmosAdapter()
    collection = asyncio.run(adapter.collect(settings, collect_infrastructure()))
    metrics = aggregate_fleet_metrics(collection.operators)
    assert metrics.missed_primary_duties_total > 0
    near = next(op for op in collection.operators if "near" in (op.display_name or ""))
    assert near.attestations.missed > 0
    assert near.duties[0].label == "Signed blocks"

    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="cosmos",
        chain_display_name="Cosmos Hub",
        operator_label="validator",
        risk_kind="slashing",
        risk_label="Slashing risk",
        primary_duty_label="Signed blocks",
        secondary_duty_label="Voting power",
        missed_duty_label="Missed blocks",
        consensus_node_label="CometBFT node",
        reward_token_symbol="ATOM",
        reward_token_decimals=6,
        reward_token_base_unit="uatom",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=metrics,
    )
    from validator_pulse.metrics import to_prometheus

    text = to_prometheus(snapshot)
    assert 'chain="cosmos"' in text
    assert "operator_risk_score" in text
