from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .capture import capture_serial
from .coolmaster import (
    DEFAULT_ASCII_PORT,
    CoolMasterClient,
    UnitStatus,
    VirtualAddress,
    parse_line_response,
    parse_ls_response,
    parse_va_response,
)
from .modbus import (
    FAN_SPEEDS,
    OPERATION_MODES,
    SWING_VALUES,
    ModbusTcpClient,
    indoor_base_address,
    indoor_buzzer_disable_coil,
    indoor_wire_base,
    wire_address,
)
from .protocols import decode_stream, parse_capture_line

COIL_FIELDS = {
    "on_off": 0,
    "filter_sign": 1,
    "inhibit": 3,
    "buzzer_disable": 4,
    "digital_output_1": 9,
    "digital_output_2": 10,
    "digital_output_3": 11,
    "digital_output_4": 12,
    "digital_output_5": 13,
    "digital_output_6": 14,
}

REGISTER_FIELDS = {
    "operation_mode": 0,
    "fan_speed": 1,
    "set_temperature_c": 2,
    "on_off_register": 3,
    "filter_sign_register": 4,
    "swing": 5,
    "local_wall_controller_locks": 8,
    "temperature_limits": 9,
    "cool_temperature_limits": 10,
    "heat_temperature_limits": 11,
}

