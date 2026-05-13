from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from typing import Iterable

DEFAULT_ASCII_PORT = 10102
DEFAULT_TIMEOUT_SECONDS = 5.0

READ_ONLY_COMMANDS = (
    "ls",
    "ls+",
    "line",
    "props",
    "va",
    "version",
    "help",
)

UID_RE = re.compile(r"^L\d\.\d{3}$")
LINE_HEADER_RE = re.compile(r"^\s*(L\d):\s+(.+?)\s*$")
LINE_COUNTER_RE = re.compile(r"(\w+):(\d+)/(\d+)")
VA_RE = re.compile(r"^(L\d\.\d{3})\s+-->\s+(\d+)\s+\[Hex:\s+0x([0-9A-Fa-f]+)\s+\|\s+Dec:\s+(\d+)\]")


@dataclass(frozen=True)
class UnitStatus:
    uid: str
    power: str
    set_temp: str
    room_temp: str
    fan: str
    mode: str
    failure: str
    filter_sign: str
    demand: str


@dataclass(frozen=True)
class LineStatus:
    line: str
    description: str
    counters: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class VirtualAddress:
    uid: str
    va: int
    base_hex: int
    base_dec: int


class CoolMasterClient:
    def __init__(self, host: str, port: int = DEFAULT_ASCII_PORT, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.host = host
        self.port = port
        self.timeout = timeout

    def command(self, command: str) -> str:
        payload = command.strip().encode("ascii") + b"\r\n"
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(payload)
            return _read_response(sock, self.timeout)

    def probe(self, commands: Iterable[str] = READ_ONLY_COMMANDS, pause: float = 0.2) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for command in commands:
            start = time.time()
            try:
                response = self.command(command)
                ok = True
                error = None
            except OSError as exc:
                response = ""
                ok = False
                error = str(exc)
            records.append(
                {
                    "ts": start,
                    "command": command,
                    "ok": ok,
                    "error": error,
                    "response": response,
                    "parsed_units": [status.__dict__ for status in parse_ls_response(response)]
                    if command.startswith("ls")
                    else [],
                }
            )
            time.sleep(pause)
        return records

    def status(self) -> dict[str, object]:
        ls_response = self.command("ls")
        line_response = self.command("line")
        ifconfig_response = self.command("ifconfig")
        modbus_response = self.command("modbus")

        units = parse_ls_response(ls_response)
        error_queries = {unit.uid: _strip_payload(self.command(f"query {unit.uid} e")) for unit in units}
        return build_status_summary(
            units=units,
            lines=parse_line_response(line_response),
            ifconfig_response=ifconfig_response,
            modbus_response=modbus_response,
            error_queries=error_queries,
            raw={
                "ls": ls_response,
                "line": line_response,
                "ifconfig": ifconfig_response,
                "modbus": modbus_response,
            },
        )

    def virtual_addresses(self) -> list[VirtualAddress]:
        return parse_va_response(self.command("va"))


def _read_response(sock: socket.socket, timeout: float) -> str:
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
        joined = b"".join(chunks)
        if b"\nOK" in joined or joined.rstrip().endswith((b"OK", b"ERROR", b"ERR")):
            break
    return b"".join(chunks).decode("utf-8", errors="replace")


def parse_ls_response(response: str) -> list[UnitStatus]:
    units: list[UnitStatus] = []
    for raw_line in response.splitlines():
        line = raw_line.strip().removeprefix(">").strip()
        if not line or line in {"OK", ">"}:
            continue
        parsed = parse_ls_line(line)
        if parsed is not None:
            units.append(parsed)
    return units


def parse_line_response(response: str) -> list[LineStatus]:
    lines: list[LineStatus] = []
    current: tuple[str, str] | None = None
    for raw_line in response.splitlines():
        line = raw_line.strip(">\r\n")
        if not line or line == "OK":
            continue
        header = LINE_HEADER_RE.match(line)
        if header:
            current = (header.group(1), header.group(2).strip())
            continue
        if current and line.lstrip().startswith("Tx:"):
            counters = {name: (int(window), int(total)) for name, window, total in LINE_COUNTER_RE.findall(line)}
            lines.append(LineStatus(current[0], current[1], counters))
            current = None
    return lines


def parse_va_response(response: str) -> list[VirtualAddress]:
    addresses: list[VirtualAddress] = []
    current_uid: str | None = None
    for raw_line in response.splitlines():
        line = raw_line.strip().removeprefix(">").strip()
        if not line or line in {"OK", "INDOORS"}:
            continue
        if line.startswith("+->") and current_uid is not None:
            line = f"{current_uid} --> {line.removeprefix('+->').strip()}"
        match = VA_RE.match(line)
        if match is None:
            continue
        current_uid = match.group(1)
        addresses.append(
            VirtualAddress(
                uid=match.group(1),
                va=int(match.group(2)),
                base_hex=int(match.group(3), 16),
                base_dec=int(match.group(4)),
            )
        )
    return addresses


def parse_ls_line(line: str) -> UnitStatus | None:
    parts = line.split()
    if len(parts) < 8 or not UID_RE.match(parts[0]):
        return None

    return UnitStatus(
        uid=parts[0],
        power=parts[1],
        set_temp=parts[2],
        room_temp=parts[3],
        fan=parts[4],
        mode=parts[5],
        failure=parts[6],
        filter_sign=parts[7],
        demand=parts[8] if len(parts) > 8 else "",
    )


def build_status_summary(
    *,
    units: list[UnitStatus],
    lines: list[LineStatus],
    ifconfig_response: str,
    modbus_response: str,
    error_queries: dict[str, str],
    raw: dict[str, str] | None = None,
) -> dict[str, object]:
    failures = [unit.__dict__ for unit in units if unit.failure != "OK"]
    filters = [unit.uid for unit in units if unit.filter_sign == "#"]
    active = [unit.__dict__ for unit in units if unit.power == "ON"]
    demand = [unit.uid for unit in units if unit.demand == "1"]
    line_items = [line.__dict__ for line in lines]
    used_lines = [line for line in lines if not line.description.startswith("Unused")]

    return {
        "unit_count": len(units),
        "active_units": active,
        "failure_units": failures,
        "filter_flag_units": filters,
        "demand_units": demand,
        "error_queries": error_queries,
        "used_lines": line_items if not used_lines else [line.__dict__ for line in used_lines],
        "network": _parse_key_value_response(ifconfig_response),
        "modbus": _parse_key_value_response(modbus_response),
        "raw": raw or {},
    }


def _parse_key_value_response(response: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_line in response.splitlines():
        line = raw_line.strip(">\r\n ")
        if not line or line == "OK" or ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _strip_payload(response: str) -> str:
    values: list[str] = []
    for raw_line in response.splitlines():
        line = raw_line.strip()
        line = line.removeprefix(">").strip()
        if line and line != "OK":
            values.append(line)
    return " ".join(values)
