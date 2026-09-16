"""Multi Frame Generation on an RTX 40 card.

NVIDIA generates up to three frames per rendered one in DLSS 4 and ships that gated to
RTX 50, leaving Ada with single frame generation. MFGAdaUnlock-RenoDx lifts the gate from
inside the process and corrects the temporal midpoint so the extra frames carry new
motion. It is a ReShade add-on configured out of ReShade.ini, which is exactly the shape
of everything else this tool deploys - so the integration is small, and what needs care
is the part that is unlike everything else here.

That part: it MULTIPLIES frame generation a game already ships. It cannot add it. Every
other path in this tool exists for a game with no DLSS at all, where the feeder builds
the contract out of ReShade's depth and estimated motion vectors - and on those games
this add-on loads, reports itself, and has nothing to work on. So the tests below are
mostly about the install saying so rather than about files landing in folders.
"""

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from dlss5_anywhere.config import (
    COMP_MFG_UNLOCK,
    COMP_NVNGX_DLSSG,
    COMPONENTS_REGISTRY,
    MFG_ADDON_NAME,
    MFG_COUNT_RANGE,
    MFG_MIN_DRIVER,
    MFG_MULTIPLIER_CHOICES,
    MFG_MULTIPLIER_LABELS,
    MFG_SECTION,
    STRATEGY_FEEDER_DX11_12,
    mfg_settings,
    wants_mfg_unlock,
)
from dlss5_anywhere.core import strategy as strategy_module
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.detector import GameAnalysis, GameDetector
from dlss5_anywhere.core.strategy import StrategyEngine

ON = {"mfg_unlock": True}


def analysis(tmp, **overrides):
    exe = tmp / "game.exe"
    exe.write_bytes(b"MZ")
    base = GameAnalysis(
        exe_path=exe, game_dir=tmp, exe_name="game.exe", file_size_bytes=2,
        architecture="x64", is_64bit=True, detected_apis=["DirectX 12"],
        primary_api="DirectX 12", recommended_strategy=STRATEGY_FEEDER_DX11_12,
        has_frame_generation=True,
    )
    return replace(base, **overrides)


class TestTheProfileSwitch(unittest.TestCase):

    def test_it_is_off_unless_asked_for(self):
        """It applies to a different kind of game than the rest of the tool, so it is not
        something to inherit by accident."""
        self.assertFalse(wants_mfg_unlock(None))
        self.assertFalse(wants_mfg_unlock({}))

    def test_and_on_when_it_is(self):
        self.assertTrue(wants_mfg_unlock(ON))

    def test_the_component_is_registered_and_downloadable(self):
        """MIT licensed, so unlike the other neural add-ons it can be fetched rather than
        imported by hand."""
        meta = COMPONENTS_REGISTRY[COMP_MFG_UNLOCK]
        self.assertFalse(meta.is_user_supplied)
        self.assertEqual(meta.expected_files, [MFG_ADDON_NAME])
        self.assertTrue(meta.repo_api_url.startswith("https://api.github.com/"))


