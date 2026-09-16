"""
Keeping other people's ReShade add-ons out of our install.

A folder holding twenty-two add-ons from an unrelated pack got all twenty-two of them
switched on by installing this mod - not because the mod added them, but because it
supplied the first addon-capable ReShade that folder ever had and pointed AddonPath at
".\\". Two mouse cursors, a black screen for the length of a load, four separate add-ons
hooking the same DLSS entry points, and an 88 MB log written during frames.

The first attempt at a fix listed the add-ons in ReShade's DisabledAddons key. It matched
nothing at all, which these tests exist to make impossible to ship again: ReShade compares
that key against the name an add-on *registers at runtime*, and a file called
ReShade64-AdjustDepth-By-seri14.addon64 registers as "Adjust Depth". No installer reading
files on disk can know that.

So the add-ons go in a folder of their own and ReShade is pointed at that folder alone.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from dlss5_anywhere.config import (
    ADDON_DIR_NAME,
    STRATEGY_FEEDER_DX11_12,
)
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.detector import GameAnalysis, GameDetector
from dlss5_anywhere.core.installer import ModInstaller


def _analysis(tmp: Path, foreign=()) -> GameAnalysis:
    exe = tmp / "game.exe"
    exe.write_bytes(b"MZ")
    return GameAnalysis(
        exe_path=exe, game_dir=tmp, exe_name="game.exe", file_size_bytes=2,
        architecture="x64", is_64bit=True, detected_apis=["DirectX 11"],
        primary_api="DirectX 11", recommended_strategy=STRATEGY_FEEDER_DX11_12,
        foreign_addons=list(foreign),
    )


def _value(ini: str, key: str):
    for line in ini.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


class TestReShadeIsPointedAwayFromTheGameFolder(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_iso_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_addon_path_names_our_folder_and_nothing_else(self):
        """This single line is what stops twenty-two add-ons loading."""
        ini = ConfigGenerator.generate_reshade_ini(_analysis(self.tmp))
        self.assertEqual(_value(ini, "AddonPath"), ".\\" + ADDON_DIR_NAME + "\\")

    def test_the_game_folder_is_never_a_search_path(self):
        ini = ConfigGenerator.generate_reshade_ini(_analysis(self.tmp, ["AutoHDR.addon64"]))
        self.assertNotEqual(_value(ini, "AddonPath"), ".\\")

    def test_disabled_addons_is_not_used_at_all(self):
        """It matches on a runtime-registered name, so anything written here is a guess.

        ReShade's own entry for one of these add-ons reads
        "LiveSplit Overlay@livesplit_overlay.addon64" - a name that does not appear
        anywhere in the file. Listing file stems there matched nothing and shipped as a
        fix that did nothing.
        """
        ini = ConfigGenerator.generate_reshade_ini(_analysis(self.tmp, ["AutoHDR.addon64"]))
        self.assertEqual(_value(ini, "DisabledAddons"), "")

    def test_only_the_preset_effects_are_compiled(self):
        """Compiling every .fx the recursive search path reaches is most of the black
        screen before the game appears."""
        ini = ConfigGenerator.generate_reshade_ini(_analysis(self.tmp))
        self.assertEqual(_value(ini, "SkipLoadingDisabledEffects"), "1")


class TestTheAddonsGoWhereReShadeLooks(unittest.TestCase):
    """A plan that puts the add-ons anywhere else installs nothing ReShade can load."""

    def _plan_destinations(self):
        from dlss5_anywhere.core.strategy import StrategyEngine
        tmp = Path(tempfile.mkdtemp(prefix="dlss5_plan_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        plan = StrategyEngine.build_plan(_analysis(tmp))
        return [item.relative_dest for item in plan.items]

    def test_every_addon_lands_in_the_addon_folder(self):
        strays = [
            d for d in self._plan_destinations()
            if d.lower().endswith((".addon64", ".addon32"))
            and os.path.dirname(d) != ADDON_DIR_NAME
        ]
        self.assertEqual(strays, [], "add-ons outside the folder ReShade searches")

    def test_the_runtimes_stay_next_to_the_executable(self):
        """renodx-dlss5 wants nvngx_dlssnr.dll beside itself; NGX wants nvngx_dlss.dll
        beside the .exe. The installer hard-links, so the plan keeps the .exe copy."""
        dests = self._plan_destinations()
        self.assertIn("nvngx_dlss.dll", dests)
        self.assertIn("nvngx_dlssnr.dll", dests)


class TestRuntimesBesideTheAddons(unittest.TestCase):

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_link_"))
        self.addCleanup(shutil.rmtree, self.game, True)
        (self.game / ADDON_DIR_NAME).mkdir()

    def test_the_runtimes_are_linked_in_beside_the_addon(self):
        """"nvngx_dlssnr.dll was not found beside the addon" is a real failure string."""
        (self.game / "nvngx_dlssnr.dll").write_bytes(b"NR")
        (self.game / "nvngx_dlss.dll").write_bytes(b"SR")
        written = ModInstaller._mirror_runtimes_beside_addons(self.game)
        for name in ("nvngx_dlssnr.dll", "nvngx_dlss.dll"):
            self.assertTrue((self.game / ADDON_DIR_NAME / name).is_file(), name)
            self.assertIn(f"{ADDON_DIR_NAME}/{name}", written)

    def test_the_link_is_not_a_second_copy(self):
        """These are 165 MB and 59 MB; a copy per install is not acceptable."""
        source = self.game / "nvngx_dlssnr.dll"
        source.write_bytes(b"NR")
        ModInstaller._mirror_runtimes_beside_addons(self.game)
        linked = self.game / ADDON_DIR_NAME / "nvngx_dlssnr.dll"
        if hasattr(os, "link"):
            self.assertEqual(source.stat().st_nlink, 2, "expected one file under two names")

    def test_reinstalling_replaces_the_old_link(self):
        source = self.game / "nvngx_dlss.dll"
        source.write_bytes(b"old")
        ModInstaller._mirror_runtimes_beside_addons(self.game)
        source.write_bytes(b"new")
        ModInstaller._mirror_runtimes_beside_addons(self.game)
        self.assertEqual((self.game / ADDON_DIR_NAME / "nvngx_dlss.dll").read_bytes(), b"new")

    def test_a_missing_runtime_is_simply_skipped(self):
        self.assertEqual(ModInstaller._mirror_runtimes_beside_addons(self.game), [])

    def test_nothing_happens_without_an_addon_folder(self):
        shutil.rmtree(self.game / ADDON_DIR_NAME)
        (self.game / "nvngx_dlss.dll").write_bytes(b"SR")
        self.assertEqual(ModInstaller._mirror_runtimes_beside_addons(self.game), [])


class TestClearingTheOldLayout(unittest.TestCase):
    """An install predating the add-on folder left its add-ons next to the executable."""

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_old_"))
        self.addCleanup(shutil.rmtree, self.game, True)
        self.backup = self.game / ".dlss5_backup"
        self.backup.mkdir()
        (self.game / ADDON_DIR_NAME).mkdir()

    def manifest(self, injected):
        (self.backup / "dlss5_manifest.json").write_text(
            json.dumps({"injected_files": injected}), encoding="utf-8"
        )

    def test_our_own_stale_addon_is_removed(self):
        (self.game / "dlss5-feed.addon64").write_bytes(b"MZ")
        (self.game / ADDON_DIR_NAME / "dlss5-feed.addon64").write_bytes(b"MZ")
        self.manifest(["dlss5-feed.addon64", "ReShade.ini"])
        removed = ModInstaller._remove_superseded_root_addons(self.game, self.backup)
        self.assertEqual(removed, ["dlss5-feed.addon64"])
        self.assertFalse((self.game / "dlss5-feed.addon64").exists())

    def test_an_addon_we_never_installed_is_left_alone(self):
        """Sharing a name with something we install is not evidence that we put it there."""
        (self.game / "renodx-dlss5.addon64").write_bytes(b"MZ")
        (self.game / ADDON_DIR_NAME / "renodx-dlss5.addon64").write_bytes(b"MZ")
        self.manifest(["ReShade.ini"])
        self.assertEqual(ModInstaller._remove_superseded_root_addons(self.game, self.backup), [])
        self.assertTrue((self.game / "renodx-dlss5.addon64").exists())

    def test_somebody_elses_addons_are_never_touched(self):
        for name in ("AutoHDR.addon64", "ShaderToggler.addon64", "renodx-dlss.addon64"):
            (self.game / name).write_bytes(b"MZ")
        self.manifest(["dlss5-feed.addon64"])
        ModInstaller._remove_superseded_root_addons(self.game, self.backup)
        for name in ("AutoHDR.addon64", "ShaderToggler.addon64", "renodx-dlss.addon64"):
            self.assertTrue((self.game / name).exists(), name)

    def test_nothing_is_removed_without_a_replacement_in_place(self):
        """If the new install did not actually land, the old one is all there is."""
        (self.game / "dlss5-feed.addon64").write_bytes(b"MZ")
        self.manifest(["dlss5-feed.addon64"])
        self.assertEqual(ModInstaller._remove_superseded_root_addons(self.game, self.backup), [])
        self.assertTrue((self.game / "dlss5-feed.addon64").exists())

    def test_non_addons_in_the_manifest_are_not_deleted(self):
        (self.game / "nvngx_dlss.dll").write_bytes(b"SR")
        (self.game / ADDON_DIR_NAME / "nvngx_dlss.dll").write_bytes(b"SR")
        self.manifest(["nvngx_dlss.dll"])
        self.assertEqual(ModInstaller._remove_superseded_root_addons(self.game, self.backup), [])
        self.assertTrue((self.game / "nvngx_dlss.dll").exists())

    def test_no_manifest_means_nothing_is_ours(self):
        (self.game / "dlss5-feed.addon64").write_bytes(b"MZ")
        (self.game / ADDON_DIR_NAME / "dlss5-feed.addon64").write_bytes(b"MZ")
        self.assertEqual(ModInstaller._remove_superseded_root_addons(self.game, self.backup), [])
        self.assertTrue((self.game / "dlss5-feed.addon64").exists())


class TestFindingAnInstallAfterTheMove(unittest.TestCase):

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_find_"))
        self.addCleanup(shutil.rmtree, self.game, True)

    def test_an_addon_in_its_own_folder_still_counts_as_installed(self):
        (self.game / ADDON_DIR_NAME).mkdir()
        (self.game / ADDON_DIR_NAME / "renodx-dlss5.addon64").write_bytes(b"MZ")
        _, has_dlss5, files = GameDetector._detect_existing_mods(self.game)
        self.assertTrue(has_dlss5, "install in the add-on folder went unnoticed")
        self.assertIn(f"{ADDON_DIR_NAME}\\renodx-dlss5.addon64", files)

    def test_a_clean_folder_is_still_clean(self):
        _, has_dlss5, _ = GameDetector._detect_existing_mods(self.game)
        self.assertFalse(has_dlss5)


if __name__ == "__main__":
    unittest.main()
