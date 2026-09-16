"""
alexs-toolkit.cfg, the multi-pass DLSS 5 cascade.

Alex's Toolkit is an optional third-party add-on that runs the DLSS 5 neural pass more
than once over the same frame. This tool does not ship it and cannot fetch it - it is
passed around informally - but it generates its settings file and puts it in the right
place, so an install is complete the moment the add-on itself is dropped in.

The shape of the file and the placement rule come from DLSS5-Feeder's DEPLOY-DEV.md:
the add-on and its cfg go wherever renodx-dlss5.addon64 went, and three_pass is the
two-pass chain with a stage inserted (B->A becomes B->C->A), so it requires two_pass=1.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from dlss5_anywhere.config import ADDON_DIR_NAME, STRATEGY_FEEDER_DX11_12
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.detector import GameAnalysis


def cfg(profile=None) -> dict:
    text = ConfigGenerator.generate_toolkit_cfg(profile)
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


class TestTheCascadeKeys(unittest.TestCase):

    # Exactly what v0.9.0-beta ships, in its order. DEPLOY-DEV.md documents only the
    # first three; writing only those would delete the rest from a file the add-on owns.
    SHIPPED_KEYS = [
        "enabled", "two_pass", "three_pass", "adaptive_extreme",
        "stage_b_blend", "stage_c_blend", "texture_boost", "texture_boost_strength",
    ]

    def test_every_key_the_add_on_ships_is_written(self):
        self.assertEqual(list(cfg()), self.SHIPPED_KEYS)

    def test_the_undocumented_keys_carry_the_add_ons_own_defaults(self):
        """We have no basis for a better value than the one it shipped with."""
        written = cfg()
        self.assertEqual(written["adaptive_extreme"], "0")
        self.assertEqual(written["stage_b_blend"], "1.00")
        self.assertEqual(written["stage_c_blend"], "1.00")
        self.assertEqual(written["texture_boost"], "0")
        self.assertEqual(written["texture_boost_strength"], "1.00")

    def test_the_shipped_default_is_the_two_pass_cascade(self):
        """Two-pass is the safe setting; three trades settle time for a heavier look.

        The one place we deliberately differ from the add-on's own file, which ships
        two_pass=0 - the cascade is the reason it is being installed at all.
        """
        self.assertEqual(cfg()["two_pass"], "1")

    def test_three_pass_switches_two_pass_on_with_it(self):
        """B->C->A is B->A with a stage inserted, so the guide requires two_pass=1.

        Asking for three passes without two is asking for a cascade that does not exist.
        Writing the contradiction out would leave the add-on to resolve it.
        """
        written = cfg({"toolkit_two_pass": False, "toolkit_three_pass": True})
        self.assertEqual((written["two_pass"], written["three_pass"]), ("1", "1"))

    def test_both_off_is_a_single_pass_and_stays_that_way(self):
        written = cfg({"toolkit_two_pass": False, "toolkit_three_pass": False})
        self.assertEqual((written["two_pass"], written["three_pass"]), ("0", "0"))

    def test_the_blends_are_written_to_two_decimals(self):
        written = cfg({"toolkit_stage_b_blend": 0.5, "toolkit_stage_c_blend": 2})
        self.assertEqual((written["stage_b_blend"], written["stage_c_blend"]), ("0.50", "2.00"))

    def test_a_nonsense_blend_falls_back_rather_than_writing_it(self):
        """The add-on re-reads this live; a broken line is worse than a default."""
        self.assertEqual(cfg({"toolkit_stage_b_blend": "loud"})["stage_b_blend"], "1.00")

    def test_the_add_on_can_be_disabled_outright(self):
        self.assertEqual(cfg({"toolkit_enabled": False})["enabled"], "0")

    def test_the_flags_are_ones_and_zeroes(self):
        """The add-on reads this file live; anything else is a parse it might not do."""
        flags = ("enabled", "two_pass", "three_pass", "adaptive_extreme", "texture_boost")
        for profile in (None, {"toolkit_three_pass": True}, {"toolkit_enabled": False}):
            with self.subTest(profile=profile):
                written = cfg(profile)
                for key in flags:
                    self.assertIn(written[key], ("0", "1"), key)


class TestWhereItGoes(unittest.TestCase):
    """It has to land beside renodx-dlss5.addon64 - the feeder looks for it there."""

    def _plan_destinations(self):
        from dlss5_anywhere.core.strategy import StrategyEngine
        tmp = Path(tempfile.mkdtemp(prefix="dlss5_tk_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        exe = tmp / "game.exe"
        exe.write_bytes(b"MZ")
        analysis = GameAnalysis(
            exe_path=exe, game_dir=tmp, exe_name="game.exe", file_size_bytes=2,
            architecture="x64", is_64bit=True, detected_apis=["DirectX 11"],
            primary_api="DirectX 11", recommended_strategy=STRATEGY_FEEDER_DX11_12,
        )
        return [i.relative_dest.replace("\\", "/") for i in StrategyEngine.build_plan(analysis).items]

    def test_the_toolkit_would_land_beside_the_neural_add_on(self):
        """Only checkable when the add-on is present; the rule is the shared prefix."""
        dests = self._plan_destinations()
        neural = [d for d in dests if d.endswith("renodx-dlss5.addon64")]
        self.assertTrue(neural, "the neural add-on should always be planned")
        self.assertEqual(Path(neural[0]).parent.as_posix(), ADDON_DIR_NAME)
        toolkit = [d for d in dests if d.endswith("alexs-toolkit.addon64")]
        for path in toolkit:  # empty when the optional add-on is not installed locally
            self.assertEqual(Path(path).parent.as_posix(), ADDON_DIR_NAME)


if __name__ == "__main__":
    unittest.main()
