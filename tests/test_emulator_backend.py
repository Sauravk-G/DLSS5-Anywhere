"""
Reading the renderer an emulator is actually set to.

An install into PPSSPP put a dxgi.dll next to the executable and reported success. That
PPSSPP was set to Vulkan, which never loads a dxgi.dll, so ReShade never attached and the
add-ons never ran - with no error anywhere, because nothing had gone wrong: the files were
all present and simply nobody opened them. The tool had printed "set its renderer to
Direct3D 11" as one caveat among four, before the install, as advice.

The renderer was recorded in the emulator's own ini the whole time.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from dlss5_anywhere.config import (
    EMULATOR_PROFILES,
    EmulatorProfile,
    read_emulator_backend,
)
from dlss5_anywhere.core.detector import GameDetector

PPSSPP = EMULATOR_PROFILES["ppsspp"]


class PPSSPPConfigTestCase(unittest.TestCase):

    def setUp(self):
        self.game = Path(tempfile.mkdtemp(prefix="dlss5_emu_"))
        self.addCleanup(shutil.rmtree, self.game, True)

    def write_config(self, body: str) -> None:
        cfg = self.game / "memstick" / "PSP" / "SYSTEM"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "ppsspp.ini").write_text(body, encoding="utf-8")


class TestReadingTheBackend(PPSSPPConfigTestCase):

    def test_vulkan_is_read_from_the_real_file_format(self):
        """PPSSPP writes the number it reads back plus a name for humans."""
        self.write_config("[Graphics]\nGraphicsBackend = 3 (VULKAN)\nFrameSkip = 0\n")
        self.assertEqual(read_emulator_backend(PPSSPP, self.game), "vulkan")

    def test_every_backend_ppsspp_can_be_set_to(self):
        for token, expected in (
            ("0 (OPENGL)", "opengl"),
            ("1 (DIRECT3D9)", "d3d9"),
            ("2 (DIRECT3D11)", "d3d11"),
            ("3 (VULKAN)", "vulkan"),
        ):
            with self.subTest(value=token):
                self.write_config(f"GraphicsBackend = {token}\n")
                self.assertEqual(
                    read_emulator_backend(PPSSPP, self.game),
                    expected,
                )

    def test_a_bare_number_still_reads(self):
        self.write_config("GraphicsBackend = 3\n")
        self.assertEqual(read_emulator_backend(PPSSPP, self.game), "vulkan")

    def test_a_key_that_merely_starts_the_same_is_not_it(self):
        self.write_config("GraphicsBackendFallback = 3 (VULKAN)\n")
        self.assertIsNone(read_emulator_backend(PPSSPP, self.game))

    def test_no_config_file_makes_no_claim(self):
        self.assertIsNone(read_emulator_backend(PPSSPP, self.game))

    def test_an_unknown_value_makes_no_claim(self):
        """A backend added after this was written must not be reported as something else."""
        self.write_config("GraphicsBackend = 9 (SOMETHING NEW)\n")
        self.assertIsNone(read_emulator_backend(PPSSPP, self.game))

    def test_an_empty_value_makes_no_claim(self):
        self.write_config("GraphicsBackend = \n")
        self.assertIsNone(read_emulator_backend(PPSSPP, self.game))

    def test_an_unreadable_config_makes_no_claim(self):
        cfg = self.game / "memstick" / "PSP" / "SYSTEM" / "ppsspp.ini"
        cfg.parent.mkdir(parents=True)
        cfg.mkdir()  # a directory where the file should be
        self.assertIsNone(read_emulator_backend(PPSSPP, self.game))

    def test_an_emulator_with_no_declared_config_makes_no_claim(self):
        """Saying nothing beats guessing at an enum nobody verified."""
        self.assertIsNone(read_emulator_backend(EMULATOR_PROFILES["rpcs3"], self.game))

    def test_no_profile_at_all_makes_no_claim(self):
        self.assertIsNone(read_emulator_backend(None, self.game))

    def test_a_profile_declaring_a_config_declares_a_key_and_values(self):
        """A half-filled profile would silently read nothing forever."""
        for profile in EMULATOR_PROFILES.values():
            if profile.config_paths:
                with self.subTest(emulator=profile.id):
                    self.assertTrue(profile.config_key, profile.id)
                    self.assertTrue(profile.config_api_values, profile.id)
                    self.assertIn(
                        profile.recommended,
                        set(profile.config_api_values.values()),
                        "the renderer we recommend must be one this config can express",
                    )


class TestWhatTheUserIsTold(PPSSPPConfigTestCase):

    def _advisories(self):
        exe = self.game / "PPSSPPWindows64.exe"
        if not exe.exists():
            exe.write_bytes(b"MZ" + b"\0" * 128)
        return [m for m in GameDetector.analyze(exe).advisories if "PPSSPP" in m]

    def _api(self):
        exe = self.game / "PPSSPPWindows64.exe"
        if not exe.exists():
            exe.write_bytes(b"MZ" + b"\0" * 128)
        return GameDetector.analyze(exe).emulator_api

    def test_the_install_follows_the_renderer_it_is_set_to(self):
        """Reading it and then installing for something else is what broke PCSX2.

        A PCSX2 set to Vulkan was given a dxgi.dll it could never load, with an advisory
        telling the user to change their emulator instead of an install that matched it.
        """
        self.write_config("GraphicsBackend = 3 (VULKAN)\n")
        self.assertEqual(self._api(), "vulkan")
        said = " ".join(self._advisories())
        self.assertIn("set to Vulkan", said)
        self.assertIn("attaches through Vulkan", said)

    def test_it_says_when_that_is_not_what_it_would_have_picked(self):
        self.write_config("GraphicsBackend = 3 (VULKAN)\n")
        said = " ".join(self._advisories())
        self.assertIn("would otherwise have picked Direct3D 11", said)

    def test_the_recommended_renderer_needs_no_such_note(self):
        self.write_config("GraphicsBackend = 2 (DIRECT3D11)\n")
        self.assertEqual(self._api(), "d3d11")
        said = " ".join(self._advisories())
        self.assertIn("attaches through Direct3D 11", said)
        self.assertNotIn("would otherwise have picked", said)

    def test_a_renderer_with_no_install_path_falls_back_and_says_so(self):
        """PPSSPP can be set to Direct3D 9, which this tool has no emulator path for."""
        self.write_config("GraphicsBackend = 1 (DIRECT3D9)\n")
        said = " ".join(self._advisories())
        self.assertIn("no install path for", said)
        self.assertEqual(self._api(), "d3d11", "falls back to the recommended renderer")

    def test_without_a_config_the_old_advice_still_appears(self):
        """Nothing is lost when the emulator will not say what it is set to."""
        said = " ".join(self._advisories())
        self.assertIn("Set its renderer to Direct3D 11", said)


class TestTheToolDoesNotWriteEmulatorSettings(unittest.TestCase):
    """An emulator's settings are the user's. This tool installs DLSS 5."""

    def test_there_is_no_writer(self):
        import dlss5_anywhere.config as config
        self.assertFalse(
            hasattr(config, "apply_emulator_backend"),
            "reading the renderer is for telling the user, not for changing it",
        )

    def test_no_profile_carries_values_to_write(self):
        for profile in EMULATOR_PROFILES.values():
            with self.subTest(emulator=profile.id):
                self.assertFalse(hasattr(profile, "config_write_values"))


if __name__ == "__main__":
    unittest.main()
