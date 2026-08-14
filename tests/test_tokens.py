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


def test_cosmos_hub_and_celestia_profiles() -> None:
    atom = resolve_reward_token(chain="cosmos", cosmos_profile="cosmoshub")
    assert atom.symbol == "ATOM"
    assert atom.decimals == 6
    assert atom.base_unit == "uatom"

    tia = resolve_reward_token(chain="cosmos", cosmos_profile="celestia")
    assert tia.symbol == "TIA"
    assert tia.decimals == 6

    overridden = resolve_reward_token(
        chain="cosmos",
        cosmos_profile="celestia",
        symbol_override="XTIA",
        decimals_override=9,
    )
    assert overridden.symbol == "XTIA"
    assert overridden.decimals == 9


def test_solana_token_defaults() -> None:
    sol = resolve_reward_token(chain="solana")
    assert sol.symbol == "SOL"
    assert sol.decimals == 9
    assert sol.base_unit == "lamports"
    text = format_token_amount(2 * 10**9, sol)
    assert "SOL" in text

    overridden = resolve_reward_token(
        chain="solana",
        symbol_override="WSOL",
        decimals_override=6,
    )
    assert overridden.symbol == "WSOL"
    assert overridden.decimals == 6
    assert overridden.base_unit == "lamports"


def test_near_token_defaults() -> None:
    near = resolve_reward_token(chain="near")
    assert near.symbol == "NEAR"
    assert near.decimals == 24
    assert near.base_unit == "yoctoNEAR"
    text = format_token_amount(2 * 10**24, near)
    assert "NEAR" in text

    overridden = resolve_reward_token(
        chain="near",
        symbol_override="WNEAR",
        decimals_override=18,
    )
    assert overridden.symbol == "WNEAR"
    assert overridden.decimals == 18
    assert overridden.base_unit == "yoctoNEAR"


def test_cardano_token_defaults() -> None:
    ada = resolve_reward_token(chain="cardano")
    assert ada.symbol == "ADA"
    assert ada.decimals == 6
    assert ada.base_unit == "lovelace"
    text = format_token_amount(2 * 10**6, ada)
    assert "ADA" in text


def test_tezos_token_defaults() -> None:
    xtz = resolve_reward_token(chain="tezos")
    assert xtz.symbol == "XTZ"
    assert xtz.decimals == 6
    assert xtz.base_unit == "mutez"
    text = format_token_amount(2 * 10**6, xtz)
    assert "XTZ" in text


def test_algorand_token_defaults() -> None:
    algo = resolve_reward_token(chain="algorand")
    assert algo.symbol == "ALGO"
    assert algo.decimals == 6
    assert algo.base_unit == "microAlgos"
    text = format_token_amount(2 * 10**6, algo)
    assert "ALGO" in text


def test_bsc_token_defaults() -> None:
    bnb = resolve_reward_token(chain="bsc")
    assert bnb.symbol == "BNB"
    assert bnb.decimals == 18
    assert bnb.base_unit == "wei"
    text = format_token_amount(2 * 10**18, bnb)
    assert "BNB" in text


def test_aptos_token_defaults() -> None:
    apt = resolve_reward_token(chain="aptos")
    assert apt.symbol == "APT"
    assert apt.decimals == 8
    assert apt.base_unit == "octas"
    text = format_token_amount(2 * 10**8, apt)
    assert "APT" in text


def test_sui_token_defaults() -> None:
    sui = resolve_reward_token(chain="sui")
    assert sui.symbol == "SUI"
    assert sui.decimals == 9
    assert sui.base_unit == "MIST"
    text = format_token_amount(2 * 10**9, sui)
    assert "SUI" in text


def test_monad_token_defaults() -> None:
    mon = resolve_reward_token(chain="monad")
    assert mon.symbol == "MON"
    assert mon.decimals == 18
    assert mon.base_unit == "wei"
    text = format_token_amount(2 * 10**18, mon)
    assert "MON" in text
