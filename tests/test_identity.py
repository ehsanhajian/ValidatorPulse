from __future__ import annotations

import asyncio

from validator_pulse.identity import enrich_operator_names, fetch_subscan_name
from validator_pulse.models import (
    AttestationStats,
    ProposalStats,
    ValidatorStats,
)


def test_well_known_alice_name() -> None:
    name = asyncio.run(
        fetch_subscan_name("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY")
    )
    assert name == "Alice"


def test_enrich_sets_display_name() -> None:
    op = ValidatorStats(
        index=0,
        pubkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
        status="active_collator",
        balance_gwei=0,
        effective_balance_gwei=0,
        attestations=AttestationStats(expected=1, successful=1, missed=0, late=0),
        proposals=ProposalStats(expected=0, successful=0, missed=0),
        rewards_gwei=0,
        effectiveness_score=100,
        slashing_risk_score=0,
    )
    asyncio.run(enrich_operator_names([op], chain="polkadot", parachain_id=2006))
    assert op.display_name == "Bob"
