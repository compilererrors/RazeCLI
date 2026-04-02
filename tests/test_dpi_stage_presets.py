import tempfile
import unittest
from pathlib import Path

from razecli.dpi_stage_presets import (
    delete_dpi_stage_preset,
    list_dpi_stage_presets,
    load_dpi_stage_preset,
    save_dpi_stage_preset,
)
from razecli.errors import RazeCliError


class DpiStagePresetsTest(unittest.TestCase):
    def test_save_load_list_delete(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "presets.json"

            path = save_dpi_stage_preset(
                name="myfps",
                model_id="deathadder-v2-pro",
                active_stage=2,
                stages=[(400, 400), (1000, 1000), (2400, 2400)],
                path=str(preset_path),
            )
            self.assertEqual(path, preset_path)
            self.assertTrue(preset_path.exists())

            rows = list_dpi_stage_presets(path=str(preset_path))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "myfps")
            self.assertEqual(rows[0]["active_stage"], 2)
            self.assertEqual(rows[0]["stages_count"], 3)

            preset = load_dpi_stage_preset("myfps", path=str(preset_path))
            self.assertEqual(preset["name"], "myfps")
            self.assertEqual(preset["model_id"], "deathadder-v2-pro")
            self.assertEqual(preset["active_stage"], 2)
            self.assertEqual(preset["stages"], [(400, 400), (1000, 1000), (2400, 2400)])

            deleted_path = delete_dpi_stage_preset("myfps", path=str(preset_path))
            self.assertEqual(deleted_path, preset_path)
            rows = list_dpi_stage_presets(path=str(preset_path))
            self.assertEqual(rows, [])

    def test_missing_preset_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "presets.json"
            with self.assertRaises(RazeCliError):
                load_dpi_stage_preset("does-not-exist", path=str(preset_path))


if __name__ == "__main__":
    unittest.main()

