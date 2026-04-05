"""BLE discovery and address resolution."""

from __future__ import annotations

import asyncio
import os
import platform
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from razecli.backends.macos_profiler_backend import MacOSProfilerBackend
from razecli.ble.alias_store import _load_alias_cache, _remember_alias
from razecli.ble.common import (
    _bytes_to_hex,
    _format_exc,
    _is_mac_like_address,
    _match_name,
    _normalize_address,
    _normalize_uuid,
)
from razecli.ble.constants import (
    DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
    DEFAULT_RAZER_BT_SERVICE_UUID,
    DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
)
from razecli.ble.sync_runner import run_ble_sync
from razecli.errors import BackendUnavailableError, RazeCliError

_CB_DELEGATE_CLASS: Optional[type] = None


def _macos_profiler_rows() -> List[Dict[str, Any]]:
    if platform.system() != "Darwin":
        return []
    backend = MacOSProfilerBackend()
    rows: List[Dict[str, Any]] = []
    for device in backend.detect():
        if device.backend != "macos-profiler":
            continue
        if not device.identifier.startswith("macos-bt:"):
            continue
        rows.append(
            {
                "name": device.name or "",
                "address": device.serial or device.identifier.split(":", 1)[-1],
                "rssi": None,
                "source": "system-profiler",
            }
        )
    return rows


