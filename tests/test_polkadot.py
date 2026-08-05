from __future__ import annotations

import asyncio

from validator_pulse.alerts import evaluate_alerts
from validator_pulse.chains.polkadot.adapter import PolkadotAdapter
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings
from validator_pulse.models import PulseSnapshot
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
        parachain_id=2000,
        verdict={"status": "degraded", "answer": "x", "summary": "y"},
        validators=collection.operators,
        consensus=collection.consensus,
        infrastructure=collection.infrastructure,
        metrics=aggregate_fleet_metrics(collection.operators),
    )
    alerts = evaluate_alerts(snapshot, settings)
    assert any("collation" in a.title.lower() or "collator" in a.title.lower() for a in alerts)
