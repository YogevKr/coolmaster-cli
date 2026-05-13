# Safe Write Matrix

Device examples use `COOLMASTER_HOST`.

This document was built against CoolMasterNet firmware `1.5.2B`; verify your own firmware before write tests.

## Addressing

```text
document_base = VA * 16 + 1
wire_base = document_base - 1
document_address = document_base + offset
wire_address = document_address - 1
```

Use the tool to generate the exact current map:

```bash
uv run coolrev modbus map --host "$COOLMASTER_HOST" --all --out captures/modbus-map-current.json
```

## Holding Registers

These use Modbus function `0x06` through:

```bash
uv run coolrev modbus write --host "$COOLMASTER_HOST" --uid L7.007 --field FIELD --value VALUE
uv run coolrev modbus write --host "$COOLMASTER_HOST" --uid L7.007 --field FIELD --value VALUE --yes
```

The first command is a dry-run. Add `--yes` only after checking the target UID and wire address.

| Field | Offset | Values | Notes |
| --- | ---: | --- | --- |
| `operation_mode` | `+0` | `cool`, `heat`, `auto`, `dry`, `fan`, raw int | User-visible HVAC mode. |
| `fan_speed` | `+1` | `low`, `med`, `high`, `auto`, `top`, `very_low`, raw int | Unsupported speeds may be ignored by the indoor unit. |
| `set_temperature_c` | `+2` | Celsius float, encoded `x10` | Normal setpoint write. |
| `on_off_register` | `+3` | boolean | Same target as on/off coil, register form. |
| `filter_sign_register` | `+4` | boolean | Use carefully; normally reset with `filt UID`. |
| `swing` | `+5` | `vertical`, `30_deg`, `45_deg`, `60_deg`, `horizontal`, `auto`, `off`, raw int | Capability depends on indoor model. |
| `local_wall_controller_locks` | `+8` | raw bitfield | Bit 0 on/off, bit 1 mode, bit 2 setpoint, bit 7 all. |
| `temperature_limits` | `+9` | raw int/hex or `LOW:HIGH` Celsius | Packed as high byte `HIGH*2`, low byte `LOW*2`; zero disables a side. |
| `cool_temperature_limits` | `+10` | raw int/hex or `LOW:HIGH` Celsius | Cool-mode-specific limits. |
| `heat_temperature_limits` | `+11` | raw int/hex or `LOW:HIGH` Celsius | Heat-mode-specific limits. |

Example:

```bash
uv run coolrev modbus write --host "$COOLMASTER_HOST" --uid L7.007 --field cool_temperature_limits --value 16:32
```

## Coils

These use Modbus function `0x05`.

| Field | Offset | Values | Notes |
| --- | ---: | --- | --- |
| `on_off` | `+0` | boolean | User-visible power. |
| `filter_sign` | `+1` | boolean | Reset/flag behavior depends on unit. |
| `inhibit` | `+3` | boolean | Forces the unit off while active; intended for window sensors. |
| `buzzer_disable` | `+4` | boolean | Samsung hidden capability. Confirmed write-accepted; readback returns illegal address on this gateway. |
| `digital_output_1`..`digital_output_6` | `+9`..`+14` | boolean | Only meaningful if external output hardware is attached. |

Dedicated buzzer command:

```bash
uv run coolrev modbus buzzer --host "$COOLMASTER_HOST" --all --set on
```

## Read-Only Coverage

`uv run coolrev modbus indoor --host "$COOLMASTER_HOST" --all` reads:

- Holding registers `+0..+15`
- Input registers `+0..+15`
- Coils `+0..+15`
- Discrete inputs `+0..+15`

Each offset is read independently. Reserved or unsupported objects are recorded with the Modbus exception instead of aborting the whole block.
