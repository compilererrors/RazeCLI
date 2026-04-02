"""Shared data structures used across CLI and backends."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


@dataclass
class DetectedDevice:
    identifier: str
    name: str
    vendor_id: int
    product_id: int
    backend: str
    serial: Optional[str] = None
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    capabilities: Set[str] = field(default_factory=set)
    backend_handle: Any = field(default=None, repr=False)

    def usb_id(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.identifier,
            "name": self.name,
            "usb_id": self.usb_id(),
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "serial": self.serial,
            "backend": self.backend,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "capabilities": sorted(self.capabilities),
        }
