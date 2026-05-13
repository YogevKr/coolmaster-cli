from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .crc import crc16_matches

STX = 0x32
ETX = 0x34

ADDRESS_CLASSES = {
    0x10: "outdoor",
    0x11: "htu",
    0x20: "indoor",
    0x30: "erv",
    0x35: "diffuser",
    0x38: "mcu",
    0x40: "rmc",
    0x50: "wired_remote",
    0x58: "pim",
    0x59: "sim",
    0x5A: "peak",
    0x5B: "power_divider",
    0x62: "wifi_kit",
}

PACKET_TYPES = {
    0: "standby",
    1: "normal",
    2: "gathering",
    3: "install",
    4: "download",
}

DATA_TYPES = {
    0: "undefined",
    1: "read",
    2: "write",
    3: "request",
    4: "notification",
    5: "response",
    6: "ack",
    7: "nack",
}

MESSAGE_PAYLOAD_TYPES = {
    0: ("enum", 1),
    1: ("variable", 2),
    2: ("long_variable", 4),
    3: ("structure", None),
}

NASA_MESSAGES = {
    0x0202: "error_code_1",
    0x0203: "error_code_2",
    0x0204: "error_code_3",
    0x0205: "error_code_4",
    0x0206: "error_code_5",
    0x0207: "indoor_unit_count",
    0x0208: "erv_unit_count",
    0x0209: "ehs_unit_count",
    0x0211: "mcu_count",
    0x0401: "main_address",
    0x0402: "rmc_address",
    0x0406: "total_power_consumption",
    0x0407: "cumulative_power_consumption",
    0x0408: "setup_address",
    0x0600: "product_options",
    0x0601: "installation_options",
    0x0605: "device_position_name",
    0x0607: "serial_number",
    0x060C: "eeprom_code_version",
    0x4000: "power_control",
    0x4001: "operation_mode",
    0x4002: "real_operation_mode",
    0x4006: "fan_speed",
    0x4008: "real_fan_speed",
    0x4011: "air_swing_up_down",
    0x4038: "current_humidity",
    0x4065: "dhw_power",
    0x4066: "dhw_operation_mode",
    0x4201: "target_temperature",
    0x4203: "current_temperature",
    0x4235: "dhw_target_temperature",
    0x4237: "dhw_current_temperature",
    0x4238: "water_outlet_temperature",
    0x4248: "water_law_target_temperature",
    0x8001: "outdoor_operation_status",
    0x8003: "outdoor_operation_mode",
    0x8061: "implementation_specific_status",
}

POWER_VALUES = {0: "off", 1: "on", 2: "on_alt"}
MODE_VALUES = {0: "auto", 1: "cool", 2: "dry", 3: "fan", 4: "heat", 21: "cool_storage", 24: "hot_water"}
FAN_VALUES = {0: "off", 1: "low", 2: "mid", 3: "high", 4: "very_high"}
SWING_VALUES = {0: "off", 1: "up", 2: "middle", 3: "down", 4: "swing"}

ENUM_VALUE_MAPS = {
    0x4000: POWER_VALUES,
    0x4001: MODE_VALUES,
    0x4002: MODE_VALUES,
    0x4006: FAN_VALUES,
    0x4008: FAN_VALUES,
    0x4011: SWING_VALUES,
    0x4065: POWER_VALUES,
}

_HEX_RE = re.compile(r"(?:0x)?([0-9a-fA-F]{2})")


@dataclass(frozen=True)
class DecodedFrame:
    protocol: str
    offset: int
    raw: bytes
    fields: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "offset": self.offset,
            "raw_hex": self.raw.hex(" ").upper(),
            "fields": self.fields,
            "warnings": self.warnings,
        }


def parse_capture_line(line: str) -> bytes:
    stripped = line.strip()
    if not stripped:
        return b""

    if stripped.startswith("{"):
        item = json.loads(stripped)
        for key in ("data_hex", "hex", "raw_hex", "rx_hex"):
            value = item.get(key)
            if isinstance(value, str):
                return bytes.fromhex(value)
        return b""

    # If a capture tool prefixes timestamps, keep only the likely byte stream after a delimiter.
    for delimiter in ("|", ":"):
        if delimiter in stripped:
            maybe_hex = stripped.rsplit(delimiter, 1)[-1]
            pairs = _HEX_RE.findall(maybe_hex)
            if pairs:
                return bytes(int(pair, 16) for pair in pairs)

    pairs = _HEX_RE.findall(stripped)
    return bytes(int(pair, 16) for pair in pairs)


