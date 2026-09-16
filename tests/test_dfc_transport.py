"""Deep Fried Chicken cannot run on the feeder's same-device D3D12 transport.

Three of the feeder's four transports do the neural work on a D3D12 device it created
itself - the Vulkan transport, the D3D11->D3D12 bridge, and the 32-bit host64 helper.
The fourth hands DLSS the game's own device, which is what a Direct3D 12 game and an
emulator set to Direct3D 12 both get, and there Chicken 1.4.8-alpha logs

    standalone FP16 codec allocation failed: game-output device identity failed (0x80070057)
    standalone neural path disabled at FP16 codec acquisition

and never runs a pass again. Nothing else in the install says so: ReShade registers the
add-on, the panel and its tabs work, and dlss5-feed.log reports interception state 2
(ARMED) and goes on delivering DLAA frames for the rest of the session. This tool
recommended Direct3D 12 for PCSX2 precisely because it is the cheapest transport, so
without these the tool's own advice is what breaks the consumer.

The second half is the 32-bit case. The neural consumer runs in host64\\ there, and
Chicken's panel is a ReShade tab in that process. The feeder mirrors a consumer's
settings onto the game's own overlay page, but it mirrors RenoDX's keys - so with the
helper window hidden, Chicken has no user interface anywhere. Measured on Prince of
Persia Two Thrones: 4,800 neural frames delivered by a panel in a window with no taskbar
button, sitting behind the game.
"""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from dlss5_anywhere.config import (
    EMULATOR_PROFILES,
    STRATEGY_EMULATOR,
    STRATEGY_FEEDER_DX11_12,
    dfc_on_same_device,
    dfc_same_device_target,
)
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.detector import GameAnalysis
from dlss5_anywhere.core.strategy import StrategyEngine

DFC = {"neural_consumer": "dfc"}
RENODX = {"neural_consumer": "renodx"}

# The line the add-on writes when it gives up, quoted in both the warning and the README
# so that searching the log for it lands on an explanation. Matched on the tail of the
# phrase: the README wraps it, and "game-output" ends up on the line above.
FP16_FAILURE = "device identity failed"


def analysis(**overrides):
    temp = Path(tempfile.gettempdir())
    base = GameAnalysis(
        exe_path=temp / "game.exe",
        game_dir=temp,
        exe_name="game.exe",
        file_size_bytes=1024,
        architecture="x64",
        is_64bit=True,
        detected_apis=["DirectX 12"],
        primary_api="DirectX 12",
        recommended_strategy=STRATEGY_FEEDER_DX11_12,
    )
    return replace(base, **overrides)


def emulator_analysis(emulator_id, api):
    profile = EMULATOR_PROFILES[emulator_id]
    return analysis(
        exe_name=profile.exe_names[0],
        emulator=profile,
        emulator_api=api,
        detected_apis=["Vulkan"],
        primary_api="Vulkan",
        recommended_strategy=STRATEGY_EMULATOR,
    )


class TestTheHelperNamesTheTransport(unittest.TestCase):

    def test_d3d12_with_chicken_is_the_bad_pairing(self):
        self.assertTrue(dfc_on_same_device("d3d12", DFC))

    def test_the_other_renderers_are_fine(self):
        for api in ("vulkan", "opengl", "d3d11"):
            with self.subTest(api=api):
                self.assertFalse(dfc_on_same_device(api, DFC))

    def test_d3d12_with_renodx_is_fine(self):
        """RenoDX is why d3d12 is recommended in the first place - it is the cheapest
        transport, and this must not start warning about it for every build."""
        self.assertFalse(dfc_on_same_device("d3d12", RENODX))
        self.assertFalse(dfc_on_same_device("d3d12", None))

    def test_a_missing_or_oddly_cased_renderer_does_not_throw(self):
        self.assertTrue(dfc_on_same_device("D3D12", DFC))
        self.assertFalse(dfc_on_same_device("", DFC))
        self.assertFalse(dfc_on_same_device(None, DFC))


