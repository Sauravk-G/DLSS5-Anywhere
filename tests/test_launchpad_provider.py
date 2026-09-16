"""
iMMERSE Launchpad as the motion-vector provider (DLSS5_MV_PROVIDER=1).

DLSS5_Feed.fx accepts five providers; this tool offered two. The one missing that
mattered was Launchpad, which is the provider to reach for exactly where the others come
back empty. Measured on real installs, the feed's probe reported "0.000 px, 0% non-zero"
on PPSSPP and PCSX2 - and with no motion vectors, DLSS accumulates the previous frame
without reprojecting it, which is a ghost image rather than a reconstruction.

Launchpad is Pascal Gilcher's, all rights reserved, so it is downloaded from his
repository rather than bundled.
"""

import unittest

from dlss5_anywhere.config import (
    COMPONENTS_REGISTRY,
    COMP_IMMERSE,
    MV_PROVIDERS,
    MV_PROVIDER_LAUNCHPAD,
    mv_provider_for,
)
from dlss5_anywhere.core.config_gen import ConfigGenerator


def preset(provider_id):
    return ConfigGenerator.generate_preset_ini({"mv_provider": provider_id})


class TestTheProviderIsOffered(unittest.TestCase):

    def test_launchpad_is_provider_one(self):
        """The number is DLSS5_Feed.fx's, not ours - it has to match the shader."""
        self.assertEqual(MV_PROVIDER_LAUNCHPAD, 1)
        self.assertIn(MV_PROVIDER_LAUNCHPAD, MV_PROVIDERS)

    def test_the_technique_is_spelled_the_way_the_shader_declares_it(self):
        """A preset naming a technique that does not exist silently enables nothing."""
        self.assertEqual(
            MV_PROVIDERS[MV_PROVIDER_LAUNCHPAD].technique,
            "MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx",
        )

    def test_it_is_selectable_from_a_profile(self):
        self.assertEqual(mv_provider_for({"mv_provider": 1}).id, MV_PROVIDER_LAUNCHPAD)


class TestThePresetItProduces(unittest.TestCase):

    def test_launchpad_runs_first(self):
        """Its own ui_label reads "enable and move to the top"."""
        techniques = preset(1).splitlines()[0].split("=", 1)[1].split(",")
        self.assertEqual(techniques[0], "MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx")

    def test_it_runs_above_the_feed(self):
        techniques = preset(1).splitlines()[0].split("=", 1)[1].split(",")
        self.assertLess(
            techniques.index("MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx"),
            techniques.index("DLSS5_Feed@DLSS5_Feed.fx"),
            "the feed would read vectors that have not been written yet",
        )

    def test_the_kernel_still_runs_for_the_other_passes(self):
        """RTAO, LSAO and TRAA read Kernel::sFlow whoever is feeding DLSS."""
        self.assertIn("Lumenite_Kernel@lumenite_Kernel.fx", preset(1).splitlines()[0])

    def test_the_feed_is_compiled_for_provider_one(self):
        body = preset(1)
        section = body[body.index("[DLSS5_Feed.fx]"):]
        self.assertIn("PreprocessorDefinitions=DLSS5_MV_PROVIDER=1", section)

    def test_choosing_it_does_not_disturb_the_other_providers(self):
        for pid in (2, 3):
            with self.subTest(provider=pid):
                self.assertNotIn("MartysMods_Launchpad", preset(pid).splitlines()[0])


class TestTheComponent(unittest.TestCase):

    def test_it_is_downloaded_rather_than_bundled(self):
        """Copyright (c) Pascal Gilcher, all rights reserved - we do not redistribute it."""
        meta = COMPONENTS_REGISTRY[COMP_IMMERSE]
        self.assertFalse(meta.is_user_supplied)
        self.assertEqual(meta.download_url, "https://github.com/martymcmodding/iMMERSE")

    def test_the_headers_are_part_of_what_is_expected(self):
        """Launchpad includes eight MartysMods/mmx_*.fxh headers and needs that folder."""
        expected = COMPONENTS_REGISTRY[COMP_IMMERSE].expected_files
        self.assertIn("Shaders\\MartysMods_LAUNCHPAD.fx", expected)
        self.assertTrue(any("MartysMods\\mmx_" in f for f in expected))

    def test_the_blue_noise_texture_is_expected_too(self):
        """It was left out of the first version of this component, and the failure was

        quiet in the worst way: not a compile error naming a header, but

            Source 'iMMERSE_bluenoise_opt.png' for texture 'V__BlueNoiseJitterTex' was
            not found in any of the texture search paths!

        after which the effect does not run and the provider writes nothing - which reads
        exactly like a motion vector field that is simply empty.
        """
        expected = COMPONENTS_REGISTRY[COMP_IMMERSE].expected_files
        self.assertTrue(
            any(f.endswith("iMMERSE_bluenoise_opt.png") for f in expected),
            "a shader pack is not complete without the textures its shaders sample",
        )

    def test_the_provider_points_at_that_component(self):
        self.assertEqual(MV_PROVIDERS[MV_PROVIDER_LAUNCHPAD].component, COMP_IMMERSE)


if __name__ == "__main__":
    unittest.main()
