"""Backend abstractions."""

from abc import ABC, abstractmethod
from typing import List, Sequence, Tuple

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