class TestOneAnswerForBothPlaces(unittest.TestCase):
    """The plan and the Builder tab have to agree, so they ask the same function.

    The tab has to answer before any plan exists - the consumer is picked first - and a
    game that reads as fine in one and broken in the other is worse than either message
    on its own.
    """

    def test_an_emulator_on_d3d12(self):
        self.assertTrue(dfc_same_device_target(DFC, emulator_api="d3d12"))
        self.assertFalse(dfc_same_device_target(DFC, emulator_api="vulkan"))

    def test_the_emulator_renderer_wins_over_the_binary(self):
        """An emulator's PE says nothing about the renderer it is set to."""
        self.assertFalse(dfc_same_device_target(
            DFC, emulator_api="vulkan", primary_api="DirectX 12"))

    def test_a_native_d3d12_game(self):
        self.assertTrue(dfc_same_device_target(DFC, primary_api="DirectX 12"))
        self.assertFalse(dfc_same_device_target(DFC, primary_api="DirectX 11"))

    def test_a_32bit_game_reaches_dlss_through_the_helper(self):
        self.assertFalse(dfc_same_device_target(
            DFC, primary_api="DirectX 12", is_64bit=False))

    def test_a_translated_game_is_not_on_its_own_api(self):
        """Behind DXVK or dgVoodoo2 the feeder sees Vulkan or D3D11, not the game's API."""
        self.assertFalse(dfc_same_device_target(
            DFC, primary_api="DirectX 12", translated=True))

    def test_renodx_is_never_the_subject_of_this(self):
        self.assertFalse(dfc_same_device_target(RENODX, primary_api="DirectX 12"))
        self.assertFalse(dfc_same_device_target(RENODX, emulator_api="d3d12"))


class TestTheBuilderTabSaysItWhenPicked(unittest.TestCase):
    """Said where the choice is made, not only where the plan is built.

    A build for a Direct3D 12 game gives no other sign: it installs, loads, arms and
    delivers frames with the consumer switched off the whole time.
    """

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError as exc:
            raise unittest.SkipTest(f"PySide6 not available: {exc}")
        cls.app = QApplication.instance() or QApplication([])

    def _view(self, consumer, report=None):
        from dlss5_anywhere.qtgui.views.dashboard_view import DashboardView
        view = DashboardView()
        self.addCleanup(view.deleteLater)
        view.current_analysis = report
        view.neural_consumer.setCurrentText(consumer)
        view._on_consumer_changed()
        return view

    def test_chicken_on_a_d3d12_game_is_called_out(self):
        note = self._view("Deep Fried Chicken", analysis()).consumer_note.text()
        self.assertIn("NOT SUPPORTED", note)
        self.assertIn(FP16_FAILURE, note)

    def test_and_the_advice_is_still_there_underneath(self):
        """The warning is prepended to the note, not swapped for it."""
        note = self._view("Deep Fried Chicken", analysis()).consumer_note.text()
        self.assertIn("replaces RenoDX", note)

    def test_chicken_on_a_d3d11_game_is_not(self):
        report = analysis(detected_apis=["DirectX 11"], primary_api="DirectX 11")
        self.assertNotIn(
            "NOT SUPPORTED", self._view("Deep Fried Chicken", report).consumer_note.text())

    def test_renodx_on_the_same_game_is_not(self):
        self.assertNotIn(
            "NOT SUPPORTED",
            self._view("RenoDX + Alex's Toolkit", analysis()).consumer_note.text())

    def test_nothing_analysed_yet_says_nothing(self):
        """The consumer is picked before a game as often as after one."""
        self.assertNotIn(
            "NOT SUPPORTED", self._view("Deep Fried Chicken").consumer_note.text())

    def test_analysing_a_game_afterwards_rewrites_the_note(self):
        """Picking Chicken first and choosing the game second is the ordinary order."""
        view = self._view("Deep Fried Chicken")
        self.assertNotIn("NOT SUPPORTED", view.consumer_note.text())
        view._apply_analysis(analysis())
        self.assertIn("NOT SUPPORTED", view.consumer_note.text())


