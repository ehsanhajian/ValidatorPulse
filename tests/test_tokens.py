from __future__ import annotations

from validator_pulse.chains.polkadot.tokens import format_token_amount, resolve_reward_token


def test_default_polkadot_is_dot() -> None:
    token = resolve_reward_token(chain="polkadot")
    assert token.symbol == "DOT"
    assert token.decimals == 10


def test_astar_parachain_maps_to_astr() -> None:
    token = resolve_reward_token(chain="polkadot", parachain_id=2006)
    assert token.symbol == "ASTR"
    assert token.decimals == 18


def test_moonbeam_and_override() -> None:
    token = resolve_reward_token(chain="polkadot", parachain_id=2004)
    assert token.symbol == "GLMR"
    overridden = resolve_reward_token(
        chain="polkadot",
        parachain_id=2004,
        symbol_override="MYTOKEN",
        decimals_override=12,
    )
    assert overridden.symbol == "MYTOKEN"
    assert overridden.decimals == 12


def test_unknown_parachain_fallback() -> None:
    token = resolve_reward_token(chain="polkadot", parachain_id=9999)
    assert token.symbol == "PARA9999"


def test_format_uses_symbol() -> None:
    token = resolve_reward_token(chain="polkadot", parachain_id=2006)
    text = format_token_amount(2 * 10**18, token)
    assert "ASTR" in text
