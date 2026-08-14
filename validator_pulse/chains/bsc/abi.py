from __future__ import annotations

from dataclasses import dataclass

SLASH_INDICATOR = "0x0000000000000000000000000000000000001001"
STAKE_HUB = "0x0000000000000000000000000000000000002002"
VALIDATOR_SET = "0x0000000000000000000000000000000000001000"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Mainnet 56, Chapel testnet 97 — never treat docs' 50/200/600 as thresholds.
EXPECTED_CHAIN_IDS = frozenset({56, 97})
MAINNET_CHAIN_ID = 56

# StakeHub.SlashType
SLASH_DOUBLE_SIGN = 0
SLASH_DOWNTIME = 1
SLASH_MALICIOUS_VOTE = 2

SELECTOR_VS_GET_VALIDATORS = "b7ab4db5"  # getValidators()
SELECTOR_VS_GET_LIVING = "3b071dcc"  # getLivingValidators()
SELECTOR_VS_GET_MINING = "4df6e0c3"  # getMiningValidators()
SELECTOR_VS_TURN_LENGTH = "8c5d749d"  # getTurnLength()
SELECTOR_VS_IS_CURRENT = "55614fcc"  # isCurrentValidator(address)

SELECTOR_SI_INDICATOR = "37c8dab9"  # getSlashIndicator(address)
SELECTOR_SI_THRESHOLDS = "8256ace6"  # getSlashThresholds()

SELECTOR_SH_GET_VALIDATORS = "bff02e20"  # getValidators(uint256,uint256)
SELECTOR_SH_BASIC = "cbb04d9d"  # getValidatorBasicInfo(address)
SELECTOR_SH_CONSENSUS = "059ddd22"  # getValidatorConsensusAddress(address)
SELECTOR_SH_VOTE = "6f8e2fa4"  # getValidatorVoteAddress(address)
SELECTOR_SH_DESCRIPTION = "a43569b3"  # getValidatorDescription(address)
SELECTOR_SH_CONSENSUS_TO_OP = "86d54506"  # consensusToOperator(address)
SELECTOR_SH_DOWNTIME_AMOUNT = "d8ca511f"  # downtimeSlashAmount()
SELECTOR_SH_FELONY_JAIL = "f1f74d84"  # felonyJailTime()
SELECTOR_SH_FELONY_AMOUNT = "bdceadf3"  # felonySlashAmount()

TOPIC_STAKEHUB_SLASHED = "0x6e9a2ee7aee95665e3a774a212eb11441b217e3e4656ab9563793094689aabb2"
TOPIC_STAKEHUB_JAILED = "0x4905ac32602da3fb8b4b7b00c285e5fc4c6c2308cc908b4a1e4e9625a29c90a3"
TOPIC_SI_SLASHED = "0xddb6012116e51abf5436d956a4f0ebd927e92c576ff96d7918290c8782291e3e"
TOPIC_SI_MALICIOUS_VOTE = "0x7b78aadacff901d8b63d0dba4f86283d4db8aef27f9ed70413dd860f1c9532b6"


def encode_selector_call(selector: str, *uints: int) -> str:
    parts = [selector.lower().removeprefix("0x")]
    for value in uints:
        parts.append(f"{int(value):064x}")
    return "0x" + "".join(parts)


def encode_address_call(selector: str, address: str) -> str:
    addr = normalize_address(address)
    padded = addr.removeprefix("0x").zfill(64)
    return "0x" + selector.lower().removeprefix("0x") + padded


