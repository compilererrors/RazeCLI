"""BLE probing compatibility facade.

Implementation is split into `razecli.ble.alias`, `razecli.ble.discovery`,
and `razecli.ble.transport`.
"""

from razecli.ble.alias import ble_alias_resolve
from razecli.ble.alias_store import _remember_alias, ble_alias_clear, ble_alias_list
from razecli.ble.constants import (
    DEFAULT_BLE_ALIAS_PATH,
    DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
    DEFAULT_RAZER_BT_SERVICE_UUID,
    DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
)
from razecli.ble.discovery import (
    _auto_resolve_corebluetooth_address,
    _resolve_device_async,
    _select_vendor_gatt_path,
    discover_vendor_gatt_path,
    probe_ble_services,
    scan_ble_devices,
)
from razecli.ble.transport import (
    _chunk_bytes,
    _normalize_vendor_key,
    _parse_razer_vendor_notify,
    ble_raw_transceive,
    ble_vendor_transceive,
)
from razecli.ble.common import parse_hex_payload

__all__ = [
    "DEFAULT_BLE_ALIAS_PATH",
    "DEFAULT_RAZER_BT_READ_CHAR_UUIDS",
    "DEFAULT_RAZER_BT_SERVICE_UUID",
    "DEFAULT_RAZER_BT_WRITE_CHAR_UUID",
    "_auto_resolve_corebluetooth_address",
    "_chunk_bytes",
    "_normalize_vendor_key",
    "_parse_razer_vendor_notify",
    "_remember_alias",
    "_resolve_device_async",
    "_select_vendor_gatt_path",
    "ble_alias_clear",
    "ble_alias_list",
    "ble_alias_resolve",
    "ble_raw_transceive",
    "ble_vendor_transceive",
    "discover_vendor_gatt_path",
    "parse_hex_payload",
    "probe_ble_services",
    "scan_ble_devices",
]
