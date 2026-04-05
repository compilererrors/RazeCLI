import unittest
from unittest.mock import patch

from razecli.model_registry import ModelRegistry


class ModelRegistryTest(unittest.TestCase):
    def test_loads_deathadder_v2_pro(self):
        registry = ModelRegistry.load()

        model = registry.get("deathadder-v2-pro")
        self.assertIsNotNone(model)
        assert model is not None

        self.assertEqual(model.name, "Razer DeathAdder V2 Pro")
        self.assertIn((0x1532, 0x007C), model.usb_ids)
        self.assertIn((0x1532, 0x007D), model.usb_ids)
        self.assertIn((0x1532, 0x008E), model.usb_ids)
        self.assertEqual(model.dpi_max, 20000)
        self.assertFalse(model.ble_poll_rate_supported)
        self.assertEqual(tuple(model.ble_supported_poll_rates), ())
        self.assertEqual(tuple(model.ble_endpoint_product_ids), (0x008E,))
        self.assertTrue(model.ble_endpoint_experimental)
        self.assertTrue(model.ble_multi_profile_table_limited)
        self.assertTrue(model.onboard_profile_bank_switch)
        self.assertEqual(tuple(model.rawhid_mirror_product_ids), (0x007C, 0x007D))
        self.assertGreaterEqual(len(tuple(model.rawhid_pid_specs)), 3)
        self.assertEqual(tuple(model.rawhid_transport_priority), (0x007C, 0x007D, 0x008E))
        self.assertTrue(model.cli_default_target)
        self.assertEqual(tuple(model.ble_button_decode_layouts), ("compact-16", "razer-v1", "slot-byte6"))

    def test_find_by_usb(self):
        registry = ModelRegistry.load()

        model = registry.find_by_usb(0x1532, 0x007D)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model.slug, "deathadder-v2-pro")

    def test_find_by_name_alias(self):
        registry = ModelRegistry.load()

        model = registry.find_by_name("DA V2 Pro")
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model.slug, "deathadder-v2-pro")

    def test_loads_additional_models(self):
        registry = ModelRegistry.load()

        da_v2 = registry.get("deathadder-v2")
        self.assertIsNotNone(da_v2)
        assert da_v2 is not None
        self.assertIn((0x1532, 0x0084), da_v2.usb_ids)

        da_v2_mini = registry.get("deathadder-v2-mini")
        self.assertIsNotNone(da_v2_mini)
        assert da_v2_mini is not None
        self.assertIn((0x1532, 0x008C), da_v2_mini.usb_ids)

        basilisk_x = registry.get("basilisk-x-hyperspeed")
        self.assertIsNotNone(basilisk_x)
        assert basilisk_x is not None
        self.assertIn((0x1532, 0x0083), basilisk_x.usb_ids)
        self.assertEqual(tuple(basilisk_x.ble_endpoint_product_ids), (0x0083,))
        self.assertEqual(len(tuple(basilisk_x.rawhid_pid_specs)), 1)

    def test_loads_builtin_modules_when_pkgutil_is_empty(self):
        with patch("razecli.model_registry.pkgutil.iter_modules", return_value=[]):
            registry = ModelRegistry.load()

        model = registry.get("deathadder-v2-pro")
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model.name, "Razer DeathAdder V2 Pro")

    def test_default_cli_model_slug(self):
        registry = ModelRegistry.load()
        self.assertEqual(registry.default_cli_model_slug(), "deathadder-v2-pro")

    def test_ble_endpoint_product_ids(self):
        registry = ModelRegistry.load()
        endpoint_pids = registry.ble_endpoint_product_ids()
        self.assertIn(0x008E, endpoint_pids)
        self.assertIn(0x0083, endpoint_pids)


if __name__ == "__main__":
    unittest.main()
