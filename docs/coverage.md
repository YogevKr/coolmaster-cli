# Coverage

## Confirmed

| Area | Command | Status |
| --- | --- | --- |
| CoolMaster ASCII raw | `printf 'ls\r\n' \| nc "$COOLMASTER_HOST" 10102` | Confirmed |
| CoolMaster firmware/settings | `coolrev coolmaster send --cmd set` | Confirmed |
| Unit status | `ls`, `ls2`, `stat2`, `stat3`, `stat4` | Confirmed |
| Per-unit raw query | `query <UID> o/m/f/t/e/a/h` | Confirmed |
| Lock status | `lock <UID>` | Confirmed |
| VA mapping | `va` | Confirmed |
| Modbus/IP | TCP `502` | Confirmed enabled after firmware upgrade |
| Indoor buzzer disable | Modbus coil `base+4` write ON | Confirmed acoustically |
| Full indoor block read | `coolrev modbus indoor` | Implemented |
| Full write address map | `coolrev modbus map` | Implemented |
| Capability audit | `coolrev coolmaster capabilities` | Implemented |
| Capability diff | `coolrev coolmaster diff-capabilities` | Implemented |
| Inventory | `coolrev coolmaster inventory` | Implemented |
| Monitor | `coolrev coolmaster monitor` | Implemented |

## Modbus Indoor Block

Document address:

```text
base = VA * 16 + 1
wire_base = base - 1
```

The tools read each offset individually so reserved cells are preserved as errors instead of failing the whole block.

Useful fields:

| Object | Offset | Field | Notes |
| --- | ---: | --- | --- |
| Holding register | `+0` | operation mode | `cool`, `heat`, `auto`, `dry`, `fan`, etc. |
| Holding register | `+1` | fan speed | `low`, `med`, `high`, `auto`, etc. |
| Holding register | `+2` | set temperature x10 C | writeable |
| Holding register | `+3` | on/off | writeable |
| Holding register | `+5` | swing | writeable when supported |
| Holding register | `+8` | local wall-controller locks | bitfield |
| Holding register | `+9` | temperature limits | packed high/low limits |
| Holding register | `+10` | cool temperature limits | packed high/low limits |
| Holding register | `+11` | heat temperature limits | packed high/low limits |
| Input register | `+0` | UID | decoded as `Ln.XYY` |
| Input register | `+1` | room temperature x10 C | read-only |
| Input register | `+2,+3` | malfunction code string | e.g. `OK` |
| Input register | `+4` | set temperature x10 C | read-only mirror |
| Coil | `+0` | on/off | writeable |
| Coil | `+1` | filter sign | writeable |
| Coil | `+3` | inhibit | writeable, forces off while active |
| Coil | `+4` | buzzer disable | Samsung, writeable, not readable on this gateway |
| Discrete input | `+0` | therm/demand status | read-only |
| Discrete input | `+1` | indoor communication failure | read-only |

## Guardrails

- `coolrev modbus write` is dry-run unless `--yes` is present.
- `coolrev modbus buzzer --set on/off` writes immediately because it is a dedicated wrapper.
- Use `--uid` or `--all` when possible. Raw `--va` exists for debugging.
- Buzzer readback is expected to return Modbus exception 2; acoustic validation is the source of truth.
- Temperature limit writes accept raw integer/hex or `LOW:HIGH` Celsius. Example `16:32` encodes to `0x4020`.

## Samsung Bus Reverse Targets

Capture raw Samsung bus while toggling:

| Action | Expected exposed layer | Why |
| --- | --- | --- |
| On/off | CoolMaster ASCII + Modbus + Samsung NASA | Baseline control |
| Mode | CoolMaster ASCII + Modbus + Samsung NASA | Map enum translation |
| Fan speed | CoolMaster ASCII + Modbus + Samsung NASA | Map enum translation |
| Setpoint including half-degree | CoolMaster ASCII + Modbus + Samsung NASA | Precision behavior |
| Buzzer disable | Modbus + Samsung NASA | Hidden capability |
| Lock mode/setpoint/on-off | CoolMaster ASCII + Modbus + Samsung NASA | Local controller behavior |
| Inhibit | Modbus + Samsung NASA | Window-sensor behavior |