def decode_stream(data: bytes, protocol: str = "auto") -> list[DecodedFrame]:
    if protocol == "coolmaster":
        text = data.decode("utf-8", errors="replace")
        return [DecodedFrame("coolmaster_ascii", 0, data, {"text": text})]

    frames: list[DecodedFrame] = []
    i = 0
    while i < len(data):
        if data[i] != STX:
            i += 1
            continue

        decoded: DecodedFrame | None = None

        if protocol in ("auto", "legacy"):
            legacy_raw = _legacy_candidate(data, i)
            if legacy_raw is not None:
                decoded = decode_legacy_frame(legacy_raw, i)

        if decoded is None and protocol in ("auto", "nasa"):
            nasa_raw = _nasa_candidate(data, i)
            if nasa_raw is not None:
                decoded = decode_nasa_frame(nasa_raw, i)

        if decoded is None:
            i += 1
            continue

        frames.append(decoded)
        i += len(decoded.raw)

    if not frames and data:
        if protocol == "nasa":
            frames.append(decode_nasa_frame(data, 0))
        elif protocol == "legacy":
            frames.append(decode_legacy_frame(data, 0))

    return frames


def _legacy_candidate(data: bytes, offset: int) -> bytes | None:
    end = offset + 14
    if end <= len(data) and data[end - 1] == ETX:
        raw = data[offset:end]
        checksum = xor_checksum(raw[1:12])
        if checksum == raw[12]:
            return raw
    return None


def _nasa_candidate(data: bytes, offset: int) -> bytes | None:
    if offset + 3 > len(data):
        return None
    size = data[offset + 1] | (data[offset + 2] << 8)
    for total_length in _nasa_length_candidates(size):
        if total_length < 16:
            continue
        end = offset + total_length
        if end <= len(data) and data[end - 1] == ETX:
            return data[offset:end]
    return None


def xor_checksum(data: Iterable[int]) -> int:
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum & 0xFF


def decode_legacy_frame(raw: bytes, offset: int = 0) -> DecodedFrame:
    warnings: list[str] = []
    if len(raw) != 14:
        warnings.append(f"legacy frame expected 14 bytes, got {len(raw)}")
    if not raw or raw[0] != STX:
        warnings.append("missing STX 0x32")
    if len(raw) < 14 or raw[-1] != ETX:
        warnings.append("missing ETX 0x34")

    payload = raw[5:12] if len(raw) >= 12 else b""
    checksum_valid = len(raw) >= 13 and xor_checksum(raw[1:12]) == raw[12]
    if not checksum_valid:
        warnings.append("legacy XOR checksum mismatch")

    fields = {
        "source": _byte(raw, 1),
        "destination": _byte(raw, 2),
        "packet_type": _byte(raw, 3),
        "command": _byte(raw, 4),
        "payload_hex": payload.hex(" ").upper(),
        "checksum": _byte(raw, 12),
        "checksum_valid": checksum_valid,
    }
    return DecodedFrame("samsung_legacy_14b", offset, raw, fields, warnings)


def decode_nasa_frame(raw: bytes, offset: int = 0) -> DecodedFrame:
    warnings: list[str] = []
    if len(raw) < 16:
        return DecodedFrame("samsung_nasa", offset, raw, {}, ["NASA frame too short"])
    if raw[0] != STX:
        warnings.append("missing STX 0x32")
    if raw[-1] != ETX:
        warnings.append("missing ETX 0x34")

    size = raw[1] | (raw[2] << 8)
    size_model = _matched_nasa_size_model(size, len(raw))
    if size_model is None:
        warnings.append(f"size field does not match known NASA length models; size={size}, len={len(raw)}")

    packet_info = raw[9]
    type_byte = raw[10]
    packet_type = (type_byte & 0xF0) >> 4
    data_type = type_byte & 0x0F
    crc_observed = raw[-3:-1]
    crc_data = raw[:-3]

    fields: dict[str, Any] = {
        "size": size,
        "size_model": size_model,
        "source": _address(raw[3], raw[4], raw[5]),
        "destination": _address(raw[6], raw[7], raw[8]),
        "packet_info": {
            "raw": packet_info,
            "has_control_info": bool((packet_info & 0x80) >> 7),
            "protocol_version": (packet_info & 0x60) >> 5,
            "retry_count": (packet_info & 0x18) >> 3,
        },
        "packet_type": {"raw": packet_type, "name": PACKET_TYPES.get(packet_type, "unknown")},
        "data_type": {"raw": data_type, "name": DATA_TYPES.get(data_type, "unknown")},
        "packet_number": raw[11],
        "capacity": raw[12],
        "messages": [],
        "crc": {
            "observed_hex": crc_observed.hex(" ").upper(),
            "matches": crc16_matches(crc_data, crc_observed),
        },
    }

    messages, message_warnings = _decode_nasa_messages(raw, raw[12])
    fields["messages"] = messages
    warnings.extend(message_warnings)
    return DecodedFrame("samsung_nasa", offset, raw, fields, warnings)


