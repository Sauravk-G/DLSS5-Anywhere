"""
Emulator handling, and refusing to install over a running game.

There is no longer a rule here that changes what the picture looks like on an emulator.
There was one, and it was wrong on both counts: it switched off Lumenite_TRAA and left the
heavier RTAO and LSAO running, and it keyed on the host rather than on the thing that
actually decides - whether Kernel::sFlow is populated. Measured on identical defaults,
RPCS3 reported 99-100% of its motion vectors non-zero and looked excellent; PPSSPP on the
same build reported 0% and ghosted.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from dlss5_anywhere.config import (
    DEFAULT_PROFILE,
    EMULATOR_PROFILES,
    find_emulator_profile,
)
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.installer import ModInstaller
from dlss5_anywhere.core.strategy import FilePlanItem, StrategyPlan


class TestNoBlanketRuleAboutEmulators(unittest.TestCase):
    """Whether the temporal passes work is a property of the flow field, not the host.

    A rule here used to switch Lumenite_TRAA off for anything recognised as an emulator.
    It was wrong twice: it left the heavier RTAO and LSAO running - they hold up to 0.98 of
    their history against TRAA's 0.9, through the same Kernel::sFlow - and it was keyed on
    the wrong property. On one machine RPCS3 reported 99-100% of its motion vectors
    non-zero and looked excellent on exactly these defaults, while PPSSPP reported 0%.
    """

    def test_emulators_get_the_same_effects_as_anything_else(self):
        preset = ConfigGenerator.generate_preset_ini(None)
        techniques = preset.splitlines()[0]
        for effect in ("Lumenite_RTAO", "Lumenite_LSAO", "Lumenite_TRAA"):
            self.assertIn(effect, techniques)

    def test_there_is_no_emulator_profile_rewriter(self):
        import dlss5_anywhere.config as config
        self.assertFalse(hasattr(config, "profile_for_emulator"))
        self.assertFalse(hasattr(config, "TEMPORAL_LUMENITE_EFFECTS"))

    def test_the_installer_does_not_rewrite_the_profile(self):
        """Silently changing what the picture looks like is not the installer's call."""
        import inspect
        from dlss5_anywhere.core import installer
        self.assertNotIn("profile_for_emulator", inspect.getsource(installer))


class TestDepthHeuristicsForEmulators(unittest.TestCase):
    """ReShade's depth heuristics assume a game rendering at the size of its own window.

    Measured on a real PCSX2 install with the game defaults: "Depth probe: min 0, max 0,
    variance 0" on every sample for a whole session, while the motion vectors underneath
    were fine. An emulator renders at an emulated internal resolution, so the buffer the
    aspect-ratio heuristic throws away is the one that matters.
    """

    def _ini(self, emulator_exe):
        from dlss5_anywhere.core.detector import GameDetector
        tmp = Path(tempfile.mkdtemp(prefix="dlss5_depth_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        exe = tmp / emulator_exe
        exe.write_bytes(b"MZ" + b"\0" * 128)
        return ConfigGenerator.generate_reshade_ini(GameDetector.analyze(exe))

    def _value(self, ini, key):
        for line in ini.splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1]
        return None

    def test_an_emulator_gets_both_heuristics_relaxed(self):
        ini = self._ini("pcsx2-qtx64.exe")
        self.assertEqual(self._value(ini, "UseAspectRatioHeuristics"), "0")
        self.assertEqual(self._value(ini, "DepthCopyBeforeClears"), "1")

    def test_a_normal_game_keeps_reshades_defaults(self):
        """These are the right values for something rendering at its own window size."""
        ini = self._ini("game.exe")
        self.assertEqual(self._value(ini, "UseAspectRatioHeuristics"), "1")
        self.assertEqual(self._value(ini, "DepthCopyBeforeClears"), "0")

    def test_the_clear_index_is_left_at_the_default(self):
        """Picking a specific clear is a per-game answer nothing here could know."""
        self.assertEqual(self._value(self._ini("pcsx2-qtx64.exe"), "DepthCopyAtClearIndex"), "0")


class TestEmuDeckExecutableNames(unittest.TestCase):
    """An install nobody recognised as an emulator got none of the emulator handling."""

    def test_emudecks_pcsx2_is_recognised(self):
        profile = find_emulator_profile("pcsx2-qtx64.exe")
        self.assertIsNotNone(profile, "EmuDeck ships this name")
        self.assertEqual(profile.id, "pcsx2")

    def test_the_older_names_still_match(self):
        for name in ("pcsx2-qt.exe", "pcsx2.exe", "pcsx2x64.exe", "pcsx2x64-avx2.exe"):
            with self.subTest(exe=name):
                self.assertEqual(find_emulator_profile(name).id, "pcsx2")

    def test_every_profile_lists_its_executables_in_lower_case(self):
        """find_emulator_profile lowercases the name it is given and nothing else."""
        for profile in EMULATOR_PROFILES.values():
            for name in profile.exe_names:
                with self.subTest(emulator=profile.id, exe=name):
                    self.assertEqual(name, name.lower())


class TestNotInstallingOverARunningGame(unittest.TestCase):
    """A locked DLL failed the copy partway through - after files, before configs."""

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_lock_"))
        self.addCleanup(shutil.rmtree, self.game, True)

    def plan(self, *dests):
        return StrategyPlan(
            strategy_id="x", display_name="x", description="x",
            target_exe=self.game / "game.exe", game_dir=self.game, is_64bit=True,
            items=[FilePlanItem(None, d, "") for d in dests],
        )

    def test_an_open_file_is_reported(self):
        target = self.game / "dinput8.dll"
        target.write_bytes(b"MZ")
        with open(target, "rb"):
            # Windows keeps an exclusive lock on a loaded DLL; an open handle stands in.
            locked = ModInstaller._locked_targets(self.game, self.plan("dinput8.dll"))
        if locked:
            self.assertEqual(locked, ["dinput8.dll"])

    def test_a_writable_file_is_not_reported(self):
        (self.game / "dinput8.dll").write_bytes(b"MZ")
        self.assertEqual(
            ModInstaller._locked_targets(self.game, self.plan("dinput8.dll")), []
        )

    def test_a_file_that_does_not_exist_yet_cannot_be_locked(self):
        self.assertEqual(
            ModInstaller._locked_targets(self.game, self.plan("dinput8.dll")), []
        )

    def test_nothing_is_written_by_the_check(self):
        target = self.game / "dinput8.dll"
        target.write_bytes(b"MZ")
        ModInstaller._locked_targets(self.game, self.plan("dinput8.dll"))
        self.assertEqual(target.read_bytes(), b"MZ")


if __name__ == "__main__":
    unittest.main()
