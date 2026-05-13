from __future__ import annotations

import itertools
import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Callable

INDOOR_BLOCK_SIZE = 16
INDOOR_BASE_OFFSET = 1
BUZZER_DISABLE_OFFSET = 4

OPERATION_MODES = {
    0: "cool",
    1: "heat",
    2: "auto",
    3: "dry",
    4: "haux",
    5: "fan",
    6: "hh",
    8: "vam_auto",
    9: "vam_bypass",
    10: "vam_heat_exchange",
    11: "vam_normal",
}

FAN_SPEEDS = {
    0: "low",
    1: "med",
    2: "high",
    3: "auto",
    4: "top",
    5: "very_low",
    7: "vam_super_high",
    8: "vam_low_freshup",
    9: "vam_high_freshup",
}

SWING_VALUES = {
    0: "vertical",
    1: "30_deg",
    2: "45_deg",
    3: "60_deg",
    4: "horizontal",
    5: "auto",
    6: "off",
}


class ModbusError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisterValue:
    offset: int
    address: int
    value: int | bool | None = None
    error: str | None = None

    def to_json_obj(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "wire_address": self.address,
            "value": self.value,
            "error": self.error,
        }


@dataclass(frozen=True)
class IndoorBlock:
    uid: str
    va: int
    document_base: int
    wire_base: int
    holding_registers: dict[int, RegisterValue] = field(default_factory=dict)
    input_registers: dict[int, RegisterValue] = field(default_factory=dict)
    coils: dict[int, RegisterValue] = field(default_factory=dict)
    discrete_inputs: dict[int, RegisterValue] = field(default_factory=dict)

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "va": self.va,
            "document_base": self.document_base,
            "wire_base": self.wire_base,
            "decoded": decode_indoor_block(self),
            "raw": {
                "holding_registers": _values_to_json(self.holding_registers),
                "input_registers": _values_to_json(self.input_registers),
                "coils": _values_to_json(self.coils),
                "discrete_inputs": _values_to_json(self.discrete_inputs),
            },
        }