def _nasa_length_candidates(size: int) -> tuple[int, ...]:
    # Public NASA references disagree about whether the size field includes
    # STX, the size bytes, and/or ETX. Try the observed variants deterministically.
    candidates = (size + 2, size + 3, size + 1, size)
    return tuple(dict.fromkeys(candidates))


def _matched_nasa_size_model(size: int, actual_len: int) -> str | None:
    models = {
        "size_plus_2": size + 2,
        "size_plus_3": size + 3,
        "size_plus_1": size + 1,
        "size_exact": size,
    }
    for name, expected_len in models.items():
        if expected_len == actual_len:
            return name
    return None


def _decode_nasa_messages(raw: bytes, capacity: int) -> tuple[list[dict[str, Any]], list[str]]:
    messages: list[dict[str, Any]] = []
    warnings: list[str] = []
    cursor = 13
    body_end = len(raw) - 3

    for index in range(capacity):
        if cursor + 2 > body_end:
            warnings.append(f"message {index} header exceeds packet body")
            break

        msg = int.from_bytes(raw[cursor : cursor + 2], "big")
        msg_le = int.from_bytes(raw[cursor : cursor + 2], "little")
        payload_type_id = (msg & 0x0600) >> 9
        payload_type_name, payload_len = MESSAGE_PAYLOAD_TYPES.get(payload_type_id, ("unknown", 1))
        if payload_len is None:
            payload_end = body_end
        else:
            payload_end = cursor + 2 + payload_len

        if payload_end > body_end:
            warnings.append(f"message 0x{msg:04X} payload exceeds packet body")
            payload_end = body_end

        payload = raw[cursor + 2 : payload_end]
        messages.append(
            {
                "index": index,
                "number": f"0x{msg:04X}",
                "number_le_if_needed": f"0x{msg_le:04X}",
                "name": NASA_MESSAGES.get(msg, "unknown"),
                "payload_type": payload_type_name,
                "payload_hex": payload.hex(" ").upper(),
                "decoded_value": decode_nasa_payload(msg, payload),
            }
        )
        cursor = payload_end

    if cursor < body_end:
        warnings.append(f"{body_end - cursor} trailing body bytes after {len(messages)} messages")

    return messages, warnings


def decode_nasa_payload(message: int, payload: bytes) -> Any:
    if not payload:
        return None

    if message in ENUM_VALUE_MAPS and len(payload) == 1:
        value = payload[0]
        return {"raw": value, "name": ENUM_VALUE_MAPS[message].get(value, "unknown")}

    payload_type_id = (message & 0x0600) >> 9
    if payload_type_id == 0 and len(payload) == 1:
        return payload[0]
    if payload_type_id == 1 and len(payload) == 2:
        value = int.from_bytes(payload, "big")
        if message in {0x4201, 0x4203, 0x4235, 0x4237, 0x4238, 0x4248}:
            return {"raw": value, "celsius": value / 10}
        return value
    if payload_type_id == 2 and len(payload) == 4:
        return int.from_bytes(payload, "big")
    return {"raw_hex": payload.hex(" ").upper()}


def _address(cls: int, channel: int, address: int) -> dict[str, Any]:
    return {
        "raw": f"{cls:02X}{channel:02X}{address:02X}",
        "class": cls,
        "class_name": ADDRESS_CLASSES.get(cls, "unknown"),
        "channel": channel,
        "address": address,
    }


def _byte(raw: bytes, index: int) -> int | None:
    if index >= len(raw):
        return None
    return raw[index]
