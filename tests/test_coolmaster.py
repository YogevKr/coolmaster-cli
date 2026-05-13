from coolmaster_cli.coolmaster import build_status_summary, parse_line_response, parse_ls_line, parse_ls_response, parse_va_response


def test_parse_ls_line() -> None:
    status = parse_ls_line("L2.102 OFF 20C 27C High Cool OK   - 0")

    assert status is not None
    assert status.uid == "L2.102"
    assert status.power == "OFF"
    assert status.set_temp == "20C"
    assert status.room_temp == "27C"
    assert status.fan == "High"
    assert status.mode == "Cool"
    assert status.failure == "OK"
    assert status.filter_sign == "-"
    assert status.demand == "0"


def test_parse_ls_response_ignores_prompt_and_ok() -> None:
    units = parse_ls_response(">ls\nL1.100 ON 19C 30C High Fan OK # 0\nOK\n")

    assert len(units) == 1
    assert units[0].uid == "L1.100"


def test_parse_line_response() -> None:
    lines = parse_line_response(
        """>  L7: SM DVM-S Master U00/G09 myid:6507FF Scan [B|A]
   Tx:30/17797 Rx:213/20930 TO:1/1833 CS:0/76 Col:1/33095 NAK:0/3
OK
>"""
    )

    assert len(lines) == 1
    assert lines[0].line == "L7"
    assert lines[0].description.startswith("SM DVM-S")
    assert lines[0].counters["TO"] == (1, 1833)
    assert lines[0].counters["Col"] == (1, 33095)


def test_build_status_summary_flags_health_items() -> None:
    units = parse_ls_response(
        """>L7.000 OFF 23C 25C Auto Cool OK   - 0
L7.001 ON  22C 24C Low  Cool OK   # 1
OK
>"""
    )
    lines = parse_line_response(
        """>  L7: SM DVM-S Master U00/G09 myid:6507FF Scan [B|A]
   Tx:30/17797 Rx:213/20930 TO:1/1833 CS:0/76 Col:1/33095 NAK:0/3
OK
>"""
    )

    summary = build_status_summary(
        units=units,
        lines=lines,
        ifconfig_response=">IP     : 192.0.2.10 (DHCP)\nRx err : 0\nOK\n>",
        modbus_response=">ModBus IP     : disabled\nserver port   : 502\nOK\n>",
        error_queries={"L7.000": "0", "L7.001": "0"},
    )

    assert summary["unit_count"] == 2
    assert summary["filter_flag_units"] == ["L7.001"]
    assert summary["demand_units"] == ["L7.001"]
    assert summary["network"]["IP"] == "192.0.2.10 (DHCP)"
    assert summary["modbus"]["ModBus IP"] == "disabled"


def test_parse_va_response() -> None:
    addresses = parse_va_response(
        """>INDOORS
L7.000 --> 0001 [Hex: 0x0011 | Dec: 00017]
L7.007 --> 0008 [Hex: 0x0081 | Dec: 00129]
OK
>"""
    )

    assert len(addresses) == 2
    assert addresses[0].uid == "L7.000"
    assert addresses[0].va == 1
    assert addresses[0].base_hex == 0x0011
    assert addresses[0].base_dec == 17
    assert addresses[1].uid == "L7.007"
    assert addresses[1].va == 8
