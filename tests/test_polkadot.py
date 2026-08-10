from __future__ import annotations

import asyncio

from validator_pulse.alerts import evaluate_alerts
from validator_pulse.chains.polkadot.adapter import PolkadotAdapter
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.pulse import collect_pulse
from validator_pulse.scoring import aggregate_fleet_metrics


def test_polkadot_demo_collect() -> None:
    settings = Settings(
        chain="polkadot",
        demo_mode=True,
        collator_addresses="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        parachain_id=2000,
    )
    adapter = PolkadotAdapter()
    infra = collect_infrastructure()
    collection = asyncio.run(adapter.collect(settings, infra))
    assert collection.consensus.beacon_reachable
    assert len(collection.operators) == 1
    assert collection.operators[0].pubkey.startswith("5")
    assert "para_2000" in collection.operators[0].status
    assert collection.operators[0].attestations.expected > 0
    assert adapter.operator_label == "collator"
    assert adapter.risk_kind == "operational"


def test_polkadot_alerts_use_collator_wording() -> None:
    settings = Settings(chain="polkadot", demo_mode=True, alert_missed_attestations=1)
    adapter = PolkadotAdapter()
    infra = collect_infrastructure()
    collection = asyncio.run(adapter.collect(settings, infra))
    # Force a miss so an alert is produced.
    op = collection.operators[0]
    op.attestations.missed = 3

    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="polkadot",
        chain_display_name="Polkadot",
        operator_label="collator",
        risk_kind="operational",
        risk_label="Downtime risk",
        primary_duty_label="Collations",
        secondary_duty_label="Blocks",
        missed_duty_label="Missed collations",
        consensus_node_label="Substrate node",
        parachain_id=2000,
        reward_token_symbol="ACA",
        reward_token_decimals=12,
        reward_token_base_unit="planck",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=aggregate_fleet_metrics(collection.operators),
    )
    alerts = evaluate_alerts(snapshot, settings)
    assert any("collation" in a.title.lower() or "collator" in a.title.lower() for a in alerts)


def test_relay_validator_demo_collect() -> None:
    settings = Settings(
        chain="polkadot",
        polkadot_role="validator",
        demo_mode=True,
        validator_stash_addresses="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        parachain_id=2006,  # ignored for relay
    )
    adapter = PolkadotAdapter()
    infra = collect_infrastructure()
    collection = asyncio.run(adapter.collect(settings, infra))
    assert adapter.operator_label == "validator"
    assert adapter.risk_kind == "slashing"
    assert adapter.primary_duty_label == "Era points"
    assert len(collection.operators) == 1
    op = collection.operators[0]
    assert op.operator_id.startswith("5")
    assert op.risk_kind == "slashing"
    assert op.duties[0].label == "Era points"
    assert "collator" not in op.status


def test_polkadot_relay_chain_alias_forces_validator_role() -> None:
    settings = Settings(
        chain="polkadot-relay",
        demo_mode=True,
        validator_stash_addresses=(
            "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY,"
            "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
        ),
    )
    assert settings.resolved_polkadot_role() == "validator"
    assert settings.resolved_chain() == "polkadot"
    snapshot = asyncio.run(collect_pulse(settings, dispatch_alerts=False))
    assert snapshot.chain == "polkadot"
    assert snapshot.chain_display_name == "Polkadot relay"
    assert snapshot.operator_label == "validator"
    assert snapshot.risk_kind == "slashing"
    assert snapshot.risk_label == "Slashing risk"
    assert snapshot.primary_duty_label == "Era points"
    assert snapshot.parachain_id is None
    assert snapshot.reward_token_symbol == "DOT"
    assert len(snapshot.validators) == 2


def test_relay_alerts_offline_and_low_era_points() -> None:
    settings = Settings(
        chain="polkadot",
        polkadot_role="validator",
        demo_mode=True,
        alert_low_era_points_below=10_000,
        alert_missed_attestations=10_000,
        alert_effectiveness_below=0,
        alert_slashing_risk_above=10_000,
        validator_stash_addresses=(
            "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY,"
            "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
        ),
    )
    adapter = PolkadotAdapter()
    infra = collect_infrastructure()
    collection = asyncio.run(adapter.collect(settings, infra))
    assert any(op.status == "offline" for op in collection.operators)

    snapshot = PulseSnapshot(
        collected_at="2026-01-01T00:00:00+00:00",
        demo_mode=True,
        chain="polkadot",
        chain_display_name="Polkadot relay",
        operator_label="validator",
        risk_kind="slashing",
        risk_label="Slashing risk",
        primary_duty_label="Era points",
        secondary_duty_label="Blocks",
        missed_duty_label="Missed era points",
        consensus_node_label="Relay node",
        reward_token_symbol="DOT",
        reward_token_decimals=10,
        reward_token_base_unit="planck",
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=aggregate_fleet_metrics(collection.operators),
    )
    alerts = evaluate_alerts(snapshot, settings)
    titles = " ".join(a.title.lower() for a in alerts)
    assert "offline" in titles
    assert "era points" in titles
    # Sync alert path: force syncing consensus
    snapshot.consensus.syncing = True
    snapshot.consensus.beacon_reachable = True
    sync_alerts = evaluate_alerts(snapshot, settings)
    assert any("sync" in a.title.lower() for a in sync_alerts)


def test_live_relay_requires_stash_addresses() -> None:
    settings = Settings(
        chain="polkadot",
        polkadot_role="validator",
        demo_mode=False,
        substrate_rpc_url="http://127.0.0.1:1",
        validator_stash_addresses="",
    )
    adapter = PolkadotAdapter()
    infra = collect_infrastructure()
    collection = asyncio.run(adapter.collect(settings, infra))
    assert collection.operators == []
    assert collection.consensus.last_error
    assert "VALIDATOR_STASH_ADDRESSES" in (collection.consensus.last_error or "")
