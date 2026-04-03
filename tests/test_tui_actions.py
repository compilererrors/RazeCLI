import unittest

from razecli.tui_actions import TuiActionsMixin


class _DummyController(TuiActionsMixin):
    def __init__(self) -> None:
        self.called = False
        self.received = None

    def _set_dpi_profile_count(self, stdscr) -> None:  # type: ignore[override]
        self.called = True
        self.received = stdscr


class TuiActionsTest(unittest.TestCase):
    def test_edit_dpi_levels_delegates_to_profile_count_editor(self):
        controller = _DummyController()
        sentinel = object()
        controller._edit_dpi_levels(sentinel)
        self.assertTrue(controller.called)
        self.assertIs(controller.received, sentinel)


if __name__ == "__main__":
    unittest.main()