class TestTheBuildWarnsAboutIt(unittest.TestCase):

    def _warnings(self, target, profile):
        return "\n".join(StrategyEngine.build_plan(target, profile=profile).warnings)

    def test_an_emulator_on_d3d12_is_warned_about(self):
        warnings = self._warnings(emulator_analysis("pcsx2", "d3d12"), DFC)
        self.assertIn(FP16_FAILURE, warnings)
        self.assertIn("Vulkan", warnings)

    def test_the_same_emulator_on_vulkan_is_not(self):
        self.assertNotIn(FP16_FAILURE, self._warnings(emulator_analysis("pcsx2", "vulkan"), DFC))

    def test_nor_is_it_warned_about_for_renodx(self):
        """PCSX2's own caveat recommends Direct3D 12. That advice is still right for the
        consumer it was written for, and this must not contradict it there."""
        warnings = self._warnings(emulator_analysis("pcsx2", "d3d12"), RENODX)
        self.assertNotIn(FP16_FAILURE, warnings)
        self.assertIn("Direct3D 12", warnings)  # the emulator's own caveat, still said

    def test_a_native_d3d12_game_is_warned_about(self):
        warnings = self._warnings(analysis(), DFC)
        self.assertIn(FP16_FAILURE, warnings)
        self.assertIn("DirectX 11 mode", warnings)

    def test_a_native_d3d11_game_is_not(self):
        target = analysis(detected_apis=["DirectX 11"], primary_api="DirectX 11")
        self.assertNotIn(FP16_FAILURE, self._warnings(target, DFC))

    def test_a_32bit_game_is_not(self):
        """A 32-bit game reaches DLSS through host64\\, which owns its own D3D12 device -
        the transport Chicken is happiest on, whatever the game presents through."""
        target = analysis(architecture="x86", is_64bit=False)
        self.assertNotIn(FP16_FAILURE, self._warnings(target, DFC))


class TestTheHelperWindowIsShownForChicken(unittest.TestCase):

    def _value(self, cfg, key):
        for line in cfg.splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1]
        return None

    def test_a_32bit_chicken_build_shows_the_window(self):
        cfg = ConfigGenerator.generate_feed_cfg(
            analysis(architecture="x86", is_64bit=False), DFC)
        self.assertEqual(self._value(cfg, "host_window"), "1")

    def test_even_when_the_profile_asked_for_it_hidden(self):
        """Hidden is the shipped default, and it is the default because the window takes
        focus. For this consumer it is also the difference between a panel and nothing."""
        cfg = ConfigGenerator.generate_feed_cfg(
            analysis(architecture="x86", is_64bit=False),
            {**DFC, "feed_host_window": False},
        )
        self.assertEqual(self._value(cfg, "host_window"), "1")

    def test_a_32bit_renodx_build_keeps_it_hidden(self):
        """RenoDX's settings are mirrored onto the game's own overlay page, so nothing is
        out of reach with the window down."""
        cfg = ConfigGenerator.generate_feed_cfg(
            analysis(architecture="x86", is_64bit=False),
            {**RENODX, "feed_host_window": False},
        )
        self.assertEqual(self._value(cfg, "host_window"), "0")

    def test_a_64bit_chicken_build_is_left_alone(self):
        """There is no helper process at all, so the window is not the consumer's home."""
        cfg = ConfigGenerator.generate_feed_cfg(
            analysis(), {**DFC, "feed_host_window": False})
        self.assertEqual(self._value(cfg, "host_window"), "0")


class TestTheReadmeSaysWhereToLook(unittest.TestCase):

    def _readme(self, profile, **overrides):
        target = analysis(**overrides)
        plan = StrategyEngine.build_plan(target, profile=profile)
        return ConfigGenerator.generate_readme_guide(
            target, plan.strategy_id, plan=plan, profile=profile)

    def test_a_chicken_build_names_the_line_and_its_log(self):
        guide = self._readme(DFC)
        self.assertIn(FP16_FAILURE, guide)
        self.assertIn("neural frame succeeded", guide)
        self.assertIn("dlss5-addons\\deep-fried-chicken.log", guide)

    def test_a_32bit_chicken_build_points_at_the_helper_folder_instead(self):
        guide = self._readme(DFC, architecture="x86", is_64bit=False)
        self.assertIn("host64\\deep-fried-chicken.log", guide)
        self.assertIn("host_window=1", guide)

    def test_a_renodx_build_is_not_told_about_a_log_it_will_never_have(self):
        guide = self._readme(RENODX)
        self.assertNotIn(FP16_FAILURE, guide)
        self.assertNotIn("deep-fried-chicken.log", guide)


if __name__ == "__main__":
    unittest.main()
