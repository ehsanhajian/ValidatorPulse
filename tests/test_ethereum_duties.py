from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from validator_pulse.chains.ethereum.adapter import EthereumAdapter
from validator_pulse.chains.ethereum.duty_tracker import (
    DutyHistoryStore,
    _outcome_from_rewards,
    effectiveness_inputs,
    refresh_live_duties,
    reset_duty_store,
)
from validator_pulse.collectors.beacon import (
    check_block_at_slot,
    fetch_attestation_rewards,
    fetch_attester_duties,
    fetch_proposer_duties,
)
from validator_pulse.config import Settings
from validator_pulse.models import (
    AttestationDuty,
    ConsensusHealth,
    InfrastructureHealth,
    ProposalDuty,
)


def test_outcome_from_rewards_success_late_missed() -> None:
    assert _outcome_from_rewards(
        {"source": "10", "target": "10", "head": "5", "inclusion_delay": "1"}
    ) == ("success", 1, 25)
    assert _outcome_from_rewards(
        {"source": "10", "target": "10", "head": "0", "inclusion_delay": "3"}
    ) == ("late", 3, 20)
    assert _outcome_from_rewards(
        {"source": "0", "target": "0", "head": "0", "inclusion_delay": "0"}
    ) == ("missed", 0, 0)


def test_duty_store_persists_and_aggregates() -> None:
    store = DutyHistoryStore()
    store.upsert_attestation(
        AttestationDuty(
            epoch=10, slot=320, validator_index=1, outcome="success", reward_gwei=100
        )
    )
    store.upsert_attestation(
        AttestationDuty(
            epoch=9, slot=300, validator_index=1, outcome="missed", reward_gwei=0
        )
    )
    store.upsert_proposal(
        ProposalDuty(epoch=10, slot=325, validator_index=1, outcome="success")
    )
    view = store.view_for(1)
    assert view.attestations.expected == 2
    assert view.attestations.successful == 1
    assert view.attestations.missed == 1
    assert view.proposals.successful == 1
    assert view.recent_attestations[0].epoch == 10
    assert len(view.recent_proposals) == 1

    # Pending must not overwrite a resolved outcome.
    store.upsert_attestation(
        AttestationDuty(epoch=10, slot=320, validator_index=1, outcome="pending")
    )
    assert store.view_for(1).attestations.successful == 1


def test_effectiveness_inputs_exclude_pending() -> None:
    store = DutyHistoryStore()
    store.upsert_attestation(
        AttestationDuty(epoch=2, slot=64, validator_index=7, outcome="success")
    )
    store.upsert_attestation(
        AttestationDuty(epoch=3, slot=96, validator_index=7, outcome="pending")
    )
    view = store.view_for(7)
    att_exp, att_ok, att_late, prop_exp, prop_ok = effectiveness_inputs(view)
    assert att_exp == 1
    assert att_ok == 1
    assert att_late == 0
    assert prop_exp == 0
    assert prop_ok == 0


def test_refresh_live_duties_resolves_from_beacon_apis() -> None:
    reset_duty_store()
    store = DutyHistoryStore()

    async def run() -> None:
        with (
            patch(
                "validator_pulse.chains.ethereum.duty_tracker.fetch_attester_duties",
                new_callable=AsyncMock,
            ) as att,
            patch(
                "validator_pulse.chains.ethereum.duty_tracker.fetch_attestation_rewards",
                new_callable=AsyncMock,
            ) as rewards,
            patch(
                "validator_pulse.chains.ethereum.duty_tracker.fetch_proposer_duties",
                new_callable=AsyncMock,
            ) as prop,
            patch(
                "validator_pulse.chains.ethereum.duty_tracker.check_block_at_slot",
                new_callable=AsyncMock,
            ) as block,
        ):
            # head_slot = 32*5 + 10 → head_epoch=5; lookback covers 2..5
            att.side_effect = lambda url, epoch, indices: (
                [{"validator_index": 42, "slot": epoch * 32 + 5, "committee_index": 0}]
                if epoch in (3, 4, 5)
                else []
            )
            rewards.side_effect = lambda url, epoch, indices: (
                {
                    42: {
                        "validator_index": "42",
                        "source": "12",
                        "target": "12",
                        "head": "4",
                        "inclusion_delay": "1",
                    }
                }
                if epoch == 3
                else {
                    42: {
                        "validator_index": "42",
                        "source": "0",
                        "target": "0",
                        "head": "0",
                        "inclusion_delay": "0",
                    }
                }
                if epoch == 4
                else None
            )
            prop.side_effect = lambda url, epoch: (
                [{"validator_index": 42, "slot": epoch * 32 + 8}]
                if epoch == 4
                else []
            )
            block.return_value = False

            await refresh_live_duties(
                "http://beacon.test",
                [42],
                head_slot=5 * 32 + 10,
                store=store,
            )

    asyncio.run(run())
    view = store.view_for(42)
    # epochs 3 success, 4 missed, 5 pending (current)
    assert view.attestations.successful == 1
    assert view.attestations.missed == 1
    assert view.attestations.expected == 3
    assert view.proposals.missed == 1
    assert view.recent_attestations
    assert any(d.outcome == "pending" for d in view.recent_attestations)