TEMPERATURE_LIMIT_FIELDS = {"temperature_limits", "cool_temperature_limits", "heat_temperature_limits"}


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="coolmaster-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="run read-only CoolMaster health checks")
    doctor_parser.add_argument("--host", default=os.environ.get("COOLMASTER_HOST"))
    doctor_parser.add_argument("--ascii-port", type=int, default=_env_int("COOLMASTER_ASCII_PORT", DEFAULT_ASCII_PORT))
    doctor_parser.add_argument("--modbus-port", type=int, default=_env_int("COOLMASTER_MODBUS_PORT", 502))
    doctor_parser.add_argument("--unit-id", type=int, default=_env_int("COOLMASTER_MODBUS_UNIT_ID", 1))
    doctor_parser.add_argument("--timeout", type=float, default=_env_float("COOLMASTER_TIMEOUT", 5.0))
    doctor_parser.add_argument("--deep", action="store_true", help="read every mapped indoor Modbus block")
    doctor_parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    doctor_parser.add_argument("--out", type=Path, default=None, help="write the JSON report")
    doctor_parser.set_defaults(func=cmd_doctor)

    capture_parser = subparsers.add_parser("capture", help="capture raw traffic")
    capture_subparsers = capture_parser.add_subparsers(dest="capture_command", required=True)
    serial_parser = capture_subparsers.add_parser("serial", help="capture bytes from a serial adapter")
    serial_parser.add_argument("device")
    serial_parser.add_argument("--baud", type=int, default=9600)
    serial_parser.add_argument("--parity", choices=("none", "even", "odd"), default="none")
    serial_parser.add_argument("--stopbits", type=int, choices=(1, 2), default=1)
    serial_parser.add_argument("--bytesize", type=int, choices=(7, 8), default=8)
    serial_parser.add_argument("--duration", type=float, default=None, help="seconds to capture")
    serial_parser.add_argument("--out", type=Path, required=True)
    serial_parser.set_defaults(func=cmd_capture_serial)

    decode_parser = subparsers.add_parser("decode", help="decode JSONL or hex captures")
    decode_parser.add_argument("input", type=Path)
    decode_parser.add_argument("--protocol", choices=("auto", "nasa", "legacy", "coolmaster"), default="auto")
    decode_parser.add_argument("--out", type=Path, default=None)
    decode_parser.set_defaults(func=cmd_decode)

    summarize_parser = subparsers.add_parser("summarize", help="summarize decoded JSONL frames")
    summarize_parser.add_argument("input", type=Path)
    summarize_parser.set_defaults(func=cmd_summarize)

    cm_parser = subparsers.add_parser("coolmaster", help="CoolMaster ASCII helpers")
    cm_subparsers = cm_parser.add_subparsers(dest="coolmaster_command", required=True)

    probe_parser = cm_subparsers.add_parser("probe", help="run safe read-only CoolMaster commands")
    probe_parser.add_argument("--host", required=True)
    probe_parser.add_argument("--port", type=int, default=DEFAULT_ASCII_PORT)
    probe_parser.add_argument("--out", type=Path, required=True)
    probe_parser.set_defaults(func=cmd_coolmaster_probe)

    send_parser = cm_subparsers.add_parser("send", help="send one CoolMaster ASCII command")
    send_parser.add_argument("--host", required=True)
    send_parser.add_argument("--port", type=int, default=DEFAULT_ASCII_PORT)
    send_parser.add_argument("--cmd", required=True)
    send_parser.set_defaults(func=cmd_coolmaster_send)

    status_parser = cm_subparsers.add_parser("status", help="run read-only CoolMaster system status summary")
    status_parser.add_argument("--host", required=True)
    status_parser.add_argument("--port", type=int, default=DEFAULT_ASCII_PORT)
    status_parser.add_argument("--out", type=Path, default=None)
    status_parser.set_defaults(func=cmd_coolmaster_status)

    capabilities_parser = cm_subparsers.add_parser("capabilities", help="audit read-only CoolMaster surfaces")
    capabilities_parser.add_argument("--host", required=True)
    capabilities_parser.add_argument("--port", type=int, default=DEFAULT_ASCII_PORT)
    capabilities_parser.add_argument("--out", type=Path, default=None)
    capabilities_parser.set_defaults(func=cmd_coolmaster_capabilities)

    diff_capabilities_parser = cm_subparsers.add_parser(
        "diff-capabilities",
        help="compare two coolmaster capabilities JSON snapshots",
    )
    diff_capabilities_parser.add_argument("before", type=Path)
    diff_capabilities_parser.add_argument("after", type=Path)
    diff_capabilities_parser.add_argument("--out", type=Path, default=None)
    diff_capabilities_parser.set_defaults(func=cmd_coolmaster_diff_capabilities)

    inventory_parser = cm_subparsers.add_parser("inventory", help="build unit inventory with VA and optional names")
    inventory_parser.add_argument("--host", required=True)
    inventory_parser.add_argument("--port", type=int, default=DEFAULT_ASCII_PORT)
    inventory_parser.add_argument("--names", type=Path, default=None, help="JSON object mapping UID to friendly name")
    inventory_parser.add_argument("--out", type=Path, default=None)
    inventory_parser.set_defaults(func=cmd_coolmaster_inventory)

    monitor_parser = cm_subparsers.add_parser("monitor", help="sample CoolMaster status and line-counter deltas")
    monitor_parser.add_argument("--host", required=True)
    monitor_parser.add_argument("--port", type=int, default=DEFAULT_ASCII_PORT)
    monitor_parser.add_argument("--interval", type=float, default=10.0)
    monitor_parser.add_argument("--samples", type=int, default=0, help="0 means run until interrupted")
    monitor_parser.add_argument("--out", type=Path, default=None)
    monitor_parser.set_defaults(func=cmd_coolmaster_monitor)

    modbus_parser = subparsers.add_parser("modbus", help="minimal Modbus TCP helpers")
    modbus_subparsers = modbus_parser.add_subparsers(dest="modbus_command", required=True)

    buzzer_parser = modbus_subparsers.add_parser("buzzer", help="read or set Samsung buzzer-disable coil by VA")
    buzzer_parser.add_argument("--host", required=True)
    buzzer_parser.add_argument("--port", type=int, default=502)
    buzzer_parser.add_argument("--unit-id", type=int, default=1)
    buzzer_target = buzzer_parser.add_mutually_exclusive_group(required=True)
    buzzer_target.add_argument("--va", type=int)
    buzzer_target.add_argument("--uid")
    buzzer_target.add_argument("--all", action="store_true")
    buzzer_parser.add_argument("--ascii-host", default=None, help="CoolMaster ASCII host for UID/--all VA lookup")
    buzzer_parser.add_argument("--ascii-port", type=int, default=DEFAULT_ASCII_PORT)
    buzzer_parser.add_argument("--address-base", choices=("one", "zero"), default="one")
    buzzer_parser.add_argument("--set", choices=("on", "off"), default=None)
    buzzer_parser.add_argument("--out", type=Path, default=None)
    buzzer_parser.set_defaults(func=cmd_modbus_buzzer)

    indoor_parser = modbus_subparsers.add_parser("indoor", help="read/decode complete 16-object indoor block")
    _add_modbus_target_args(indoor_parser)
    indoor_parser.add_argument("--out", type=Path, default=None)
    indoor_parser.set_defaults(func=cmd_modbus_indoor)

    map_parser = modbus_subparsers.add_parser("map", help="emit writable Modbus address map by UID/VA")
    _add_modbus_target_args(map_parser)
    map_parser.add_argument("--out", type=Path, default=None)
    map_parser.set_defaults(func=cmd_modbus_map)

    write_parser = modbus_subparsers.add_parser("write", help="guarded named Modbus write; dry-run unless --yes")
    _add_modbus_target_args(write_parser)
    write_parser.add_argument("--field", choices=sorted(set(COIL_FIELDS) | set(REGISTER_FIELDS)), required=True)
    write_parser.add_argument("--value", required=True)
    write_parser.add_argument("--yes", action="store_true", help="perform the write")
    write_parser.set_defaults(func=cmd_modbus_write)

    args = parser.parse_args(argv)
    return args.func(args)


