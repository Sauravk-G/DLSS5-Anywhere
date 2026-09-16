"""ReShade's Vulkan layer is registered once; games are enrolled one at a time.

The installer used to read "an implicit-layer entry exists under HKLM" as "this game is
covered". It is not. The layer loads into every Vulkan application and then checks the
executable against an allow-list beside the layer DLL:

    C:\\ProgramData\\ReShade\\ReShadeApps.ini
    Apps=...\\NvRemixBridge.exe,...\\pcsx2-qtx64.exe,...\\rpcs3.exe

A game missing from that line gets a registered layer and no ReShade, which is
indistinguishable from the layer not working - the game starts and the overlay never
appears. Every Vulkan or DXVK install after the first one landed in that state.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from dlss5_anywhere.core.installer import ModInstaller


class TestTheAllowList(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_vkapps_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.layer = self.tmp / "ReShade64.json"
        self.layer.write_text("{}", encoding="utf-8")
        self.apps = self.tmp / "ReShadeApps.ini"
        self.game = self.tmp / "POP3.exe"
        self.game.write_bytes(b"MZ")

    def _write(self, text):
        self.apps.write_text(text, encoding="utf-8-sig")

    def _read(self):
        return self.apps.read_text(encoding="utf-8-sig")

    def _entries(self):
        for line in self._read().splitlines():
            if line.startswith("Apps="):
                return [e for e in line[len("Apps="):].split(",") if e.strip()]
        return []

    def _add(self, exe=None):
        return ModInstaller._add_to_reshade_vulkan_apps(exe or self.game, str(self.layer))

    def test_the_game_is_appended(self):
        self._write("Apps=C:\\games\\one.exe\n")
        ok, detail = self._add()
        self.assertTrue(ok, detail)
        self.assertIn(str(self.game.resolve()), self._entries())

    def test_the_other_games_are_left_alone(self):
        """Those entries are somebody else's installs; rewriting the line would drop them."""
        others = ["C:\\games\\one.exe", "D:\\emu\\rpcs3.exe"]
        self._write("Apps=" + ",".join(others) + "\n")
        self._add()
        for other in others:
            self.assertIn(other, self._entries())

    def test_adding_twice_does_not_duplicate(self):
        self._write("Apps=\n")
        self._add()
        ok, detail = self._add()
        self.assertTrue(ok)
        self.assertIn("already listed", detail)
        self.assertEqual(len(self._entries()), 1)

    def test_the_comparison_ignores_case(self):
        """Windows paths, and the user may not have typed them the way Windows stores them."""
        self._write("Apps=\n")
        self._add()
        ok, detail = self._add(Path(str(self.game).upper()))
        self.assertTrue(ok)
        self.assertIn("already listed", detail)
        self.assertEqual(len(self._entries()), 1)

    def test_the_byte_order_mark_survives(self):
        """ReShade's own setup writes one and reads it back."""
        self._write("Apps=\n")
        self._add()
        self.assertTrue(self.apps.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_a_missing_file_is_created(self):
        self.assertFalse(self.apps.exists())
        ok, _ = self._add()
        self.assertTrue(ok)
        self.assertEqual(self._entries(), [str(self.game.resolve())])

    def test_a_file_with_no_apps_line_gains_one(self):
        self._write("[Something]\nOther=1\n")
        ok, _ = self._add()
        self.assertTrue(ok)
        self.assertIn(str(self.game.resolve()), self._entries())
        self.assertIn("Other=1", self._read())


class TestWhereTheListLives(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_vkwhere_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_it_is_found_beside_the_registered_layer(self):
        """Read from the registry entry, so a ReShade installed elsewhere still works."""
        layer = self.tmp / "ReShade64.json"
        layer.write_text("{}", encoding="utf-8")
        self.assertEqual(
            ModInstaller._reshade_vulkan_apps_file(str(layer)),
            self.tmp / "ReShadeApps.ini",
        )

    def test_without_a_layer_it_falls_back_to_program_data(self):
        found = ModInstaller._reshade_vulkan_apps_file(None)
        if found is not None:
            self.assertEqual(found.name, "ReShadeApps.ini")
            self.assertEqual(found.parent.name, "ReShade")


class TestItEnrolsTheRendererNotTheLauncher(unittest.TestCase):
    """The layer is enrolled per executable, so it has to name the one that draws.

    Prince of Persia ships PrinceOfPersia.exe next to POP3.EXE and the launcher imports no
    graphics API at all. Enrolling the selection alone enrols something that never renders,
    and the failure is silent: the game starts and no overlay appears.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_render_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_analysis_carries_the_candidates(self):
        from dlss5_anywhere.core.detector import GameAnalysis
        analysis = GameAnalysis(
            exe_path=self.tmp / "Launcher.exe", game_dir=self.tmp,
            exe_name="Launcher.exe", file_size_bytes=2,
            architecture="x86", is_64bit=False,
        )
        self.assertEqual(analysis.renderer_candidates, [])

    def test_this_tools_own_executables_are_not_candidates(self):
        """dgVoodoo's control panel links a graphics API and is not the game."""
        from dlss5_anywhere.core.detector import COMPANION_EXECUTABLES
        for name in ("dgvoodoocpl.exe", "dlss5-feed-host64.exe", "nvremixbridge.exe"):
            self.assertIn(name, COMPANION_EXECUTABLES)
