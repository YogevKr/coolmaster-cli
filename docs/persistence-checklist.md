# Persistence Checklist

## Current State

Confirmed on one Samsung/CoolMasterNet installation:

- CoolMaster gateway melody is `Silence`.
- Samsung indoor-unit buzzer-disable writes were accepted for `L7.000` through `L7.008`.
- The acoustic test after writing the Modbus buzzer-disable coil confirmed the indoor unit no longer beeped.
- Buzzer-disable readback returns Modbus exception `2` on this firmware, so readback is not a valid persistence signal.

Not yet provable from software alone:

- Whether Samsung indoor units persist the buzzer-disable bit across indoor-unit power loss.
- Whether CoolMaster replays that bit after gateway reboot.

## Non-Disruptive Check

Run after normal use, without rebooting anything:

```bash
uv run coolrev coolmaster status --host "$COOLMASTER_HOST" --out captures/status-after-buzzer.json
uv run coolrev modbus indoor --host "$COOLMASTER_HOST" --all --out captures/modbus-indoor-after-buzzer.json
```

Then turn one unit off/on from its normal controller and listen. No beep means the write is still effective.

## Reboot/Power Persistence Check

Use this only during a maintenance window:

1. Save baseline:

```bash
uv run coolrev coolmaster inventory --host "$COOLMASTER_HOST" --names docs/office-names.example.json --out captures/inventory-before-power-cycle.json
uv run coolrev coolmaster capabilities --host "$COOLMASTER_HOST" --out captures/capabilities-before-power-cycle.json
```

2. Power-cycle the gateway or the selected indoor unit.
3. Wait until `line` shows `L7` scanning normally and `ls2` lists all nine units.
4. Test one unit acoustically.
5. Re-apply all buzzer-disable writes if any unit beeps:

```bash
uv run coolrev modbus buzzer --host "$COOLMASTER_HOST" --all --set on
```

6. Save after snapshot:

```bash
uv run coolrev coolmaster inventory --host "$COOLMASTER_HOST" --names docs/office-names.example.json --out captures/inventory-after-power-cycle.json
uv run coolrev coolmaster capabilities --host "$COOLMASTER_HOST" --out captures/capabilities-after-power-cycle.json
uv run coolrev coolmaster diff-capabilities captures/capabilities-before-power-cycle.json captures/capabilities-after-power-cycle.json
```
