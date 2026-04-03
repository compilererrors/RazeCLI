"""Backend abstractions."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from razecli.errors import CapabilityUnsupportedError
from razecli.types import DetectedDevice


class Backend(ABC):
    name = "unknown"

    @abstractmethod
    def detect(self) -> List[DetectedDevice]:
        raise NotImplementedError

    def get_dpi(self, device: DetectedDevice) -> Tuple[int, int]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support DPI reads")

    def set_dpi(self, device: DetectedDevice, dpi_x: int, dpi_y: int) -> None:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support DPI writes")

    def get_dpi_stages(self, device: DetectedDevice) -> Tuple[int, Sequence[Tuple[int, int]]]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support DPI stages")

    def set_dpi_stages(self, device: DetectedDevice, active_stage: int, stages: Sequence[Tuple[int, int]]) -> None:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support DPI stage writes")

    def get_poll_rate(self, device: DetectedDevice) -> int:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support poll-rate reads")

    def set_poll_rate(self, device: DetectedDevice, hz: int) -> None:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support poll-rate writes")

    def get_supported_poll_rates(self, device: DetectedDevice) -> Sequence[int]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not expose supported poll rates")

    def get_battery(self, device: DetectedDevice) -> int:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support battery reads")

    def get_rgb(self, device: DetectedDevice) -> Dict[str, Any]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support RGB reads")

    def set_rgb(
        self,
        device: DetectedDevice,
        *,
        mode: str,
        brightness: Optional[int] = None,
        color: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support RGB writes")

    def get_button_mapping(self, device: DetectedDevice) -> Dict[str, Any]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support button-mapping reads")

    def set_button_mapping(
        self,
        device: DetectedDevice,
        *,
        button: str,
        action: str,
    ) -> Dict[str, Any]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support button-mapping writes")

    def reset_button_mapping(self, device: DetectedDevice) -> Dict[str, Any]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support button-mapping reset")

    def list_button_mapping_actions(self, device: DetectedDevice) -> Dict[str, Any]:
        raise CapabilityUnsupportedError(f"{self.name} backend does not support button-mapping action listing")
