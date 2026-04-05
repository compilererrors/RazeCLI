import time
import unittest

from razecli.tui_controller import TuiController
from razecli.types import DetectedDevice


class _FakeBackend:
    def __init__(self) -> None:
        self.rgb_reads = 0
        self.button_reads = 0
        self.fail_button_reads_remaining = 0
        self.read_delay_s = 0.0

    def get_rgb(self, _device):
        if self.read_delay_s > 0:
            time.sleep(self.read_delay_s)
        self.rgb_reads += 1
        return {
            "mode": "static",
            "brightness": 55,
            "color": "112233",
            "modes_supported": ["off", "static", "breathing"],
        }

    def get_button_mapping(self, _device):
        if self.read_delay_s > 0:
            time.sleep(self.read_delay_s)
        self.button_reads += 1
        if self.fail_button_reads_remaining > 0:
            self.fail_button_reads_remaining -= 1
            raise RuntimeError("transient button mapping read failure")
        return {
            "mapping": {"side_1": "mouse:back", "side_2": "mouse:forward"},
            "buttons_supported": ["side_1", "side_2"],
            "actions_suggested": ["mouse:back", "mouse:forward"],
        }


class _FakeService:
    def __init__(self, backend: _FakeBackend) -> None:
        self._backend = backend

    def resolve_backend(self, _device):
        return self._backend


class TuiControllerTest(unittest.TestCase):
    @staticmethod
    def _device(*, backend: str = "macos-ble") -> DetectedDevice:
        return DetectedDevice(
            identifier="dev-1",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend=backend,
            model_id="deathadder-v2-pro",
            capabilities={"rgb", "button-mapping"},
        )

    @staticmethod
    def _wait_until(predicate, *, timeout_s: float = 1.0) -> bool:
        due = time.monotonic() + timeout_s
        while time.monotonic() < due:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_feature_prefetch_loads_rgb_and_buttons_for_selected_device(self):
        backend = _FakeBackend()
        controller = TuiController(service=_FakeService(backend))
        controller.devices = [self._device()]
        controller.selected_index = 0

        controller._maybe_schedule_feature_prefetch()
        loaded = self._wait_until(
            lambda: "dev-1" in controller._rgb_cache and "dev-1" in controller._button_mapping_cache
        )

        self.assertTrue(loaded)
        self.assertEqual(controller._rgb_cache["dev-1"]["mode"], "static")
        self.assertIn("mapping", controller._button_mapping_cache["dev-1"])
        self.assertEqual(backend.rgb_reads, 1)
        self.assertEqual(backend.button_reads, 1)

    def test_feature_prefetch_runs_only_once_per_feature(self):
        backend = _FakeBackend()
        controller = TuiController(service=_FakeService(backend))
        controller.devices = [self._device()]
        controller.selected_index = 0

        controller._maybe_schedule_feature_prefetch()
        self.assertTrue(
            self._wait_until(lambda: "dev-1" in controller._rgb_cache and "dev-1" in controller._button_mapping_cache)
        )
        controller._maybe_schedule_feature_prefetch()
        time.sleep(0.05)

        self.assertEqual(backend.rgb_reads, 1)
        self.assertEqual(backend.button_reads, 1)

    def test_feature_prefetch_skips_detect_only_backend(self):
        backend = _FakeBackend()
        controller = TuiController(service=_FakeService(backend))
        controller.devices = [self._device(backend="macos-profiler")]
        controller.selected_index = 0

        controller._maybe_schedule_feature_prefetch()
        time.sleep(0.05)

        self.assertNotIn("dev-1", controller._rgb_cache)
        self.assertNotIn("dev-1", controller._button_mapping_cache)
        self.assertEqual(backend.rgb_reads, 0)
        self.assertEqual(backend.button_reads, 0)

    def test_feature_prefetch_retries_buttons_after_transient_failure(self):
        backend = _FakeBackend()
        backend.fail_button_reads_remaining = 1
        controller = TuiController(service=_FakeService(backend))
        controller._feature_prefetch_retry_delay_s = 0.01
        controller.devices = [self._device()]
        controller.selected_index = 0

        controller._maybe_schedule_feature_prefetch()
        self.assertTrue(self._wait_until(lambda: backend.button_reads >= 1))
        self.assertNotIn("dev-1", controller._button_mapping_cache)

        time.sleep(0.02)
        controller._maybe_schedule_feature_prefetch()
        self.assertTrue(self._wait_until(lambda: "dev-1" in controller._button_mapping_cache))
        self.assertGreaterEqual(backend.button_reads, 2)

    def test_feature_prefetch_marks_and_clears_inflight_feature_flags(self):
        backend = _FakeBackend()
        backend.read_delay_s = 0.05
        controller = TuiController(service=_FakeService(backend))
        controller.devices = [self._device()]
        controller.selected_index = 0

        controller._maybe_schedule_feature_prefetch()
        self.assertTrue(controller._is_feature_prefetch_inflight("dev-1", "rgb"))
        self.assertTrue(controller._is_feature_prefetch_inflight("dev-1", "button-mapping"))

        self.assertTrue(
            self._wait_until(lambda: "dev-1" in controller._rgb_cache and "dev-1" in controller._button_mapping_cache)
        )
        self.assertTrue(self._wait_until(lambda: not controller._is_feature_prefetch_inflight("dev-1", "rgb")))
        self.assertTrue(
            self._wait_until(lambda: not controller._is_feature_prefetch_inflight("dev-1", "button-mapping"))
        )


if __name__ == "__main__":
    unittest.main()
