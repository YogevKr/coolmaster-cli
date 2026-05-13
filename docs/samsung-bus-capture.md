# Samsung Bus Capture

Goal: passively capture CoolMaster-to-Samsung traffic and correlate it with known ASCII/Modbus actions.

## Current Machine

No USB serial/RS485 adapter is currently visible beyond macOS built-ins:

```text
/dev/cu.Bluetooth-Incoming-Port
/dev/cu.debug-console
/dev/cu.wlan-debug
```

So the repo is ready for capture, but the bus capture cannot start until an isolated adapter is connected.

## Hardware Setup

- Use an isolated USB-RS485 adapter.
- Connect as a passive listener first.
- Do not disconnect CoolMaster from the Samsung line during normal operation.
- Start with the line settings from CoolMaster `line` output; if unreadable, sweep baud/parity read-only.

## Capture

```bash
uv run coolrev capture serial /dev/tty.usbserial-XXXX --baud 9600 --parity none --stopbits 1 --out captures/samsung-bus.jsonl
```

Try these if frames do not decode:

```bash
uv run coolrev capture serial /dev/tty.usbserial-XXXX --baud 9600 --parity even --stopbits 1 --out captures/samsung-bus-9600-8e1.jsonl
uv run coolrev capture serial /dev/tty.usbserial-XXXX --baud 19200 --parity none --stopbits 1 --out captures/samsung-bus-19200-8n1.jsonl
```

## Correlation Sequence

For each action, note the exact wall-clock time and run only one change at a time.

| Action | CoolMaster command | Modbus equivalent |
| --- | --- | --- |
| Status only | `ls2`, `line`, `va` | `modbus indoor --all` |
| Power | `on UID`, `off UID` | `modbus write --field on_off --value on/off --yes` |
| Mode | `cool UID`, `heat UID`, `auto UID`, `dry UID`, `fan UID` | `modbus write --field operation_mode --value MODE --yes` |
| Fan | `fspeed UID l/m/h/a` | `modbus write --field fan_speed --value low/med/high/auto --yes` |
| Setpoint | `temp UID 23.5` | `modbus write --field set_temperature_c --value 23.5 --yes` |
| Swing | `swing UID ...` | `modbus write --field swing --value ... --yes` |
| Buzzer | not exposed in ASCII app | `modbus buzzer --uid UID --set on` |
| Locks | `lock UID +/-o/m/t/n` | `modbus write --field local_wall_controller_locks --value RAW --yes` |
| Inhibit | `inhibit UID 0/1` | `modbus write --field inhibit --value on/off --yes` |

## Decode

```bash
uv run coolrev decode captures/samsung-bus.jsonl --protocol auto --out captures/samsung-bus.decoded.jsonl
uv run coolrev summarize captures/samsung-bus.decoded.jsonl
```

The decoder already recognizes Samsung NASA packet framing, common NASA message numbers, legacy 14-byte frames, checksums, source/destination classes, and key enum values for power/mode/fan/swing.
