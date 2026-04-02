# RazeCLI

Modular CLI/TUI for detecting and configuring Razer mice.

RazeCLI is a lightweight CLI and TUI focused on practical mouse settings, mainly DPI and poll-rate, with macOS as a key target where official lightweight tooling is limited.
The current scope is stable read/write for DPI, DPI stages, poll-rate, and battery where supported.
RGB and button mapping are now available as local scaffolds (command contract + local persistence), with hardware write support planned next.

Supported models:
- `deathadder-v2-pro` (`1532:007C`, `1532:007D`, `1532:008E`)
- `deathadder-v2` (`1532:0084`)
- `deathadder-v2-mini` (`1532:008C`)
- `basilisk-x-hyperspeed` (`1532:0083`)

## Current Status

- USB mode (`007C`) and 2.4G dongle mode (`007D`) are the most stable paths.
- Bluetooth endpoint (`008E`) is handled by a dedicated macOS GATT backend: `macos-ble`.
- Bluetooth support is still experimental on macOS and connect/discovery can fail on some hosts.
- Poll-rate over Bluetooth has an experimental key-probe implementation in `macos-ble` and may require per-device key overrides.
- Protocol framing follows known Razer packet structure, with additional BLE reverse-engineering work.
- Most real-hardware validation has been done on DeathAdder V2 Pro. Other models may need key/path tuning; see `Adding BT Support for More Razer Models`.

## Features

- Hardware detection of connected Razer devices
- Modular model registry (`razecli/models/*.py`)
- DPI get/set
- DPI profiles (stages) get/set/add/update/remove/activate
- DPI stage presets (save/load/list/delete)
- Poll-rate get/set
- Battery level (when backend/device supports it)
- Local RGB scaffold (`rgb get/set`) for DA V2 Pro integration work
- Local button-mapping scaffold (`button-mapping get/set/reset/actions`) for DA V2 Pro integration work
- JSON output for scripting

## Feature Gap (Local Backends)

- **DPI**
  Available now: `get/set` + stages + presets on `rawhid`; experimental on `macos-ble`.
  Missing for production: more BT model key mappings.
  Next local priority: expand BT key maps per model and add fixture-based tests.

- **Poll-rate**
  Available now: `get/set` on `rawhid`; experimental BT probing in `macos-ble`.
  Missing for production: stable BT write/read mapping on more hosts/models.
  Next local priority: promote verified BT key mappings and tighten verification.

- **Battery**
  Available now: `get` on `rawhid`; BT read path in `macos-ble`.
  Missing for production: better reconnect robustness on unstable BLE sessions.
  Next local priority: add retry/backoff and host-specific fallback paths.

- **RGB**
  Available now: local scaffold API + persistence.
  Missing for production: device-side HID/BLE packet mapping and write/verify loops.
  Next local priority: implement DA V2 Pro hardware mapping first.

- **Button mapping**
  Available now: local scaffold API + persistence.
  Missing for production: device-side button-binding packet mapping + validation.
  Next local priority: implement DA V2 Pro hardware mapping first.

- **RGB/button UX**
  Available now: CLI contract exists.
  Missing for production: TUI editor flows and live apply feedback.
  Next local priority: add TUI panels once DA V2 Pro write path is stable.

## Next Local Priorities

1. Implement DA V2 Pro RGB hardware read/write in `rawhid` first, then `macos-ble`.
2. Implement DA V2 Pro button-mapping hardware read/write in `rawhid` first, then `macos-ble`.
3. Add TUI pages for RGB/button editing after hardware calls are stable.
4. Extend model coverage only after DA V2 Pro paths are validated with repeatable tests.

## Architecture

- `razecli/models/`: one file per model (`MODEL = ModelSpec(...)`)
- `razecli/model_registry.py`: dynamic model loader
- `razecli/backends/rawhid_backend.py`: direct HID packet control via `hidapi` (USB + experimental BT)
- `razecli/backends/macos_ble_backend.py`: dedicated experimental BT backend for `008E` on macOS (vendor GATT)
- `razecli/backends/hidapi_backend.py`: detection fallback
- `razecli/backends/macos_profiler_backend.py`: macOS detection via `system_profiler` (USB + Bluetooth)

