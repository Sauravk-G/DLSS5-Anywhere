"""
Tests for ComponentManager and ComponentDownloader.
"""

from pathlib import Path
import unittest

from dlss5_anywhere.config import (
    COMP_FEEDER,
    COMP_LUMENITE,
    COMP_NVNGX_DLSSNR,
    COMP_RENODX,
    COMP_RESHADE,
    COMPONENTS_REGISTRY,
)
from dlss5_anywhere.core.components import ComponentManager


class TestComponents(unittest.TestCase):

    def test_registry_completeness(self):
        for comp_id in [COMP_RESHADE, COMP_FEEDER, COMP_LUMENITE, COMP_RENODX, COMP_NVNGX_DLSSNR]:
            self.assertIn(comp_id, COMPONENTS_REGISTRY)
            meta = COMPONENTS_REGISTRY[comp_id]
            self.assertTrue(len(meta.name) > 0)
            self.assertTrue(len(meta.expected_files) > 0)

    def test_status_reporting(self):
        statuses = ComponentManager.get_all_statuses()
        self.assertEqual(len(statuses), len(COMPONENTS_REGISTRY))
        for comp_id, status in statuses.items():
            self.assertIn(status.status_label, ["Ready", "Missing"])

    def test_user_file_import_mock(self):
        # Create a mock shader file
        mock_file = Path("mock_shader.fx")
        try:
            mock_file.write_text("// mock dlss5 shader", encoding="utf-8")
            ok, msg, comp_id = ComponentManager.import_user_file(mock_file)
            self.assertTrue(ok)
        finally:
            if mock_file.exists():
                mock_file.unlink()


if __name__ == "__main__":
    unittest.main()
