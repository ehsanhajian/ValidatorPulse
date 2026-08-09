from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx

from validator_pulse.collectors.beacon import _withdrawal_address
from validator_pulse.identity import (
    decode_graffiti,
    enrich_operator_names,
    fetch_ens_primary_name,
    fetch_rated_operator_name,
    fetch_recent_graffiti,
    fetch_subscan_name,
    reset_identity_cache,
)
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
    assert op.display_name_source == "subscan"


def _ethereum_operator() -> ValidatorStats:
    return ValidatorStats(
        index=42,
        pubkey="0x" + ("ab" * 48),
        withdrawal_address="0x" + ("12" * 20),
        status="active_ongoing",
        balance_gwei=32_000_000_000,
        effective_balance_gwei=32_000_000_000,
        attestations=AttestationStats(expected=1, successful=1, missed=0, late=0),
        proposals=ProposalStats(expected=0, successful=0, missed=0),
        rewards_gwei=0,
        effectiveness_score=100,
        slashing_risk_score=0,
    )


def test_execution_withdrawal_credentials_extract_address() -> None:
    address = "12" * 20
    assert _withdrawal_address(f"0x01{'00' * 11}{address}") == f"0x{address}"
    assert _withdrawal_address(f"0x02{'00' * 11}{address}") == f"0x{address}"
    assert _withdrawal_address(f"0x00{'00' * 31}") is None
    assert _withdrawal_address("not-hex") is None


def test_rated_operator_mapping_prefers_node_operator() -> None:
    async def run() -> str | None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer secret"
            return httpx.Response(
                200,
                json={
                    "validatorPubkey": "0xabc",
                    "nodeOperators": [{"displayName": "Example Operator"}],
                    "dvtOperators": [{"name": "DVT Group"}],
                    "pool": "Example Pool",
                },
            )

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch("httpx.AsyncClient", side_effect=factory):
            return await fetch_rated_operator_name(
                index=42, api_key="secret"
            )

    assert asyncio.run(run()) == "Example Operator"


def test_ens_lookup_is_opt_in_and_parses_primary_name() -> None:
    assert (
        asyncio.run(
            fetch_ens_primary_name(
                "0x" + ("12" * 20),
                enabled=False,
            )
        )
        is None
    )

    async def run() -> str | None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "address": "0x" + ("12" * 20),
                    "primary_name": "validator.eth",
                    "display": "Validator.eth",
                },
            )
        )
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch("httpx.AsyncClient", side_effect=factory):
            return await fetch_ens_primary_name(
                "0x" + ("12" * 20),
                enabled=True,
            )

    assert asyncio.run(run()) == "Validator.eth"


def test_graffiti_decoding_and_recent_block_lookup() -> None:
    encoded = "0x" + "Validator 42".encode().hex().ljust(64, "0")
    assert decode_graffiti(encoded) == "Validator 42"
    assert decode_graffiti("not-hex") is None

    async def run() -> str | None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/100"):
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "message": {
                            "body": {
                                "graffiti": encoded,
                            }
                        }
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        with patch("httpx.AsyncClient", side_effect=factory):
            return await fetch_recent_graffiti(
                "http://beacon.test", [100, 99]
            )

    assert asyncio.run(run()) == "Validator 42"


def test_ethereum_fallback_uses_rated_and_caches_result() -> None:
    reset_identity_cache()
    op = _ethereum_operator()

    async def run() -> None:
        with (
            patch(
                "validator_pulse.identity.fetch_beaconcha_name",
                new_callable=AsyncMock,
                return_value=None,
            ) as beaconcha,
            patch(
                "validator_pulse.identity.fetch_rated_operator_name",
                new_callable=AsyncMock,
                return_value="Rated Operator",
            ) as rated,
            patch(
                "validator_pulse.identity.fetch_ens_primary_name",
                new_callable=AsyncMock,
            ) as ens,
            patch(
                "validator_pulse.identity.fetch_recent_graffiti",
                new_callable=AsyncMock,
            ) as graffiti,
        ):
            await enrich_operator_names(
                [op],
                chain="ethereum",
                rated_api_key="secret",
            )
            # A fresh object for the same validator should use the cache.
            cached_op = _ethereum_operator()
            await enrich_operator_names(
                [cached_op],
                chain="ethereum",
                rated_api_key="secret",
            )
            assert cached_op.display_name == "Rated Operator"
            assert beaconcha.await_count == 1
            assert rated.await_count == 1
            ens.assert_not_awaited()
            graffiti.assert_not_awaited()

    asyncio.run(run())
    assert op.display_name == "Rated Operator"
    assert op.display_name_source == "rated"


def test_disabled_enrichment_leaves_identity_untouched() -> None:
    reset_identity_cache()
    op = _ethereum_operator()

    async def run() -> None:
        with patch(
            "validator_pulse.identity.fetch_beaconcha_name",
            new_callable=AsyncMock,
        ) as beaconcha:
            await enrich_operator_names([op], chain="ethereum", enabled=False)
            beaconcha.assert_not_awaited()

    asyncio.run(run())
    assert op.display_name is None
