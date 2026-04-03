# DeathAdder V2 Pro Bluetooth (0x008E) Reverse Engineering Plan

## Goals

- Achieve stable read/write for `dpi`, `dpi-stages`, `poll-rate`, and `battery` in BT mode on macOS.
- Keep USB/dongle (`007C`/`007D`) behavior isolated from BT experiments.
- Document command mapping by transport and onboard profile bank.

## Known Facts

- `007C` (USB) and `007D` (2.4G dongle) work via `rawhid`.
- `008E` (Bluetooth) is discoverable, but HID feature reports may fail (`send_feature_report failed`).
- The bottom LED/DPI button can switch onboard banks; the same LED color does not always mean the same absolute DPI across transports.

## Code Building Blocks

- `razecli/backends/macos_ble_backend.py`
  - Dedicated backend for `008E` on macOS.
  - Isolates BT from USB/dongle control paths.
  - Reuses existing packet framing and BLE vendor GATT transport.

## Iterative Workflow

1. USB baseline
   - Set known stages on `007C`:
     - `1700:1700`, `1000:1000`
   - Verify with `dpi-stages get`.
2. BT probe with separate backend
   - Use BT path only:
     - `python -m razecli.cli --backend macos-ble --json devices`
     - `python -m razecli.cli --backend macos-ble dpi get --model deathadder-v2-pro`
3. Collect failure telemetry
   - For each operation log:
     - transport, PID, command class/id, report-id, transaction-id, error text.
4. Validate bank behavior
   - Press the bottom DPI button between reads.
   - Record whether `active_stage` and stage list change without explicit writes.
5. Derive BT-specific deltas
   - Compare:
     - report-id (`0x00`, `0x02`)
     - transaction-id (`0x3F`, `0x1F`, and others)
     - timing/retry characteristics for reads.
6. Promote mapping when verified
   - Move `008E` from experimental to stable backend profile.
   - Add regression tests with recorded response fixtures.

## Command Cheat Sheet (Reverse Engineering)

Install/update local editable package:

```bash
python -m pip install -e .
```

Device and transport visibility:

```bash
razecli --json devices --all-transports --model deathadder-v2-pro
razecli --backend macos-ble --json devices --model deathadder-v2-pro
```

BLE discovery and service inspection:

```bash
razecli --json ble scan --timeout 10
razecli --json ble scan --name "DA" --timeout 10
razecli --json ble services --address F6:F2:0D:4E:D9:30 --timeout 12
razecli --json ble services --address F6:F2:0D:4E:D9:30 --read --timeout 12
```

Alias cache troubleshooting (MAC -> CoreBluetooth UUID):

```bash
razecli --json ble alias list
razecli --json ble alias resolve --address F6:F2:0D:4E:D9:30 --timeout 12
razecli --json ble alias clear --address F6:F2:0D:4E:D9:30
razecli --json ble alias clear --all
```

Raw BLE vendor transaction (manual key/payload testing):

```bash
razecli --json ble raw --address F6:F2:0D:4E:D9:30 --payload "30 00 00 00 05 81 00 01" --timeout 10 --response-timeout 1.5
razecli --json ble raw --address F6:F2:0D:4E:D9:30 --payload "31 00 00 00 05 80 00 01" --timeout 10 --response-timeout 1.5
razecli --json ble raw --address F6:F2:0D:4E:D9:30 --payload "32 00 00 00 0B 84 01 00" --timeout 10 --response-timeout 1.5
```

Focused poll-rate key probing:

```bash
razecli --json ble poll-probe --address F6:F2:0D:4E:D9:30 --attempts 2
```

Optional poll probe tuning:

```bash
razecli --json ble poll-probe --address F6:F2:0D:4E:D9:30 --attempts 3 --key 00850001 --key 0b850100
```

Force BT poll-rate API path for unsupported models (RE only):

```bash
RAZECLI_BLE_POLL_CAP=1 \
RAZECLI_BLE_POLL_FORCE=1 \
razecli --backend macos-ble poll-rate get --model deathadder-v2-pro
```

Enable BT poll-rate by model slug (only after validation):

```bash
RAZECLI_BLE_POLL_CAP=1 \
RAZECLI_BLE_POLL_SUPPORTED_MODELS=basilisk-x-hyperspeed \
razecli --backend macos-ble poll-rate get --model basilisk-x-hyperspeed
```

## Capture Matrix (Fill During RE)

- Device: DeathAdder V2 Pro
- Host: macOS version
- Transport: USB / Dongle / BT
- For each operation:
  - `dpi get`
  - `dpi set`
  - `dpi-stages get`
  - `dpi-stages set`
  - `poll-rate get/set`
  - `battery get`
- Record fields:
  - success/fail
  - report-id
  - tx-id
  - status byte
  - latency (ms)
  - observed mouse-side behavior

## Troubleshooting Guide

- `Device ... was not found` on macOS BLE:
  - Resolve alias again and retry:
    - `razecli --json ble alias resolve --address <MAC>`
  - Run `ble scan` and retry while mouse is active in BT mode.
  - Use `RAZECLI_BLE_BRUTEFORCE=1` only for debugging discovery edge cases.

- `poll-probe` returns `status: unsupported` with `status_code: 3` or `5`:
  - Treat BT poll-rate as unavailable on that firmware/host.
  - Use USB/2.4 for poll-rate.
  - Keep model BT poll-rate config disabled.

- `poll-probe` returns `status: transport-error`:
  - This is connect/resolve instability, not protocol rejection.
  - Retry with active device traffic and refresh alias mapping.

- BT read/write commands are slow or flaky:
  - Avoid parallel BLE operations.
  - Keep response timeout modest (`1.0-2.5s`) and use repeated short attempts instead of one very long timeout.

## Reference Sources

- OpenSnek (macOS USB+BT, protocol docs and captures)
- Linux Razer driver projects (USB packet format and model matrices)
- OpenRGB (secondary reference for wireless Razer edge cases)
- razer-macos (historical macOS implementation, mostly RGB)

## Done Criteria

- `macos-ble` handles 20/20 repeated `dpi get` calls without timeout/failure.
- `dpi-stages set` and `activate` are deterministic after reconnect.
- No unintended reset to `400/1000` within the same bank after write/reconnect.
