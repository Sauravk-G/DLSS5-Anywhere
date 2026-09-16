"""
Two ways a correct install looks like nothing at all.

PPSSPP ships PPSSPPWindows.exe and PPSSPPWindows64.exe side by side. The install went to
the 64-bit one and worked - ReShade attached, both add-ons registered, every effect
compiled. The 32-bit one was being launched. It picked up an unrelated system-wide ReShade
from C:\\ProgramData\\ReShade, which has none of these add-ons in it and writes its own
ReShade.log into the game folder, so the log in front of you describes a ReShade that is
not the one installed.

From the outside both of those are indistinguishable from an install that did nothing.
"""

from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock

from dlss5_anywhere.core.detector import GameDetector


def write_pe(path: Path, machine: int) -> None:
    """Enough of a PE for the detector's architecture check."""
    pe_offset = 0x80
    data = bytearray(0x200)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe_offset)
    data[pe_offset:pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<H", data, pe_offset + 4, machine)
    path.write_bytes(bytes(data))


X64, X86 = 0x8664, 0x14C


class BitnessTestCase(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="dlss5_bits_"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def exe(self, name, machine):
        path = self.dir / name
        write_pe(path, machine)
        return path


class TestPickingTheWrongBuild(BitnessTestCase):

    def test_the_64_bit_sibling_is_named(self):
        thirty_two = self.exe("PPSSPPWindows.exe", X86)
        self.exe("PPSSPPWindows64.exe", X64)
        said = " ".join(GameDetector._check_for_better_bitness(thirty_two, False))
        self.assertIn("PPSSPPWindows64.exe", said)
        self.assertIn("32-bit build", said)

    def test_the_64_bit_build_is_told_nothing(self):
        sixty_four = self.exe("PPSSPPWindows64.exe", X64)
        self.assertEqual(GameDetector._check_for_better_bitness(sixty_four, True), [])

    def test_the_common_suffix_spellings_are_recognised(self):
        for name in ("game64.exe", "game_64.exe", "game-64.exe", "gamex64.exe"):
            with self.subTest(sibling=name):
                d = Path(tempfile.mkdtemp(prefix="dlss5_sfx_"))
                self.addCleanup(shutil.rmtree, d, True)
                write_pe(d / "game.exe", X86)
                write_pe(d / name, X64)
                said = " ".join(GameDetector._check_for_better_bitness(d / "game.exe", False))
                self.assertIn(name, said)

    def test_an_unrelated_32_bit_exe_alone_says_nothing(self):
        """Most 32-bit games have no 64-bit build; that is not worth a warning."""
        only = self.exe("oldgame.exe", X86)
        self.exe("crashreporter.exe", X86)
        self.assertEqual(GameDetector._check_for_better_bitness(only, False), [])

    def test_a_32_bit_sibling_named_64_is_not_recommended(self):
        """The name is a hint; the PE header is the evidence."""
        thirty_two = self.exe("game.exe", X86)
        self.exe("game64.exe", X86)
        self.assertEqual(GameDetector._check_for_better_bitness(thirty_two, False), [])

    def test_an_unreadable_folder_says_nothing(self):
        missing = self.dir / "gone" / "game.exe"
        self.assertEqual(GameDetector._check_for_better_bitness(missing, False), [])


class TestTheOtherReShade(unittest.TestCase):

    def setUp(self):
        self.fake = Path(tempfile.mkdtemp(prefix="dlss5_global_"))
        self.addCleanup(shutil.rmtree, self.fake, True)
        patcher = mock.patch.object(GameDetector, "GLOBAL_RESHADE_DIR", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_machine_wide_reshade_is_reported(self):
        (self.fake / "ReShade64.dll").write_bytes(b"MZ")
        said = " ".join(GameDetector._check_for_global_reshade())
        self.assertIn("system-wide ReShade", said)
        self.assertIn("Add-ons tab is", said, "the user needs a way to tell which one attached")

    def test_the_32_bit_copy_counts_too(self):
        """It was the 32-bit copy that attached to the 32-bit emulator."""
        (self.fake / "ReShade32.dll").write_bytes(b"MZ")
        self.assertTrue(GameDetector._check_for_global_reshade())

    def test_nothing_is_said_when_there_is_no_global_install(self):
        self.assertEqual(GameDetector._check_for_global_reshade(), [])

    def test_an_empty_folder_is_not_an_install(self):
        (self.fake / "ReShadeApps.ini").write_bytes(b"")
        self.assertEqual(GameDetector._check_for_global_reshade(), [])


if __name__ == "__main__":
    unittest.main()
