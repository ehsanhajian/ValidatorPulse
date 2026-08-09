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
    out: list[str] = []
    m = snapshot.metrics
    out.append(
        _line(
            "validator_effectiveness_score",
            m.validator_effectiveness_score,
            help_text="Fleet-average validator effectiveness score (0-100)",
        )
    )
    out.append(
        _line(
            "validator_missed_attestations_total",
            m.validator_missed_attestations_total,
            help_text="Total missed attestations across monitored validators",
            metric_type="counter",
        )
    )
    out.append(
        _line(
            "validator_slashing_risk_score",
            m.validator_slashing_risk_score,
            help_text="Fleet-average slashing risk score (0-100)",
        )
    )
    out.append(
        _line(
            "validator_rewards_gwei",
            sum(v.rewards_gwei for v in snapshot.validators),
            help_text=(
                "Net consensus duty rewards in the rolling monitoring window "
                "(attestation, proposal, and sync committee)"
            ),
        )
    )

    for v in snapshot.validators:
        labels = {"validator_index": str(v.index)}
        out.append(_line("validator_effectiveness_score", v.effectiveness_score, labels))
        out.append(
            _line("validator_missed_attestations_total", v.attestations.missed, labels)
        )
        out.append(_line("validator_slashing_risk_score", v.slashing_risk_score, labels))
        out.append(_line("validator_balance_gwei", v.balance_gwei, labels))
        out.append(_line("validator_rewards_gwei", v.rewards_gwei, labels))
        if snapshot.chain == "ethereum":
            out.append(
                _line(
                    "validator_attestation_rewards_gwei",
                    v.attestation_rewards_gwei,
                    labels,
                )
            )
            out.append(
                _line(
                    "validator_proposal_rewards_gwei",
                    v.proposal_rewards_gwei,
                    labels,
                )
            )
            out.append(
                _line(
                    "validator_sync_committee_rewards_gwei",
                    v.sync_committee_rewards_gwei,
                    labels,
                )
            )
            out.append(
                _line(
                    "validator_rewards_complete",
                    int(v.reward_data_complete),
                    labels,
                )
            )

    c = snapshot.consensus
    i = snapshot.infrastructure
    out.append(_line("beacon_sync_distance", c.sync_distance, help_text="Beacon sync distance in slots"))
    out.append(_line("beacon_peer_count", c.peer_count))
    out.append(_line("beacon_head_slot", c.head_slot))
    out.append(_line("beacon_finalized_epoch", c.finalized_epoch))
    out.append(_line("infra_cpu_usage_percent", i.cpu_usage_percent))
    out.append(_line("infra_memory_usage_percent", i.memory_usage_percent))
    out.append(_line("infra_disk_usage_percent", i.disk_usage_percent))
    out.append(_line("infra_disk_latency_ms", i.disk_latency_ms))
    out.append(_line("infra_clock_drift_ms", i.clock_drift_ms))
    return "".join(out)
