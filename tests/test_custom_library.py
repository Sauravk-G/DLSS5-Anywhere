"""
The user's own list of games.

The behaviour worth pinning down here is persistence and what happens when the world
changes underneath a stored entry: the whole point of this store is that an addition
survives closing the app, and the failure mode it replaces was one where it did not.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from dlss5_anywhere.core import custom_library
from dlss5_anywhere.core.custom_library import CustomLibrary, suggest_name
from dlss5_anywhere.core.library_scanner import DiscoveredGame


class CustomLibraryTestCase(unittest.TestCase):
    """Redirects the store at a temporary file so tests never touch the real library."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = self.root / "custom_games.json"
        patcher = mock.patch.object(custom_library, "CUSTOM_GAMES_FILE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def make_exe(self, *parts: str) -> Path:
        """A real file on disk, so `add` sees what it will see in practice."""
        exe = self.root.joinpath(*parts)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")
        return exe


class TestAddingAndPersistence(CustomLibraryTestCase):

    def test_an_addition_survives_a_restart(self):
        """The failure this store exists to fix: added, then gone on the next launch."""
        exe = self.make_exe("Emulators", "RPCS3", "rpcs3.exe")
        added, _ = CustomLibrary.add(exe, "RPCS3")
        self.assertTrue(added)

        # Nothing cached: every read goes back to the file, which is what a restart does.
        reloaded = CustomLibrary.load()
        self.assertEqual([g.name for g in reloaded], ["RPCS3"])
        self.assertEqual(reloaded[0].exe_path, exe)
        self.assertEqual(reloaded[0].source, "Custom")

    def test_the_file_is_written_where_it_can_be_read_back(self):
        exe = self.make_exe("Games", "Thing", "thing.exe")
        CustomLibrary.add(exe)
        self.assertTrue(self.store.exists())
        payload = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(payload["games"][0]["exe_path"], str(exe))

    def test_the_same_game_is_not_added_twice(self):
        exe = self.make_exe("Games", "Thing", "thing.exe")
        self.assertTrue(CustomLibrary.add(exe)[0])
        added, message = CustomLibrary.add(exe)
        self.assertFalse(added)
        self.assertIn("already", message.lower())
        self.assertEqual(len(CustomLibrary.entries()), 1)

    def test_a_path_spelled_differently_is_still_the_same_game(self):
        """Windows paths are case-insensitive, so the store has to be too."""
        exe = self.make_exe("Games", "Thing", "thing.exe")
        CustomLibrary.add(exe)
        shouty = Path(str(exe).upper())
        self.assertFalse(CustomLibrary.add(shouty)[0])
        self.assertTrue(CustomLibrary.contains(shouty))

    def test_something_that_is_not_an_executable_is_refused(self):
        readme = self.root / "readme.txt"
        readme.write_text("not a game", encoding="utf-8")
        added, message = CustomLibrary.add(readme)
        self.assertFalse(added)
        self.assertIn(".exe", message)

    def test_a_path_that_does_not_exist_is_refused(self):
        added, message = CustomLibrary.add(self.root / "nope" / "ghost.exe")
        self.assertFalse(added)
        self.assertIn("No executable", message)

    def test_a_batch_reports_only_what_was_new(self):
        first = self.make_exe("A", "a.exe")
        second = self.make_exe("B", "b.exe")
        games = [
            DiscoveredGame("A", first, first.parent, "Custom"),
            DiscoveredGame("B", second, second.parent, "Custom"),
        ]
        self.assertEqual(CustomLibrary.add_many(games), 2)
        self.assertEqual(CustomLibrary.add_many(games), 0)
        self.assertEqual(len(CustomLibrary.entries()), 2)


class TestRemovingAndRenaming(CustomLibraryTestCase):

    def test_removing_forgets_the_entry_and_leaves_the_file_alone(self):
        exe = self.make_exe("Games", "Thing", "thing.exe")
        CustomLibrary.add(exe)
        self.assertTrue(CustomLibrary.remove(exe))
        self.assertEqual(CustomLibrary.entries(), [])
        self.assertTrue(exe.exists(), "removing a library entry must not delete the game")

    def test_removing_something_that_is_not_there_says_so(self):
        self.assertFalse(CustomLibrary.remove(self.root / "ghost.exe"))

    def test_renaming_keeps_the_path(self):
        exe = self.make_exe("portable", "pcsx2-v2.0.3", "pcsx2-qtx64.exe")
        CustomLibrary.add(exe)
        self.assertTrue(CustomLibrary.rename(exe, "PCSX2"))
        entry = CustomLibrary.entries()[0]
        self.assertEqual(entry.name, "PCSX2")
        self.assertEqual(entry.exe_path, exe)

    def test_renaming_to_nothing_is_refused(self):
        exe = self.make_exe("Games", "Thing", "thing.exe")
        CustomLibrary.add(exe)
        self.assertFalse(CustomLibrary.rename(exe, "   "))
        self.assertEqual(CustomLibrary.entries()[0].name, "Thing")


class TestEntriesThatOutliveTheirFile(CustomLibraryTestCase):

    def test_a_deleted_game_is_marked_not_dropped(self):
        """A game uninstalled, or a drive unplugged, must not silently edit the list."""
        exe = self.make_exe("Games", "Gone", "gone.exe")
        CustomLibrary.add(exe, "Gone")
        exe.unlink()

        games = CustomLibrary.load()
        self.assertEqual(len(games), 1, "the entry must still be listed")
        self.assertTrue(games[0].missing)
        self.assertEqual(len(CustomLibrary.entries()), 1, "and still be stored")

    def test_a_present_game_is_not_marked(self):
        exe = self.make_exe("Games", "Here", "here.exe")
        CustomLibrary.add(exe)
        self.assertFalse(CustomLibrary.load()[0].missing)


class TestABrokenStore(CustomLibraryTestCase):

    def test_unreadable_json_is_moved_aside_rather_than_overwritten(self):
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text("{ this is not json", encoding="utf-8")

        self.assertEqual(CustomLibrary.entries(), [])
        self.assertTrue(
            self.store.with_suffix(".json.corrupt").exists(),
            "a hand-broken list is the user's data and has to be recoverable",
        )

    def test_a_missing_store_is_simply_empty(self):
        self.assertEqual(CustomLibrary.entries(), [])
        self.assertEqual(CustomLibrary.load(), [])

    def test_an_entry_without_a_path_is_skipped_not_fatal(self):
        exe = self.make_exe("Games", "Good", "good.exe")
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps({"version": 1, "games": [
            {"name": "broken"},
            {"name": "Good", "exe_path": str(exe), "install_dir": str(exe.parent)},
        ]}), encoding="utf-8")
        self.assertEqual([e.name for e in CustomLibrary.entries()], ["Good"])


