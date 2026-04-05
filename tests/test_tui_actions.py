import unittest

from razecli.tui_actions import TuiActionsMixin
from razecli.types import DetectedDevice


class _DummyController(TuiActionsMixin):
    def __init__(self) -> None:
        self.called = False
        self.received = None

    def _set_dpi_profile_count(self, stdscr) -> None:  # type: ignore[override]
        self.called = True
        self.received = stdscr


class _DummyBackend:
    def __init__(self) -> None:
        self.raise_rgb = False
        self.raise_button_mapping = False
        self.rgb_state = {
            "mode": "breathing",
            "brightness": 60,
            "color": "00ff00",
            "modes_supported": ["off", "static", "breathing"],
        }
        self.button_mapping_state = {
            "mapping": {
                "left_click": "mouse:left",
                "right_click": "mouse:right",
                "side_1": "mouse:back",
                "side_2": "mouse:forward",
            },
            "buttons_supported": ["left_click", "right_click", "side_1", "side_2"],
            "actions_suggested": ["mouse:left", "mouse:right", "mouse:back", "mouse:forward"],
        }

    def get_rgb(self, _device):
        if self.raise_rgb:
            raise RuntimeError("rgb failed")
        return dict(self.rgb_state)

    def get_button_mapping(self, _device):
        if self.raise_button_mapping:
            raise RuntimeError("button mapping failed")
        return dict(self.button_mapping_state)


class _DummyService:
    def __init__(self, backend: _DummyBackend) -> None:
        self._backend = backend

    def resolve_backend(self, _device):
        return self._backend


class _DummyCacheController(TuiActionsMixin):
    def __init__(self, backend: _DummyBackend) -> None:
        self.service = _DummyService(backend)
        self._rgb_cache = {}
        self._button_mapping_cache = {}


class TuiActionsTest(unittest.TestCase):
    @staticmethod
    def _device() -> DetectedDevice:
        return DetectedDevice(
            identifier="dev-1",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"rgb", "button-mapping"},
        )

    def test_edit_dpi_levels_delegates_to_profile_count_editor(self):
        controller = _DummyController()
        sentinel = object()
        controller._edit_dpi_levels(sentinel)
        self.assertTrue(controller.called)
        self.assertIs(controller.received, sentinel)

    def test_read_rgb_state_for_ui_caches_loaded_state(self):
        backend = _DummyBackend()
        controller = _DummyCacheController(backend)
        device = self._device()

        payload = controller._read_rgb_state_for_ui(device)

        self.assertEqual(payload["mode"], "breathing")
        self.assertIn(device.identifier, controller._rgb_cache)
        self.assertEqual(controller._rgb_cache[device.identifier]["mode"], "breathing")

    def test_read_button_mapping_for_ui_caches_loaded_state(self):
        backend = _DummyBackend()
        controller = _DummyCacheController(backend)
        device = self._device()

        payload = controller._read_button_mapping_for_ui(device)

        self.assertIn("mapping", payload)
        self.assertIn(device.identifier, controller._button_mapping_cache)
        self.assertIn("mapping", controller._button_mapping_cache[device.identifier])

    def test_read_rgb_state_for_ui_caches_fallback_scaffold_on_error(self):
        backend = _DummyBackend()
        backend.raise_rgb = True
        controller = _DummyCacheController(backend)
        device = self._device()

        payload = controller._read_rgb_state_for_ui(device)

        self.assertIn("mode", payload)
        self.assertIn(device.identifier, controller._rgb_cache)
        self.assertIn("mode", controller._rgb_cache[device.identifier])

    def test_read_button_mapping_for_ui_caches_fallback_scaffold_on_error(self):
        backend = _DummyBackend()
        backend.raise_button_mapping = True
        controller = _DummyCacheController(backend)
        device = self._device()

        payload = controller._read_button_mapping_for_ui(device)

        self.assertIn("mapping", payload)
        self.assertIn(device.identifier, controller._button_mapping_cache)
        self.assertIn("mapping", controller._button_mapping_cache[device.identifier])


if __name__ == "__main__":
    unittest.main()
