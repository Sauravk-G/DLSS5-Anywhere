"""
Starred games, and the warning about add-ons already in a game folder.

Both exist because of one install into Resident Evil 4: a folder holding 22 ReShade
add-ons from an unrelated pack, into which this tool added two more and said nothing.
"""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from dlss5_anywhere.core import favourites as fav_mod
from dlss5_anywhere.core.detector import GameDetector
from dlss5_anywhere.core.favourites import Favourites


class FavouritesTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_fav_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(
            fav_mod, "FAVOURITES_FILE", self.tmp / "favourites.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = self.tmp / "favourites.json"


class TestStarring(FavouritesTestCase):

    def test_a_star_survives_a_restart(self):
        """Every read goes back to the file, which is what a restart does."""
        Favourites.add(r"D:\Games\Thing\thing.exe")
        self.assertTrue(Favourites.contains(r"D:\Games\Thing\thing.exe"))
        self.assertTrue(self.store.exists())

    def test_toggle_reports_the_state_it_left(self):
        path = r"D:\Games\Thing\thing.exe"
        self.assertTrue(Favourites.toggle(path), "first toggle stars it")
        self.assertFalse(Favourites.toggle(path), "second un-stars it")
        self.assertFalse(Favourites.contains(path))

    def test_a_path_spelled_differently_is_the_same_game(self):
        """Windows paths are case-insensitive, and so is the custom library's key."""
        Favourites.add(r"D:\Games\Thing\thing.exe")
        self.assertTrue(Favourites.contains(r"D:\GAMES\THING\THING.EXE"))
        self.assertTrue(Favourites.contains(r"D:\Games\Thing\..\Thing\thing.exe"))

    def test_starring_twice_stores_one_entry(self):
        Favourites.add(r"D:\Games\Thing\thing.exe")
        Favourites.add(r"D:\Games\Thing\thing.exe")
        self.assertEqual(len(Favourites.paths()), 1)

    def test_removing_something_unstarred_says_so(self):
        self.assertFalse(Favourites.remove(r"D:\nope.exe"))

    def test_a_missing_store_is_simply_empty(self):
        self.assertEqual(Favourites.load(), set())
        self.assertEqual(Favourites.paths(), [])

    def test_a_broken_store_does_not_raise(self):
        self.store.write_text("{ not json", encoding="utf-8")
        self.assertEqual(Favourites.load(), set())


class TestPruning(FavouritesTestCase):

    def test_pruning_drops_only_what_is_gone(self):
        Favourites.add(r"D:\Games\Here\here.exe")
        Favourites.add(r"D:\Games\Gone\gone.exe")
        dropped = Favourites.prune([r"D:\Games\Here\here.exe"])
        self.assertEqual(dropped, 1)
        self.assertTrue(Favourites.contains(r"D:\Games\Here\here.exe"))
        self.assertFalse(Favourites.contains(r"D:\Games\Gone\gone.exe"))

    def test_pruning_is_never_automatic(self):
        """A drive that is not plugged in must not cost the user their stars.

        `prune` exists but nothing calls it during a scan; this test is here so that
        stays a deliberate choice rather than something quietly added later.
        """
        import inspect
        from dlss5_anywhere.core import library_scanner
        source = inspect.getsource(library_scanner)
        self.assertNotIn("prune", source)


class TestForeignAddonDetection(unittest.TestCase):
    """Add-ons already in a game folder are part of the install whether chosen or not."""

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_addons_"))
        self.addCleanup(shutil.rmtree, self.game, True)

    def write(self, *names):
        for n in names:
            (self.game / n).write_bytes(b"MZ")

    def manifest(self, injected):
        backup = self.game / ".dlss5_backup"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "dlss5_manifest.json").write_text(
            json.dumps({"injected_files": injected}), encoding="utf-8"
        )

    def test_our_own_addons_are_not_foreign(self):
        self.write("dlss5-feed.addon64", "renodx-dlss5.addon64")
        self.manifest(["dlss5-feed.addon64", "renodx-dlss5.addon64"])
        ours = {n.lower() for n in GameDetector._files_this_tool_installed(self.game)}
        self.assertEqual(GameDetector._detect_foreign_addons(self.game, ours), [])

    def test_everything_else_is_reported(self):
        self.write("dlss5-feed.addon64", "AutoHDR.addon64", "obs_capture.addon64")
        self.manifest(["dlss5-feed.addon64"])
        ours = {n.lower() for n in GameDetector._files_this_tool_installed(self.game)}
        found = GameDetector._detect_foreign_addons(self.game, ours)
        self.assertEqual(found, ["AutoHDR.addon64", "obs_capture.addon64"])

    def test_a_folder_we_have_never_touched_reports_everything(self):
        """No manifest means nothing is ours, so every add-on there is somebody else's."""
        self.write("ShaderToggler.addon64")
        self.assertEqual(
            GameDetector._detect_foreign_addons(self.game, set()),
            ["ShaderToggler.addon64"],
        )

    def test_non_addons_are_ignored(self):
        self.write("readme.txt", "d3d12.dll", "game.exe")
        self.assertEqual(GameDetector._detect_foreign_addons(self.game, set()), [])

    def test_the_known_clashes_are_explained(self):
        """A list of names is not actionable; the reason is what makes it one."""
        for name, needle in (
            ("renodx-dlss.addon64", "RenoDX"),
            ("renodx-upgrade.addon64", "tone-mapping"),
            ("AutoHDR.addon64", "tone-mapper"),
            ("srReshade_v2.1.0.addon64", "upscaler"),
            ("obs_capture.addon64", "Present"),
        ):
            with self.subTest(addon=name):
                reason = GameDetector.describe_addon_conflict(name)
                self.assertIsNotNone(reason, f"{name} should be flagged")
                self.assertIn(needle, reason)

    def test_an_unknown_addon_is_listed_but_not_accused(self):
        """Reporting is cheap; calling somebody's mod broken without knowing is not."""
        self.assertIsNone(GameDetector.describe_addon_conflict("SomeoneElses.addon64"))

    def test_our_own_addon_is_never_called_a_conflict(self):
        self.assertIsNone(GameDetector.describe_addon_conflict("renodx-dlss5.addon64"))



# What an install does about those add-ons - the add-on folder, the ReShade.ini that
# points at it, and the clearing of the old layout - is in test_addon_isolation.py.

if __name__ == "__main__":
    unittest.main()