class TestTheIniSection(unittest.TestCase):
    """The add-on reads its settings from the game folder's ReShade.ini, like RenoDX."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_mfg_ini_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _ini(self, profile):
        return ConfigGenerator.generate_reshade_ini(analysis(self.tmp), profile)

    def _value(self, ini, key):
        section, found = False, None
        for line in ini.splitlines():
            if line.startswith("["):
                section = line.strip() == f"[{MFG_SECTION}]"
            elif section and line.startswith(key + "="):
                found = line.split("=", 1)[1]
        return found

    def test_no_section_at_all_when_it_is_off(self):
        """An [MFGUnlock] block in every ReShade.ini would read as a feature that is on."""
        self.assertNotIn(MFG_SECTION, self._ini(None))

    def test_the_defaults_are_the_addons_own(self):
        ini = self._ini(ON)
        self.assertIn(f"[{MFG_SECTION}]", ini)
        self.assertEqual(self._value(ini, "Enabled"), "1")
        self.assertEqual(self._value(ini, "MaxCount"), "4")
        self.assertEqual(self._value(ini, "HDRCompatibilityMode"), "2")

    def test_the_multiplier_is_left_to_the_game_by_default(self):
        """Forcing a multiplier the game's own UI does not know about is the setting most
        likely to look broken from inside the game."""
        self.assertEqual(self._value(self._ini(ON), "ForceMultiplier"), "0")

    def test_dynamic_mfg_is_off_by_default(self):
        """It has a driver floor under it that nothing here can check."""
        self.assertEqual(self._value(self._ini(ON), "DynamicMFG"), "0")

    def test_the_profile_drives_every_key(self):
        ini = self._ini({**ON, "mfg_max_count": 6, "mfg_force_multiplier": 3,
                         "mfg_dynamic": True, "mfg_hdr_compat": 1})
        self.assertEqual(self._value(ini, "MaxCount"), "6")
        self.assertEqual(self._value(ini, "ForceMultiplier"), "3")
        self.assertEqual(self._value(ini, "DynamicMFG"), "1")
        self.assertEqual(self._value(ini, "HDRCompatibilityMode"), "1")

    def test_a_count_outside_the_addons_range_falls_back(self):
        """1 would mean no generation and 99 is not a thing it offers; either would be
        written straight into a config the add-on then has to make sense of."""
        for asked in (1, 0, 99, -2):
            with self.subTest(asked=asked):
                ini = self._ini({**ON, "mfg_max_count": asked})
                self.assertEqual(self._value(ini, "MaxCount"), "4")
        self.assertEqual(min(MFG_COUNT_RANGE), 2)

    def test_the_neural_section_is_untouched_by_it(self):
        """Two add-ons, two sections, one file - neither may eat the other."""
        ini = self._ini(ON)
        self.assertIn("[RenoDX.DLSS5]", ini)
        self.assertIn("NRUICorrection=", ini)


class TestWhatGetsDeployed(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_mfg_plan_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # A component folder with the add-on actually in it, so these tests do not depend
        # on whether this machine has pressed Download.
        self.components = self.tmp / "components"
        (self.components / COMP_MFG_UNLOCK).mkdir(parents=True)
        (self.components / COMP_MFG_UNLOCK / MFG_ADDON_NAME).write_bytes(b"MZ")
        patch = mock.patch.object(
            strategy_module.ComponentManager, "get_component_dir",
            side_effect=lambda comp: self.components / comp,
        )
        patch.start()
        self.addCleanup(patch.stop)

    def _plan(self, profile, **overrides):
        return StrategyEngine.build_plan(analysis(self.tmp, **overrides), profile=profile)

    def _destinations(self, plan):
        return {item.relative_dest.replace("\\", "/") for item in plan.items}

    def test_the_addon_lands_beside_the_others(self):
        self.assertIn(
            f"dlss5-addons/{MFG_ADDON_NAME}", self._destinations(self._plan(ON)))

    def test_nothing_is_deployed_when_it_is_off(self):
        self.assertNotIn(
            f"dlss5-addons/{MFG_ADDON_NAME}", self._destinations(self._plan(None)))

    def test_a_32bit_game_is_refused_and_told_why(self):
        """DLSS-G is x64 only, so there is no 32-bit frame generation to multiply."""
        plan = self._plan(ON, architecture="x86", is_64bit=False)
        self.assertNotIn(MFG_ADDON_NAME, " ".join(self._destinations(plan)))
        self.assertIn("32-bit", " ".join(plan.warnings))

    def test_the_card_and_driver_are_said_because_they_cannot_be_read(self):
        warnings = " ".join(self._plan(ON).warnings)
        self.assertIn("RTX 40", warnings)
        self.assertIn(MFG_MIN_DRIVER, warnings)

    def test_anti_cheat_is_named(self):
        """It is frame generation injected into a running game; that belongs nowhere near
        an anti-cheat, and this tool says so everywhere else it matters."""
        self.assertIn("anti-cheat", " ".join(self._plan(ON).warnings))

    def test_a_game_with_no_frame_generation_is_warned_about(self):
        """The failure this whole feature can produce: it loads, and does nothing."""
        plan = self._plan(ON, has_frame_generation=False)
        joined = " ".join(plan.warnings)
        self.assertIn("nvngx_dlssg.dll", joined)
        self.assertIn("cannot add it", joined)

    def test_but_it_is_still_deployed_there(self):
        """A game can carry DLSS-G in a subfolder the scan does not reach, and the choice
        is the user's - so this warns rather than overriding."""
        plan = self._plan(ON, has_frame_generation=False)
        self.assertIn(f"dlss5-addons/{MFG_ADDON_NAME}", self._destinations(plan))

    def test_a_game_that_has_it_is_not_nagged(self):
        self.assertNotIn("cannot add it", " ".join(self._plan(ON).warnings))

    def test_a_missing_download_is_reported_rather_than_crashing(self):
        (self.components / COMP_MFG_UNLOCK / MFG_ADDON_NAME).unlink()
        plan = self._plan(ON)
        self.assertIn(MFG_ADDON_NAME, " ".join(plan.warnings))
        row = next(i for i in plan.items if i.relative_dest.endswith(MFG_ADDON_NAME))
        self.assertIsNone(row.source_path, "the row stays, with nothing to copy from")