def _add_modbus_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit-id", type=int, default=1)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--va", type=int)
    target.add_argument("--uid")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--ascii-host", default=None, help="CoolMaster ASCII host for UID/--all VA lookup")
    parser.add_argument("--ascii-port", type=int, default=DEFAULT_ASCII_PORT)


def cmd_capture_serial(args: argparse.Namespace) -> int:
    return capture_serial(
        args.device,
        args.out,
        baud=args.baud,
        parity=args.parity,
        stopbits=args.stopbits,
        bytesize=args.bytesize,
        duration=args.duration,
    )


def cmd_decode(args: argparse.Namespace) -> int:
    out_fh = args.out.open("w", encoding="utf-8") if args.out else sys.stdout
    try:
        with args.input.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh, start=1):
                data = parse_capture_line(line)
                if not data:
                    continue
                frames = decode_stream(data, args.protocol)
                if not frames:
                    print(
                        json.dumps(
                            {
                                "line": line_no,
                                "protocol": "unknown",
                                "raw_hex": data.hex(" ").upper(),
                                "warnings": ["no frame decoded"],
                            },
                            sort_keys=True,
                        ),
                        file=out_fh,
                    )
                    continue
                for frame in frames:
                    item = frame.to_json_obj()
                    item["line"] = line_no
                    print(json.dumps(item, sort_keys=True), file=out_fh)
        return 0
    finally:
        if args.out:
            out_fh.close()


