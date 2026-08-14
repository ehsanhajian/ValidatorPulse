from __future__ import annotations

from dataclasses import dataclass

STAKING_PRECOMPILE = "0x0000000000000000000000000000000000001000"
EXPECTED_CHAIN_ID = 143

SELECTOR_GET_VALIDATOR = "2b6d639a"
SELECTOR_GET_CONSENSUS_SET = "fb29b729"
SELECTOR_GET_EPOCH = "757991a8"
SELECTOR_GET_PROPOSER = "fbacb0be"

# Docs: ValidatorFlagsOk vs ValidatorFlagsStakeTooLow / INSUFFICIENT_VALIDATOR_STAKE.
FLAG_STAKE_TOO_LOW = 1


def encode_selector_call(selector: str, *uints: int) -> str:
    """Build eth_call data: 4-byte selector + ABI-encoded uint arguments."""
    parts = [selector.lower().removeprefix("0x")]
    for value in uints:
        parts.append(f"{int(value):064x}")
    return "0x" + "".join(parts)


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


def decode_dynamic_bytes(data: str, offset_word: int) -> bytes:
    """Decode ABI `bytes` given the offset (in bytes) stored in a word."""
    raw = _strip_hex(data)
    start = offset_word * 2
    if start + 64 > len(raw):
        return b""
    length = int(raw[start : start + 64], 16)
    payload = raw[start + 64 : start + 64 + length * 2]
    if len(payload) < length * 2:
        payload = payload.ljust(length * 2, "0")
    return bytes.fromhex(payload)


@dataclass(frozen=True)
class EpochInfo:
    epoch: int
    in_epoch_delay_period: bool


def decode_epoch(data: str) -> EpochInfo:
    words = decode_words(data)
    epoch = words[0] if words else 0
    delay = bool(words[1]) if len(words) > 1 else False
    return EpochInfo(epoch=epoch, in_epoch_delay_period=delay)


def decode_proposer_val_id(data: str) -> int:
    words = decode_words(data)
    return words[0] if words else 0


@dataclass(frozen=True)
class ValidatorSetPage:
    is_done: bool
    next_index: int
    val_ids: list[int]


def decode_validator_set_page(data: str) -> ValidatorSetPage:
    words = decode_words(data)
    if len(words) < 3:
        return ValidatorSetPage(is_done=True, next_index=0, val_ids=[])
    is_done = bool(words[0])
    next_index = words[1]
    array_offset_bytes = words[2]
    array_word = array_offset_bytes // 32
    if array_word >= len(words):
        return ValidatorSetPage(is_done=is_done, next_index=next_index, val_ids=[])
    length = words[array_word]
    ids = [words[array_word + 1 + i] for i in range(length) if array_word + 1 + i < len(words)]
    return ValidatorSetPage(is_done=is_done, next_index=next_index, val_ids=ids)


@dataclass(frozen=True)
class ValidatorView:
    auth_address: str
    flags: int
    stake: int
    acc_reward_per_token: int
    commission: int
    unclaimed_rewards: int
    consensus_stake: int
    consensus_commission: int
    snapshot_stake: int
    snapshot_commission: int
    secp_pubkey: bytes
    bls_pubkey: bytes

    @property
    def stake_too_low(self) -> bool:
        return bool(self.flags & FLAG_STAKE_TOO_LOW)

    @property
    def eligible(self) -> bool:
        return not self.stake_too_low and self.stake > 0

    @property
    def secp_pubkey_hex(self) -> str:
        return self.secp_pubkey.hex() if self.secp_pubkey else ""


def decode_validator(data: str) -> ValidatorView:
    words = decode_words(data)
    padded = words + [0] * 12
    secp = b""
    bls = b""
    if padded[10]:
        secp = decode_dynamic_bytes(data, padded[10])
    if padded[11]:
        bls = decode_dynamic_bytes(data, padded[11])
    return ValidatorView(
        auth_address=decode_address_word(padded[0]),
        flags=padded[1],
        stake=padded[2],
        acc_reward_per_token=padded[3],
        commission=padded[4],
        unclaimed_rewards=padded[5],
        consensus_stake=padded[6],
        consensus_commission=padded[7],
        snapshot_stake=padded[8],
        snapshot_commission=padded[9],
        secp_pubkey=secp,
        bls_pubkey=bls,
    )