def _merge_rows(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in primary + secondary:
        name = str(row.get("name") or "")
        address = str(row.get("address") or "")
        key = (name.lower(), address.lower())
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
            continue
        existing_sources = {str(existing.get("source") or "").strip(), str(row.get("source") or "").strip()}
        existing["source"] = "+".join(sorted(filter(None, existing_sources)))
    rows = list(merged.values())
    rows.sort(key=lambda row: (row.get("name") or "", row.get("address") or ""))
    return rows


def _device_display_name(device: Any, adv_data: Any) -> str:
    direct = str(getattr(device, "name", "") or "").strip()
    if direct:
        return direct
    local_name = str(getattr(adv_data, "local_name", "") or "").strip()
    if local_name:
        return local_name
    return ""


def _adv_service_uuids(adv_data: Any) -> List[str]:
    values = getattr(adv_data, "service_uuids", None)
    if values is None:
        return []
    return sorted(str(value) for value in values)


def _adv_manufacturer_ids(adv_data: Any) -> List[int]:
    values = getattr(adv_data, "manufacturer_data", None)
    if isinstance(values, dict):
        ids: List[int] = []
        for key in values.keys():
            try:
                ids.append(int(key))
            except Exception:
                continue
        return sorted(set(ids))
    return []


def _match_query(device: Any, adv_data: Any, query: Optional[str]) -> bool:
    if not query:
        return True
    q = query.lower()
    candidates = [
        str(getattr(device, "name", "") or ""),
        str(getattr(adv_data, "local_name", "") or ""),
    ]
    return any(q in candidate.lower() for candidate in candidates if candidate)


def _candidate_base_score(device: Any, adv_data: Any) -> int:
    score = 0
    name = _device_display_name(device, adv_data).lower()
    local_name = str(getattr(adv_data, "local_name", "") or "").lower()

    if "razer" in name or "razer" in local_name:
        score += 40
    if "deathadder" in name or "deathadder" in local_name:
        score += 40
    if "da v2" in name or "da v2" in local_name:
        score += 40
    if "jabra" in name or "jabra" in local_name:
        score -= 40

    manufacturer_ids = _adv_manufacturer_ids(adv_data)
    if 0x1532 in manufacturer_ids:
        score += 70
    if 0x004C in manufacturer_ids:
        score -= 5

    uuids = [uuid.lower() for uuid in _adv_service_uuids(adv_data)]
    if "00001812-0000-1000-8000-00805f9b34fb" in uuids:
        score += 20
    return score


def _ble_bruteforce_enabled() -> bool:
    value = str(os.getenv("RAZECLI_BLE_BRUTEFORCE", "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _get_macos_central_delegate_class(objc_module: Any, nsobject_cls: Any) -> Any:
    global _CB_DELEGATE_CLASS
    if _CB_DELEGATE_CLASS is not None:
        return _CB_DELEGATE_CLASS

    class_name = "RazeCLICentralDelegate"
    try:
        _CB_DELEGATE_CLASS = objc_module.lookUpClass(class_name)
        return _CB_DELEGATE_CLASS
    except Exception:
        pass

    class RazeCLICentralDelegate(nsobject_cls):  # type: ignore[misc]
        def init(self):  # type: ignore[override]
            self = objc_module.super(RazeCLICentralDelegate, self).init()
            if self is None:
                return None
            self.state = None
            return self

        def centralManagerDidUpdateState_(self, central):  # pragma: no cover - runtime callback
            try:
                self.state = int(central.state())
            except Exception:
                self.state = None

    _CB_DELEGATE_CLASS = RazeCLICentralDelegate
    return _CB_DELEGATE_CLASS


def _macos_connected_peripherals_for_vendor_service(timeout: float = 1.0) -> List[Dict[str, str]]:
    if platform.system() != "Darwin":
        return []

    try:
        import objc  # type: ignore
        from Foundation import NSDate, NSObject, NSRunLoop  # type: ignore
        import CoreBluetooth  # type: ignore
    except Exception:
        return []

    try:
        delegate_cls = _get_macos_central_delegate_class(objc, NSObject)
        delegate = delegate_cls.alloc().init()
        central = CoreBluetooth.CBCentralManager.alloc().initWithDelegate_queue_options_(
            delegate,
            None,
            None,
        )
    except Exception:
        return []

    try:
        deadline = time.monotonic() + max(0.2, float(timeout))
        while getattr(delegate, "state", None) is None and time.monotonic() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))

        try:
            powered_on = int(CoreBluetooth.CBManagerStatePoweredOn)
        except Exception:
            powered_on = 5

        if getattr(delegate, "state", None) != powered_on:
            _ = central
            return []

        svc_uuid = CoreBluetooth.CBUUID.UUIDWithString_(DEFAULT_RAZER_BT_SERVICE_UUID)
        peripherals = central.retrieveConnectedPeripheralsWithServices_([svc_uuid]) or []
    except Exception:
        _ = central
        return []

    rows: List[Dict[str, str]] = []
    for peripheral in peripherals:
        try:
            identifier = str(peripheral.identifier().UUIDString())
        except Exception:
            identifier = ""
        if not identifier:
            continue
        try:
            name = str(peripheral.name() or "")
        except Exception:
            name = ""
        rows.append(
            {
                "address": identifier,
                "name": name,
                "source": "corebluetooth-connected",
            }
        )
    _ = central
    rows.sort(key=lambda row: (row.get("name", ""), row.get("address", "")))
    return rows


async def _macos_connected_ble_devices_for_vendor_service_async(
    timeout: float = 1.0,
) -> List[Dict[str, Any]]:
    if platform.system() != "Darwin":
        return []

    try:
        from CoreBluetooth import CBUUID  # type: ignore
        from bleak.backends.corebluetooth.CentralManagerDelegate import (  # type: ignore
            CentralManagerDelegate,
        )
        from bleak.backends.device import BLEDevice  # type: ignore
    except Exception:
        return []

    try:
        manager = CentralManagerDelegate.alloc().init()
    except Exception:
        return []

    try:
        svc_uuid = CBUUID.UUIDWithString_(DEFAULT_RAZER_BT_SERVICE_UUID)
        peripherals = manager.central_manager.retrieveConnectedPeripheralsWithServices_([svc_uuid]) or []
    except Exception:
        return []

    rows: List[Dict[str, Any]] = []
    for peripheral in peripherals:
        try:
            address = str(peripheral.identifier().UUIDString())
        except Exception:
            address = ""
        if not address:
            continue
        try:
            name = str(peripheral.name() or "")
        except Exception:
            name = ""
        try:
            ble_device = BLEDevice(address, name or None, (peripheral, manager))
        except Exception:
            ble_device = None
        row: Dict[str, Any] = {
            "address": address,
            "name": name,
            "source": "corebluetooth-connected",
        }
        if ble_device is not None:
            row["ble_device"] = ble_device
        rows.append(row)

    rows.sort(key=lambda row: (row.get("name", ""), row.get("address", "")))
    _ = timeout
    return rows


def _service_uuid_list(services: Any) -> List[str]:
    uuids: List[str] = []
    try:
        for service in services:
            uuids.append(str(service.uuid).lower())
    except Exception:
        return []
    return sorted(set(uuids))


def _service_score(service_uuids: Sequence[str]) -> int:
    score = 0
    normalized = [uuid.lower() for uuid in service_uuids]
    if "00001812-0000-1000-8000-00805f9b34fb" in normalized:
        score += 80
    if "0000180f-0000-1000-8000-00805f9b34fb" in normalized:
        score += 20
    if any(uuid.startswith("0000ff") for uuid in normalized):
        score += 30
    if any(uuid.startswith("a3c8") for uuid in normalized):
        score += 25
    return score


async def _discover_with_adv_async(timeout: float) -> List[Tuple[Any, Any]]:
    try:
        from bleak import BleakScanner  # type: ignore
    except Exception as exc:
        raise BackendUnavailableError(
            "BLE probe requires bleak. Install with: pip install 'bleak>=0.22' "
            "or pip install -e '.[ble]'"
        ) from exc

    try:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except TypeError:
        try:
            devices = await BleakScanner.discover(timeout=timeout)
        except Exception as exc:
            raise RazeCliError(f"BLE scan failed: {exc}") from exc
        return [(device, None) for device in devices]
    except Exception as exc:
        raise RazeCliError(f"BLE scan failed: {exc}") from exc

    if isinstance(discovered, dict):
        return list(discovered.values())
    return []


async def _collect_service_rows(client: Any, read_values: bool) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    services = client.services
    for service in services:
        service_row = {
            "uuid": service.uuid,
            "description": service.description or "",
            "characteristics": [],
        }

        for char in service.characteristics:
            row: Dict[str, Any] = {
                "uuid": char.uuid,
                "description": char.description or "",
                "properties": sorted(str(prop) for prop in char.properties),
            }
            if read_values and "read" in {str(prop) for prop in char.properties}:
                try:
                    value = await client.read_gatt_char(char.uuid)
                    row["value_hex"] = _bytes_to_hex(value)
                except Exception as exc:
                    row["read_error"] = str(exc)
            service_row["characteristics"].append(row)

        rows.append(service_row)
    return rows


def _select_vendor_gatt_path(services: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates: List[Tuple[int, Dict[str, Any]]] = []

    for service in services:
        service_uuid = _normalize_uuid(str(service.get("uuid") or ""))
        if not service_uuid:
            continue

        chars = service.get("characteristics", [])
        if not isinstance(chars, list):
            continue

        write_chars: List[str] = []
        read_chars: List[str] = []
        notify_chars: List[str] = []
        for char in chars:
            if not isinstance(char, dict):
                continue
            char_uuid = _normalize_uuid(str(char.get("uuid") or ""))
            if not char_uuid:
                continue
            props = {str(value).lower() for value in char.get("properties", [])}
            if "write" in props or "write-without-response" in props:
                write_chars.append(char_uuid)
            if "notify" in props or "indicate" in props:
                notify_chars.append(char_uuid)
            if "read" in props:
                read_chars.append(char_uuid)

        if not write_chars:
            continue
        response_chars: List[str] = []
        for char_uuid in notify_chars + read_chars:
            if char_uuid not in response_chars:
                response_chars.append(char_uuid)
        if not response_chars:
            continue

        score = 0
        if service_uuid == DEFAULT_RAZER_BT_SERVICE_UUID:
            score += 300
        if service_uuid.startswith("5240"):
            score += 120
        if len(chars) == 3:
            score += 40
        score += min(len(response_chars), 4) * 15
        if write_chars and response_chars:
            score += 20

        preferred_write = write_chars[0]
        preferred_reads = response_chars[:2]
        if DEFAULT_RAZER_BT_WRITE_CHAR_UUID in write_chars:
            preferred_write = DEFAULT_RAZER_BT_WRITE_CHAR_UUID
        preferred_reads = [
            uuid for uuid in DEFAULT_RAZER_BT_READ_CHAR_UUIDS if uuid in response_chars
        ] or preferred_reads

        candidates.append(
            (
                score,
                {
                    "service_uuid": service_uuid,
                    "write_char_uuid": preferred_write,
                    "read_char_uuids": preferred_reads,
                },
            )
        )

    if not candidates:
        return None

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates[0][1]


def discover_vendor_gatt_path(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
) -> Dict[str, Any]:
    payload = probe_ble_services(
        address=address,
        name_query=name_query,
        timeout=float(timeout),
        read_values=False,
    )
    services = payload.get("services", [])
    if not isinstance(services, list):
        raise RazeCliError("BLE service discovery returned invalid service data")

    path = _select_vendor_gatt_path(services)
    if path is None:
        raise RazeCliError("Could not find a writable vendor-like GATT path on this device")

    result = dict(path)
    result["address"] = payload.get("address")
    if payload.get("resolved_from"):
        result["resolved_from"] = payload.get("resolved_from")
    return result


def _try_decode_text(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        payload = bytes(raw)
    else:
        try:
            payload = bytes(raw)
        except Exception:
            return str(raw)
    if not payload:
        return ""
    try:
        return payload.decode("utf-8", errors="ignore").strip("\x00").strip()
    except Exception:
        return None


async def _read_named_char(client: Any, uuid: str) -> Optional[str]:
    try:
        value = await client.read_gatt_char(uuid)
    except Exception:
        return None
    decoded = _try_decode_text(value)
    if decoded is None:
        return None
    return decoded.strip()


async def _read_candidate_identity(client: Any, service_uuids: Sequence[str]) -> Dict[str, Optional[str]]:
    identity: Dict[str, Optional[str]] = {
        "device_name": None,
        "manufacturer_name": None,
        "model_number": None,
    }

    # Try standard GATT Device Information / GAP chars first.
    device_name_uuid = "00002a00-0000-1000-8000-00805f9b34fb"
    manufacturer_uuid = "00002a29-0000-1000-8000-00805f9b34fb"
    model_uuid = "00002a24-0000-1000-8000-00805f9b34fb"

    identity["device_name"] = await _read_named_char(client, device_name_uuid)
    identity["manufacturer_name"] = await _read_named_char(client, manufacturer_uuid)
    identity["model_number"] = await _read_named_char(client, model_uuid)

    if identity["device_name"]:
        identity["device_name"] = identity["device_name"][:128]
    if identity["manufacturer_name"]:
        identity["manufacturer_name"] = identity["manufacturer_name"][:128]
    if identity["model_number"]:
        identity["model_number"] = identity["model_number"][:128]

    _ = service_uuids
    return identity


def _identity_score(identity: Dict[str, Optional[str]]) -> int:
    score = 0
    text_parts = [
        str(identity.get("device_name") or "").lower(),
        str(identity.get("manufacturer_name") or "").lower(),
        str(identity.get("model_number") or "").lower(),
    ]
    joined = " ".join(text_parts)
    if "razer" in joined:
        score += 140
    if "deathadder" in joined:
        score += 120
    if "da v2" in joined or "v2 pro" in joined:
        score += 90
    if "jabra" in joined:
        score -= 80
    return score


async def _auto_resolve_corebluetooth_address(
    *,
    mac_address: str,
    timeout: float,
    allow_alias_cache: bool = True,
) -> Dict[str, Any]:
    try:
        from bleak import BleakClient  # type: ignore
    except Exception as exc:
        raise BackendUnavailableError(
            "BLE probe requires bleak. Install with: pip install 'bleak>=0.22' "
            "or pip install -e '.[ble]'"
        ) from exc

    profiler_name = ""
    normalized_mac = _normalize_address(mac_address)
    for row in _macos_profiler_rows():
        if _normalize_address(str(row.get("address") or "")) == normalized_mac:
            profiler_name = str(row.get("name") or "").strip()
            break

    if allow_alias_cache:
        aliases = _load_alias_cache()
        alias = aliases.get(normalized_mac, {})
        alias_address = str(alias.get("resolved_address") or "").strip()
        if alias_address and "-" in alias_address:
            try:
                alias_timeout = max(0.8, min(float(timeout), 1.8))
                async with BleakClient(alias_address, timeout=alias_timeout):
                    return {
                        "requested_mac": mac_address,
                        "resolved_address": alias_address,
                        "candidates": [
                            {
                                "address": alias_address,
                                "name": profiler_name or "",
                                "score": 260,
                                "connect_ok": True,
                                "source": "alias-cache",
                            }
                        ],
                    }
            except Exception:
                pass

    connected = await _macos_connected_ble_devices_for_vendor_service_async(timeout=min(timeout, 1.2))
    if not connected:
        connected = _macos_connected_peripherals_for_vendor_service(timeout=min(timeout, 1.2))
    connected_hints: List[Dict[str, Any]] = []
    if connected:
        matches = connected
        if profiler_name:
            named = [
                row
                for row in connected
                if _match_name(str(row.get("name") or ""), profiler_name)
            ]
            if named:
                matches = named
        if len(matches) == 1:
            resolved = str(matches[0].get("address") or "").strip()
            if resolved:
                verify_timeout = max(1.0, min(float(timeout), 2.5))
                connect_target: Any = matches[0].get("ble_device") or resolved
                try:
                    async with BleakClient(connect_target, timeout=verify_timeout):
                        return {
                            "requested_mac": mac_address,
                            "resolved_address": resolved,
                            "resolved_device": matches[0].get("ble_device"),
                            "candidates": [
                                {
                                    "address": str(row.get("address") or ""),
                                    "name": str(row.get("name") or ""),
                                    "score": 220 if row is matches[0] else 140,
                                    "connect_ok": row is matches[0],
                                    "source": str(row.get("source") or "corebluetooth-connected"),
                                }
                                for row in connected
                            ],
                        }
                except Exception as exc:
                    connected_hints = [
                        {
                            "address": str(row.get("address") or ""),
                            "name": str(row.get("name") or ""),
                            "local_name": "",
                            "manufacturer_ids": [],
                            "adv_service_uuids": [],
                            "base_score": 120 if row is matches[0] else 80,
                            "score": 120 if row is matches[0] else 80,
                            "connect_ok": False,
                            "source": str(row.get("source") or "corebluetooth-connected"),
                            "error": _format_exc(exc) if row is matches[0] else "",
                        }
                        for row in connected
                    ]

    discovered = await _discover_with_adv_async(timeout=timeout)
    candidates: List[Dict[str, Any]] = list(connected_hints)

    for device, adv_data in discovered:
        address = str(getattr(device, "address", "") or "")
        if "-" not in address:
            continue
        base_score = _candidate_base_score(device, adv_data)
        row: Dict[str, Any] = {
            "address": address,
            "name": _device_display_name(device, adv_data),
            "local_name": str(getattr(adv_data, "local_name", "") or ""),
            "manufacturer_ids": _adv_manufacturer_ids(adv_data),
            "adv_service_uuids": _adv_service_uuids(adv_data),
            "base_score": base_score,
            "score": base_score,
            "connect_ok": False,
        }
        if not any(str(existing.get("address") or "") == address for existing in candidates):
            candidates.append(row)

    candidates.sort(key=lambda row: int(row.get("base_score", 0)), reverse=True)
    hinted = [row for row in candidates if int(row.get("base_score", 0)) >= 20]
    bruteforce = _ble_bruteforce_enabled()
    if hinted:
        probe_candidates = hinted
    elif bruteforce:
        probe_candidates = candidates
    else:
        return {
            "requested_mac": mac_address,
            "resolved_address": None,
            "reason": "no_hints",
            "candidates": candidates[:6],
        }

    probe_limit = min(6 if bruteforce else 4, len(probe_candidates))
    per_attempt_timeout = max(1.0, min(float(timeout), 2.0))

    for row in probe_candidates[:probe_limit]:
        address = str(row["address"])
        try:
            async with BleakClient(address, timeout=per_attempt_timeout) as client:
                row["connect_ok"] = True
                service_uuids = _service_uuid_list(client.services)
                row["service_uuids"] = service_uuids
                identity = await _read_candidate_identity(client, service_uuids)
                row["identity"] = identity
                row["score"] = (
                    int(row["base_score"])
                    + _service_score(service_uuids)
                    + _identity_score(identity)
                )
        except Exception as exc:
            row["connect_ok"] = False
            row["error"] = str(exc)
            row["score"] = int(row["base_score"]) - 3

    ranked = sorted(probe_candidates[:probe_limit], key=lambda row: int(row.get("score", 0)), reverse=True)
    connected = [row for row in ranked if bool(row.get("connect_ok"))]

    resolved_address: Optional[str] = None
    if connected:
        best = connected[0]
        best_identity_score = _identity_score(best.get("identity", {})) if isinstance(best.get("identity"), dict) else 0
        if len(connected) == 1:
            resolved_address = str(best["address"])
        elif best_identity_score >= 120:
            resolved_address = str(best["address"])
        else:
            next_score = int(connected[1].get("score", 0))
            if int(best.get("score", 0)) - next_score >= 12:
                resolved_address = str(best["address"])

    return {
        "requested_mac": mac_address,
        "resolved_address": resolved_address,
        "candidates": ranked,
    }


def _candidate_preview(candidates: Sequence[Dict[str, Any]], *, limit: int = 6) -> str:
    return ", ".join(
        (
            f"{row.get('address')}[score={row.get('score')} ok={row.get('connect_ok')}"
            f" src={row.get('source') or '-'}"
            f" name={row.get('name') or row.get('local_name') or '-'}"
            f" id={((row.get('identity') or {}).get('manufacturer_name') or '-') if isinstance(row.get('identity'), dict) else '-'}"
            f" err={(str(row.get('error') or '-')[:64])}"
            "]"
        )
        for row in list(candidates)[:limit]
    )


def _retryable_connect_error(message: str) -> bool:
    text = str(message or "").lower()
    return ("not found" in text) or ("timeout" in text)


async def _scan_devices_async(timeout: float = 8.0) -> List[Dict[str, Any]]:
    discovered = await _discover_with_adv_async(timeout=timeout)
    rows: List[Dict[str, Any]] = []
    for device, adv_data in discovered:
        rows.append(
            {
                "name": _device_display_name(device, adv_data),
                "local_name": str(getattr(adv_data, "local_name", "") or ""),
                "address": getattr(device, "address", ""),
                "rssi": getattr(device, "rssi", None),
                "source": "bleak",
                "manufacturer_ids": _adv_manufacturer_ids(adv_data),
                "service_uuids": _adv_service_uuids(adv_data),
            }
        )
    return _merge_rows(rows, _macos_profiler_rows())


async def _resolve_device_async(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
) -> Any:
    if address:
        return address

    discovered = await _discover_with_adv_async(timeout=timeout)
    profiler_rows = _macos_profiler_rows()

    for device, adv_data in discovered:
        if _match_query(device, adv_data, name_query):
            return device

    if name_query:
        profiler_matches = [row for row in profiler_rows if _match_name(str(row.get("name") or ""), name_query)]
        if profiler_matches:
            profiler_names = [str(row.get("name") or "").strip() for row in profiler_matches]
            by_profile_name = [
                device
                for device, adv_data in discovered
                if any(_match_query(device, adv_data, profile_name) for profile_name in profiler_names if profile_name)
            ]
            if len(by_profile_name) == 1:
                return by_profile_name[0]

            razer_candidates = [
                device
                for device, adv_data in discovered
                if 0x1532 in _adv_manufacturer_ids(adv_data)
            ]
            if len(razer_candidates) == 1:
                return razer_candidates[0]

            if len(profiler_matches) == 1:
                profiler_address = str(profiler_matches[0].get("address") or "").strip()
                if profiler_address:
                    return profiler_address

            addresses = ", ".join(str(row.get("address") or "-") for row in profiler_matches[:5])
            discovered_addresses = ", ".join(
                str(getattr(device, "address", "") or "-") for device, _adv in discovered[:6]
            )
            raise RazeCliError(
                f"No BLE device from scan matched '{name_query}', "
                f"but system_profiler reports: {addresses}. "
                "Try `razecli ble scan` without --name and use a UUID address from scan results. "
                f"Example scan addresses: {discovered_addresses}"
            )
        raise RazeCliError(f"No BLE device name matched '{name_query}'")
    raise RazeCliError("No BLE device found. Provide --address or --name")


async def _probe_services_async(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
    read_values: bool,
) -> Dict[str, Any]:
    try:
        from bleak import BleakClient  # type: ignore
    except Exception as exc:
        raise BackendUnavailableError(
            "BLE probe requires bleak. Install with: pip install 'bleak>=0.22' "
            "or pip install -e '.[ble]'"
        ) from exc

    device = await _resolve_device_async(address=address, name_query=name_query, timeout=timeout)

    payload: Dict[str, Any] = {
        "name": getattr(device, "name", "") or "",
        "address": getattr(device, "address", "") if not isinstance(device, str) else device,
        "services": [],
    }

    target = str(payload.get("address") or "-")
    if isinstance(device, str) and _is_mac_like_address(device):
        connect_error: Optional[str] = None
        try:
            auto = await _auto_resolve_corebluetooth_address(mac_address=device, timeout=timeout)
        except Exception as exc:
            raise RazeCliError(
                f"BLE connect/probe failed for {target}: auto-resolution failed: {_format_exc(exc)}"
            ) from exc
        resolved_address = auto.get("resolved_address")
        resolved_device = auto.get("resolved_device")
        if resolved_address:
            try:
                connect_target: Any = resolved_device or str(resolved_address)
                async with BleakClient(connect_target, timeout=timeout) as client:
                    payload["resolved_from"] = device
                    payload["address"] = str(resolved_address)
                    payload["services"] = await _collect_service_rows(client, read_values=read_values)
                    payload["auto_resolution"] = {
                        "requested_mac": device,
                        "resolved_address": str(resolved_address),
                    }
                    _remember_alias(device, str(resolved_address))
                    return payload
            except Exception as exc:
                first_error = _format_exc(exc)
                connect_error = first_error
                if not _retryable_connect_error(first_error):
                    raise RazeCliError(
                        f"BLE connect/probe failed for {target}: {first_error}. "
                        "On macOS, CoreBluetooth UUIDs are often used instead of MAC addresses."
                    ) from exc

                try:
                    refreshed = await _auto_resolve_corebluetooth_address(
                        mac_address=device,
                        timeout=timeout,
                        allow_alias_cache=False,
                    )
                except Exception as refresh_resolve_exc:
                    raise RazeCliError(
                        f"BLE connect/probe failed for {target}: refresh auto-resolution failed: "
                        f"{_format_exc(refresh_resolve_exc)}"
                    ) from refresh_resolve_exc
                refreshed_address = refreshed.get("resolved_address")
                refreshed_device = refreshed.get("resolved_device")
                if refreshed_address:
                    try:
                        connect_target = refreshed_device or str(refreshed_address)
                        async with BleakClient(connect_target, timeout=timeout) as client:
                            payload["resolved_from"] = device
                            payload["address"] = str(refreshed_address)
                            payload["services"] = await _collect_service_rows(client, read_values=read_values)
                            payload["auto_resolution"] = {
                                "requested_mac": device,
                                "resolved_address": str(refreshed_address),
                            }
                            _remember_alias(device, str(refreshed_address))
                            return payload
                    except Exception as refresh_exc:
                        connect_error = f"{first_error}; refresh={_format_exc(refresh_exc)}"
                auto = refreshed

        candidates = auto.get("candidates", [])
        reason = str(auto.get("reason") or "")
        preview = _candidate_preview(candidates)
        has_connect_ok = any(bool(row.get("connect_ok")) for row in candidates)
        if has_connect_ok and connect_error:
            raise RazeCliError(
                f"BLE connect/probe failed for {target}: a candidate was found but connection failed ({connect_error}). "
                f"Candidates: {preview}"
            )
        if reason == "no_hints":
            raise RazeCliError(
                f"BLE connect/probe failed for {target}: device with address {device} was not found. "
                "No Razer hints were found in BLE scan data (name/manufacturer/service), so auto-resolution was stopped to avoid long probing. "
                "Put the mouse in active BT/pairing mode and scan again, or run "
                "`RAZECLI_BLE_BRUTEFORCE=1 razecli --json ble services --address <MAC> --timeout 15` "
                "for full candidate probing. "
                f"Candidates: {preview}"
            )
        raise RazeCliError(
            f"BLE connect/probe failed for {target}: device with address {device} was not found. "
            "On macOS, CoreBluetooth UUIDs are often used instead of MAC addresses. "
            f"Auto-resolution did not find a clear candidate. Candidates: {preview}"
        )

    try:
        async with BleakClient(device, timeout=timeout) as client:
            payload["services"] = await _collect_service_rows(client, read_values=read_values)
    except Exception as exc:
        raise RazeCliError(
            f"BLE connect/probe failed for {target}: {_format_exc(exc)}. "
            "On macOS, CoreBluetooth UUIDs are often used instead of MAC addresses."
        ) from exc

    return payload


def scan_ble_devices(timeout: float = 8.0) -> List[Dict[str, Any]]:
    return run_ble_sync(_scan_devices_async(timeout=timeout))


def probe_ble_services(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
    read_values: bool = False,
) -> Dict[str, Any]:
    return run_ble_sync(
        _probe_services_async(
            address=address,
            name_query=name_query,
            timeout=timeout,
            read_values=read_values,
        )
    )
