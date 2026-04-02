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

## Reference Sources

- OpenSnek (macOS USB+BT, protocol docs and captures)
- Linux Razer driver projects (USB packet format and model matrices)
- OpenRGB (secondary reference for wireless Razer edge cases)
- razer-macos (historical macOS implementation, mostly RGB)

## Done Criteria

- `macos-ble` handles 20/20 repeated `dpi get` calls without timeout/failure.
- `dpi-stages set` and `activate` are deterministic after reconnect.
- No unintended reset to `400/1000` within the same bank after write/reconnect.
