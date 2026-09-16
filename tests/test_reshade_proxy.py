"""
Choosing the DLL name ReShade is installed as.

ReShade gets into a process by being named after a DLL that process loads. Which name
works is a property of the executable, and the render API is only a good guess at it.

PPSSPP is where the guess failed. It renders through Direct3D 11, so the API mapping chose
dxgi.dll - but PPSSPP does not import dxgi.dll at all: it loads d3d11.dll at runtime, and
DXGI comes in as a dependency of the System32 copy, resolved against System32. The
dxgi.dll next to the executable was opened by nobody. The install was complete, correct,
and inert, and there was no ReShade.log to say so because ReShade never ran.

PPSSPP statically imports dinput8.dll, which the loader resolves from the application
directory at process start, before any of the program's own code runs.
"""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from dlss5_anywhere.config import (
    RESHADE_PROXY_FALLBACK_ORDER,
    choose_reshade_proxy,
)
from dlss5_anywhere.core.installer import ModInstaller
from dlss5_anywhere.core.strategy import StrategyPlan, FilePlanItem

PPSSPP_IMPORTS = [
    "advapi32.dll", "comctl32.dll", "comdlg32.dll", "d3d9.dll", "dinput8.dll",
    "dsound.dll", "gdi32.dll", "kernel32.dll", "opengl32.dll", "user32.dll",
]


class TestChoosingTheProxyName(unittest.TestCase):

    def test_the_api_choice_stands_when_the_exe_imports_it(self):
        """A game that does import dxgi.dll keeps dxgi.dll - RE4 must not move."""
        name, note = choose_reshade_proxy("dxgi.dll", ["kernel32.dll", "dxgi.dll"])
        self.assertEqual(name, "dxgi.dll")
        self.assertIsNone(note)

    def test_ppsspp_gets_dinput8(self):
        name, note = choose_reshade_proxy("dxgi.dll", PPSSPP_IMPORTS)
        self.assertEqual(name, "dinput8.dll")
        self.assertIn("does not import dxgi.dll", note)

    def test_the_substitution_is_explained_not_silent(self):
        """The whole failure was that nothing said anything."""
        _, note = choose_reshade_proxy("dxgi.dll", PPSSPP_IMPORTS)
        self.assertIsNotNone(note)
        self.assertIn("never be loaded", note)

    def test_a_non_graphics_name_is_preferred_over_a_graphics_one(self):
        """Standing in for d3d9.dll in a D3D11 game invites ReShade onto the wrong API;
        dinput8.dll cannot, because it is not a renderer at all."""
        name, _ = choose_reshade_proxy("dxgi.dll", ["d3d9.dll", "dinput8.dll", "opengl32.dll"])
        self.assertEqual(name, "dinput8.dll")

    def test_a_graphics_name_is_used_when_that_is_all_there_is(self):
        name, _ = choose_reshade_proxy("dxgi.dll", ["opengl32.dll", "kernel32.dll"])
        self.assertEqual(name, "opengl32.dll")

    def test_an_unreadable_import_table_changes_nothing(self):
        """No evidence is not evidence for a substitution."""
        for imports in ([], None):
            with self.subTest(imports=imports):
                name, note = choose_reshade_proxy("dxgi.dll", imports)
                self.assertEqual(name, "dxgi.dll")
                self.assertIsNone(note)

    def test_an_exe_importing_nothing_usable_is_told_so(self):
        name, note = choose_reshade_proxy("dxgi.dll", ["kernel32.dll", "user32.dll"])
        self.assertEqual(name, "dxgi.dll", "no better option exists, so do not invent one")
        self.assertIn("is a guess", note)

    def test_vulkan_has_no_local_dll_and_stays_that_way(self):
        self.assertEqual(choose_reshade_proxy(None, PPSSPP_IMPORTS), (None, None))

    def test_matching_ignores_case(self):
        name, _ = choose_reshade_proxy("dxgi.dll", ["DXGI.DLL", "KERNEL32.DLL"])
        self.assertEqual(name, "dxgi.dll")

    def test_every_candidate_is_one_reshade_can_actually_stand_in_for(self):
        """A name ReShade exports no entry point for would load and then break the game."""
        self.assertEqual(
            set(RESHADE_PROXY_FALLBACK_ORDER),
            {"dinput8.dll", "d3d9.dll", "d3d11.dll", "opengl32.dll", "dxgi.dll"},
        )


class TestNotLeavingTwoReShades(unittest.TestCase):
    """Two copies of ReShade in one process is worse than the problem being fixed."""

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_proxy_"))
        self.addCleanup(shutil.rmtree, self.game, True)
        self.backup = self.game / ".dlss5_backup"
        self.backup.mkdir()

    def manifest(self, injected):
        (self.backup / "dlss5_manifest.json").write_text(
            json.dumps({"injected_files": injected}), encoding="utf-8"
        )

    def plan(self, *dests):
        return StrategyPlan(
            strategy_id="x", display_name="x", description="x",
            target_exe=self.game / "game.exe", game_dir=self.game, is_64bit=True,
            items=[FilePlanItem(None, d, "") for d in dests],
        )

    def test_the_old_proxy_is_removed_when_the_name_changes(self):
        (self.game / "dxgi.dll").write_bytes(b"MZ-old")
        self.manifest(["dxgi.dll", "ReShade.ini"])
        removed = ModInstaller._remove_superseded_proxy(
            self.game, self.backup, self.plan("dinput8.dll")
        )
        self.assertEqual(removed, ["dxgi.dll"])
        self.assertFalse((self.game / "dxgi.dll").exists())

    def test_the_removed_proxy_is_kept_in_the_backup(self):
        (self.game / "dxgi.dll").write_bytes(b"MZ-old")
        self.manifest(["dxgi.dll"])
        ModInstaller._remove_superseded_proxy(self.game, self.backup, self.plan("dinput8.dll"))
        self.assertEqual((self.backup / "dxgi.dll").read_bytes(), b"MZ-old")

    def test_the_proxy_still_in_use_is_never_removed(self):
        (self.game / "dxgi.dll").write_bytes(b"MZ")
        self.manifest(["dxgi.dll"])
        self.assertEqual(
            ModInstaller._remove_superseded_proxy(self.game, self.backup, self.plan("dxgi.dll")),
            [],
        )
        self.assertTrue((self.game / "dxgi.dll").exists())

    def test_a_proxy_we_did_not_install_is_left_alone(self):
        """Somebody else's ReShade, or the game's own d3d9.dll, is not ours to delete."""
        (self.game / "d3d9.dll").write_bytes(b"MZ")
        self.manifest(["ReShade.ini"])
        self.assertEqual(
            ModInstaller._remove_superseded_proxy(self.game, self.backup, self.plan("dinput8.dll")),
            [],
        )
        self.assertTrue((self.game / "d3d9.dll").exists())

    def test_ordinary_files_are_not_mistaken_for_proxies(self):
        (self.game / "nvngx_dlss.dll").write_bytes(b"SR")
        self.manifest(["nvngx_dlss.dll"])
        self.assertEqual(
            ModInstaller._remove_superseded_proxy(self.game, self.backup, self.plan("dinput8.dll")),
            [],
        )
        self.assertTrue((self.game / "nvngx_dlss.dll").exists())

    def test_no_manifest_means_nothing_is_ours(self):
        (self.game / "dxgi.dll").write_bytes(b"MZ")
        self.assertEqual(
            ModInstaller._remove_superseded_proxy(self.game, self.backup, self.plan("dinput8.dll")),
            [],
        )
        self.assertTrue((self.game / "dxgi.dll").exists())


if __name__ == "__main__":
    unittest.main()
