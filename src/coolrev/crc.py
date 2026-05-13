from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Crc16Variant:
    name: str
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int


CRC16_VARIANTS = (
    Crc16Variant("ccitt-false", 0x1021, 0xFFFF, False, False, 0x0000),
    Crc16Variant("xmodem", 0x1021, 0x0000, False, False, 0x0000),
    Crc16Variant("kermit", 0x1021, 0x0000, True, True, 0x0000),
    Crc16Variant("x25", 0x1021, 0xFFFF, True, True, 0xFFFF),
    Crc16Variant("ibm", 0x8005, 0x0000, True, True, 0x0000),
    Crc16Variant("modbus", 0x8005, 0xFFFF, True, True, 0x0000),
)


def _reflect(value: int, width: int) -> int:
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def crc16(data: bytes, variant: Crc16Variant) -> int:
    crc = variant.init
    for byte in data:
        if variant.refin:
            byte = _reflect(byte, 8)
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ variant.poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    if variant.refout:
        crc = _reflect(crc, 16)
    return (crc ^ variant.xorout) & 0xFFFF


def crc16_matches(data: bytes, observed: bytes) -> list[str]:
    if len(observed) != 2:
        return []

    observed_be = int.from_bytes(observed, "big")
    observed_le = int.from_bytes(observed, "little")
    matches: list[str] = []
    for variant in CRC16_VARIANTS:
        value = crc16(data, variant)
        if value == observed_be:
            matches.append(f"{variant.name}:be")
        if value == observed_le:
            matches.append(f"{variant.name}:le")
    return matches
