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

    def test_loads_builtin_modules_when_pkgutil_is_empty(self):
        with patch("razecli.model_registry.pkgutil.iter_modules", return_value=[]):
            registry = ModelRegistry.load()

        model = registry.get("deathadder-v2-pro")
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model.name, "Razer DeathAdder V2 Pro")


if __name__ == "__main__":
    unittest.main()
