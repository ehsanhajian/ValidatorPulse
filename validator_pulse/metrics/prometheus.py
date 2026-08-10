from __future__ import annotations

from validator_pulse.models import PulseSnapshot


def _line(
    name: str,
    value: float | int,
    labels: dict[str, str] | None = None,
    help_text: str | None = None,
    metric_type: str = "gauge",
) -> str:
    parts = []
    if help_text:
        parts.append(f"# HELP {name} {help_text}")
        parts.append(f"# TYPE {name} {metric_type}")
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        parts.append(f"{name}{{{label_str}}} {value}")
    else:
        parts.append(f"{name} {value}")
    return "\n".join(parts) + "\n"


def to_prometheus(snapshot: PulseSnapshot) -> str:
    """Export fleet + per-operator gauges.

    Dual-emits legacy ``validator_*`` series (Ethereum-era names) and chain-aware
    ``operator_*`` series so existing scrapes keep working while new adapters
    can rely on ``operator_id`` / ``chain`` labels and base-unit rewards.
    """
    out: list[str] = []
    m = snapshot.metrics
    effectiveness = (
        m.effectiveness_score
        if m.effectiveness_score is not None
        else m.validator_effectiveness_score
    )
    missed = (
        m.missed_primary_duties_total
        if m.missed_primary_duties_total is not None
        else m.validator_missed_attestations_total
    )
    risk = (
        m.risk_score if m.risk_score is not None else m.validator_slashing_risk_score
    )
    rewards_total = sum(
        (v.rewards_base_units if v.rewards_base_units is not None else v.rewards_gwei)
        for v in snapshot.validators
    )
    chain_labels = {"chain": snapshot.chain}

    out.append(
        _line(
            "validator_effectiveness_score",
            effectiveness,
            help_text="Fleet-average validator effectiveness score (0-100)",
        )
    )
    out.append(
        _line(
            "operator_effectiveness_score",
            effectiveness,
            chain_labels,
            help_text="Fleet-average operator effectiveness score (0-100)",
        )
    )
    out.append(
        _line(
            "validator_missed_attestations_total",
            missed,
            help_text="Total missed primary duties across monitored operators",
            metric_type="counter",
        )
    )
    out.append(
        _line(
            "operator_missed_primary_duties_total",
            missed,
            chain_labels,
            help_text="Total missed primary duties across monitored operators",
            metric_type="counter",
        )
    )
    out.append(
        _line(
            "validator_slashing_risk_score",
            risk,
            help_text="Fleet-average operational/penalty risk score (0-100)",
        )
    )
    out.append(
        _line(
            "operator_risk_score",
            risk,
            {**chain_labels, "risk_kind": snapshot.risk_kind},
            help_text="Fleet-average operational/penalty risk score (0-100)",
        )
    )
    out.append(
        _line(
            "validator_rewards_gwei",
            rewards_total,
            help_text=(
                "Net consensus duty rewards in the rolling monitoring window "
                "(legacy name; value is token base units)"
            ),
        )
    )
    out.append(
        _line(
            "operator_rewards_base_units",
            rewards_total,
            {
                **chain_labels,
                "token": snapshot.reward_token_symbol,
                "base_unit": snapshot.reward_token_base_unit,
            },
            help_text="Net consensus duty rewards in token base units",
        )
    )

    for v in snapshot.validators:
        operator_id = v.operator_id or v.pubkey or str(v.index)
        legacy_labels = {"validator_index": str(v.index)}
        modern_labels = {
            "chain": snapshot.chain,
            "operator_id": operator_id,
        }
        risk_score = (
            v.risk_score if v.risk_score is not None else v.slashing_risk_score
        )
        balance = (
            v.balance_base_units
            if v.balance_base_units is not None
            else v.balance_gwei
        )
        rewards = (
            v.rewards_base_units
            if v.rewards_base_units is not None
            else v.rewards_gwei
        )
        missed_primary = v.primary_duty().missed

        out.append(
            _line("validator_effectiveness_score", v.effectiveness_score, legacy_labels)
        )
        out.append(
            _line("operator_effectiveness_score", v.effectiveness_score, modern_labels)
        )
        out.append(
            _line(
                "validator_missed_attestations_total",
                missed_primary,
                legacy_labels,
            )
        )
        out.append(
            _line(
                "operator_missed_primary_duties_total",
                missed_primary,
                modern_labels,
            )
        )
        out.append(_line("validator_slashing_risk_score", risk_score, legacy_labels))
        out.append(
            _line(
                "operator_risk_score",
                risk_score,
                {**modern_labels, "risk_kind": v.risk_kind},
            )
        )
        out.append(_line("validator_balance_gwei", balance, legacy_labels))
        out.append(_line("operator_balance_base_units", balance, modern_labels))
        out.append(_line("validator_rewards_gwei", rewards, legacy_labels))
        out.append(_line("operator_rewards_base_units", rewards, modern_labels))

        if snapshot.chain == "ethereum":
            out.append(
                _line(
                    "validator_attestation_rewards_gwei",
                    v.attestation_rewards_gwei,
                    legacy_labels,
                )
            )
            out.append(
                _line(
                    "validator_proposal_rewards_gwei",
                    v.proposal_rewards_gwei,
                    legacy_labels,
                )
            )
            out.append(
                _line(
                    "validator_sync_committee_rewards_gwei",
                    v.sync_committee_rewards_gwei,
                    legacy_labels,
                )
            )
            out.append(
                _line(
                    "validator_rewards_complete",
                    int(v.reward_data_complete),
                    legacy_labels,
                )
            )

    c = snapshot.consensus
    i = snapshot.infrastructure
    out.append(
        _line("beacon_sync_distance", c.sync_distance, help_text="Consensus sync distance")
    )
    out.append(_line("beacon_peer_count", c.peer_count))
    out.append(_line("beacon_head_slot", c.head_slot))
    out.append(_line("beacon_finalized_epoch", c.finalized_epoch))
    out.append(_line("infra_cpu_usage_percent", i.cpu_usage_percent))
    out.append(_line("infra_memory_usage_percent", i.memory_usage_percent))
    out.append(_line("infra_disk_usage_percent", i.disk_usage_percent))
    out.append(_line("infra_disk_latency_ms", i.disk_latency_ms))
    out.append(_line("infra_clock_drift_ms", i.clock_drift_ms))
    return "".join(out)