class TestFrameGenerationDetection(unittest.TestCase):
    """Whether the game ships DLSS-G at all - the one requirement readable from disk."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_mfg_det_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _fg(self, imports=()):
        return GameDetector._detect_upscalers(self.tmp, list(imports), [])[6]

    def test_the_frame_generation_runtime_is_the_signal(self):
        (self.tmp / "nvngx_dlssg.dll").write_bytes(b"MZ")
        self.assertTrue(self._fg())

    def test_streamlines_frame_generation_plugin_counts_too(self):
        (self.tmp / "sl.dlss_g.dll").write_bytes(b"MZ")
        self.assertTrue(self._fg())

    def test_the_upscaler_on_its_own_does_not(self):
        """nvngx_dlss.dll is super resolution. A game can ship it and no frame generation
        at all, and then there is nothing to multiply."""
        (self.tmp / "nvngx_dlss.dll").write_bytes(b"MZ")
        self.assertFalse(self._fg())

    def test_an_empty_folder_says_no(self):
        self.assertFalse(self._fg())

    def test_it_reaches_the_report(self):
        (self.tmp / "nvngx_dlssg.dll").write_bytes(b"MZ")
        exe = self.tmp / "game.exe"
        exe.write_bytes(b"MZ" + b"\x00" * 512)
        self.assertTrue(GameDetector.analyze(exe).has_frame_generation)


class TestTheTwoNumbersCannotContradict(unittest.TestCase):
    """MaxCount is the ceiling reported to the runtime; ForceMultiplier is what runs.

    Forcing 6x under a ceiling of 4 asks for something the same config has just said is
    unavailable. mfg_settings is the one place that reconciles them, and it raises the
    ceiling rather than clamping the choice - the number the user picked is the one they
    will go looking for in the overlay.
    """

    def test_the_defaults_are_the_addons_own(self):
        self.assertEqual(mfg_settings(None), (0, 4))

    def test_a_forced_value_raises_the_ceiling_to_cover_it(self):
        self.assertEqual(
            mfg_settings({"mfg_force_multiplier": 6, "mfg_max_count": 4}), (6, 6))

    def test_a_ceiling_above_the_forced_value_is_left_alone(self):
        """They are different questions: 3x runs, and the game may still be offered 4x."""
        self.assertEqual(
            mfg_settings({"mfg_force_multiplier": 3, "mfg_max_count": 4}), (3, 4))

    def test_a_multiplier_outside_the_range_falls_back_to_the_game(self):
        for asked in (1, 7, 99, -1):
            with self.subTest(asked=asked):
                self.assertEqual(mfg_settings({"mfg_force_multiplier": asked})[0], 0)

    def test_every_offered_choice_is_one_the_addon_accepts(self):
        for label, value in MFG_MULTIPLIER_CHOICES.items():
            with self.subTest(label=label):
                self.assertTrue(value == 0 or value in MFG_COUNT_RANGE)

    def test_the_labels_round_trip(self):
        for label, value in MFG_MULTIPLIER_CHOICES.items():
            self.assertEqual(MFG_MULTIPLIER_LABELS[value], label)


class TestTheFrameGenerationRuntime(unittest.TestCase):
    """The multi-frame code is in nvngx_dlssg.dll, not in the game."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_mfg_rt_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.supplied = self.tmp / "user_supplied"
        self.supplied.mkdir()
        self.components = self.tmp / "components"
        (self.components / COMP_MFG_UNLOCK).mkdir(parents=True)
        (self.components / COMP_MFG_UNLOCK / MFG_ADDON_NAME).write_bytes(b"MZ")
        for target, value in (("USER_SUPPLIED_DIR", self.supplied),):
            patch = mock.patch.object(strategy_module, target, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(
            strategy_module.ComponentManager, "get_component_dir",
            side_effect=lambda comp: self.components / comp,
        )
        patch.start()
        self.addCleanup(patch.stop)

    def _plan(self):
        return StrategyEngine.build_plan(analysis(self.tmp), profile=ON)

    def test_it_is_registered_as_a_component(self):
        meta = COMPONENTS_REGISTRY[COMP_NVNGX_DLSSG]
        self.assertTrue(meta.is_user_supplied, "NVIDIA's runtime is not ours to ship")
        self.assertEqual(meta.expected_files, ["nvngx_dlssg.dll"])

    def test_it_is_staged_beside_the_executable(self):
        """Not in the private add-on folder: the game's own loader looks beside the .exe."""
        (self.supplied / "nvngx_dlssg.dll").write_bytes(b"MZ")
        dests = {i.relative_dest.replace("\\", "/") for i in self._plan().items}
        self.assertIn("nvngx_dlssg.dll", dests)

    def test_a_missing_one_is_explained_rather_than_ignored(self):
        joined = " ".join(self._plan().warnings)
        self.assertIn("nvngx_dlssg.dll", joined)
        self.assertIn("310.x", joined)

    def test_it_is_not_staged_when_the_unlock_is_off(self):
        (self.supplied / "nvngx_dlssg.dll").write_bytes(b"MZ")
        plan = StrategyEngine.build_plan(analysis(self.tmp), profile=None)
        dests = {i.relative_dest.replace("\\", "/") for i in plan.items}
        self.assertNotIn("nvngx_dlssg.dll", dests)


class TestTheSwitchOnTheBuilderTab(unittest.TestCase):
    """A profile key nothing in the application can set is a key nobody will use."""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as exc:
            raise unittest.SkipTest(f"PySide6 not available: {exc}")
        cls.app = QApplication.instance() or QApplication([])

    def _view(self):
        from dlss5_anywhere.qtgui.views.dashboard_view import DashboardView
        view = DashboardView()
        self.addCleanup(view.deleteLater)
        return view

    def test_it_is_off_when_the_tab_opens(self):
        self.assertFalse(self._view().mfg_unlock.isChecked())

    def test_it_reaches_the_profile_the_build_uses(self):
        view = self._view()
        self.assertFalse(view._profile()["mfg_unlock"])
        view.mfg_unlock.setChecked(True)
        self.assertTrue(view._profile()["mfg_unlock"])

    def test_a_loaded_profile_moves_the_switch(self):
        """Otherwise the control silently overrides the profile on the next build."""
        view = self._view()
        view._apply_profile_to_controls({"mfg_unlock": True})
        self.assertTrue(view.mfg_unlock.isChecked())

    def test_a_game_with_no_frame_generation_is_called_out_at_the_switch(self):
        tmp = Path(tempfile.mkdtemp(prefix="dlss5_mfg_gui_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        view = self._view()
        view.mfg_unlock.setChecked(True)
        view.current_analysis = analysis(tmp, has_frame_generation=False)
        view._on_mfg_changed()
        self.assertIn("NOTHING TO MULTIPLY", view.mfg_note.text())

    def test_and_is_not_when_the_game_has_it(self):
        tmp = Path(tempfile.mkdtemp(prefix="dlss5_mfg_gui2_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        view = self._view()
        view.mfg_unlock.setChecked(True)
        view.current_analysis = analysis(tmp)
        view._on_mfg_changed()
        self.assertNotIn("NOTHING TO MULTIPLY", view.mfg_note.text())
        self.assertIn(MFG_MIN_DRIVER, view.mfg_note.text())


if __name__ == "__main__":
    unittest.main()