@dataclass(frozen=True)
class ModbusTcpClient:
    host: str
    port: int = 502
    unit_id: int = 1
    timeout: float = 5.0

    def read_coils(self, address: int, quantity: int = 1) -> list[bool]:
        response = self._request(0x01, struct.pack(">HH", address, quantity))
        return _decode_bits(response, quantity, "read-coils")

    def read_discrete_inputs(self, address: int, quantity: int = 1) -> list[bool]:
        response = self._request(0x02, struct.pack(">HH", address, quantity))
        return _decode_bits(response, quantity, "read-discrete-inputs")

    def read_holding_registers(self, address: int, quantity: int = 1) -> list[int]:
        response = self._request(0x03, struct.pack(">HH", address, quantity))
        return _decode_registers(response, quantity, "read-holding-registers")

    def read_input_registers(self, address: int, quantity: int = 1) -> list[int]:
        response = self._request(0x04, struct.pack(">HH", address, quantity))
        return _decode_registers(response, quantity, "read-input-registers")

    def write_single_coil(self, address: int, value: bool) -> None:
        payload = struct.pack(">HH", address, write_single_coil_payload(value))
        response = self._request(0x05, payload)
        if response != payload:
            raise ModbusError(f"unexpected write response: {response.hex(' ')}")

    def write_single_register(self, address: int, value: int) -> None:
        payload = struct.pack(">HH", address, value)
        response = self._request(0x06, payload)
        if response != payload:
            raise ModbusError(f"unexpected write response: {response.hex(' ')}")

    def write_coils(self, address: int, values: list[bool]) -> None:
        if not values:
            raise ValueError("values must not be empty")
        bytes_out = bytearray((len(values) + 7) // 8)
        for index, value in enumerate(values):
            if value:
                bytes_out[index // 8] |= 1 << (index % 8)
        payload = struct.pack(">HHB", address, len(values), len(bytes_out)) + bytes(bytes_out)
        response = self._request(0x0F, payload)
        expected = struct.pack(">HH", address, len(values))
        if response != expected:
            raise ModbusError(f"unexpected write response: {response.hex(' ')}")

    def read_indoor_block(self, uid: str, va: int) -> IndoorBlock:
        wire_base = indoor_wire_base(va)
        return IndoorBlock(
            uid=uid,
            va=va,
            document_base=indoor_base_address(va),
            wire_base=wire_base,
            holding_registers=_read_object_offsets(self.read_holding_registers, wire_base),
            input_registers=_read_object_offsets(self.read_input_registers, wire_base),
            coils=_read_object_offsets(self.read_coils, wire_base),
            discrete_inputs=_read_object_offsets(self.read_discrete_inputs, wire_base),
        )

    def _request(self, function: int, payload: bytes) -> bytes:
        transaction_id = next(_TRANSACTION_IDS)
        pdu = bytes([function]) + payload
        mbap = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, self.unit_id)
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(mbap + pdu)
            header = _recv_exact(sock, 7)
            rx_transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
            if rx_transaction_id != transaction_id:
                raise ModbusError("transaction id mismatch")
            if protocol_id != 0:
                raise ModbusError(f"unexpected protocol id {protocol_id}")
            if unit_id != self.unit_id:
                raise ModbusError(f"unexpected unit id {unit_id}")
            body = _recv_exact(sock, length - 1)

        if not body:
            raise ModbusError("empty Modbus response")
        rx_function = body[0]
        if rx_function & 0x80:
            code = body[1] if len(body) > 1 else -1
            raise ModbusError(f"Modbus exception {code} for function {function}")
        if rx_function != function:
            raise ModbusError(f"unexpected function {rx_function}")
        return body[1:]


def indoor_base_address(va: int) -> int:
    return va * INDOOR_BLOCK_SIZE + INDOOR_BASE_OFFSET


def indoor_wire_base(va: int) -> int:
    return indoor_base_address(va) - 1


def indoor_buzzer_disable_coil(va: int) -> int:
    return indoor_base_address(va) + BUZZER_DISABLE_OFFSET


def indoor_buzzer_disable_wire_coil(va: int) -> int:
    return indoor_wire_base(va) + BUZZER_DISABLE_OFFSET


def wire_address(document_address: int, address_base: str) -> int:
    if address_base == "one":
        return document_address - 1
    if address_base == "zero":
        return document_address
    raise ValueError("address_base must be one or zero")


def write_single_coil_payload(value: bool) -> int:
    return 0xFF00 if value else 0x0000


def decode_indoor_block(block: IndoorBlock) -> dict[str, Any]:
    holding = block.holding_registers
    inputs = block.input_registers
    coils = block.coils
    discrete = block.discrete_inputs
    return {
        "holding_registers": {
            "operation_mode": _enum_field(holding, 0, OPERATION_MODES),
            "fan_speed": _enum_field(holding, 1, FAN_SPEEDS),
            "set_temperature_c": _temperature_field(holding, 2),
            "on_off": _bool_int_field(holding, 3),
            "filter_sign": _bool_int_field(holding, 4),
            "swing": _enum_field(holding, 5, SWING_VALUES),
            "room_temperature_c": _temperature_field(holding, 6),
            "malfunction_code": _register_field(holding, 7),
            "local_wall_controller_locks": _lock_field(holding, 8),
            "temperature_limits": _temperature_limits_field(holding, 9),
            "cool_temperature_limits": _temperature_limits_field(holding, 10),
            "heat_temperature_limits": _temperature_limits_field(holding, 11),
            "water_temperature_c": _temperature_field(holding, 13),
        },
        "input_registers": {
            "uid": _uid_field(inputs, 0),
            "room_temperature_c": _temperature_field(inputs, 1),
            "malfunction_code_string": _malfunction_string_field(inputs, 2, 3),
            "set_temperature_c": _temperature_field(inputs, 4),
            "analog_input_1": _register_field(inputs, 13),
            "analog_input_2": _register_field(inputs, 14),
        },
        "coils": {
            "on_off": _bool_field(coils, 0),
            "filter_sign": _bool_field(coils, 1),
            "external_terminals_closed": _bool_field(coils, 2),
            "inhibit": _bool_field(coils, 3),
            "buzzer_disable": _bool_field(coils, 4),
            "digital_outputs": {str(offset - 8): _bool_field(coils, offset) for offset in range(9, 15)},
        },
        "discrete_inputs": {
            "therm_on_demand": _bool_field(discrete, 0),
            "indoor_communication_failure": _bool_field(discrete, 1),
            "digital_inputs": {str(offset - 8): _bool_field(discrete, offset) for offset in range(9, 15)},
        },
    }


def _read_object_offsets(
    reader: Callable[[int, int], list[int] | list[bool]],
    wire_base: int,
) -> dict[int, RegisterValue]:
    values: dict[int, RegisterValue] = {}
    for offset in range(INDOOR_BLOCK_SIZE):
        address = wire_base + offset
        try:
            value = reader(address, 1)[0]
            values[offset] = RegisterValue(offset=offset, address=address, value=value)
        except Exception as exc:
            values[offset] = RegisterValue(offset=offset, address=address, error=f"{type(exc).__name__}: {exc}")
    return values


def _decode_bits(response: bytes, quantity: int, operation: str) -> list[bool]:
    if len(response) < 1:
        raise ModbusError(f"short {operation} response")
    byte_count = response[0]
    data = response[1 : 1 + byte_count]
    if len(data) != byte_count:
        raise ModbusError(f"short {operation} data")
    bits: list[bool] = []
    for byte in data:
        for bit in range(8):
            bits.append(bool(byte & (1 << bit)))
    return bits[:quantity]


def _decode_registers(response: bytes, quantity: int, operation: str) -> list[int]:
    if len(response) < 1:
        raise ModbusError(f"short {operation} response")
    byte_count = response[0]
    data = response[1 : 1 + byte_count]
    if len(data) != byte_count or byte_count != quantity * 2:
        raise ModbusError(f"short {operation} data")
    return list(struct.unpack(f">{quantity}H", data))


def _values_to_json(values: dict[int, RegisterValue]) -> dict[str, dict[str, object]]:
    return {str(offset): value.to_json_obj() for offset, value in values.items()}


def _value(values: dict[int, RegisterValue], offset: int) -> int | bool | None:
    item = values.get(offset)
    if item is None or item.error is not None:
        return None
    return item.value


def _error(values: dict[int, RegisterValue], offset: int) -> str | None:
    item = values.get(offset)
    return None if item is None else item.error


def _register_field(values: dict[int, RegisterValue], offset: int) -> dict[str, object]:
    return {"value": _value(values, offset), "error": _error(values, offset)}


def _enum_field(values: dict[int, RegisterValue], offset: int, names: dict[int, str]) -> dict[str, object]:
    raw = _value(values, offset)
    return {
        "raw": raw,
        "name": names.get(raw, "unknown") if isinstance(raw, int) else None,
        "error": _error(values, offset),
    }


def _bool_int_field(values: dict[int, RegisterValue], offset: int) -> dict[str, object]:
    raw = _value(values, offset)
    return {"raw": raw, "value": bool(raw) if isinstance(raw, int) else None, "error": _error(values, offset)}


def _bool_field(values: dict[int, RegisterValue], offset: int) -> dict[str, object]:
    raw = _value(values, offset)
    return {"value": raw if isinstance(raw, bool) else None, "error": _error(values, offset)}


def _temperature_field(values: dict[int, RegisterValue], offset: int) -> dict[str, object]:
    raw = _value(values, offset)
    if raw == 0xFFFF:
        return {
            "raw": raw,
            "celsius": None,
            "unavailable": True,
            "error": _error(values, offset),
        }
    return {
        "raw": raw,
        "celsius": raw / 10 if isinstance(raw, int) else None,
        "unavailable": False,
        "error": _error(values, offset),
    }


def _temperature_limits_field(values: dict[int, RegisterValue], offset: int) -> dict[str, object]:
    raw = _value(values, offset)
    if not isinstance(raw, int):
        return {
            "raw": raw,
            "low_celsius": None,
            "high_celsius": None,
            "low_enabled": False,
            "high_enabled": False,
            "error": _error(values, offset),
        }
    high_raw = (raw >> 8) & 0xFF
    low_raw = raw & 0xFF
    return {
        "raw": raw,
        "low_celsius": low_raw / 2 if low_raw else None,
        "high_celsius": high_raw / 2 if high_raw else None,
        "low_enabled": low_raw != 0,
        "high_enabled": high_raw != 0,
        "error": _error(values, offset),
    }


def _uid_field(values: dict[int, RegisterValue], offset: int) -> dict[str, object]:
    raw = _value(values, offset)
    if not isinstance(raw, int):
        return {"raw": raw, "uid": None, "error": _error(values, offset)}
    line = (raw >> 12) & 0xF
    x = (raw >> 8) & 0xF
    yy = raw & 0xFF
    return {
        "raw": raw,
        "line": line,
        "x": x,
        "yy": yy,
        "uid": f"L{line}.{x}{yy:02d}",
        "error": _error(values, offset),
    }


def _malfunction_string_field(values: dict[int, RegisterValue], first_offset: int, second_offset: int) -> dict[str, object]:
    first = _value(values, first_offset)
    second = _value(values, second_offset)
    error = _error(values, first_offset) or _error(values, second_offset)
    if not isinstance(first, int) or not isinstance(second, int):
        return {"value": None, "error": error}
    chars = bytes([first >> 8, first & 0xFF, second >> 8, second & 0xFF]).decode("ascii", errors="replace")
    return {"value": chars.strip(), "error": error}


def _lock_field(values: dict[int, RegisterValue], offset: int) -> dict[str, object]:
    raw = _value(values, offset)
    if not isinstance(raw, int):
        return {"raw": raw, "error": _error(values, offset)}
    return {
        "raw": raw,
        "inhibit_on_off": bool(raw & 0x01),
        "inhibit_mode": bool(raw & 0x02),
        "inhibit_set_temperature": bool(raw & 0x04),
        "inhibit_all": bool(raw & 0x80),
        "error": _error(values, offset),
    }


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ModbusError("connection closed before full response")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


_TRANSACTION_IDS = itertools.cycle(range(1, 0x10000))