## How It Works

- `rawhid` sends 90-byte Razer feature reports.
- CLI/TUI calls the selected backend (`rawhid`, `macos-ble`, `hidapi`, `macos-profiler`).
- Model modules in `razecli/models/` define USB IDs and constraints; registry loads them dynamically.
- Cross-transport autosync is opt-in via `RAZECLI_AUTOSYNC=1` (disabled by default for stability).

## Requirements

Option A (`rawhid` backend):
- `hidapi` installed.
- DeathAdder V2 Pro control works in direct HID mode (`007C`/`007D`).
- Bluetooth PID `008E` in `rawhid` is experimental and host/driver dependent.

Option B (BLE reverse engineering):
- `bleak` installed for GATT scan/probe.

Install `hidapi`:

```bash
python -m pip install "hidapi>=0.14"
```

Install BLE probe extras:

```bash
python -m pip install -e '.[ble]'
```

## Installation

```bash
python -m pip install -e .
```

## Run Without Activating venv

Option 1 (recommended for development): install with `pipx` and run `razecli` directly.

```bash
brew install pipx
pipx ensurepath
pipx install --editable ".[detect]"
razecli --help
```

Option 2 (distribution): build a standalone macOS binary (no Python required on target machine).

```bash
./scripts/build_macos_onefile.sh
./dist/razecli --help
```

The build script creates a self-contained executable in `dist/`.  
Use that binary directly on machines where you do not want to manage Python/venv.

## Usage

List models:

```bash
razecli models
```

List devices:

```bash
razecli devices
```

Show all transport endpoints separately (USB/dongle/BT):

```bash
razecli devices --all-transports
```

Show devices for one model:

```bash
razecli devices --model deathadder-v2-pro
```

Read DPI:

```bash
razecli dpi get
```

Set DPI (X/Y):

```bash
razecli dpi set --x 1600 --y 1600
```

Read DPI stages:

```bash
razecli dpi-stages get
```

Replace all DPI stages:

```bash
razecli dpi-stages set --active 2 --stage 800:800 --stage 1600:1600 --stage 3200:3200
```

Add a stage:

```bash
razecli dpi-stages add --x 2400 --y 2400
razecli dpi-stages add --x 2400 --y 2400 --active
```

Update/remove/activate stage:

```bash
razecli dpi-stages update --index 2 --x 1800 --y 1800
razecli dpi-stages remove --index 3
razecli dpi-stages activate --index 1
```

Save/load presets (quick restore):

```bash
razecli dpi-stages preset save --name fps3
razecli dpi-stages preset load --name fps3
razecli dpi-stages preset list
razecli dpi-stages preset delete --name fps3
```

Read poll-rate:

```bash
razecli poll-rate get
```

Set poll-rate:

```bash
razecli poll-rate set --hz 1000
```

Read battery:

```bash
razecli battery get
```

Local RGB scaffold (stored locally, does not write device firmware yet):

```bash
razecli rgb get
razecli rgb set --mode static --brightness 55 --color 00ff88
```

Local button-mapping scaffold (stored locally, does not write device firmware yet):

```bash
razecli button-mapping get
razecli button-mapping actions
razecli button-mapping set --button side_1 --action mouse:back
razecli button-mapping reset
```

Start TUI:

```bash
razecli tui
```

Show all models in TUI:

```bash
razecli tui --all-models
```

Show all transport endpoints in TUI:

```bash
razecli tui --all-transports
```

JSON output:

```bash
razecli --json devices
razecli --json dpi get
```

BLE probing (GATT):

```bash
razecli ble scan --name Razer
razecli --json ble services --name "DA V2 Pro"
razecli --json ble services --address 02:11:22:33:44:55 --read
```

