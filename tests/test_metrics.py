from __future__ import annotations

from types import SimpleNamespace

from validator_pulse.metrics import to_prometheus
from validator_pulse.models import PulseSnapshot
from validator_pulse.web import _format_rewards


def test_small_signed_ethereum_rewards_remain_visible() -> None:
    snapshot = SimpleNamespace(
        reward_token_base_unit="gwei",
        reward_token_symbol="ETH",
    )
    assert _format_rewards(25_000, snapshot) == "25,000 gwei"
    assert _format_rewards(-7, snapshot) == "-7 gwei"


def test_prometheus_exports_fleet_and_validator_reward_window() -> None:
    validator_base = {
        "pubkey": None,
        "display_name": None,
        "status": "active_ongoing",
        "balance_gwei": 32_000_000_000,
        "effective_balance_gwei": 32_000_000_000,
        "attestations": {
            "expected": 1,
            "successful": 1,
            "missed": 0,
            "late": 0,
        },
        "proposals": {"expected": 0, "successful": 0, "missed": 0},
        "effectiveness_score": 100,
        "slashing_risk_score": 0,
    }
    snapshot = PulseSnapshot(
        collected_at="2026-08-09T00:00:00+00:00",
        demo_mode=False,
        verdict={"status": "healthy", "answer": "Yes", "summary": "Healthy"},
        validators=[
            {**validator_base, "index": 1, "rewards_gwei": 25},
            {**validator_base, "index": 2, "rewards_gwei": -10},
        ],
        consensus={
            "beacon_reachable": True,
            "syncing": False,
            "sync_distance": 0,
            "head_slot": 100,
            "finalized_epoch": 1,
            "justified_epoch": 2,
            "peer_count": 10,
            "connected_peers": 10,
            "status": "healthy",
        },
        infrastructure={
            "cpu_usage_percent": 10,
            "memory_usage_percent": 20,
            "memory_used_bytes": 1,
            "memory_total_bytes": 2,
            "disk_usage_percent": 30,
            "disk_used_bytes": 1,
            "disk_total_bytes": 2,
            "disk_latency_ms": 1,
            "network_healthy": True,
            "network_rx_bytes_per_sec": 0,
            "network_tx_bytes_per_sec": 0,
            "clock_drift_ms": 1,
            "status": "healthy",
        },
        metrics={
            "validator_effectiveness_score": 100,
            "validator_missed_attestations_total": 0,
            "validator_slashing_risk_score": 0,
        },
    )

    output = to_prometheus(snapshot)
    assert "# HELP validator_rewards_gwei Net consensus duty rewards" in output
    assert "validator_rewards_gwei 15" in output
    assert 'validator_rewards_gwei{validator_index="1"} 25' in output
    assert 'validator_rewards_gwei{validator_index="2"} -10' in output
    assert 'validator_attestation_rewards_gwei{validator_index="1"} 0' in output
    assert 'validator_proposal_rewards_gwei{validator_index="1"} 0' in output
    assert 'validator_sync_committee_rewards_gwei{validator_index="1"} 0' in output
    assert 'validator_rewards_complete{validator_index="1"} 1' in output
    assert 'operator_rewards_base_units{chain="ethereum"' in output
    assert "operator_effectiveness_score" in output
    assert "operator_risk_score" in output
