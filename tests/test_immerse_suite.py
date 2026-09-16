"""iMMERSE is a suite, and picking it used to install one shader out of six.

Choosing iMMERSE as the motion vector provider staged `MartysMods_LAUNCHPAD.fx` and
stopped there. MXAO, SOLARIS, SMAA, Sharpen and Film Grain were downloaded into
components/immerse/Shaders/ and never copied anywhere, and the catalogue of tickable
effects held only LumeniteFX keys - so every visible pass in an "iMMERSE" build came from
LumeniteFX, which is not what picking iMMERSE is supposed to mean.
"""
import re
import unittest
from pathlib import Path

from dlss5_anywhere.config import (
    COMPONENTS_REGISTRY,
    COMP_IMMERSE,
    DEFAULT_PROFILE,
    MV_PROVIDER_LAUNCHPAD,
    MV_PROVIDER_LUMENITE,
)
from dlss5_anywhere.core.config_gen import (
    ConfigGenerator,
    IMMERSE_EFFECTS,
    LUMENITE_EFFECTS,
    VISIBLE_EFFECTS,
)
from dlss5_anywhere.core.components import ComponentManager
from dlss5_anywhere.core.strategy import StrategyEngine

SHADERS = ComponentManager.get_component_dir(COMP_IMMERSE) / "Shaders"
HAVE_IMMERSE = (SHADERS / "MartysMods_LAUNCHPAD.fx").is_file()


def techniques_for(profile):
    line = ConfigGenerator.generate_preset_ini(profile).splitlines()[0]
    assert line.startswith("Techniques=")
    return line.split("=", 1)[1].split(",")


def staged(profile):
    warnings = []
    items = StrategyEngine._motion_vector_items(
        {**DEFAULT_PROFILE, **profile}, warnings
    )
    return [Path(item.relative_dest).name for item in items], warnings


class TestTheCatalogueMatchesTheShaders(unittest.TestCase):
    """Technique names are read out of the .fx files, not guessed.

    Two of iMMERSE's are missing an "s" upstream - the file declares `MartyMods_Sharpen`
    and `MartyMods_FilmGrain` - and a preset naming a technique that does not exist is a
    line ReShade drops silently.
    """

    @unittest.skipUnless(HAVE_IMMERSE, "iMMERSE component not downloaded")
    def test_every_technique_is_declared_by_the_file_it_names(self):
        for key, entries in IMMERSE_EFFECTS.items():
            for entry in entries:
                technique, _, filename = entry.partition("@")
                source = (SHADERS / filename).read_text(encoding="utf-8", errors="ignore")
                self.assertRegex(
                    source,
                    rf"(?m)^\s*technique\s+{re.escape(technique)}\b",
                    f"{key}: {filename} does not declare technique {technique}",
                )

    @unittest.skipUnless(HAVE_IMMERSE, "iMMERSE component not downloaded")
    def test_no_immerse_effect_is_left_out_of_the_catalogue(self):
        """The whole point of the change: six shaders ship, six are offered."""
        shipped = {path.name for path in SHADERS.glob("MartysMods_*.fx")}
        offered = {
            entry.partition("@")[2]
            for entries in IMMERSE_EFFECTS.values()
            for entry in entries
        }
        offered.add("MartysMods_LAUNCHPAD.fx")  # the provider, not a visible effect
        self.assertEqual(shipped, offered)

    def test_the_two_suites_do_not_collide(self):
        self.assertEqual(set(LUMENITE_EFFECTS) & set(IMMERSE_EFFECTS), set())
        self.assertEqual(
            set(VISIBLE_EFFECTS), set(LUMENITE_EFFECTS) | set(IMMERSE_EFFECTS)
        )