def test_live_adapter_does_not_invent_attestation_from_status() -> None:
    reset_duty_store()
    adapter = EthereumAdapter()
    consensus = ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=100,
        finalized_epoch=1,
        justified_epoch=2,
        peer_count=40,
        connected_peers=38,
        status="healthy",
    )
    infra = InfrastructureHealth(
        cpu_usage_percent=10,
        memory_usage_percent=20,
        memory_used_bytes=1,
        memory_total_bytes=2,
        disk_usage_percent=30,
        disk_used_bytes=1,
        disk_total_bytes=2,
        disk_latency_ms=1,
        network_healthy=True,
        network_rx_bytes_per_sec=0,
        network_tx_bytes_per_sec=0,
        clock_drift_ms=5,
        status="healthy",
    )

    async def run() -> list:
        with (
            patch(
                "validator_pulse.chains.ethereum.adapter.collect_validator_balances",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "index": 9,
                        "pubkey": "0xabc",
                        "status": "active_ongoing",
                        "balance_gwei": 32_000_000_000,
                        "effective_balance_gwei": 32_000_000_000,
                    }
                ],
            ),
            patch(
                "validator_pulse.chains.ethereum.adapter.refresh_live_duties",
                new_callable=AsyncMock,
            ),
        ):
            return await adapter._live_validators(
                "http://beacon.test", ["9"], consensus, infra
            )

    validators = asyncio.run(run())
    assert len(validators) == 1
    v = validators[0]
    # Empty history → zeros, not the old 31/32 stub from active status.
    assert v.attestations.expected == 0
    assert v.attestations.successful == 0
    assert v.attestations.missed == 0
    assert v.recent_attestations == []


def test_demo_mode_unchanged() -> None:
    settings = Settings(chain="ethereum", demo_mode=True, validator_indices="1,2")
    adapter = EthereumAdapter()
    infra = InfrastructureHealth(
        cpu_usage_percent=10,
        memory_usage_percent=20,
        memory_used_bytes=1,
        memory_total_bytes=2,
        disk_usage_percent=30,
        disk_used_bytes=1,
        disk_total_bytes=2,
        disk_latency_ms=1,
        network_healthy=True,
        network_rx_bytes_per_sec=0,
        network_tx_bytes_per_sec=0,
        clock_drift_ms=5,
        status="healthy",
    )
    collection = asyncio.run(adapter.collect(settings, infra))
    assert len(collection.operators) >= 1
    assert collection.operators[0].attestations.expected == 32
    assert collection.operators[0].recent_attestations


def test_beacon_duty_helpers_handle_http_errors() -> None:
    import httpx

    async def with_mock() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/eth/v2/beacon/blocks/" in request.url.path:
                return httpx.Response(404)
            return httpx.Response(501, json={})

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch("httpx.AsyncClient", side_effect=factory):
            assert await fetch_attester_duties("http://b", 1, [1]) == []
            assert await fetch_proposer_duties("http://b", 1) == []
            assert await fetch_attestation_rewards("http://b", 1, [1]) is None
            assert await check_block_at_slot("http://b", 99) is False

    asyncio.run(with_mock())
