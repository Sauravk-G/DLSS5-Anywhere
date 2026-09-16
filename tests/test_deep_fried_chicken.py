"""
Deep Fried Chicken as an alternative neural consumer.

Two add-ons can run the DLSS 5 neural passes and they cannot share a process: both
intercept the same NGX feature-1 entry points, and Chicken's own documentation for that
situation is "one or the other, never both". Selecting it therefore replaces
renodx-dlss5 rather than joining it, and takes Alex's Toolkit with it - the cascade only
attaches to RenoDX.

Its configuration is 346 lines: sixteen globals and thirty layer blocks of eleven keys
each. This tool patches the three keys it owns and carries the rest through untouched.
Generating a third-party config from a partial idea of its schema is exactly how
alexs-toolkit.cfg lost five of its eight keys.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from dlss5_anywhere.config import (
    COMPONENTS_REGISTRY,
    COMP_DFC,
    DEFAULT_PROFILE,
    uses_dfc,
)
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.installer import ModInstaller

VENDOR_CFG = (
    "config_schema=6\n"
    "arm=0\n"
    "enabled=0\n"
    "layers=1\n"
    "neural_work_percent=100\n"
    "texture_boost=0\n"
    "layer_1_nr_preset=0\n"
    "layer_1_intensity=2.000\n"
    "layer_2_nr_preset=0\n"
)


def keys(text):
    return [line.split("=", 1)[0] for line in text.splitlines() if "=" in line]


def value(text, key):
    for line in text.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


class TestChoosingTheConsumer(unittest.TestCase):

    def test_renodx_is_still_the_default(self):
        self.assertEqual(DEFAULT_PROFILE["neural_consumer"], "renodx")
        self.assertFalse(uses_dfc(None))

    def test_a_profile_can_select_chicken(self):
        self.assertTrue(uses_dfc({"neural_consumer": "dfc"}))
        self.assertTrue(uses_dfc({"neural_consumer": "DFC"}))

    def test_an_unknown_consumer_is_not_chicken(self):
        """A typo must not silently swap the whole neural stack."""
        self.assertFalse(uses_dfc({"neural_consumer": "chicken"}))

    def test_it_is_user_supplied(self):
        """Its licence asks that the release link be shared, not the archive rehosted."""
        self.assertTrue(COMPONENTS_REGISTRY[COMP_DFC].is_user_supplied)

    def test_its_own_config_is_one_of_the_expected_files(self):
        """It is patched rather than generated, so the vendor copy has to be imported."""
        self.assertIn("deep-fried-chicken.cfg", COMPONENTS_REGISTRY[COMP_DFC].expected_files)


class TestPatchingItsConfig(unittest.TestCase):

    def test_every_key_survives_in_order(self):
        out = ConfigGenerator.patch_dfc_cfg(VENDOR_CFG, None)
        self.assertEqual(keys(out), keys(VENDOR_CFG))

    def test_only_the_owned_keys_change(self):
        out = ConfigGenerator.patch_dfc_cfg(VENDOR_CFG, {"dfc_layers": 3})
        changed = [(a, b) for a, b in zip(VENDOR_CFG.splitlines(), out.splitlines()) if a != b]
        self.assertEqual({b.split("=")[0] for _, b in changed}, {"arm", "enabled", "layers"})

    def test_the_layer_count_comes_from_the_profile(self):
        self.assertEqual(value(ConfigGenerator.patch_dfc_cfg(VENDOR_CFG, {"dfc_layers": 7}), "layers"), "7")

    def test_arm_is_forced_on(self):
        """arm=0 is a restart-only hard disarm: it claims nothing and hooks nothing, so the
        feeder would find Chicken present and idle."""
        self.assertEqual(value(ConfigGenerator.patch_dfc_cfg(VENDOR_CFG, None), "arm"), "1")
        self.assertEqual(value(ConfigGenerator.patch_dfc_cfg(VENDOR_CFG, None), "enabled"), "1")

    def test_the_layer_count_is_clamped_to_what_it_supports(self):
        for asked, expected in ((0, "1"), (99, "30"), (-4, "1")):
            with self.subTest(asked=asked):
                out = ConfigGenerator.patch_dfc_cfg(VENDOR_CFG, {"dfc_layers": asked})
                self.assertEqual(value(out, "layers"), expected)

    def test_a_nonsense_layer_count_falls_back(self):
        out = ConfigGenerator.patch_dfc_cfg(VENDOR_CFG, {"dfc_layers": "lots"})
        self.assertEqual(value(out, "layers"), "1")

    def test_a_missing_key_is_appended_rather_than_assumed(self):
        out = ConfigGenerator.patch_dfc_cfg("config_schema=6\n", None)
        for key in ("layers", "arm", "enabled"):
            self.assertIsNotNone(value(out, key), key)

    def test_line_endings_are_left_alone(self):
        out = ConfigGenerator.patch_dfc_cfg(
            "arm=0\r\nlayers=1\r\nenabled=0\r\n", None)
        self.assertEqual(out, "arm=1\r\nlayers=1\r\nenabled=1\r\n")

    def test_an_appended_key_uses_the_files_own_line_ending(self):
        """Appending a bare newline to a CRLF config leaves it mixed."""
        out = ConfigGenerator.patch_dfc_cfg("arm=0\r\nlayers=1\r\n", None)
        self.assertEqual(out, "arm=1\r\nlayers=1\r\nenabled=1\r\n")


class TestNotLoadingFromDllMain(unittest.TestCase):
    """Chicken must not be named in [ADDON] LoadFromDllMain.

    It writes that key into a ReShade.ini beside itself, which ReShade never reads, so
    putting it into the real config looked like an obvious fix. It is not: the key loads
    an add-on from DllMain, inside the loader lock, and Chicken's first log line is
    "initialized outside loader lock".

        without it   initialised and armed on two emulators across three runs
        with it      "No add-on was registered by deep-fried-chicken.addon64.
                      Unloading again", and its own log never written

    Written empty rather than omitted, so a value from an older install cannot survive.
    """

    def _ini(self, profile):
        import tempfile
        from dlss5_anywhere.config import STRATEGY_FEEDER_DX11_12
        from dlss5_anywhere.core.detector import GameAnalysis
        tmp = Path(tempfile.mkdtemp(prefix="dlss5_early_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        exe = tmp / "game.exe"
        exe.write_bytes(b"MZ")
        analysis = GameAnalysis(
            exe_path=exe, game_dir=tmp, exe_name="game.exe", file_size_bytes=2,
            architecture="x64", is_64bit=True, detected_apis=["DirectX 11"],
            primary_api="DirectX 11", recommended_strategy=STRATEGY_FEEDER_DX11_12,
        )
        return ConfigGenerator.generate_reshade_ini(analysis, profile)

    def _value(self, ini, key):
        for line in ini.splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1]
        return None

    def test_chicken_is_not_named_there(self):
        self.assertEqual(self._value(self._ini({"neural_consumer": "dfc"}), "LoadFromDllMain"), "")

    def test_nor_is_anything_else(self):
        self.assertEqual(self._value(self._ini(None), "LoadFromDllMain"), "")

    def test_the_key_is_still_written_so_an_old_value_cannot_persist(self):
        self.assertIsNotNone(self._value(self._ini(None), "LoadFromDllMain"))


class TestNotSharingAProcess(unittest.TestCase):

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_dfc_"))
        self.addCleanup(shutil.rmtree, self.game, True)
        self.addons = self.game / "dlss5-addons"
        self.addons.mkdir()
        self.backup = self.game / ".dlss5_backup"

    def test_the_rival_consumers_are_removed(self):
        for name in ("renodx-dlss5.addon64", "alexs-toolkit.addon64"):
            (self.addons / name).write_bytes(b"MZ")
        moved = ModInstaller._remove_rival_neural_addons(self.game, self.backup)
        self.assertEqual(sorted(moved), ["alexs-toolkit.addon64", "renodx-dlss5.addon64"])
        for name in moved:
            self.assertFalse((self.addons / name).exists())

    def test_they_go_to_the_backup_rather_than_away(self):
        """One of them is this tool's own default; a later install puts it straight back."""
        (self.addons / "renodx-dlss5.addon64").write_bytes(b"MZ-renodx")
        ModInstaller._remove_rival_neural_addons(self.game, self.backup)
        self.assertEqual((self.backup / "renodx-dlss5.addon64").read_bytes(), b"MZ-renodx")

    def test_chicken_itself_is_never_removed(self):
        (self.addons / "deep-fried-chicken.addon64").write_bytes(b"MZ")
        (self.addons / "dlss5-feed.addon64").write_bytes(b"MZ")
        self.assertEqual(ModInstaller._remove_rival_neural_addons(self.game, self.backup), [])
        self.assertTrue((self.addons / "deep-fried-chicken.addon64").exists())

    def test_the_feeder_is_kept(self):
        """Chicken consumes the feeder's contract; removing it would defeat the point."""
        (self.addons / "dlss5-feed.addon64").write_bytes(b"MZ")
        ModInstaller._remove_rival_neural_addons(self.game, self.backup)
        self.assertTrue((self.addons / "dlss5-feed.addon64").exists())

    def test_a_folder_without_an_addon_directory_is_left_alone(self):
        shutil.rmtree(self.addons)
        self.assertEqual(ModInstaller._remove_rival_neural_addons(self.game, self.backup), [])


if __name__ == "__main__":
    unittest.main()
