from coolrev.modbus import (
    IndoorBlock,
    RegisterValue,
    decode_indoor_block,
    indoor_base_address,
    indoor_buzzer_disable_coil,
    indoor_buzzer_disable_wire_coil,
    indoor_wire_base,
    wire_address,
)
from coolrev.cli import _build_modbus_address_map, _build_write_plan, _encode_temperature_limits
from coolrev.coolmaster import VirtualAddress


def test_indoor_modbus_addresses() -> None:
    assert indoor_base_address(1) == 17
    assert indoor_wire_base(1) == 16
    assert indoor_buzzer_disable_coil(1) == 21
    assert indoor_buzzer_disable_wire_coil(1) == 20
    assert indoor_base_address(8) == 129
    assert indoor_wire_base(8) == 128
    assert indoor_buzzer_disable_coil(8) == 133
    assert indoor_buzzer_disable_wire_coil(8) == 132
    assert wire_address(133, "one") == 132
    assert wire_address(133, "zero") == 133


def test_decode_indoor_block() -> None:
    block = IndoorBlock(
        uid="L7.007",
        va=8,
        document_base=129,
        wire_base=128,
        holding_registers={
            0: RegisterValue(0, 128, 0),
            1: RegisterValue(1, 129, 2),
            2: RegisterValue(2, 130, 215),
            3: RegisterValue(3, 131, 1),
            8: RegisterValue(8, 136, 0x83),
            9: RegisterValue(9, 137, 0x4020),
            13: RegisterValue(13, 141, 0xFFFF),
        },
        input_registers={
            0: RegisterValue(0, 128, 0x7007),
            1: RegisterValue(1, 129, 241),
            2: RegisterValue(2, 130, 0x4F4B),
            3: RegisterValue(3, 131, 0x2020),
        },
        coils={
            0: RegisterValue(0, 128, True),
            4: RegisterValue(4, 132, None, "ModbusError: Modbus exception 2 for function 1"),
        },
        discrete_inputs={
            0: RegisterValue(0, 128, False),
            1: RegisterValue(1, 129, False),
        },
    )

    decoded = decode_indoor_block(block)

    assert decoded["holding_registers"]["operation_mode"]["name"] == "cool"
    assert decoded["holding_registers"]["fan_speed"]["name"] == "high"
    assert decoded["holding_registers"]["set_temperature_c"]["celsius"] == 21.5
    assert decoded["holding_registers"]["temperature_limits"]["low_celsius"] == 16
    assert decoded["holding_registers"]["temperature_limits"]["high_celsius"] == 32
    assert decoded["holding_registers"]["water_temperature_c"]["unavailable"] is True
    assert decoded["holding_registers"]["local_wall_controller_locks"]["inhibit_all"] is True
    assert decoded["input_registers"]["uid"]["uid"] == "L7.007"
    assert decoded["input_registers"]["malfunction_code_string"]["value"] == "OK"
    assert decoded["coils"]["on_off"]["value"] is True
    assert decoded["coils"]["buzzer_disable"]["error"].startswith("ModbusError")


def test_encode_temperature_limits() -> None:
    assert _encode_temperature_limits("16:32") == 0x4020
    assert _encode_temperature_limits("none:30.5") == 0x3D00
    assert _encode_temperature_limits("0x4020") == 0x4020


def test_write_plan_covers_temperature_limits() -> None:
    target = VirtualAddress("L7.007", 8, 0x0081, 129)
    plan = _build_write_plan(target, "cool_temperature_limits", "16:32")

    assert plan["document_address"] == 139
    assert plan["wire_address"] == 138
    assert plan["encoded_value"] == 0x4020


def test_modbus_address_map_includes_hidden_buzzer_coil() -> None:
    target = VirtualAddress("L7.007", 8, 0x0081, 129)
    address_map = _build_modbus_address_map(target)

    assert address_map["coils"]["buzzer_disable"]["document_address"] == 133
    assert address_map["coils"]["buzzer_disable"]["wire_address"] == 132
    assert address_map["holding_registers"]["heat_temperature_limits"]["document_address"] == 140
