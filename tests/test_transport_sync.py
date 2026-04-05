import unittest

from razecli.model_registry import ModelRegistry
from razecli.transport_sync import iter_transport_mirror_targets
from razecli.types import DetectedDevice


class _FakeService:
    def __init__(self, devices):
        self._devices = devices
        self.registry = ModelRegistry.load()

    def discover_devices(self, model_filter=None, collapse_transports=True):  # noqa: ARG002
        return list(self._devices)


class TransportSyncTest(unittest.TestCase):
    def test_targets_include_stable_rawhid_peers_only(self):
        source = DetectedDevice(
            identifier="rawhid:1532:007C",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x007C,
            backend="rawhid",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "dpi-stages", "poll-rate"},
            backend_handle={},
        )
        peer_dongle = DetectedDevice(
            identifier="rawhid:1532:007D",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x007D,
            backend="rawhid",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "dpi-stages", "poll-rate"},
            backend_handle={},
        )
        bt_experimental = DetectedDevice(
            identifier="rawhid:1532:008E",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="rawhid",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "dpi-stages", "poll-rate"},
            backend_handle={"profile": type("P", (), {"experimental": True})()},
        )
        other_model = DetectedDevice(
            identifier="rawhid:1532:0084",
            name="DeathAdder V2",
            vendor_id=0x1532,
            product_id=0x0084,
            backend="rawhid",
            model_id="deathadder-v2",
            capabilities={"dpi"},
            backend_handle={},
        )

        service = _FakeService([source, peer_dongle, bt_experimental, other_model])
        targets = list(
            iter_transport_mirror_targets(
                service,
                source,
                required_capability="dpi",
            )
        )
        self.assertEqual([device.identifier for device in targets], ["rawhid:1532:007D"])


if __name__ == "__main__":
    unittest.main()