class TestNameSuggestion(unittest.TestCase):
    """The guessed name is what the user is offered; it should rarely need correcting."""

    def test_the_folder_names_the_game_better_than_the_executable(self):
        self.assertEqual(
            suggest_name(r"D:\Games\Metro 2033 Redux\metro.exe"), "Metro 2033 Redux"
        )

    def test_a_folder_that_only_says_where_the_binary_lives_is_skipped(self):
        self.assertEqual(suggest_name(r"C:\Emulators\RPCS3\bin\rpcs3.exe"), "RPCS3")
        self.assertEqual(
            suggest_name(r"D:\Games\Foo\Binaries\Win64\Foo-Win64-Shipping.exe"), "Foo"
        )

    def test_an_existing_capital_is_left_alone(self):
        """Title-casing RPCS3 into Rpcs3 is a downgrade, not a tidy-up."""
        self.assertEqual(suggest_name(r"F:\RPCS3\rpcs3.exe"), "RPCS3")

    def test_separators_become_spaces(self):
        self.assertEqual(suggest_name(r"F:\dolphin-emu\Dolphin.exe"), "Dolphin Emu")

    def test_it_falls_back_to_the_executable_at_a_drive_root(self):
        """No folder to read, so the executable is all there is to go on - and from an
        all-lowercase "rpcs3" there is nothing that says the name is an initialism."""
        self.assertEqual(suggest_name(r"D:\rpcs3.exe"), "Rpcs3")


class TestScanAllIncludesTheUsersOwnGames(CustomLibraryTestCase):

    def test_manual_entries_come_back_from_scan_all(self):
        """The CLI and the Builder's quick-pick read the library through scan_all."""
        from dlss5_anywhere.core.library_scanner import GameLibraryScanner

        exe = self.make_exe("Emulators", "Flycast", "flycast.exe")
        CustomLibrary.add(exe, "Flycast")

        with mock.patch.object(GameLibraryScanner, "scan_steam", return_value=[]), \
             mock.patch.object(GameLibraryScanner, "scan_epic", return_value=[]), \
             mock.patch.object(GameLibraryScanner, "scan_gog", return_value=[]):
            names = [g.name for g in GameLibraryScanner.scan_all()]
            self.assertEqual(names, ["Flycast"])
            self.assertEqual(GameLibraryScanner.scan_all(include_custom=False), [])

    def test_a_detected_game_beats_a_manual_entry_for_the_same_exe(self):
        """If it has become detectable, the launcher knows its real name and store."""
        from dlss5_anywhere.core.library_scanner import GameLibraryScanner

        exe = self.make_exe("DetectedGames", "Thing", "thing.exe")
        CustomLibrary.add(exe, "My hand-typed name")
        detected = DiscoveredGame("Thing", exe, exe.parent, "Steam")

        with mock.patch.object(GameLibraryScanner, "scan_steam", return_value=[detected]), \
             mock.patch.object(GameLibraryScanner, "scan_epic", return_value=[]), \
             mock.patch.object(GameLibraryScanner, "scan_gog", return_value=[]):
            games = GameLibraryScanner.scan_all()

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].source, "Steam")
        self.assertEqual(games[0].name, "Thing")


if __name__ == "__main__":
    unittest.main()
