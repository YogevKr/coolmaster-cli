# Protocol Map

## Layers

### CoolMaster ASCII Side

Known safe commands to use as anchors:

| Command | Direction | Purpose | Notes |
| --- | --- | --- | --- |
| `ls` | read | visible indoor unit status | Fixed-width output in CoolMaster PRM. |
| `ls+` | read | all indoor unit status, including invisible | Useful for complete UID inventory. |
| `stat*` | read | legacy status variants | Deprecated, still useful for older firmware. |
| `props UID ...` | read/write | names, visibility, limits | Use reads first; writes can change behavior. |
| `line` | read/write | line type/config | Read-only first; line writes can affect integration. |
| service-data commands | read mostly | service/capability data | Product/firmware dependent. |

Status line fields from `ls`:

| Field | Example |
| --- | --- |
| UID | `L2.102` |
| Power | `ON` / `OFF` |
| Set temperature | `20C` |
| Room temperature | `27C` |
| Fan speed | `High` |
| Mode | `Cool` |
| Failure code | `OK` or code |
| Filter sign | `-` / `#` |
| Demand | `0` / `1` |

### Samsung VRF Side

Most modern Samsung VRF integrations use NASA frames:

```text
STX  size-le  src[3]  dst[3]  packet-info  type  packet-no  capacity  messages...  crc16  ETX
0x32 ...                                                                                    0x34
```

Address class examples:

| Hex | Device class |
| --- | --- |
| `0x10` | Outdoor |
| `0x20` | Indoor |
| `0x40` | Remote controller |
| `0x50` | Wired remote |
| `0x58` | Power interface module |
| `0x59` | Serial interface module |
| `0x62` | WiFi kit |

NASA message anchors:

| Message | Name | Payload | Exposure target |
| --- | --- | --- | --- |
| `0x4000` | Power control | enum: `0` off, `1` on | CoolMaster power |
| `0x4001` | Operation mode | enum | Cool/Heat/Fan/Dry/Auto |
| `0x4002` | Real operation mode | enum | read-only actual mode |
| `0x4006` | Fan speed | enum | CoolMaster fan |
| `0x4008` | Real fan speed | enum | read-only fan |
| `0x4011` | Air swing up/down | enum | swing |
| `0x4201` | Target temperature | u16 big-endian, tenths C | setpoint |
| `0x4203` | Current temperature | u16 big-endian, tenths C | room temperature |
| `0x4038` | Current humidity | enum percentage | possible unexposed field |
| `0x0202` | Error code 1 | variable | diagnostics |
| `0x0207` | Indoor unit count | variable | topology |
| `0x0406` | Total power consumption | u32 big-endian | possible unexposed field |
| `0x0407` | Cumulative power consumption | u32 big-endian | possible unexposed field |
| `0x0600` | Product options | structure | capabilities |
| `0x0601` | Installation options | structure | capabilities |
| `0x0607` | Serial number | structure | inventory |
| `0x060C` | EEPROM code version | structure | firmware/capabilities |

## Working Hypotheses

- CoolMaster exposes a normalized subset of Samsung NASA fields through ASCII.
- CoolMaster Modbus/IP exposes several Samsung capabilities that are not available in the CoolMaster app or common ASCII controls.
- Unexposed capabilities will likely appear as NASA read responses that CoolMaster uses internally or can query through service-data.
- Useful capability families: energy, humidity, error history, static pressure, filters, limits, option bytes, defrost/recovery states, demand/capacity signals.

## Confirmed CoolMaster Modbus/IP Extras

| Field | Object | Status |
| --- | --- | --- |
| Indoor buzzer disable | Coil `base+4` | Confirmed on Samsung; write accepted, acoustic validation. |
| Inhibit/window-sensor behavior | Coil `base+3` | Exposed by PRM and Modbus table; not tested live here. |
| Local controller lock bits | Holding register `base+8` | Exposed by PRM and Modbus table; decoded by tool. |
| Global/cool/heat temperature limits | Holding registers `base+9..+11` | Exposed by Modbus table; decoded and write-planned by tool. |
| Digital outputs 1..6 | Coils `base+9..+14` | Exposed by Modbus table; only useful with attached I/O hardware. |
| Digital inputs 1..6 | Discrete inputs `base+9..+14` | Read-only; only useful with attached I/O hardware. |
| Analog inputs 1..2 | Input registers `base+13..+14` | Read-only; only useful with attached I/O hardware. |

## Evidence Table

Fill this as captures come in.

| Observation | Operator action | Frames/messages | Confidence | Notes |
| --- | --- | --- | --- | --- |
| Samsung indoor unit beep muted | Modbus write single coil `base+4` ON for all VA blocks | CoolMaster Modbus/IP accepted writes to wire coils `20,36,52,68,84,100,116,132,148` | Confirmed | `base = VA*16+1`; wire address is one less than document address. Coil is write-only/unsupported for read. |

## Office VA Map

| UID | VA | Document Base | Buzzer Disable Document Coil | Buzzer Disable Wire Coil |
| --- | ---: | ---: | ---: | ---: |
| `L7.000` | 1 | 17 | 21 | 20 |
| `L7.001` | 2 | 33 | 37 | 36 |
| `L7.002` | 3 | 49 | 53 | 52 |
| `L7.003` | 4 | 65 | 69 | 68 |
| `L7.004` | 5 | 81 | 85 | 84 |
| `L7.005` | 6 | 97 | 101 | 100 |
| `L7.006` | 7 | 113 | 117 | 116 |
| `L7.007` | 8 | 129 | 133 | 132 |
| `L7.008` | 9 | 145 | 149 | 148 |

## References

- CoolAutomation CoolMaster Product Line PRM, ASCII interface, status formats, and commands: <https://support.coolautomation.com/hc/en-us/article_attachments/17317574262557>
- CoolAutomation Samsung integration/error-code wiki: <https://www.coolautomation.wiki/index.php?title=Samsung>
- `pysamsungnasa` packet-structure reference: <https://pantherale0.github.io/pysamsungnasa/protocol/packet-structure/>
- `pysamsungnasa` message reference: <https://pantherale0.github.io/pysamsungnasa/protocol/messages/>