BLE alias cache (MAC -> CoreBluetooth UUID):

```bash
razecli --json ble alias list
razecli --json ble alias resolve --address 02:11:22:33:44:55
razecli --json ble alias clear --address 02:11:22:33:44:55
razecli --json ble alias clear --all
```

Experimental raw BLE transceive over Razer vendor service (`...1524/1525/1526`):

```bash
razecli --json ble raw --name "DA V2 Pro" --payload "00 ff 01"
razecli --json ble raw --address 02:11:22:33:44:55 --payload "00ff01" --response-timeout 2.0
razecli --json ble raw --name "DA V2 Pro" --payload "00 ff 01" --no-notify --no-read
```

Notes:
- `ble raw` is a reverse-engineering tool (experimental).
- `macos-ble` now uses the same vendor-GATT path for battery + DPI + DPI stages.
- In the macOS Bluetooth UI, the device may appear as `DA V2 Pro` instead of `Razer ...`.
- `ble services --address <MAC>` tries to auto-resolve MAC to CoreBluetooth UUID if needed.
- Fast candidate matching is used by default; set `RAZECLI_BLE_BRUTEFORCE=1` for a slower, more aggressive probe.
- Successful MAC -> UUID mapping is cached in `~/.config/razecli/ble_aliases.json` (override with `RAZECLI_BLE_ALIAS_PATH`).
- `macos-ble` automatically retries with a discovered writable/notify GATT path if the default vendor UUID path is missing.

Backend override (troubleshooting):

```bash
razecli --backend rawhid devices
razecli --backend rawhid dpi get
razecli --backend rawhid poll-rate set --hz 1000
razecli --backend rawhid battery get
razecli --backend macos-ble devices --model deathadder-v2-pro
razecli --backend macos-ble dpi get --model deathadder-v2-pro
razecli --backend macos-ble dpi-stages get --model deathadder-v2-pro
razecli --backend macos-ble battery get --model deathadder-v2-pro
razecli --backend macos-profiler devices
razecli --backend hidapi devices
razecli --backend macos-profiler tui --all-models
```

Bluetooth in `rawhid` (`008E`) is experimental HID mode. On macOS BT, prefer `--backend macos-ble`.

Debug rawhid BT probe (path/rid/tx attempts on stderr):

```bash
RAZECLI_RAWHID_DEBUG=1 razecli --backend rawhid dpi get --model deathadder-v2-pro
```

If you see `IOHIDDeviceSetReport ... 0xE00002F0`, switch to `--backend macos-ble` (GATT) instead of raw HID.

Optional probe overrides:

```bash
RAZECLI_RAWHID_TX_IDS=0x3F,0x1F,0xFF \
RAZECLI_RAWHID_REPORT_IDS=0x00,0x02 \
RAZECLI_RAWHID_EXPERIMENTAL_ATTEMPTS=8 \
razecli --backend macos-ble dpi get --model deathadder-v2-pro
```

Experimental BT poll-rate mapping (vendor keys):

```bash
# Optional: expose poll-rate capability in macos-ble device listing/TUI
RAZECLI_BLE_POLL_CAP=1 razecli --backend macos-ble devices --model deathadder-v2-pro

# Optional: override candidate vendor keys for poll read/write
RAZECLI_BLE_POLL_READ_KEYS=00850001,00850000 \
RAZECLI_BLE_POLL_WRITE_KEYS=00050001,00050000 \
razecli --backend macos-ble poll-rate get --model deathadder-v2-pro
```

## Safe vs Legacy Stage Activation

- Default behavior (`safe`):
  - `dpi-stages activate --index N` and `s` in TUI do **not** rewrite the full stage table.
  - They only set DPI to the selected stage value (`set_dpi`).
  - This is safer on unstable firmware/transport combinations where full stage rewrites may reset stages unexpectedly.

