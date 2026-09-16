"""Choosing between RenoDX + Alex's Toolkit and Deep Fried Chicken.

The installer has staged either one correctly for a long time, including into host64\\ for
a 32-bit D3D9 game behind dgVoodoo2. What was missing was any way to say which: the
"neural_consumer" key had no control in the application at all, so reaching Chicken meant
editing a profile's JSON by hand.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dlss5_anywhere.config import (
    DEFAULT_PROFILE,
    DFC_LAYER_RANGE,
    NEURAL_CONSUMER_CHOICES,
    NEURAL_CONSUMER_LABELS,
    STRATEGY_DGVOODOO_DX9,
    STRATEGY_FEEDER_DX11_12,
    uses_dfc,
)
from dlss5_anywhere.core import strategy
from dlss5_anywhere.core.detector import GameAnalysis
from dlss5_anywhere.core.profiles import ProfileManager
from dlss5_anywhere.core.strategy import StrategyEngine


class TestTheChoiceIsWellFormed(unittest.TestCase):
    def test_both_values_are_what_uses_dfc_reads(self):
        values = set(NEURAL_CONSUMER_CHOICES.values())
        self.assertEqual(values, {"renodx", "dfc"})
        self.assertTrue(uses_dfc({"neural_consumer": "dfc"}))
        self.assertFalse(uses_dfc({"neural_consumer": "renodx"}))

    def test_the_labels_round_trip(self):
        for label, value in NEURAL_CONSUMER_CHOICES.items():
            self.assertEqual(NEURAL_CONSUMER_LABELS[value], label)

    def test_the_default_profile_names_one_of_them(self):
        self.assertIn(DEFAULT_PROFILE["neural_consumer"], NEURAL_CONSUMER_CHOICES.values())

    def test_chicken_starts_at_one_pass(self):
        """Its own quick start says to begin at one, and thirty is its documented ceiling."""
        self.assertEqual(DFC_LAYER_RANGE.start, 1)
        self.assertEqual(DFC_LAYER_RANGE.stop, 31)
        self.assertIn(DEFAULT_PROFILE["dfc_layers"], DFC_LAYER_RANGE)


class TestAnOldGameGetsEitherOne(unittest.TestCase):
    """Prince of Persia's era: 32-bit, D3D9, translated by dgVoodoo2.

    NGX is 64-bit only, so the neural add-on runs in the helper process. That folder goes
    beside the add-on which spawns it, not beside the executable: the 32-bit add-on
    resolves host64\ against its own module directory, and while it sat in the game root
    the feed reported "the 64-bit host is not installed" with a complete host64\ right
    there. Alex's Toolkit and Deep Fried Chicken both live in it, so neither could run on
    any translation layer until this was right.
    """

    # Every add-on named here is user-supplied: none of it is in the repository, and the
    # plan only carries an optional one when the file is actually on disk. Reading the
    # real components folder therefore made these tests pass for whoever had pressed
    # Download and fail for everyone else - including every CI runner, which is the
    # strictest clean checkout there is. They get a folder of their own instead.
    SUPPLIED = (
        "renodx-dlss5.addon64",
        "alexs-toolkit.addon64",
        "deep-fried-chicken.addon64",
        "deep-fried-chicken-nvngx.dll",
        "nvngx_dlssnr.dll",
        "nvngx_dlss.dll",
    )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_dx9_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        exe = self.tmp / "PrinceOfPersia.exe"
        exe.write_bytes(b"MZ")
        self.analysis = GameAnalysis(
            exe_path=exe, game_dir=self.tmp, exe_name=exe.name, file_size_bytes=2,
            architecture="x86", is_64bit=False, detected_apis=["DirectX 9"],
            primary_api="DirectX 9", recommended_strategy=STRATEGY_DGVOODOO_DX9,
        )
        supplied = self.tmp / "user_supplied"
        supplied.mkdir()
        for name in self.SUPPLIED:
            (supplied / name).write_bytes(b"MZ")
        patch = mock.patch.object(strategy, "USER_SUPPLIED_DIR", supplied)
        patch.start()
        self.addCleanup(patch.stop)

    def _destinations(self, consumer):
        plan = StrategyEngine.build_plan(
            self.analysis,
            strategy_override=STRATEGY_DGVOODOO_DX9,
            profile={**DEFAULT_PROFILE, "neural_consumer": consumer},
        )
        self.assertTrue(plan.uses_host64, "a 32-bit game needs the 64-bit helper")
        return [item.relative_dest.replace("/", "\\") for item in plan.items]

    def test_renodx_and_the_toolkit_land_in_the_helper_folder(self):
        dests = self._destinations("renodx")
        self.assertIn("dlss5-addons\\host64\\renodx-dlss5.addon64", dests)
        self.assertIn("dlss5-addons\\host64\\alexs-toolkit.addon64", dests)

    def test_chicken_replaces_them_both(self):
        dests = self._destinations("dfc")
        self.assertIn("dlss5-addons\\host64\\deep-fried-chicken.addon64", dests)
        self.assertIn("dlss5-addons\\host64\\deep-fried-chicken-nvngx.dll", dests)
        self.assertNotIn("dlss5-addons\\host64\\renodx-dlss5.addon64", dests)
        self.assertNotIn("dlss5-addons\\host64\\alexs-toolkit.addon64", dests)

    def test_the_dlss_runtimes_go_with_whichever_is_chosen(self):
        for consumer in ("renodx", "dfc"):
            with self.subTest(consumer=consumer):
                dests = self._destinations(consumer)
                self.assertIn("dlss5-addons\\host64\\nvngx_dlssnr.dll", dests)
                self.assertIn("dlss5-addons\\host64\\nvngx_dlss.dll", dests)

    def test_dgvoodoo_still_translates_d3d9_underneath(self):
        dests = self._destinations("dfc")
        self.assertIn("D3D9.dll", dests)


class TestChickenIsNotImportedYet(unittest.TestCase):
    """Planning a Chicken build before its files have been imported.

    Which is the state every new install starts in: nothing here is redistributed, so a
    fresh checkout has an empty user_supplied folder and the Components tab is where the
    files come from. Selecting the consumer first is the obvious order to do it in.

    _dfc_items reports each missing file and puts None in the plan row so the build still
    shows what it needs - and the loop that turned those rows into FilePlanItems called
    .exists() on it:

        AttributeError: 'NoneType' object has no attribute 'exists'

    straight out of build_plan, before any of those warnings could be shown. It was
    invisible to anyone who had already downloaded the add-on, which is everyone who
    would have run this by hand.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_nodfc_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        exe = self.tmp / "game.exe"
        exe.write_bytes(b"MZ")
        self.analysis = GameAnalysis(
            exe_path=exe, game_dir=self.tmp, exe_name=exe.name, file_size_bytes=2,
            architecture="x64", is_64bit=True, detected_apis=["DirectX 11"],
            primary_api="DirectX 11", recommended_strategy=STRATEGY_FEEDER_DX11_12,
        )
        empty = self.tmp / "user_supplied"
        empty.mkdir()
        patch = mock.patch.object(strategy, "USER_SUPPLIED_DIR", empty)
        patch.start()
        self.addCleanup(patch.stop)

    def _plan(self):
        return StrategyEngine.build_plan(
            self.analysis, profile={**DEFAULT_PROFILE, "neural_consumer": "dfc"})

    def test_the_plan_is_still_built(self):
        self.assertTrue(self._plan().items)

    def test_and_says_which_files_are_missing(self):
        warnings = "\n".join(self._plan().warnings)
        for name in ("deep-fried-chicken.addon64", "deep-fried-chicken-nvngx.dll"):
            with self.subTest(name=name):
                self.assertIn(name, warnings)
        self.assertIn("Components tab", warnings)

    def test_the_rows_are_there_with_nothing_to_copy_from(self):
        """The row is what makes the build folder list the file it is waiting for."""
        rows = {
            item.relative_dest.replace("/", "\\"): item
            for item in self._plan().items
        }
        row = rows.get("dlss5-addons\\deep-fried-chicken.addon64")
        self.assertIsNotNone(row, sorted(rows))
        self.assertIsNone(row.source_path)