def cmd_summarize(args: argparse.Namespace) -> int:
    protocols: Counter[str] = Counter()
    message_names: Counter[str] = Counter()
    message_numbers: Counter[str] = Counter()
    data_types: Counter[str] = Counter()
    warnings: Counter[str] = Counter()

    with args.input.open("r", encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line)
            protocols[item.get("protocol", "unknown")] += 1
            for warning in item.get("warnings", []):
                warnings[warning] += 1
            fields = item.get("fields", {})
            if isinstance(fields, dict):
                data_type = fields.get("data_type", {})
                if isinstance(data_type, dict):
                    data_types[data_type.get("name", "unknown")] += 1
                for message in fields.get("messages", []):
                    if not isinstance(message, dict):
                        continue
                    message_names[message.get("name", "unknown")] += 1
                    message_numbers[message.get("number", "unknown")] += 1

    print("protocols")
    _print_counter(protocols)
    print("\ndata_types")
    _print_counter(data_types)
    print("\nmessage_names")
    _print_counter(message_names)
    print("\nmessage_numbers")
    _print_counter(message_numbers)
    if warnings:
        print("\nwarnings")
        _print_counter(warnings)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    if not args.host:
        print("Missing --host. Set COOLMASTER_HOST in .env or pass --host.", file=sys.stderr)
        return 2

    report = _run_doctor(
        host=args.host,
        ascii_port=args.ascii_port,
        modbus_port=args.modbus_port,
        unit_id=args.unit_id,
        timeout=args.timeout,
        deep=args.deep,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_doctor_report(report)
    return 2 if report["summary"]["status"] == "fail" else 0


def cmd_coolmaster_probe(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    client = CoolMasterClient(args.host, args.port)
    with args.out.open("a", encoding="utf-8") as fh:
        for record in client.probe():
            record["logged_at"] = time.time()
            print(json.dumps(record, sort_keys=True), file=fh)
            fh.flush()
            print(f"{record['command']}: {'ok' if record['ok'] else record['error']}", file=sys.stderr)
    return 0


def cmd_coolmaster_send(args: argparse.Namespace) -> int:
    client = CoolMasterClient(args.host, args.port)
    print(client.command(args.cmd))
    return 0


def cmd_coolmaster_status(args: argparse.Namespace) -> int:
    client = CoolMasterClient(args.host, args.port)
    status = client.status()
    payload = json.dumps(status, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def cmd_coolmaster_capabilities(args: argparse.Namespace) -> int:
    client = CoolMasterClient(args.host, args.port)
    commands = [
        "set",
        "line",
        "modbus",
        "va",
        "props",
        "ls2",
        "rest",
        "bacnet",
        "sddp",
        "ssdp",
        "wpan",
        "gpio",
        "ifconfig",
    ]
    responses = {command: _command_record(client, command) for command in commands}
    units = parse_ls_response(responses["ls2"]["response"])
    lock_responses = {unit.uid: _command_record(client, f"lock {unit.uid}") for unit in units}
    report = {
        "host": args.host,
        "ascii_port": args.port,
        "unit_count": len(units),
        "units": [asdict(unit) for unit in units],
        "commands": responses,
        "locks": lock_responses,
        "features": _summarize_capabilities(responses),
    }
    return _emit_json(report, args.out)


def cmd_coolmaster_diff_capabilities(args: argparse.Namespace) -> int:
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    report = {
        "before": str(args.before),
        "after": str(args.after),
        "unit_count": {
            "before": before.get("unit_count"),
            "after": after.get("unit_count"),
            "changed": before.get("unit_count") != after.get("unit_count"),
        },
        "features": _diff_dict(before.get("features", {}), after.get("features", {})),
        "command_ok": _diff_dict(_command_ok_map(before), _command_ok_map(after)),
    }
    return _emit_json(report, args.out)


def cmd_coolmaster_inventory(args: argparse.Namespace) -> int:
    client = CoolMasterClient(args.host, args.port)
    units = parse_ls_response(client.command("ls2"))
    va_by_uid = {address.uid: address for address in client.virtual_addresses()}
    names = _load_names(args.names)
    inventory = []
    for unit in units:
        address = va_by_uid.get(unit.uid)
        inventory.append(
            {
                "uid": unit.uid,
                "name": names.get(unit.uid, ""),
                "va": address.va if address else None,
                "document_base": address.base_dec if address else None,
                "wire_base": address.base_dec - 1 if address else None,
                "status": asdict(unit),
            }
        )
    return _emit_json({"host": args.host, "units": inventory}, args.out)


def cmd_coolmaster_monitor(args: argparse.Namespace) -> int:
    client = CoolMasterClient(args.host, args.port)
    out_fh = args.out.open("a", encoding="utf-8") if args.out else sys.stdout
    previous: dict[str, dict[str, tuple[int, int]]] | None = None
    sample = 0
    try:
        while args.samples == 0 or sample < args.samples:
            status = client.status()
            current = _line_counter_map(status)
            record = {
                "ts": time.time(),
                "sample": sample,
                "unit_count": status["unit_count"],
                "active_units": status["active_units"],
                "failure_units": status["failure_units"],
                "filter_flag_units": status["filter_flag_units"],
                "demand_units": status["demand_units"],
                "line_deltas": _line_deltas(previous, current),
                "line_counters": current,
            }
            print(json.dumps(record, sort_keys=True), file=out_fh)
            out_fh.flush()
            previous = current
            sample += 1
            if args.samples and sample >= args.samples:
                break
            time.sleep(args.interval)
    finally:
        if args.out:
            out_fh.close()
    return 0


def cmd_modbus_buzzer(args: argparse.Namespace) -> int:
    client = ModbusTcpClient(args.host, args.port, args.unit_id)
    targets = _resolve_buzzer_targets(args)
    results = []
    for target in targets:
        results.append(_handle_buzzer_target(client, target, args))
    return _emit_json(results[0] if len(results) == 1 else results, args.out)


def cmd_modbus_indoor(args: argparse.Namespace) -> int:
    client = ModbusTcpClient(args.host, args.port, args.unit_id)
    targets = _resolve_modbus_targets(args)
    blocks = [client.read_indoor_block(target.uid, target.va).to_json_obj() for target in targets]
    return _emit_json(blocks[0] if len(blocks) == 1 else blocks, args.out)


def cmd_modbus_map(args: argparse.Namespace) -> int:
    targets = _resolve_modbus_targets(args)
    maps = [_build_modbus_address_map(target) for target in targets]
    return _emit_json(maps[0] if len(maps) == 1 else maps, args.out)


def cmd_modbus_write(args: argparse.Namespace) -> int:
    client = ModbusTcpClient(args.host, args.port, args.unit_id)
    targets = _resolve_modbus_targets(args)
    plans = [_build_write_plan(target, args.field, args.value) for target in targets]
    for plan in plans:
        if args.yes:
            if plan["object_type"] == "coil":
                client.write_single_coil(plan["wire_address"], plan["encoded_value"])
            else:
                client.write_single_register(plan["wire_address"], plan["encoded_value"])
            plan["write_accepted"] = True
        else:
            plan["write_accepted"] = False
            plan["dry_run"] = True
    print(json.dumps(plans[0] if len(plans) == 1 else plans, sort_keys=True))
    return 0


def _resolve_buzzer_targets(args: argparse.Namespace) -> list[VirtualAddress]:
    return _resolve_modbus_targets(args)


def _resolve_modbus_targets(args: argparse.Namespace) -> list[VirtualAddress]:
    if args.va is not None:
        return [
            VirtualAddress(
                uid=f"VA{args.va}",
                va=args.va,
                base_hex=indoor_base_address(args.va),
                base_dec=indoor_base_address(args.va),
            )
        ]
    ascii_host = args.ascii_host or args.host
    addresses = CoolMasterClient(ascii_host, args.ascii_port).virtual_addresses()
    if args.all:
        return addresses
    matches = [address for address in addresses if address.uid == args.uid]
    if not matches:
        raise SystemExit(f"UID {args.uid} not found in VA map")
    return matches


def _build_write_plan(target: VirtualAddress, field: str, value: str) -> dict[str, object]:
    if field in COIL_FIELDS:
        document_address = target.base_dec + COIL_FIELDS[field]
        return {
            "uid": target.uid,
            "va": target.va,
            "field": field,
            "object_type": "coil",
            "document_address": document_address,
            "wire_address": document_address - 1,
            "encoded_value": _parse_bool(value),
        }
    document_address = target.base_dec + REGISTER_FIELDS[field]
    return {
        "uid": target.uid,
        "va": target.va,
        "field": field,
        "object_type": "holding_register",
        "document_address": document_address,
        "wire_address": document_address - 1,
        "encoded_value": _encode_register_value(field, value),
    }


def _build_modbus_address_map(target: VirtualAddress) -> dict[str, object]:
    return {
        "uid": target.uid,
        "va": target.va,
        "document_base": target.base_dec,
        "wire_base": target.base_dec - 1,
        "holding_registers": {
            field: _address_entry(target, offset, "holding_register") for field, offset in sorted(REGISTER_FIELDS.items())
        },
        "coils": {field: _address_entry(target, offset, "coil") for field, offset in sorted(COIL_FIELDS.items())},
        "temperature_limit_encoding": "LOW:HIGH Celsius, packed as MSB=high*2 and LSB=low*2; zero disables a side",
    }


def _address_entry(target: VirtualAddress, offset: int, object_type: str) -> dict[str, object]:
    document_address = target.base_dec + offset
    return {
        "object_type": object_type,
        "offset": offset,
        "document_address": document_address,
        "wire_address": document_address - 1,
    }


def _handle_buzzer_target(
    client: ModbusTcpClient,
    target: VirtualAddress,
    args: argparse.Namespace,
) -> dict[str, object]:
    document_address = indoor_buzzer_disable_coil(target.va)
    address = wire_address(document_address, args.address_base)
    write_accepted = False
    if args.set is not None:
        client.write_single_coil(address, args.set == "on")
        write_accepted = True
    try:
        value: bool | None = client.read_coils(address, 1)[0]
        read_error = None
    except Exception as exc:
        value = None
        read_error = f"{type(exc).__name__}: {exc}"
    return {
        "uid": target.uid,
        "va": target.va,
        "document_coil": document_address,
        "wire_coil": address,
        "address_base": args.address_base,
        "buzzer_disable": value,
        "read_error": read_error,
        "write_accepted": write_accepted,
    }


def _command_record(client: CoolMasterClient, command: str) -> dict[str, object]:
    started = time.time()
    try:
        response = client.command(command)
        command_ok = not any(marker in response for marker in ("Unknown Command", "Bad Format", "Bad Function"))
        return {"command": command, "ok": command_ok, "response": response, "duration_s": time.time() - started}
    except Exception as exc:
        return {
            "command": command,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_s": time.time() - started,
        }


def _summarize_capabilities(responses: dict[str, dict[str, object]]) -> dict[str, object]:
    def response(command: str) -> str:
        value = responses.get(command, {}).get("response", "")
        return value if isinstance(value, str) else ""

    return {
        "modbus_ip_enabled": "ModBus IP     : enabled" in response("modbus")
        or "ModBus IP      : enabled" in response("modbus"),
        "rest_enabled": "REST          : enabled" in response("rest"),
        "bacnet_license_active": "License       : not activated" not in response("bacnet"),
        "ssdp_enabled": "SSDP          : enabled" in response("ssdp"),
        "gpio_present": response("gpio").strip().endswith("OK\n>") or "ALL OFF" in response("gpio"),
        "wpan_present": "PAN ID" in response("wpan"),
        "sddp_present": "version" in response("sddp"),
    }


def _command_ok_map(report: object) -> dict[str, object]:
    if not isinstance(report, dict):
        return {}
    commands = report.get("commands", {})
    if not isinstance(commands, dict):
        return {}
    return {
        str(command): record.get("ok")
        for command, record in commands.items()
        if isinstance(record, dict) and "ok" in record
    }


def _diff_dict(before: object, after: object) -> dict[str, dict[str, object]]:
    if not isinstance(before, dict):
        before = {}
    if not isinstance(after, dict):
        after = {}
    diff: dict[str, dict[str, object]] = {}
    for key in sorted(set(before) | set(after)):
        before_value = before.get(key)
        after_value = after.get(key)
        if before_value != after_value:
            diff[str(key)] = {"before": before_value, "after": after_value}
    return diff


def _run_doctor(
    *,
    host: str,
    ascii_port: int,
    modbus_port: int,
    unit_id: int,
    timeout: float,
    deep: bool,
) -> dict[str, object]:
    client = CoolMasterClient(host, ascii_port, timeout=timeout)
    commands = {command: _command_record(client, command) for command in ("set", "ls2", "line", "modbus", "va")}
    checks: list[dict[str, object]] = []

    _add_check(
        checks,
        "pass" if commands["set"].get("ok") else "fail",
        "ascii_server",
        f"ASCII server answered on {host}:{ascii_port}"
        if commands["set"].get("ok")
        else f"ASCII server did not answer on {host}:{ascii_port}",
        _record_error(commands["set"]),
    )

    settings = _parse_key_values(commands["set"].get("response", ""))
    modbus_settings = _parse_key_values(commands["modbus"].get("response", ""))
    units = parse_ls_response(_record_response(commands["ls2"]))
    lines = parse_line_response(_record_response(commands["line"]))
    addresses = parse_va_response(_record_response(commands["va"]))

    unit_failures = [asdict(unit) for unit in units if unit.failure != "OK"]
    filter_flags = [unit.uid for unit in units if unit.filter_sign == "#"]
    if not units:
        _add_check(checks, "fail", "inventory", "No indoor units returned by ls2")
    elif unit_failures:
        _add_check(checks, "fail", "inventory", f"{len(unit_failures)} indoor unit(s) report failure", unit_failures)
    elif filter_flags:
        _add_check(checks, "warn", "inventory", f"{len(units)} indoor unit(s), filter flag on {len(filter_flags)}", filter_flags)
    else:
        _add_check(checks, "pass", "inventory", f"{len(units)} indoor unit(s), no failures")

    line_alerts = _line_health_alerts(lines)
    used_lines = [line for line in lines if not line.description.startswith("Unused")]
    if not used_lines:
        _add_check(checks, "fail", "line_health", "No active HVAC line found")
    elif line_alerts:
        _add_check(checks, "warn", "line_health", f"{len(line_alerts)} line counter alert(s)", line_alerts)
    else:
        _add_check(checks, "pass", "line_health", f"{len(used_lines)} active line(s), no current TO/CS/NAK counters")

    uid_set = {unit.uid for unit in units}
    va_uid_set = {address.uid for address in addresses}
    missing_va = sorted(uid_set - va_uid_set)
    if not addresses:
        _add_check(checks, "fail", "va_map", "No virtual-address map returned")
    elif missing_va:
        _add_check(checks, "warn", "va_map", f"{len(missing_va)} unit(s) missing VA mappings", missing_va)
    else:
        _add_check(checks, "pass", "va_map", f"{len(addresses)} VA mapping(s)")

    modbus_enabled = modbus_settings.get("ModBus IP", "").lower() == "enabled"
    if modbus_enabled:
        _add_check(checks, "pass", "modbus_config", f"Modbus/IP enabled on port {modbus_settings.get('server port', modbus_port)}")
    else:
        _add_check(checks, "warn", "modbus_config", "Modbus/IP is not enabled in CoolMaster settings", modbus_settings)

    modbus_probe = _probe_modbus(host, modbus_port, unit_id, timeout, addresses, enabled=modbus_enabled, deep=deep)
    _add_check(checks, modbus_probe["status"], "modbus_tcp", modbus_probe["message"], modbus_probe["details"])

    units_json = [asdict(unit) for unit in units]
    lines_json = [asdict(line) for line in lines]
    va_json = [asdict(address) for address in addresses]
    report = {
        "host": host,
        "ascii_port": ascii_port,
        "modbus_port": modbus_port,
        "unit_id": unit_id,
        "deep": deep,
        "checked_at": time.time(),
        "device": {
            "serial": settings.get("S/N"),
            "version": settings.get("version"),
            "build_date": settings.get("build date"),
            "application": settings.get("application"),
            "melody": settings.get("melody"),
        },
        "settings": {
            "modbus": modbus_settings,
        },
        "units": units_json,
        "lines": lines_json,
        "virtual_addresses": va_json,
        "checks": checks,
    }
    report["summary"] = _doctor_summary(checks)
    return report


def _probe_modbus(
    host: str,
    port: int,
    unit_id: int,
    timeout: float,
    addresses: list[VirtualAddress],
    *,
    enabled: bool,
    deep: bool,
) -> dict[str, object]:
    if not enabled:
        return {"status": "skip", "message": "Skipped because Modbus/IP is disabled", "details": {}}
    if not addresses:
        return {"status": "skip", "message": "Skipped because VA map is empty", "details": {}}

    client = ModbusTcpClient(host, port, unit_id, timeout=timeout)
    targets = addresses if deep else addresses[:1]
    probes: list[dict[str, object]] = []
    for target in targets:
        try:
            block = client.read_indoor_block(target.uid, target.va)
            input_uid = block.input_registers.get(0)
            holding_mode = block.holding_registers.get(0)
            errors = _block_error_count(block)
            ok = input_uid is not None and input_uid.error is None and holding_mode is not None and holding_mode.error is None
            probes.append(
                {
                    "uid": target.uid,
                    "va": target.va,
                    "document_base": target.base_dec,
                    "ok": ok,
                    "object_errors": errors,
                }
            )
        except Exception as exc:
            probes.append(
                {
                    "uid": target.uid,
                    "va": target.va,
                    "document_base": target.base_dec,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    failed = [probe for probe in probes if not probe["ok"]]
    if failed:
        return {"status": "fail", "message": f"Modbus read failed for {len(failed)} target(s)", "details": probes}
    scope = "all mapped indoor blocks" if deep else f"{targets[0].uid} indoor block"
    return {"status": "pass", "message": f"Read {scope} on {host}:{port}", "details": probes}


def _block_error_count(block: object) -> int:
    count = 0
    for values in (
        getattr(block, "holding_registers", {}),
        getattr(block, "input_registers", {}),
        getattr(block, "coils", {}),
        getattr(block, "discrete_inputs", {}),
    ):
        count += sum(1 for value in values.values() if value.error is not None)
    return count


def _line_health_alerts(lines: list[object]) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []
    for line in lines:
        if getattr(line, "description", "").startswith("Unused"):
            continue
        for counter in ("TO", "CS", "NAK"):
            current, total = line.counters.get(counter, (0, 0))
            if current:
                alerts.append({"line": line.line, "counter": counter, "current": current, "total": total})
    return alerts


def _add_check(
    checks: list[dict[str, object]],
    status: str,
    name: str,
    message: str,
    details: object | None = None,
) -> None:
    item: dict[str, object] = {"name": name, "status": status, "message": message}
    if details not in (None, {}, []):
        item["details"] = details
    checks.append(item)


def _doctor_summary(checks: list[dict[str, object]]) -> dict[str, object]:
    counts = Counter(str(check["status"]) for check in checks)
    if counts["fail"]:
        status = "fail"
    elif counts["warn"]:
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "pass": counts["pass"],
        "warn": counts["warn"],
        "fail": counts["fail"],
        "skip": counts["skip"],
    }


def _print_doctor_report(report: dict[str, object]) -> None:
    summary = report["summary"]
    print(f"CoolMaster doctor: {summary['status'].upper()}")
    print(f"target: {report['host']} ascii={report['ascii_port']} modbus={report['modbus_port']}")
    device = report.get("device", {})
    if isinstance(device, dict) and device.get("version"):
        print(f"device: {device.get('version')} {device.get('application', '')}".rstrip())
    print()
    for check in report["checks"]:
        print(f"{str(check['status']).upper():>4} {check['name']}: {check['message']}")
    print()
    print(
        "summary: "
        f"{summary['pass']} pass, {summary['warn']} warn, {summary['fail']} fail, {summary['skip']} skip"
    )


def _parse_key_values(response: object) -> dict[str, str]:
    if not isinstance(response, str):
        return {}
    values: dict[str, str] = {}
    for raw_line in response.splitlines():
        line = raw_line.strip(">\r\n ")
        if not line or line == "OK" or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _record_response(record: dict[str, object]) -> str:
    response = record.get("response", "")
    return response if isinstance(response, str) else ""


def _record_error(record: dict[str, object]) -> object | None:
    if record.get("ok"):
        return None
    return record.get("error") or _record_response(record)


def _load_names(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("--names must point to a JSON object")
    return {str(key): str(value) for key, value in data.items()}


def _emit_json(payload: object, out: Path | None) -> int:
    text = json.dumps(payload, indent=2, sort_keys=True)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _line_counter_map(status: dict[str, object]) -> dict[str, dict[str, tuple[int, int]]]:
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for line in status.get("used_lines", []):
        if not isinstance(line, dict):
            continue
        counters = line.get("counters", {})
        if not isinstance(counters, dict):
            continue
        out[str(line.get("line", ""))] = {
            str(name): tuple(value) for name, value in counters.items() if isinstance(value, (tuple, list)) and len(value) == 2
        }
    return out


def _line_deltas(
    previous: dict[str, dict[str, tuple[int, int]]] | None,
    current: dict[str, dict[str, tuple[int, int]]],
) -> dict[str, dict[str, int]]:
    if previous is None:
        return {}
    deltas: dict[str, dict[str, int]] = {}
    for line, counters in current.items():
        old = previous.get(line, {})
        deltas[line] = {
            name: total - old.get(name, (0, total))[1]
            for name, (_window, total) in counters.items()
        }
    return deltas


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "on", "true", "yes", "enable", "enabled"}:
        return True
    if normalized in {"0", "off", "false", "no", "disable", "disabled"}:
        return False
    raise SystemExit(f"expected boolean value, got {value!r}")


def _encode_register_value(field: str, value: str) -> int:
    normalized = value.strip().lower()
    if field == "operation_mode":
        return _name_or_int(normalized, OPERATION_MODES)
    if field == "fan_speed":
        return _name_or_int(normalized, FAN_SPEEDS)
    if field == "swing":
        return _name_or_int(normalized, SWING_VALUES)
    if field == "set_temperature_c":
        return round(float(value) * 10)
    if field in TEMPERATURE_LIMIT_FIELDS:
        return _encode_temperature_limits(value)
    if field in {"on_off_register", "filter_sign_register"}:
        return int(_parse_bool(value))
    return int(value, 0)


def _encode_temperature_limits(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.startswith("0x") or re.fullmatch(r"\d+", normalized):
        return int(normalized, 0)
    parts = [part.strip() for part in re.split(r"[:,/]", normalized)]
    if len(parts) != 2:
        raise SystemExit("temperature limits must be raw integer/hex or LOW:HIGH in Celsius")
    low_raw = _encode_limit_part(parts[0])
    high_raw = _encode_limit_part(parts[1])
    return (high_raw << 8) | low_raw


def _encode_limit_part(value: str) -> int:
    if value in {"", "0", "none", "off", "disabled"}:
        return 0
    encoded = round(float(value) * 2)
    if encoded < 0 or encoded > 0xFF:
        raise SystemExit(f"temperature limit {value!r} is outside encodable range")
    return encoded


def _name_or_int(value: str, mapping: dict[int, str]) -> int:
    reverse = {name: raw for raw, name in mapping.items()}
    if value in reverse:
        return reverse[value]
    return int(value, 0)


def _print_counter(counter: Counter[str], limit: int = 25) -> None:
    if not counter:
        print("  none")
        return
    for key, count in counter.most_common(limit):
        print(f"  {count:5d} {key}")


if __name__ == "__main__":
    raise SystemExit(main())