- Legacy behavior (`unsafe`):
  - Enable with `RAZECLI_UNSAFE_STAGE_ACTIVATE=1`.
  - Activation rewrites the full stage list and active index (`set_dpi_stages`).
  - This can be useful when you explicitly need stage-index semantics, but it is more likely to trigger profile resets on problematic transports.

## Why `--backend macos-ble` May Return No Devices

`macos-ble` is BT-only and only targets the Bluetooth endpoint (`1532:008E`).

If `razecli --backend macos-ble --json devices --model deathadder-v2-pro` returns `[]`:
- Make sure the mouse is switched to Bluetooth mode (not USB/dongle mode).
- Confirm it is connected in macOS Bluetooth settings.
- Run `razecli --json ble scan --name "DA"` and then probe with `ble services`.
- If name/MAC resolution is unstable, retry with `RAZECLI_BLE_BRUTEFORCE=1`.

## Adding BT Support for More Razer Models

`macos-ble` can be expanded to additional product IDs that expose the same vendor service.

- Default BT PID set includes `0x008E` and `0x0083`.
- Override/extend at runtime:

```bash
RAZECLI_BLE_PRODUCT_IDS=0x008E,0x0083,0x00A5 razecli --backend macos-ble --json devices
```

Recommended workflow when adding a new BT model:

1. Verify detection:
`razecli --backend macos-ble --json devices --all-transports`
2. Verify GATT service/characteristics:
`razecli --json ble services --name "<device name>"`
3. Test vendor transaction path:
`razecli --json ble raw --name "<device name>" --payload "30 00 00 00 05 81 00 01"`
4. Map keys for battery/DPI/stages/poll-rate and add/update backend key map.
5. Add/extend tests in `tests/test_macos_ble_backend.py` and `tests/test_ble_probe.py`.

## TUI Shortcuts

- `q`: quit
- `r`: refresh device list
- `up/down` or `k/j`: select device
- `+` / `-`: change DPI in steps of 100
- `d`: enter custom DPI X/Y
- `s`: switch to next DPI stage (on devices with `dpi-stages`)
- `n`: set DPI stage count (1-5) on selected device
- `p`: cycle poll-rate

## Add a New Model

1. Create a file in `razecli/models/`, for example `viper_v2_pro.py`.
2. Export `MODEL = ModelSpec(...)` with USB IDs and constraints.
3. Done: the registry auto-loads the model.

Example:

```python
from razecli.models.base import ModelSpec

MODEL = ModelSpec(
    slug="viper-v2-pro",
    name="Razer Viper V2 Pro",
    usb_ids=((0x1532, 0x00A5), (0x1532, 0x00A6)),
    dpi_min=100,
    dpi_max=30000,
    supported_poll_rates=(125, 500, 1000),
)
```

## Additional Notes

- Backend auto-priority is `rawhid` > `macos-ble` > `hidapi` > `macos-profiler`.
- If multiple devices match, pass `--device`.
- `rawhid` collapses transport variants by default (priority: USB > dongle > BT). Use `--all-transports` to see each endpoint.
- `tui` defaults to `deathadder-v2-pro` filter. Use `razecli tui --all-models` to view everything detected.
- `macos-profiler` is detect-only and cannot write DPI, DPI stages, poll-rate, or battery.
- `macos-ble` targets BT reverse engineering for `008E` and reuses Razer packet framing over vendor GATT.
- DeathAdder V2 Pro supports up to 5 DPI stages per active onboard profile bank.
- The bottom DPI button/LED can switch onboard banks; stage count/values may differ between banks.
- `007C` (USB) and `007D` (dongle) transport mirroring is disabled by default.
- Enable mirror only when needed: `RAZECLI_TRANSPORT_MIRROR=1`.
- Bluetooth endpoint `008E` is excluded from mirror mode.
- Optional USB/BT autosync can be enabled with `RAZECLI_AUTOSYNC=1`.
- See [BLE reverse engineering plan](docs/ble_reverse_engineering_plan.md) for capture workflow.