def normalize_address(address: str) -> str:
    text = (address or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    text = text.zfill(40)
    return "0x" + text[-40:]


def is_zero_address(address: str | None) -> bool:
    if not address:
        return True
    return normalize_address(address) == ZERO_ADDRESS


def topic_address(address: str) -> str:
    return "0x" + normalize_address(address).removeprefix("0x").zfill(64)


def _strip_hex(data: str) -> str:
    text = (data or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) % 2:
        text = "0" + text
    return text


def decode_words(data: str) -> list[int]:
    raw = _strip_hex(data)
    words: list[int] = []
    for i in range(0, len(raw), 64):
        chunk = raw[i : i + 64]
        if len(chunk) < 64:
            chunk = chunk.ljust(64, "0")
        words.append(int(chunk, 16) if chunk else 0)
    return words


def decode_address_word(word: int) -> str:
    return f"0x{word:040x}"[-42:]


def decode_bool(data: str) -> bool:
    words = decode_words(data)
    return bool(words[0]) if words else False


def decode_uint(data: str) -> int:
    words = decode_words(data)
    return words[0] if words else 0


def decode_address(data: str) -> str:
    words = decode_words(data)
    return decode_address_word(words[0]) if words else ZERO_ADDRESS


def decode_address_array(data: str, *, offset_bytes: int | None = None) -> list[str]:
    words = decode_words(data)
    if not words:
        return []
    start = (offset_bytes if offset_bytes is not None else words[0]) // 32
    if start >= len(words):
        # Some nodes return the array without a leading offset.
        start = 0
    length = words[start]
    out: list[str] = []
    for i in range(length):
        idx = start + 1 + i
        if idx >= len(words):
            break
        addr = decode_address_word(words[idx])
        if not is_zero_address(addr):
            out.append(addr)
    return out


def decode_two_uints(data: str) -> tuple[int, int]:
    words = decode_words(data)
    first = words[0] if words else 0
    second = words[1] if len(words) > 1 else 0
    return first, second


def decode_dynamic_bytes(data: str) -> bytes:
    raw = _strip_hex(data)
    words = decode_words(data)
    if not words:
        return b""
    start = words[0] * 2
    if start + 64 > len(raw):
        start = 0
    length = int(raw[start : start + 64] or "0", 16)
    payload = raw[start + 64 : start + 64 + length * 2]
    if len(payload) < length * 2:
        payload = payload.ljust(length * 2, "0")
    return bytes.fromhex(payload) if payload else b""


def decode_abi_string(raw_hex: str, offset_bytes: int) -> str:
    raw = _strip_hex(raw_hex)
    start = offset_bytes * 2
    if start + 64 > len(raw):
        return ""
    length = int(raw[start : start + 64] or "0", 16)
    payload = raw[start + 64 : start + 64 + length * 2]
    try:
        return bytes.fromhex(payload).decode("utf-8", errors="replace")
    except ValueError:
        return ""


@dataclass(frozen=True)
class ValidatorBasicInfo:
    created_time: int
    jailed: bool
    jail_until: int


def decode_basic_info(data: str) -> ValidatorBasicInfo:
    words = decode_words(data) + [0, 0, 0]
    return ValidatorBasicInfo(
        created_time=words[0],
        jailed=bool(words[1]),
        jail_until=words[2],
    )


@dataclass(frozen=True)
class ValidatorDescription:
    moniker: str
    identity: str
    website: str
    details: str


def decode_description(data: str) -> ValidatorDescription:
    words = decode_words(data)
    if len(words) < 4:
        return ValidatorDescription("", "", "", "")
    # Tuple of four strings: four relative offsets from the start of the tuple.
    return ValidatorDescription(
        moniker=decode_abi_string(data, words[0]),
        identity=decode_abi_string(data, words[1]),
        website=decode_abi_string(data, words[2]),
        details=decode_abi_string(data, words[3]),
    )


@dataclass(frozen=True)
class StakeHubValidatorPage:
    operators: list[str]
    credits: list[str]
    total_length: int


def decode_stakehub_validator_page(data: str) -> StakeHubValidatorPage:
    words = decode_words(data)
    if len(words) < 3:
        return StakeHubValidatorPage([], [], 0)
    ops = decode_address_array(data, offset_bytes=words[0])
    credits = decode_address_array(data, offset_bytes=words[1])
    total = words[2]
    return StakeHubValidatorPage(operators=ops, credits=credits, total_length=total)


def slash_type_label(slash_type: int) -> str:
    if slash_type == SLASH_DOUBLE_SIGN:
        return "double-sign"
    if slash_type == SLASH_DOWNTIME:
        return "downtime"
    if slash_type == SLASH_MALICIOUS_VOTE:
        return "malicious finality vote"
    return f"slash-type-{slash_type}"
