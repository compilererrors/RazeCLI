import tempfile
import unittest
from pathlib import Path

from razecli.errors import RazeCliError
from razecli.feature_scaffolds import (
    get_button_mapping_scaffold,
    get_rgb_scaffold,
    reset_button_mapping_scaffold,
    set_button_mapping_scaffold,
    set_rgb_scaffold,
)


class FeatureScaffoldsTest(unittest.TestCase):
    def test_rgb_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = str(Path(tmp_dir) / "feature_store.json")
            _, state = set_rgb_scaffold(
                "deathadder-v2-pro",
                mode="static",
                brightness=42,
                color="#aa22cc",
                path=store,
            )
            self.assertEqual(state["mode"], "static")
            self.assertEqual(state["brightness"], 42)
            self.assertEqual(state["color"], "aa22cc")

            current = get_rgb_scaffold("deathadder-v2-pro", path=store)
            self.assertEqual(current["mode"], "static")
            self.assertEqual(current["brightness"], 42)
            self.assertEqual(current["color"], "aa22cc")

    def test_button_mapping_set_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = str(Path(tmp_dir) / "feature_store.json")
            _, state = set_button_mapping_scaffold(
                "deathadder-v2-pro",
                button="side_2",
                action="mouse:back",
                path=store,
            )
            self.assertEqual(state["mapping"]["side_2"], "mouse:back")

            _, reset_state = reset_button_mapping_scaffold("deathadder-v2-pro", path=store)
            defaults = get_button_mapping_scaffold("deathadder-v2-pro", path=store)["mapping"]
            self.assertEqual(reset_state["mapping"], defaults)

    def test_invalid_button_raises_for_da_v2_pro(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = str(Path(tmp_dir) / "feature_store.json")
            with self.assertRaises(RazeCliError):
                set_button_mapping_scaffold(
                    "deathadder-v2-pro",
                    button="unknown_button",
                    action="mouse:left",
                    path=store,
                )


if __name__ == "__main__":
    unittest.main()
