from __future__ import annotations

"""Minimal Bech32 encode/decode for Cosmos address prefix conversion."""

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= generator[i]
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _create_checksum(hrp: str, data: list[int]) -> list[int]:
    values = _hrp_expand(hrp) + data
    polymod = _polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _verify_checksum(hrp: str, data: list[int]) -> bool:
    return _polymod(_hrp_expand(hrp) + data) == 1


def _convertbits(data: bytes | list[int], from_bits: int, to_bits: int, pad: bool = True) -> list[int] | None:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << to_bits) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            return None
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        return None
    return ret


def bech32_decode(address: str) -> tuple[str, bytes]:
    address = address.strip()
    if address != address.lower() and address != address.upper():
        raise ValueError(f"Invalid bech32 address casing: {address}")
    address = address.lower()
    if address.rfind("1") < 1:
        raise ValueError(f"Invalid bech32 address: {address}")
    pos = address.rfind("1")
    hrp = address[:pos]
    data_part = address[pos + 1 :]
    if not hrp or len(data_part) < 6:
        raise ValueError(f"Invalid bech32 address: {address}")
    data: list[int] = []
    for char in data_part:
        idx = _CHARSET.find(char)
        if idx == -1:
            raise ValueError(f"Invalid bech32 character in {address}")
        data.append(idx)
    if not _verify_checksum(hrp, data):
        raise ValueError(f"Invalid bech32 checksum: {address}")
    decoded = _convertbits(data[:-6], 5, 8, False)
    if decoded is None:
        raise ValueError(f"Invalid bech32 data: {address}")
    return hrp, bytes(decoded)


def bech32_encode(hrp: str, raw: bytes) -> str:
    data = _convertbits(raw, 8, 5, True)
    if data is None:
        raise ValueError("Unable to convert address bytes to bech32")
    combined = data + _create_checksum(hrp, data)
    return hrp + "1" + "".join(_CHARSET[d] for d in combined)


def retarget_bech32(address: str, new_hrp: str) -> str:
    """Re-encode the same payload under a different human-readable prefix."""
    _hrp, raw = bech32_decode(address)
    return bech32_encode(new_hrp, raw)