class TestOrder(unittest.TestCase):
    def test_smaa_resolves_after_its_own_prepass(self):
        order = techniques_for({"lumenite_effects": ["smaa"]})
        self.assertLess(
            order.index("MartysMods_AntiAliasing_Prepass@MartysMods_SMAA.fx"),
            order.index("MartysMods_AntiAliasing@MartysMods_SMAA.fx"),
        )

    def test_mxao_pulls_launchpad_in_and_above_the_feed(self):
        """MXAO calls Deferred::get_normals and Deferred::get_motion; Launchpad writes both.

        Ticked on its own, with LumeniteFX still feeding DLSS, it would otherwise shade an
        empty G-buffer - installed, enabled, and drawing nothing.
        """
        order = techniques_for(
            {"mv_provider": MV_PROVIDER_LUMENITE, "lumenite_effects": ["mxao"]}
        )
        self.assertIn("MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx", order)
        self.assertLess(
            order.index("MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx"),
            order.index("DLSS5_Feed@DLSS5_Feed.fx"),
        )
        self.assertLess(
            order.index("DLSS5_Feed@DLSS5_Feed.fx"),
            order.index("MartysMods_MXAO@MartysMods_MXAO.fx"),
        )

    def test_visible_effects_are_drawn_on_the_neural_output(self):
        order = techniques_for(
            {"lumenite_effects": ["mxao", "solaris", "smaa", "sharpen", "filmgrain"]}
        )
        feed = order.index("DLSS5_Feed@DLSS5_Feed.fx")
        for entries in IMMERSE_EFFECTS.values():
            for entry in entries:
                self.assertGreater(order.index(entry), feed, entry)

    def test_launchpad_is_named_once_even_when_it_is_both_provider_and_dependency(self):
        order = techniques_for(
            {"mv_provider": MV_PROVIDER_LAUNCHPAD, "lumenite_effects": ["mxao"]}
        )
        self.assertEqual(
            order.count("MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx"), 1
        )
        self.assertEqual(order[0], "MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx")


@unittest.skipUnless(HAVE_IMMERSE, "iMMERSE component not downloaded")
class TestStaging(unittest.TestCase):
    def test_choosing_immerse_stages_the_whole_suite(self):
        names, warnings = staged(
            {"mv_provider": MV_PROVIDER_LAUNCHPAD, "lumenite_effects": ["rtao"]}
        )
        for filename in (
            "MartysMods_LAUNCHPAD.fx",
            "MartysMods_MXAO.fx",
            "MartysMods_SOLARIS.fx",
            "MartysMods_SMAA.fx",
            "MartysMods_SHARPEN.fx",
            "MartysMods_FILMGRAIN.fx",
        ):
            self.assertIn(filename, names)
        self.assertEqual(warnings, [])

    def test_an_immerse_effect_stages_it_even_when_lumenite_feeds_dlss(self):
        """The provider and the visible effects are separate choices.

        SMAA, Sharpen and Film Grain care about neither, so tying the suite to being the
        motion vector provider meant they could be ticked and never installed.
        """
        names, _ = staged(
            {"mv_provider": MV_PROVIDER_LUMENITE, "lumenite_effects": ["smaa"]}
        )
        self.assertIn("MartysMods_SMAA.fx", names)

    def test_nothing_from_immerse_when_nothing_asks_for_it(self):
        names, _ = staged(
            {"mv_provider": MV_PROVIDER_LUMENITE, "lumenite_effects": ["rtao", "traa"]}
        )
        self.assertEqual([n for n in names if n.startswith("MartysMods")], [])

    def test_the_textures_the_other_effects_sample_are_staged(self):
        """MXAO and SMAA sample a texture each, and neither was ever copied.

        A missing one is not a compile error - ReShade logs that the source was not found
        in any texture search path and the effect simply does not run.
        """
        names, _ = staged({"mv_provider": MV_PROVIDER_LAUNCHPAD})
        for texture in (
            "iMMERSE_bluenoise_opt.png",
            "iMMERSE_bluenoise_temporal.png",
            "AreaLUT.png",
        ):
            self.assertIn(texture, names)

    def test_the_headers_keep_their_folder_name(self):
        names, _ = staged({"mv_provider": MV_PROVIDER_LAUNCHPAD})
        self.assertIn("MartysMods", names)


class TestTheComponentAdvertisesTheSuite(unittest.TestCase):
    def test_the_extra_effects_are_expected_from_the_download(self):
        expected = COMPONENTS_REGISTRY[COMP_IMMERSE].expected_files
        self.assertIn("Shaders\\MartysMods_MXAO.fx", expected)
        self.assertIn("Shaders\\MartysMods_SMAA.fx", expected)

    def test_so_are_the_textures_they_sample(self):
        expected = COMPONENTS_REGISTRY[COMP_IMMERSE].expected_files
        self.assertIn("Textures\\iMMERSE_bluenoise_temporal.png", expected)
        self.assertIn("Textures\\AreaLUT.png", expected)


class TestTheEditorOffersThemAll(unittest.TestCase):
    """A key with no checkbox is one the profile editor deletes on the next save."""

    def test_every_catalogue_key_has_a_box(self):
        try:
            from dlss5_anywhere.qtgui.views.profiles_view import EFFECT_LABELS
        except ImportError as exc:  # PySide6 is optional for the test run
            self.skipTest(f"Qt not available: {exc}")
        self.assertEqual({key for key, _ in EFFECT_LABELS}, set(VISIBLE_EFFECTS))
