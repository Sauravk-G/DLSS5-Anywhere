"""A saved profile has to be the thing that gets built.

The Profiles tab wrote profiles/<name>.json; the Builder assembled its own six-key
dictionary from the sliders on its page and let DEFAULT_PROFILE supply the rest. So a
profile could be created, saved, and change nothing: the effect list, the neural rendering
settings, the cascade and the D3D9 path all came from the defaults, and the only settings
that ever varied were the ones with a widget on the Builder page.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dlss5_anywhere import config
from dlss5_anywhere.config import DEFAULT_PROFILE, MV_PROVIDER_LAUNCHPAD
from dlss5_anywhere.core import profiles as profiles_module
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.profiles import ProfileManager

# A profile that differs from the defaults in exactly the places that used to be dropped.
CUSTOM = {
    **DEFAULT_PROFILE,
    "mv_provider": MV_PROVIDER_LAUNCHPAD,
    "lumenite_effects": ["traa", "mxao", "smaa", "sharpen"],
    "toolkit_three_pass": True,
    "toolkit_two_pass": False,
    "nr_style": 1,
}


class TestItSurvivesTheRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_profiles_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._original = profiles_module.PROFILES_DIR
        profiles_module.PROFILES_DIR = self.tmp
        self.addCleanup(setattr, profiles_module, "PROFILES_DIR", self._original)

    def test_saving_then_loading_keeps_the_settings_the_builder_ignored(self):
        ProfileManager.save_profile("Round Trip", CUSTOM)
        loaded = ProfileManager.load_profile("Round Trip")
        self.assertEqual(loaded["lumenite_effects"], CUSTOM["lumenite_effects"])
        self.assertEqual(loaded["mv_provider"], MV_PROVIDER_LAUNCHPAD)
        self.assertTrue(loaded["toolkit_three_pass"])
        self.assertEqual(loaded["nr_style"], 1)

    def test_a_custom_profile_is_listed_next_to_the_presets(self):
        ProfileManager.save_profile("Round Trip", CUSTOM)
        self.assertIn("Round Trip", ProfileManager.list_profiles())


class TestItReachesTheGeneratedFiles(unittest.TestCase):
    """Loading it is only half the job; the generators have to see the same dictionary."""

    def test_the_preset_runs_the_effects_the_profile_chose(self):
        preset = ConfigGenerator.generate_preset_ini(CUSTOM)
        techniques = preset.splitlines()[0].split("=", 1)[1]
        self.assertIn("MartysMods_MXAO@MartysMods_MXAO.fx", techniques)
        self.assertIn("MartysMods_AntiAliasing@MartysMods_SMAA.fx", techniques)
        self.assertIn("MartyMods_Sharpen@MartysMods_SHARPEN.fx", techniques)

    def test_the_feed_is_compiled_for_the_profiles_provider(self):
        self.assertIn(
            "PreprocessorDefinitions=DLSS5_MV_PROVIDER=1",
            ConfigGenerator.generate_preset_ini(CUSTOM),
        )

    def test_the_cascade_is_the_one_the_profile_asked_for(self):
        cfg = ConfigGenerator.generate_toolkit_cfg(CUSTOM)
        self.assertIn("three_pass=1", cfg)


class TestTheBuilderStartsFromTheSelectedProfile(unittest.TestCase):
    """The Builder page merges its own five controls over the selected profile.

    Written as a widget test because the bug was not in any of the generators - each of
    them did exactly what it was given. It was that nothing ever gave them a profile.
    """

    @classmethod
    def setUpClass(cls):
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

    def test_the_builder_page_offers_a_profile_at_all(self):
        view = self._view()
        listed = [
            view.profile_combo.itemText(i) for i in range(view.profile_combo.count())
        ]
        self.assertEqual(listed, ProfileManager.list_profiles())
        self.assertTrue(view.profile_combo.currentText())

    def test_selecting_one_carries_the_keys_no_widget_covers(self):
        view = self._view()
        name = "Zz Builder Test"
        ProfileManager.save_profile(name, CUSTOM)
        self.addCleanup(ProfileManager.delete_profile, name)
        view.reload_profiles()
        view.profile_combo.setCurrentText(name)

        built = view._profile()
        self.assertEqual(built["lumenite_effects"], CUSTOM["lumenite_effects"])
        self.assertTrue(built["toolkit_three_pass"])
        self.assertEqual(built["nr_style"], 1)

    def test_the_controls_move_to_match_so_the_page_is_not_lying(self):
        """Picking a profile has to load its values into the sliders.

        If it did not, the five settings those widgets cover would silently override the
        profile on the next build - which is the same bug in a smaller box.
        """
        view = self._view()
        name = "Zz Builder Controls"
        ProfileManager.save_profile(
            name, {**CUSTOM, "depth_reversed": True, "feed_work_resolution": 75}
        )
        self.addCleanup(ProfileManager.delete_profile, name)
        view.reload_profiles()
        view.profile_combo.setCurrentText(name)

        self.assertTrue(view.depth_reversed.isChecked())
        self.assertEqual(view.work_res.value(), 75)
        built = view._profile()
        self.assertTrue(built["depth_reversed"])
        self.assertEqual(built["feed_work_resolution"], 75)
        self.assertEqual(built["mv_provider"], MV_PROVIDER_LAUNCHPAD)

    def test_a_profile_saved_while_the_app_is_open_shows_up(self):
        view = self._view()
        name = "Zz Builder Late"
        before = view.profile_combo.count()
        ProfileManager.save_profile(name, CUSTOM)
        self.addCleanup(ProfileManager.delete_profile, name)
        view.reload_profiles(keep=view.profile_combo.currentText())
        self.assertEqual(view.profile_combo.count(), before + 1)
