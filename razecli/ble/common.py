"""Common BLE utility helpers."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from razecli.errors import RazeCliError


def _bytes_to_hex(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, (bytes, bytearray)):
        return data.hex()
    try:
        payload = bytes(data)
    except Exception:
        return str(data)
    return payload.hex()


def parse_hex_payload(value: str) -> bytes:
    text = str(value or "").strip().lower()
    if not text:
        raise RazeCliError("Empty payload. Provide a hex string, for example '00 ff 01' or '00ff01'.")

    for token in ("0x", " ", "\t", "\n", "\r", ",", ":", "-", "_"):
        text = text.replace(token, "")

    if not text:
        raise RazeCliError("Payload does not contain any hex bytes")
    if len(text) % 2 != 0:
        raise RazeCliError("Hex payload must contain an even number of characters")
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise RazeCliError(f"Invalid hex payload: {value}") from exc


def _normalize_uuid(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_uuid_list(values: Optional[Sequence[str]]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = _normalize_uuid(value)
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _match_name(device_name: Optional[str], query: Optional[str]) -> bool:
    if not query:
        return True
    if not device_name:
        return False
    return query.lower() in device_name.lower()


def _normalize_address(value: Optional[str]) -> str:
    text = str(value or "")
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _format_exc(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return exc.__class__.__name__


def _is_mac_like_address(value: str) -> bool:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 6:
        return False
    for part in parts:
        if len(part) != 2:
            return False
        try:
            int(part, 16)
        except ValueError:
            return False
    return True
