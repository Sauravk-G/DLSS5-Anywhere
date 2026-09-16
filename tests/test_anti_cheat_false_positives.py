"""The anti-cheat warning has to be right, because its whole value is being believed.

RPCS3 was reported as shipping Treyarch Anti-Cheat. The signature fragment is "t7ac",
tested as a bare substring against every file name in the folder tree, and RPCS3's PPU
recompiler cache contains

    ppu-qrgfcgxdku16t7acnf5r8j4s4owp-libm4aacdec.sprx

Four characters against a folder full of content-addressed names will collide eventually.
The fix is two rules - fragments match on token boundaries, and only files that could be a
loadable module are considered at all - plus not scanning emulator folders, which have no
account to ban and are full of file names that belong to the user's games rather than to
the emulator.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from dlss5_anywhere.config import (
    ANTI_CHEAT_MODULE_SUFFIXES,
    ANTI_CHEAT_SIGNATURES,
    anti_cheat_fragment_matches,
)
from dlss5_anywhere.core.detector import GameDetector

REAL_FILES = [
    ("EasyAntiCheat.sys", "Easy Anti-Cheat"),
    ("easyanticheat_x64.dll", "Easy Anti-Cheat"),
    ("EAC_Server.exe", "Easy Anti-Cheat"),
    ("start_protected_game.exe", "Easy Anti-Cheat"),
    ("BEService.exe", "BattlEye"),
    ("BEClient_x64.dll", "BattlEye"),
    ("BEDaisy.sys", "BattlEye"),
    ("vgk.sys", "Riot Vanguard"),
    ("vgc.exe", "Riot Vanguard"),
    ("anticheat_win64.dll", "Denuvo Anti-Cheat"),
    ("nProtect.dll", "nProtect GameGuard"),
    ("npggNT.des", "nProtect GameGuard"),
    ("xhunter1.sys", "XIGNCODE3"),
    ("mhyprot2.sys", "HoYoverse Anti-Cheat"),
    ("PnkBstrA.exe", "PunkBuster"),
    ("pbcl.dll", "PunkBuster"),
]

# Names that share letters with a signature but do not line up with whole tokens.
INNOCENT_FILES = [
    "ppu-qrgfcgxdku16t7acnf5r8j4s4owp-libm4aacdec.sprx",  # the RPCS3 one, verbatim
    "spu-1a2beac3d4-cache.dat",
    "research_notes.dll",
    "surfaceit_helper.dll",
    "vgk_texture_atlas.dds",
]

# These *do* line up with a token - "ricochet" and "codac" are genuinely in there - and are
# stopped by the second rule instead: a .bnk sound bank and a .pak archive cannot be a
# loadable module, so the scan never looks at them.
INNOCENT_BY_SUFFIX = ["ricochet_ambience.bnk", "codac_lighting.pak"]


def label_for(name: str):
    for label, fragments, _ in ANTI_CHEAT_SIGNATURES:
        if any(anti_cheat_fragment_matches(name, fragment) for fragment in fragments):
            return label
    return None


class TestItStillFindsTheRealThing(unittest.TestCase):
    def test_every_known_anti_cheat_file_is_recognised(self):
        for name, expected in REAL_FILES:
            with self.subTest(name=name):
                self.assertEqual(label_for(name), expected)

    def test_a_folder_named_after_one_counts_whatever_is_inside(self):
        """EasyAntiCheat\\ and BattlEye\\ are how these announce themselves."""
        self.assertEqual(label_for("easyanticheat"), "Easy Anti-Cheat")
        self.assertEqual(label_for("battleye"), "BattlEye")


class TestItNoLongerFiresOnHashes(unittest.TestCase):
    def test_content_addressed_names_do_not_match(self):
        for name in INNOCENT_FILES:
            with self.subTest(name=name):
                self.assertIsNone(label_for(name))

    def test_the_fragment_has_to_be_a_whole_token(self):
        self.assertFalse(anti_cheat_fragment_matches("qrgfcgxdku16t7acnf5r8j4s4owp", "t7ac"))
        self.assertTrue(anti_cheat_fragment_matches("t7ac.dll", "t7ac"))

    def test_a_trailing_version_number_is_still_allowed(self):
        """xhunter1.sys and mhyprot2.sys are the real file names."""
        self.assertTrue(anti_cheat_fragment_matches("xhunter1.sys", "xhunter"))
        self.assertTrue(anti_cheat_fragment_matches("mhyprot3.sys", "mhyprot"))
        self.assertFalse(anti_cheat_fragment_matches("xhunterfoo.sys", "xhunter"))

    def test_an_empty_fragment_matches_nothing(self):
        self.assertFalse(anti_cheat_fragment_matches("anything.dll", ""))


class TestOnlyLoadableFilesAreConsidered(unittest.TestCase):
    """A sound bank called ricochet is content; a driver called ricochet is not."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_ac_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "game.exe").write_bytes(b"MZ")

    def _scan(self):
        return GameDetector._detect_anticheat_and_risks("game.exe", self.tmp)

    def test_a_data_file_does_not_raise_the_warning(self):
        for name in INNOCENT_BY_SUFFIX + ["ricochet.bnk"]:
            with self.subTest(name=name):
                # Written and removed one at a time so each name is judged on its own.
                path = self.tmp / name
                path.write_bytes(b"")
                has_ac, _, _, found, level = self._scan()
                path.unlink()
                self.assertFalse(has_ac)
                self.assertEqual(found, [])
                self.assertEqual(level, "none")

    def test_those_same_names_do_match_the_token_rule(self):
        """Which is the point: it is the suffix gate that saves them, not the token rule."""
        for name in INNOCENT_BY_SUFFIX:
            with self.subTest(name=name):
                self.assertIsNotNone(label_for(name))

    def test_the_same_name_as_a_driver_does(self):
        (self.tmp / "ricochet.sys").write_bytes(b"")
        has_ac, warnings, _, found, level = self._scan()
        self.assertTrue(has_ac)
        self.assertEqual(found, ["Ricochet"])
        self.assertEqual(level, "anti_cheat")
        self.assertTrue(any("kernel-level" in w for w in warnings))

    def test_one_level_of_subfolder_is_searched(self):
        (self.tmp / "EasyAntiCheat").mkdir()
        (self.tmp / "EasyAntiCheat" / "EasyAntiCheat_x64.dll").write_bytes(b"")
        _, _, _, found, _ = self._scan()
        self.assertIn("Easy Anti-Cheat", found)

    def test_the_suffix_list_is_modules_only(self):
        self.assertEqual(ANTI_CHEAT_MODULE_SUFFIXES, {".exe", ".dll", ".sys", ".des"})


class TestEmulatorsAreNotScanned(unittest.TestCase):
    """An emulator has no account to ban, and its folder is named after the user's games.

    Shader caches, save data and recompiler output are all content-addressed or titled by
    whatever the user happens to own, so nothing found there says anything about the risk
    of injecting into the emulator itself.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_emu_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # A file that would trip the scan for any non-emulator.
        (self.tmp / "EasyAntiCheat.sys").write_bytes(b"")

    def test_an_emulator_folder_is_skipped_entirely(self):
        for exe in ("rpcs3.exe", "pcsx2-qtx64.exe", "PPSSPPWindows64.exe"):
            with self.subTest(exe=exe):
                has_ac, warnings, _, found, level = (
                    GameDetector._detect_anticheat_and_risks(exe, self.tmp)
                )
                self.assertFalse(has_ac)
                self.assertEqual(found, [])
                self.assertEqual(warnings, [])
                self.assertEqual(level, "none")

    def test_a_game_in_the_same_folder_is_not(self):
        has_ac, _, _, found, _ = GameDetector._detect_anticheat_and_risks(
            "game.exe", self.tmp
        )
        self.assertTrue(has_ac)
        self.assertEqual(found, ["Easy Anti-Cheat"])