class TestTheWidgetsOfferIt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as exc:
            raise unittest.SkipTest(f"PySide6 not available: {exc}")
        cls.app = QApplication.instance() or QApplication([])

    def test_the_builder_puts_the_choice_into_the_build(self):
        from dlss5_anywhere.qtgui.views.dashboard_view import DashboardView
        view = DashboardView()
        self.addCleanup(view.deleteLater)

        self.assertEqual(view._profile()["neural_consumer"], "renodx")
        view.neural_consumer.setCurrentText("Deep Fried Chicken")
        self.assertEqual(view._profile()["neural_consumer"], "dfc")

    def test_selecting_a_profile_moves_the_builder_control(self):
        from dlss5_anywhere.qtgui.views.dashboard_view import DashboardView
        name = "Zz Chicken Profile"
        ProfileManager.save_profile(name, {**DEFAULT_PROFILE, "neural_consumer": "dfc"})
        self.addCleanup(ProfileManager.delete_profile, name)

        view = DashboardView()
        self.addCleanup(view.deleteLater)
        view.reload_profiles()
        view.profile_combo.setCurrentText(name)
        self.assertEqual(view.neural_consumer.currentText(), "Deep Fried Chicken")
        self.assertEqual(view._profile()["neural_consumer"], "dfc")

    def test_the_profile_editor_saves_and_reloads_it(self):
        from dlss5_anywhere.qtgui.views.profiles_view import ProfilesView
        view = ProfilesView()
        self.addCleanup(view.deleteLater)

        view.neural_consumer.setCurrentText("Deep Fried Chicken")
        view.dfc_layers.setCurrentText("4")
        settings = {
            "neural_consumer": NEURAL_CONSUMER_CHOICES[view.neural_consumer.currentText()],
            "dfc_layers": int(view.dfc_layers.currentText()),
        }
        self.assertEqual(settings, {"neural_consumer": "dfc", "dfc_layers": 4})

        view._load_profile("Balanced (Recommended)")
        self.assertEqual(view.neural_consumer.currentText(), "RenoDX + Alex's Toolkit")

    def test_only_the_half_that_applies_stays_live(self):
        """The cascade is Alex's Toolkit; the pass count is Chicken's. Never both."""
        from dlss5_anywhere.qtgui.views.profiles_view import ProfilesView
        view = ProfilesView()
        self.addCleanup(view.deleteLater)

        view.neural_consumer.setCurrentText("RenoDX + Alex's Toolkit")
        self.assertTrue(view.cascade.isEnabled())
        self.assertFalse(view.dfc_layers.isEnabled())

        view.neural_consumer.setCurrentText("Deep Fried Chicken")
        self.assertFalse(view.cascade.isEnabled())
        self.assertTrue(view.dfc_layers.isEnabled())
