# Razer BLE vendor GATT reverse engineering

This document is the working plan for Bluetooth control of Razer mice on macOS. DeathAdder V2 Pro (`1532:008E`) remains the primary case study because most hands-on validation has happened there. Other models reuse the same GATT framing and key catalog when they declare a Bluetooth product ID in the model registry.

## USB and Bluetooth

USB and the 2.4 GHz dongle path use 90-byte feature reports (command class, command id, arguments, CRC). That matches the layout described in the [OpenRazer USB reverse-engineering wiki](https://github.com/openrazer/openrazer/wiki/Reverse-Engineering-USB-Protocol) and lives in `razecli/backends/rawhid_backend.py`.

Bluetooth is a different transport: vendor GATT write and notify characteristics, 8-byte request headers, and 4-byte keys. Details are in OpenSnek’s BLE protocol write-up; the implementation is in `razecli/backends/macos_ble_backend.py`.

DPI, battery, and lighting are related ideas across USB and BLE, but the bytes on the wire are not interchangeable. Treat each transport separately.

## Reference projects

- [OpenSnek](https://github.com/gh123man/opensnek) — strongest public reference for this BLE stack. Start with [BLE_PROTOCOL.md](https://github.com/gh123man/opensnek/blob/main/docs/protocol/BLE_PROTOCOL.md), then [USB_PROTOCOL.md](https://github.com/gh123man/opensnek/blob/main/docs/protocol/USB_PROTOCOL.md) and [PARITY.md](https://github.com/gh123man/opensnek/blob/main/docs/protocol/PARITY.md) when comparing transports.

- [OpenRazer](https://github.com/openrazer/openrazer) — source of USB product IDs (`driver/razermouse_driver.h`) and the 90-byte report format used on Linux.

- [OpenRGB](https://github.com/calcprogrammer1/OpenRGB) — sometimes useful for device lists and RGB-focused behavior; not the main reference for vendor BLE keys.

- [razer-macos](https://github.com/1kc/razer-macos) and [librazermacos](https://github.com/stickoking/librazermacos) — older macOS efforts; prefer OpenSnek and OpenRazer for protocol-level detail.

## Goals

- Stable read and write for `dpi`, `dpi-stages`, `poll-rate`, and `battery` over BLE on macOS.

- Stable read and write for `rgb` and `button-mapping` over BLE on macOS.

- Keep USB and dongle experiments on `rawhid` separate from BLE work where practical.

- Record how commands map per transport and per onboard profile bank.

## Model registry and Bluetooth

`ModelRegistry.ble_endpoint_product_ids()` collects every `ble_endpoint_product_ids` tuple from `razecli/models/*.py`. `macos-ble` only considers a Bluetooth device if its `product_id` appears in that merged set, unless `RAZECLI_BLE_PRODUCT_IDS` overrides the list.

You can add OpenRazer USB IDs for `rawhid` without enabling BLE. Only set `ble_endpoint_product_ids` when you know the Bluetooth enumeration PID and you have evidence the device speaks the same vendor service as OpenSnek’s captures.

Until hardware proves otherwise, keep `ble_endpoint_experimental` set, limit `ble_supported_rgb_modes`, and use `ble_multi_profile_table_limited` where DPI banks are unclear.

`ModelRegistry.find_by_name()` picks the longest matching product name or alias so similar names (for example Basilisk V3 versus Basilisk V3 X) do not collide.

## DeathAdder V2 Pro facts

- `007C` USB and `007D` dongle work through `rawhid`.

- `008E` Bluetooth is visible to the host, but HID feature reports often fail; the reliable path is vendor GATT.

- The bottom profile control changes banks. LED color is not a reliable stand-in for absolute DPI across transports.

- BLE is used in-tree for DPI, DPI stages, battery, RGB probes, and button probes on this model.

## Already mapped in tooling

- `ble bank-probe`, `ble bank-snapshot`, and `ble bank-compare` for onboard profile banks.

- `ble bank-probe --deep` for extra `0x84` family keys and signature grouping.

- Shallow bank probes also try `0b840101` next to `0b840100`; `0b840000` alone is often empty. If two banks share the same `primary_bank_signature`, the decoded DPI tables match on the wire—change per-bank DPI or use `--deep` if you need clearer separation.

- `ble rgb-probe` for key decode hints and mode selector state.

- `ble button-probe` for standard mouse slots when the payload matches a known layout.

- CLI and TUI expose hardware versus local scope and read confidence (`verified`, `mixed`, `inferred`).

## OpenSnek key catalog

Reads typically use `0x84` in the second key byte; writes use `0x04` in the same family. The following matches what OpenSnek documents and what RazeCLI targets.

### DPI stage table

Read `0b840100`, write `0b040100` (38-byte stage table on write).

### Battery

Read `05810001` (raw) and `05800001` (status). No standard write key in the shared catalog for these.

### RGB frame legacy

Read `10840000`, write `10040000` (8-byte frame style on supported devices).

### RGB mode selector

Read `10830000`, write `10030000` (four-byte little-endian selector payload).

### Button binding

Write `080401` plus slot byte, with a ten-byte action payload. Reads use the `088401` family per slot.

### Sleep timeout

Read `05840000`, write `05040000` (16-bit little-endian seconds). Documented in OpenSnek; not yet exposed as a first-class CLI command—use `ble raw` to experiment.

## Terminology

- Users say onboard profile; debug output may say bank. Same concept.

- DPI levels are the steps behind the top DPI buttons.

- The bottom profile button switches banks; each bank can carry its own stage table and indicator behavior.

## Code locations

- `razecli/backends/macos_ble_backend.py` implements the GATT client, serialization, and key mapping.

- `razecli/models/*.py` holds USB IDs, optional BLE PIDs, RGB policy, `ble_button_decode_layouts`, and experimental flags.

## Iterative workflow

1. Establish a USB baseline on `007C` and confirm with `dpi-stages get`.

2. Switch to BLE only: `python -m razecli.cli --backend macos-ble --json devices` and `dpi get --model <slug>`.

3. Log failures with transport, PID, keys, status bytes, and timeouts.

4. Exercise the bottom profile button between reads to observe bank changes.

5. Note any `rawhid` quirks on Bluetooth HID nodes separately from GATT timing.

6. Run `ble rgb-probe` and `ble button-probe` when RGB or binds are in scope.

7. Tighten `ModelSpec` and add tests in `tests/test_macos_ble_backend.py` once stable.

## Command cheat sheet

Install or refresh the editable package:

```bash
python -m pip install -e .
```

Models and devices:

```bash
razecli --json models
razecli --json devices --all-transports --model deathadder-v2-pro
razecli --backend macos-ble --json devices --model deathadder-v2-pro
```

Discovery and services:

```bash
razecli --json ble scan --timeout 10
razecli --json ble scan --name "DA" --timeout 10
razecli --json ble services --address F6:F2:0D:4E:D9:30 --timeout 12
razecli --json ble services --address F6:F2:0D:4E:D9:30 --read --timeout 12
```

Alias cache:

```bash
razecli --json ble alias list
razecli --json ble alias resolve --address F6:F2:0D:4E:D9:30 --timeout 12
razecli --json ble alias clear --address F6:F2:0D:4E:D9:30
razecli --json ble alias clear --all
```

Manual vendor transactions:

```bash
razecli --json ble raw --address F6:F2:0D:4E:D9:30 --payload "30 00 00 00 05 81 00 01" --timeout 10 --response-timeout 1.5
razecli --json ble raw --address F6:F2:0D:4E:D9:30 --payload "31 00 00 00 05 80 00 01" --timeout 10 --response-timeout 1.5
razecli --json ble raw --address F6:F2:0D:4E:D9:30 --payload "32 00 00 00 0B 84 01 00" --timeout 10 --response-timeout 1.5
```

Poll-rate probes:

```bash
razecli --json ble poll-probe --address F6:F2:0D:4E:D9:30 --attempts 2
razecli --json ble poll-probe --address F6:F2:0D:4E:D9:30 --attempts 3 --key 00850001 --key 0b850100
```

Force poll-rate API for research:

```bash
RAZECLI_BLE_POLL_CAP=1 \
RAZECLI_BLE_POLL_FORCE=1 \
razecli --backend macos-ble poll-rate get --model deathadder-v2-pro
```

Allowlist poll-rate by slug after validation:

```bash
RAZECLI_BLE_POLL_CAP=1 \
RAZECLI_BLE_POLL_SUPPORTED_MODELS=basilisk-x-hyperspeed \
razecli --backend macos-ble poll-rate get --model basilisk-x-hyperspeed
```

RGB and buttons:

```bash
razecli --json ble rgb-probe --address F6:F2:0D:4E:D9:30 --attempts 2
razecli --json ble rgb-probe --address F6:F2:0D:4E:D9:30 --attempts 1 --mode-key 10830000
razecli --json ble button-probe --address F6:F2:0D:4E:D9:30 --attempts 2
razecli --json ble button-probe --address F6:F2:0D:4E:D9:30 --key 08840104 --attempts 2
```

Banks:

```bash
razecli --json ble bank-probe --address F6:F2:0D:4E:D9:30 --attempts 2
razecli --json ble bank-probe --address F6:F2:0D:4E:D9:30 --attempts 2 --deep
razecli --json ble bank-snapshot --address F6:F2:0D:4E:D9:30 --attempts 2 --label bank-a
razecli --json ble bank-snapshot --address F6:F2:0D:4E:D9:30 --attempts 2 --label bank-b
razecli --json ble bank-compare --label-a bank-a --label-b bank-b
```

## Capture checklist

When validating a mouse, record the model slug, macOS version, and whether you used USB, dongle, or Bluetooth.

For each operation you care about (`dpi`, `dpi-stages`, `poll-rate`, `battery`, `rgb`, `button-mapping`), note success or failure, which keys ran, status bytes, latency, and what changed on the device.

On DeathAdder V2 Pro over `rawhid` on `008E`, also note report id and transaction id when debugging HID-side attempts.

## Troubleshooting

- `Device ... was not found`: refresh aliases, rescan, confirm the mouse is in Bluetooth mode. Reserve `RAZECLI_BLE_BRUTEFORCE=1` for discovery debugging.

- `poll-probe` reports unsupported with status `3` or `5`: assume BT poll-rate is unavailable; use USB or dongle; keep the model’s BT poll flags off.

- `poll-probe` reports transport errors: treat as connectivity, not protocol rejection; retry after reconnect.

- Slow or flaky sessions: avoid overlapping BLE commands; prefer short timeouts and repeated attempts over one long wait.

## Done criteria for DeathAdder V2 Pro on BLE

- Twenty consecutive `dpi get` calls succeed without timeouts.

- `dpi-stages set` and activation behave the same after reconnect.

- No surprise jumps to `400`/`1000` DPI inside the same bank after writes.

- RGB reads reach `verified` confidence for mode, brightness, and color in normal use.

- Button mapping reads reach `verified` for the slots you ship, including DPI cycle.

## Done criteria for additional models

Repeat the capture checklist, run `rgb-probe`, `button-probe`, and `bank-probe`, then only then clear `ble_endpoint_experimental` and widen `ble_supported_rgb_modes` when the captures justify it.
