"""The launcher an emulator gets, and why it is not the same one a game gets.

ReShade attaches once per graphics device, and re-scans the add-on folder on every
attach. An emulator makes a device every time emulation starts, so one session holds
several: booting a title from RPCS3's game list boots the game folder (Vulkan device at
+6.5s), stops emulation at +10.4s, and boots the game's own .SELF with direct=1 because
that EBOOT.BIN is a launcher for another executable (second device at +14s).
deep-fried-chicken.log is stamped 0.2s after the first device, and the ReShade.log that
survives is the second one, which registered nothing.

The front end's own "Vulkan Device Enumeration Thread" instance at +0.9s is not part of
this. If ReShade had loaded the add-ons there, Chicken's log would be stamped +1s. It is
the extra emulation start that costs the install, not the front end being open.

Loading a DLL that is already in the process does nothing: LoadLibrary returns the handle
it has, the entry point does not run again, and nothing calls ReShadeRegisterAddon. So the
second attach logs

    No add-on was registered by '...\\deep-fried-chicken.addon64'. Unloading again ...

and the neural consumer is left holding its NGX hooks with no ReShade registration - no
Add-ons tab, and dlss5-feed.log reporting "still not ARMED 900 frames after the feature
was created (state CLAIMING)" while frames are delivered and nothing errors. Add-ons that
pin themselves do it to survive exactly this teardown, so the ones worth having are the
ones this breaks.

Booting the game directly is the whole fix, which makes the launcher part of the install
rather than a convenience.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dlss5_anywhere.config import EMULATOR_PROFILES, STRATEGY_EMULATOR
from dlss5_anywhere.core import installer as installer_module
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.detector import GameAnalysis
from dlss5_anywhere.core.installer import ModInstaller


class Plan:
    """The two plan flags the launcher reads."""

    def __init__(self, needs_vulkan_layer=False, uses_dxvk=False):
        self.needs_vulkan_layer = needs_vulkan_layer
        self.uses_dxvk = uses_dxvk


def analysis_for(exe_name, emulator=None):
    temp = Path(tempfile.gettempdir())
    return GameAnalysis(
        exe_path=temp / exe_name,
        game_dir=temp,
        exe_name=exe_name,
        file_size_bytes=1024,
        architecture="x64",
        is_64bit=True,
        detected_apis=["Vulkan"],
        primary_api="Vulkan",
        emulator=emulator,
        emulator_api="vulkan" if emulator else "",
        recommended_strategy=STRATEGY_EMULATOR if emulator else "",
    )


class TestDirectBootFlag(unittest.TestCase):

    def test_rpcs3_has_one(self):
        self.assertEqual(EMULATOR_PROFILES["rpcs3"].direct_boot_flag, "--no-gui")

    def test_unverified_emulators_do_not_guess_at_one(self):
        """A flag the emulator does not recognise stops it from starting at all.

        Every emulator here has some form of this switch, and the launcher only earns the
        right to pass one after it has been run against a real install.
        """
        for profile in EMULATOR_PROFILES.values():
            if profile.id == "rpcs3":
                continue
            self.assertEqual(
                profile.direct_boot_flag, "",
                f"{profile.id} claims a direct-boot flag with nothing behind it",
            )


class TestEmulatorLauncher(unittest.TestCase):

    def setUp(self):
        self.bat = ConfigGenerator.generate_launcher_batch(
            analysis_for("rpcs3.exe", EMULATOR_PROFILES["rpcs3"]),
            Plan(needs_vulkan_layer=True),
        )

    def test_a_game_path_is_booted_through_the_hold_script(self):
        self.assertIn(
            'powershell -NoProfile -ExecutionPolicy Bypass -File '
            '"%~dp0dlss5-hold-consumer.ps1" -Game "%GAME%" %MODE%',
            self.bat,
        )

    def test_the_path_can_come_from_the_command_line(self):
        self.assertIn('else if not "%~1"=="" (set "GAME=%~1")', self.bat)

    def test_hold_can_be_overruled_from_the_command_line(self):
        """The recorded guess is wrong for one launch after a title changes behaviour,
        and /hold and /nohold are how that is fixed without deleting state."""
        self.assertIn('if /i "%~2"=="/hold" set "MODE=-Mode hold"', self.bat)
        self.assertIn('if /i "%~2"=="/nohold" set "MODE=-Mode nohold"', self.bat)
        self.assertIn("-Game \"%GAME%\" %MODE%", self.bat)

    def test_a_second_copy_is_refused(self):
        """The first process still holds the add-on, so the second registers nothing -
        the same failure as the second attach, reached a different way."""
        self.assertIn('tasklist.exe" /fi "imagename eq rpcs3.exe"', self.bat)
        self.assertIn("already running", self.bat)
        self.assertIn("crash or error dialog", self.bat)

    def test_the_running_check_cannot_be_shadowed_by_a_unix_find(self):
        """Measured while dry-running this file from a Git Bash shell: `find /i` ran
        Git's find, which failed, which reads as "not running" and lets a second copy
        through - the exact case the check exists to stop."""
        self.assertIn("%SystemRoot%\\System32\\tasklist.exe", self.bat)
        self.assertIn("%SystemRoot%\\System32\\find.exe", self.bat)

    def test_no_path_still_launches_but_says_what_it_costs(self):
        """Refusing to start the emulator at all would be worse than starting it in the
        state every other launcher starts it in."""
        self.assertIn(
            'powershell -NoProfile -ExecutionPolicy Bypass -File '
            '"%~dp0dlss5-hold-consumer.ps1" %MODE%\n',
            self.bat,
        )
        self.assertIn("front-end", self.bat)

    def test_it_names_the_log_line_that_proves_the_problem(self):
        self.assertIn("No add-on was registered", self.bat)

    def test_rival_vulkan_overlays_are_kept_out_of_the_swapchain(self):
        self.assertIn("VK_LOADER_LAYERS_DISABLE", self.bat)
        self.assertIn("VK_LAYER_VALVE_steam_overlay", self.bat)

    def test_the_overlay_keys_are_still_documented(self):
        self.assertIn("[Home]", self.bat)
        self.assertIn("[F2]", self.bat)

    def test_no_vulkan_layer_means_no_layer_variables(self):
        bat = ConfigGenerator.generate_launcher_batch(
            analysis_for("rpcs3.exe", EMULATOR_PROFILES["rpcs3"]), Plan()
        )
        self.assertNotIn("VK_LOADER_LAYERS_DISABLE", bat)
        self.assertIn("dlss5-hold-consumer.ps1", bat)

    def test_the_consumer_named_is_the_one_the_profile_selected(self):
        dfc = ConfigGenerator.generate_launcher_batch(
            analysis_for("rpcs3.exe", EMULATOR_PROFILES["rpcs3"]),
            Plan(needs_vulkan_layer=True),
            {"neural_consumer": "dfc"},
        )
        self.assertIn("deep-fried-chicken.addon64", dfc)
        self.assertIn("renodx-dlss5.addon64", self.bat)


class TestHoldScript(unittest.TestCase):
    """The script that makes the consumer miss the first emulation start.

    Verified end to end on RPCS3 with God of War: Ascension - held out for boot #1,
    moved back 7.5s in when the log said "Stopping emulator...", and ReShade logged
    'Registered add-on "Deep Fried Chicken 1.4.8-alpha"' on boot #2's attach, with the
    feeder then reporting ARMED and 1200 neural frames. The same title had produced
    "No add-on was registered" on every previous run.
    """

    def setUp(self):
        self.ps1 = ConfigGenerator.generate_hold_script(
            EMULATOR_PROFILES["rpcs3"], "deep-fried-chicken.addon64"
        )

    def test_it_watches_the_emulators_own_log_for_the_stop(self):
        self.assertIn("log/RPCS3.log", self.ps1)
        self.assertIn("Stopping emulator", self.ps1)
        self.assertIn("Emulator::BootGame", self.ps1)

    def test_it_moves_the_consumer_aside_and_back(self):
        self.assertIn("dlss5-addons\\deep-fried-chicken.addon64", self.ps1)
        self.assertIn('$held     = "$consumer.held"', self.ps1)

    def test_a_killed_session_costs_one_launch_not_the_consumer(self):
        """The file is put back in a finally block, and again at the start of the next
        run - because a finally block does not run when the console is closed."""
        self.assertIn("finally {", self.ps1)
        self.assertEqual(self.ps1.count("Restore-Consumer"), 5)

    def test_it_gives_up_holding_if_the_title_never_restarts(self):
        """Otherwise a title that boots once would spend a whole session without its
        consumer, which is worse than the problem being solved."""
        self.assertIn("TotalSeconds -gt 120", self.ps1)

    def test_it_records_what_it_learned_for_the_next_run(self):
        self.assertIn("restarts_emulation=", self.ps1)
        self.assertIn("dlss5-launch-state.ini", self.ps1)

    def test_the_boot_flag_is_only_used_when_there_is_a_game(self):
        self.assertIn("if ($Game) { $emuArgs = @('--no-gui', ", self.ps1)

    def test_the_game_path_is_quoted_for_start_process(self):
        """-ArgumentList joins an array with spaces and quotes nothing, so
        "...\\GOW Ascension\\PS3_GAME" arrives as two arguments and RPCS3 tries to boot
        "H:\\Emulation\\roms\\ps3\\GOW": "Invalid file or folder". Seen twice."""
        self.assertIn("""('"' + $Game.Trim('"') + '"')""", self.ps1)

    def test_only_emulators_whose_log_has_been_read_get_one(self):
        self.assertTrue(
            ConfigGenerator.emulator_can_hold_consumer(EMULATOR_PROFILES["rpcs3"])
        )
        for other in ("pcsx2", "shadps4"):
            self.assertFalse(
                ConfigGenerator.emulator_can_hold_consumer(EMULATOR_PROFILES[other]),
                f"{other} would be watched for a line nobody has read",
            )


class TestTheBuildCarriesIt(unittest.TestCase):
    """The generators are only worth anything if a build actually emits them.

    Both were reached by hand first - written into a live RPCS3 folder to prove the
    mechanism - and a fix that only exists when someone runs a snippet is not a fix, so
    this drives ModInstaller the way the Builder tab does.
    """

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dlss5_launcher_test_"))
        exe = self.temp_dir / "rpcs3.exe"
        exe.write_text("dummy", encoding="utf-8")
        self.analysis = GameAnalysis(
            exe_path=exe,
            game_dir=self.temp_dir,
            exe_name="rpcs3.exe",
            file_size_bytes=1024,
            architecture="x64",
            is_64bit=True,
            detected_apis=["Vulkan"],
            primary_api="Vulkan",
            emulator=EMULATOR_PROFILES["rpcs3"],
            emulator_api="vulkan",
            recommended_strategy=STRATEGY_EMULATOR,
        )

        # Chicken's config is carried through from its release zip and patched, never
        # written from scratch, so a build can only produce one where that file has been
        # imported. Reading the real components folder would make this test pass on a
        # machine that has downloaded it and fail on every other - which is what it did.
        supplied = self.temp_dir / "user_supplied"
        supplied.mkdir()
        (supplied / "deep-fried-chicken.cfg").write_text(
            "config_schema=6\narm=0\nlayers=1\nenabled=0\n", encoding="utf-8"
        )
        patch = mock.patch.object(installer_module, "USER_SUPPLIED_DIR", supplied)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build(self, profile):
        out = self.temp_dir / "Build"
        result = ModInstaller.prepare_build_folder(
            analysis=self.analysis,
            strategy_override=STRATEGY_EMULATOR,
            profile=profile,
            custom_output_dir=out,
        )
        self.assertTrue(result.success, result.message)
        return out / "build_files"

    def test_a_dfc_build_ships_the_launcher_and_its_script(self):
        files = self._build({"neural_consumer": "dfc"})
        launcher = (files / "launch_with_dlss5.bat").read_text(encoding="utf-8")
        script = (files / "dlss5-hold-consumer.ps1").read_text(encoding="utf-8")

        self.assertIn("dlss5-hold-consumer.ps1", launcher)
        self.assertIn("deep-fried-chicken.addon64", script)
        self.assertNotIn("renodx-dlss5.addon64", script)

    def test_a_dfc_build_ships_chickens_config_too(self):
        """The add-on without its config starts on the vendor defaults, and every add-on
        file being present is exactly what made that look like a complete build."""
        files = self._build({"neural_consumer": "dfc"})
        cfg = files / "dlss5-addons" / "deep-fried-chicken.cfg"
        self.assertTrue(cfg.exists(), "Chicken's config never reached the build folder")
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("arm=1", text)
        self.assertIn("enabled=1", text)

    def test_a_renodx_build_names_renodx(self):
        """The consumer is a profile choice, and the script moves a specific file."""
        script = (self._build(None) / "dlss5-hold-consumer.ps1").read_text(encoding="utf-8")
        self.assertIn("renodx-dlss5.addon64", script)
        self.assertNotIn("deep-fried-chicken.addon64", script)

    def test_installing_straight_into_the_emulator_folder_writes_it_too(self):
        """The Builder tab's other button. Same block of code as the build path, which is
        exactly why it is worth asserting separately - it was added twice by hand."""
        result = ModInstaller.install_to_game(
            analysis=self.analysis,
            strategy_override=STRATEGY_EMULATOR,
            profile={"neural_consumer": "dfc"},
        )
        self.assertTrue(result.success, result.message)
        script = self.temp_dir / "dlss5-hold-consumer.ps1"
        self.assertTrue(script.exists())
        self.assertIn("deep-fried-chicken.addon64", script.read_text(encoding="utf-8"))

        # And Restore has to know about it, or it is left behind pointing at files that
        # are gone.
        manifest = json.loads(
            (self.temp_dir / ".dlss5_backup" / "dlss5_manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn("dlss5-hold-consumer.ps1", manifest["injected_files"])

        ok, _, _ = ModInstaller.restore_and_uninstall(self.temp_dir)
        self.assertTrue(ok)
        self.assertFalse(script.exists())

    def test_the_readme_names_the_consumer_that_was_actually_staged(self):
        """A DFC build that tells you to check renodx-dlss5.addon64 sends you looking for
        a file the build deliberately left out."""
        out = self.temp_dir / "Build"
        self._build({"neural_consumer": "dfc"})
        readme = (out / "README_INSTALL.txt").read_text(encoding="utf-8")
        self.assertIn("deep-fried-chicken.addon64", readme)
        self.assertIn("deep-fried-chicken.cfg", readme)
        # The one place RenoDX may still appear is the warning never to combine them.
        for line in readme.splitlines():
            if "renodx" in line.lower():
                self.assertIn("never be installed", readme)

    def test_the_1_click_apply_takes_it_into_the_game_folder(self):
        """apply_dlss5.bat copies build_files\\* wholesale, so the script rides along -
        but only if it was written inside build_files rather than beside it."""
        out = self.temp_dir / "Build"
        self._build({"neural_consumer": "dfc"})
        apply_bat = (out / "apply_dlss5.bat").read_text(encoding="utf-8")
        self.assertIn('xcopy /E /Y /I "%~dp0build_files\\*"', apply_bat)
        self.assertTrue((out / "build_files" / "dlss5-hold-consumer.ps1").exists())

    def test_the_uninstaller_puts_a_held_consumer_back_before_deleting_anything(self):
        """A session killed mid-hold leaves deep-fried-chicken.addon64.held. Deleting
        dlss5-addons\\ without renaming it back would take the consumer with it."""
        uninstall = ConfigGenerator.generate_uninstall_batch(self.analysis)
        self.assertIn('for %%F in ("dlss5-addons\\*.addon64.held")', uninstall)
        self.assertIn("dlss5-hold-consumer.ps1", uninstall)
        self.assertLess(
            uninstall.index(".addon64.held"),
            uninstall.index('rmdir /s /q "dlss5-addons"')
            if 'rmdir /s /q "dlss5-addons"' in uninstall
            else len(uninstall),
        )


class TestOtherTargetsAreUntouched(unittest.TestCase):

    def test_a_plain_game_keeps_the_simple_launcher(self):
        bat = ConfigGenerator.generate_launcher_batch(analysis_for("MockGame.exe"))
        self.assertIn('start "" "MockGame.exe"', bat)
        self.assertNotIn("--no-gui", bat)
        self.assertNotIn("tasklist.exe", bat)

    def test_an_emulator_without_a_verified_flag_keeps_it_too(self):
        bat = ConfigGenerator.generate_launcher_batch(
            analysis_for("shadps4.exe", EMULATOR_PROFILES["shadps4"]), Plan(needs_vulkan_layer=True)
        )
        self.assertIn('start "" "shadps4.exe"', bat)
        self.assertNotIn("tasklist.exe", bat)

    def test_dxvk_still_gets_its_config_path(self):
        bat = ConfigGenerator.generate_launcher_batch(
            analysis_for("MockGame.exe"), Plan(uses_dxvk=True)
        )
        self.assertIn("DXVK_CONFIG_FILE", bat)


if __name__ == "__main__":
    unittest.main()
