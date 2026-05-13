# coolmaster-reverse

Open-source toolkit for inspecting and reverse-engineering CoolMaster gateways connected to Samsung VRF systems.

The useful split:

1. CoolMaster ASCII/API side: documented, safe to enumerate first, and good for generating known actions.
2. Samsung bus side: raw HVAC traffic, likely NASA on newer Samsung VRF lines. Capture passively first, then correlate frames with the ASCII actions.

## Safety Boundaries

- Start read-only: `ls`, `ls+`, `stat`, `line`, `props`, service-data reads.
- Do not write unknown NASA messages into a live VRF bus until a frame is observed and replay-scoped.
- Keep CoolMaster connected normally while sniffing. Use a high-impedance listener or isolated RS485 adapter.
- Timestamp every operator action. Correlation is the difference between "bytes" and a protocol map.

## Setup

```bash
uv sync --extra dev
cp .env.example .env
```

Run without install:

```bash
uv run coolrev --help
```

For shell examples, load your local target first:

```bash
source .env
```

## First Capture Session

1. Find the CoolMaster ASCII endpoint:

```bash
export COOLMASTER_HOST=192.0.2.10
uv run coolrev coolmaster probe --host "$COOLMASTER_HOST" --port 10102 --out captures/coolmaster-probe.jsonl
```

If port `10102` does not answer, try the configured ASCII/TCP port from the CoolMaster UI or use the serial terminal.

2. Passively capture the Samsung-side bus:

```bash
uv run coolrev capture serial /dev/tty.usbserial-XXXX --baud 9600 --parity none --stopbits 1 --out captures/samsung-bus.jsonl
```

Samsung VRF adapters often use different baud/parity settings by generation and line type. If the decode looks random, repeat with the actual line settings from the gateway/adapter docs.

3. Decode:

```bash
uv run coolrev decode captures/samsung-bus.jsonl --protocol auto --out captures/samsung-bus.decoded.jsonl
```

4. Correlate by time:

```bash
uv run coolrev summarize captures/samsung-bus.decoded.jsonl
```

## Common Commands

CoolMaster ASCII endpoint:

```bash
printf 'ls\r\n' | nc "$COOLMASTER_HOST" 10102
```

Modbus/IP is enabled on the upgraded CoolMasterNet firmware. Indoor-unit Modbus blocks use:

```text
document base address = VA * 16 + 1
document buzzer disable coil = base + 4
wire coil address = document coil - 1
```

Mute Samsung indoor-unit buzzer for one unit:

```bash
uv run coolrev modbus buzzer --host "$COOLMASTER_HOST" --uid L7.007 --set on
```

Mute every unit in the current VA map:

```bash
uv run coolrev modbus buzzer --host "$COOLMASTER_HOST" --all --set on
uv run coolrev modbus buzzer --host "$COOLMASTER_HOST" --all --set on --out captures/buzzer-disable-current.json
```

Undo for one unit:

```bash
uv run coolrev modbus buzzer --host "$COOLMASTER_HOST" --uid L7.007 --set off
```

The buzzer coil accepts writes but returns Modbus illegal-address on read, so validation is acoustic: turn a unit on/off and listen.

Audit all currently exposed CoolMaster surfaces:

```bash
uv run coolrev coolmaster capabilities --host "$COOLMASTER_HOST" --out captures/capabilities.json
```

Create a UID/VA/status inventory:

```bash
uv run coolrev coolmaster inventory --host "$COOLMASTER_HOST" --out captures/inventory.json
uv run coolrev coolmaster inventory --host "$COOLMASTER_HOST" --names docs/office-names.example.json --out captures/inventory-named.json
```

Read and decode one complete indoor Modbus block:

```bash
uv run coolrev modbus indoor --host "$COOLMASTER_HOST" --uid L7.007
```

Read every mapped indoor Modbus block:

```bash
uv run coolrev modbus indoor --host "$COOLMASTER_HOST" --all --out captures/modbus-indoor.json
```

Export the complete writable address map:

```bash
uv run coolrev modbus map --host "$COOLMASTER_HOST" --all --out captures/modbus-map.json
```

Dry-run a named Modbus write:

```bash
uv run coolrev modbus write --host "$COOLMASTER_HOST" --uid L7.007 --field set_temperature_c --value 22
```

Execute a named Modbus write:

```bash
uv run coolrev modbus write --host "$COOLMASTER_HOST" --uid L7.007 --field set_temperature_c --value 22 --yes
```

Set a cool-mode temperature limit:

```bash
uv run coolrev modbus write --host "$COOLMASTER_HOST" --uid L7.007 --field cool_temperature_limits --value 16:32
```

Monitor line health and unit status:

```bash
uv run coolrev coolmaster monitor --host "$COOLMASTER_HOST" --interval 10 --samples 6
```

## Reverse Plan

1. Baseline idle traffic for 10-15 minutes.
2. Run CoolMaster read commands and mark the exact timestamps.
3. Change one known field at a time from CoolMaster: power, mode, setpoint, fan, swing.
4. Decode frame deltas and build the message map.
5. Query service-data or candidate read-only NASA messages for capabilities not exposed by CoolMaster.
6. Only after confirmed read behavior, test writes on one sacrificial indoor unit with guardrails.

## Useful Files

- `docs/protocol-map.md`: current known command/message map.
- `docs/experiment-log.md`: template for capture sessions.
- `docs/coverage.md`: confirmed exposed and hidden surfaces.
- `docs/safe-write-matrix.md`: all guarded write fields and address math.
- `docs/persistence-checklist.md`: reboot/power persistence validation.
- `docs/samsung-bus-capture.md`: passive Samsung bus capture workflow.
- `src/coolrev/protocols.py`: Samsung frame decoders.
- `src/coolrev/coolmaster.py`: CoolMaster ASCII client and parsers.

## License

MIT. See `LICENSE`.
