"""
Tests for Installer, Backup, Staging Build, and Restore functions.
"""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from dlss5_anywhere.config import (
    COMP_FEEDER,
    COMP_VORT,
    DEFAULT_PROFILE,
    EMULATOR_PROFILES,
    MV_PROVIDER_LUMENITE,
    MV_PROVIDER_VORT,
    STRATEGY_32BIT_FEEDER,
    STRATEGY_DGVOODOO_DX9,
    STRATEGY_DXVK_DX9,
    STRATEGY_EMULATOR,
    STRATEGY_FEEDER_DX11_12,
)
from dlss5_anywhere.core.components import ComponentManager
from dlss5_anywhere.core.detector import GameAnalysis
from dlss5_anywhere.core.config_gen import ConfigGenerator
from dlss5_anywhere.core.installer import ModInstaller
from dlss5_anywhere.core.profiles import ProfileManager
from dlss5_anywhere.core.strategy import StrategyEngine
from dlss5_anywhere.core import strategy as strategy_module


class TestInstaller(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dlss5_test_"))
        self.mock_exe = self.temp_dir / "MockGame.exe"
        self.mock_exe.write_text("dummy exe content", encoding="utf-8")

        self.mock_analysis = GameAnalysis(
            exe_path=self.mock_exe,
            game_dir=self.temp_dir,
            exe_name="MockGame.exe",
            file_size_bytes=1024,
            architecture="x64",
            is_64bit=True,
            detected_apis=["DirectX 11"],
            primary_api="DirectX 11",
            recommended_strategy=STRATEGY_FEEDER_DX11_12,
        )

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @contextmanager
    def downloaded_vulkan_layer(self):
        """Pretend the DLSS5-Feeder component has been fetched.

        The plan stages the Vulkan interop layer only if components/feeder/layer-x64 is
        really on disk, which makes any assertion about it an assertion about whether
        whoever is running the tests had already pressed Download. On a fresh clone -
        which is what every new contributor and every CI run has - components/ is empty,
        so these tests failed there and passed here, which is the worst way round.

        Only the feeder's directory is redirected; every other component keeps its real
        path, so the rest of the plan is unchanged.
        """
        original = ComponentManager.get_component_dir
        fake_feeder = self.temp_dir / "components" / "feeder"
        for arch in ("layer-x64", "layer-x86"):
            (fake_feeder / arch).mkdir(parents=True, exist_ok=True)

        def routed(comp_id):
            return fake_feeder if comp_id == COMP_FEEDER else original(comp_id)

        with mock.patch.object(ComponentManager, "get_component_dir", side_effect=routed):
            yield

    def test_prepare_build_staging(self):
        build_output = self.temp_dir / "TestBuild"
        result = ModInstaller.prepare_build_folder(
            analysis=self.mock_analysis,
            custom_output_dir=build_output,
        )
        self.assertTrue(result.success)
        self.assertTrue((build_output / "apply_dlss5.bat").exists())
        self.assertTrue((build_output / "README_INSTALL.txt").exists())
        build_files = build_output / "build_files"
        self.assertTrue((build_files / "ReShade.ini").exists())
        self.assertTrue((build_files / "DLSS5_Preset.ini").exists())

        # The effect has to land where ReShade's EffectSearchPaths points, and the
        # add-on binary is what actually shows up in the Add-ons tab.
        reshade_ini = (build_files / "ReShade.ini").read_text(encoding="utf-8")
        self.assertIn("reshade-shaders\\Shaders", reshade_ini)
        self.assertIn("DLSS5_MV_PROVIDER=3", reshade_ini)

        preset = (build_files / "DLSS5_Preset.ini").read_text(encoding="utf-8")
        self.assertIn("Lumenite_Kernel@lumenite_Kernel.fx", preset)
        self.assertLess(
            preset.index("Lumenite_Kernel@lumenite_Kernel.fx"),
            preset.index("DLSS5_Feed@DLSS5_Feed.fx"),
            "the motion vector provider must be ordered above the feed",
        )

    def test_build_turns_neural_rendering_on_and_enables_visible_effects(self):
        """A finished build has to render something: the add-on switch and the effects.

        The failure this covers is silent - the install works, the log says the DLAA
        contract is being delivered, and the frame looks untouched, because the two
        techniques that were enabled (the motion vector kernel and the feed) draw nothing
        and the neural add-on's own switch was never written anywhere.
        """
        build_output = self.temp_dir / "LookBuild"
        result = ModInstaller.prepare_build_folder(
            analysis=self.mock_analysis,
            profile=ProfileManager.load_profile("Balanced (Recommended)"),
            custom_output_dir=build_output,
        )
        self.assertTrue(result.success)
        build_files = build_output / "build_files"

        # renodx-dlss5 reads its settings from ReShade's config, not a file of its own.
        reshade_ini = (build_files / "ReShade.ini").read_text(encoding="utf-8")
        self.assertIn("[RenoDX.DLSS5]", reshade_ini)
        self.assertIn("NeuralUplift=1", reshade_ini)

        preset = (build_files / "DLSS5_Preset.ini").read_text(encoding="utf-8")
        techniques = preset.splitlines()[0]
        self.assertIn("Lumenite_RTAO@lumenite_RTAO.fx", techniques)
        # Effects run after the feed, so they are applied to the neural output.
        self.assertLess(
            techniques.index("DLSS5_Feed@DLSS5_Feed.fx"),
            techniques.index("Lumenite_RTAO@lumenite_RTAO.fx"),
        )

    def test_32bit_build_writes_neural_settings_into_host64(self):
        """On a 32-bit game the neural add-on lives in host64/ and reads the ini there.

        Writing [RenoDX.DLSS5] into the game folder's ReShade.ini instead would put the
        settings in a file the add-on never opens, and every 32-bit install would come up
        at the add-on's own defaults no matter what profile was chosen.
        """
        analysis32 = replace(self.mock_analysis, architecture="x86", is_64bit=False)
        build_output = self.temp_dir / "Build32"
        result = ModInstaller.prepare_build_folder(
            analysis=analysis32,
            profile=ProfileManager.load_profile("Legacy 32-bit Game Preset"),
            custom_output_dir=build_output,
        )
        self.assertTrue(result.success)
        build_files = build_output / "build_files"

        # Beside the add-on that spawns the helper, not beside the executable: the
        # 32-bit add-on resolves that folder against its own module directory.
        host_ini = build_files / "dlss5-addons" / "host64" / "ReShade.ini"
        self.assertTrue(host_ini.exists(), "host64/ReShade.ini is the only one that add-on reads")
        self.assertIn("NeuralUplift=1", host_ini.read_text(encoding="utf-8"))
        self.assertNotIn(
            "[RenoDX.DLSS5]", (build_files / "ReShade.ini").read_text(encoding="utf-8")
        )

    def test_presets_differ_in_what_they_look_like(self):
        """Picking a preset has to change the output, or the picker is decoration."""
        balanced = ProfileManager.load_profile("Balanced (Recommended)")
        cinematic = ProfileManager.load_profile("High Quality / Cinematic")
        performance = ProfileManager.load_profile("High Framerate Performance (120 FPS Target)")

        self.assertNotEqual(balanced["nr_style"], cinematic["nr_style"])
        self.assertNotEqual(
            balanced["lumenite_effects"], performance["lumenite_effects"]
        )
        self.assertTrue(cinematic["toolkit_three_pass"])
        self.assertFalse(performance["toolkit_enabled"])

    def test_profile_picks_the_d3d9_path_but_an_explicit_override_wins(self):
        """`d3d9_translation` has to actually choose, or it is a setting that does nothing."""
        d3d9 = replace(
            self.mock_analysis,
            architecture="x86", is_64bit=False,
            detected_apis=["DirectX 9"], primary_api="DirectX 9",
            recommended_strategy=STRATEGY_DXVK_DX9,
        )
        self.assertEqual(
            StrategyEngine.build_plan(d3d9, profile={"d3d9_translation": "dxvk"}).strategy_id,
            STRATEGY_DXVK_DX9,
        )
        self.assertEqual(
            StrategyEngine.build_plan(d3d9, profile={"d3d9_translation": "dgvoodoo"}).strategy_id,
            STRATEGY_DGVOODOO_DX9,
        )
        # Naming a strategy outright is the more specific instruction and must win.
        self.assertEqual(
            StrategyEngine.build_plan(
                d3d9,
                strategy_override=STRATEGY_DGVOODOO_DX9,
                profile={"d3d9_translation": "dxvk"},
            ).strategy_id,
            STRATEGY_DGVOODOO_DX9,
        )
        # DXVK reaches D3D8 through a shim over its D3D9 path; the feeder is not tested
        # against that, so a D3D8 title stays on dgVoodoo2 whatever the profile asks for.
        d3d8 = replace(d3d9, detected_apis=["DirectX 8"], primary_api="DirectX 8")
        self.assertEqual(
            StrategyEngine.build_plan(d3d8, profile={"d3d9_translation": "dxvk"}).strategy_id,
            STRATEGY_DGVOODOO_DX9,
        )

    def test_a_reduced_work_resolution_forces_the_d3d11_transport(self):
        """Asking DLSS to render below native has to pick a path that can do it.

        The add-on exposes 'Work resolution' on its D3D11 transport and pins it at 100%
        on OpenGL and Vulkan. A D3D9 game routed through DXVK therefore cannot honour a
        profile that asks for less than 100% - the slider is simply greyed out in the
        overlay, with nothing to say why. dgVoodoo2 lands on D3D11, so that is where a
        profile asking to upscale has to go.
        """
        d3d9 = replace(
            self.mock_analysis,
            architecture="x86", is_64bit=False,
            detected_apis=["DirectX 9"], primary_api="DirectX 9",
            recommended_strategy=STRATEGY_DXVK_DX9,
        )
        self.assertEqual(
            StrategyEngine.build_plan(d3d9, profile={"feed_work_resolution": 100}).strategy_id,
            STRATEGY_DXVK_DX9,
        )

        plan = StrategyEngine.build_plan(d3d9, profile={"feed_work_resolution": 75})
        self.assertEqual(plan.strategy_id, STRATEGY_DGVOODOO_DX9)
        self.assertTrue(
            any("Work resolution" in w for w in plan.warnings),
            "swapping the translation layer under the user has to be explained",
        )

    def test_dxvk_conf_can_raise_what_the_game_sees_and_is_passed_by_path(self):
        """The two-key VRAM fix, and getting the file read at all.

        `d3d9.maxAvailableMemory` is a ceiling - min(deviceMemory + systemMemory, it) -
        so on its own it can never raise a game above what the adapter reports, and DXVK's
        built-in profile pins GTA IV's adapter memory to 128 MB. And DXVK reads
        `$PWD/dxvk.conf`, not the file beside the executable, so the launcher has to name
        it outright or the whole file can go unread.
        """
        rage = replace(
            self.mock_analysis,
            architecture="x86", is_64bit=False,
            detected_apis=["DirectX 9"], primary_api="DirectX 9",
            recommended_strategy=STRATEGY_DXVK_DX9,
            supports_rage_commandline=True,
        )
        build_output = self.temp_dir / "VramBuild"
        result = ModInstaller.prepare_build_folder(
            analysis=rage,
            strategy_override=STRATEGY_DXVK_DX9,
            profile={"dxvk_max_device_memory_mb": 8192, "dxvk_max_available_memory_mb": 8192},
            custom_output_dir=build_output,
        )
        self.assertTrue(result.success)
        build_files = build_output / "build_files"

        conf = (build_files / "dxvk.conf").read_text(encoding="utf-8")
        self.assertIn("dxgi.maxDeviceMemory = 8192", conf)
        self.assertIn("d3d9.maxAvailableMemory = 8192", conf)

        launcher = (build_files / "launch_with_dlss5.bat").read_text(encoding="utf-8")
        self.assertIn("DXVK_CONFIG_FILE", launcher)

    def test_dxvk_plan_replaces_d3d9_and_takes_the_vulkan_layer_route(self):
        """The no-wrapper D3D9 path: DXVK's d3d9.dll, and no local ReShade DLL.

        Under DXVK the game presents through Vulkan, which has no local-DLL injection
        point - a dxgi.dll dropped next to the executable would be loaded by nothing, and
        the install would come up with no overlay and no explanation.
        """
        analysis32 = replace(self.mock_analysis, architecture="x86", is_64bit=False)
        with self.downloaded_vulkan_layer():
            plan = StrategyEngine.build_plan(analysis32, strategy_override=STRATEGY_DXVK_DX9)
        dests = {item.relative_dest for item in plan.items}

        self.assertTrue(plan.uses_dxvk)
        self.assertFalse(plan.uses_dgvoodoo)
        self.assertIn("d3d9.dll", dests)
        self.assertNotIn("dxgi.dll", dests, "Vulkan has no local-DLL entry point")
        self.assertTrue(plan.needs_vulkan_layer)
        # The interop fallback layer has to match the process it loads into.
        self.assertIn("feed-vk-layer", dests)

    def test_dxvk_build_writes_a_dxvk_conf_reporting_real_video_memory(self):
        """The generated dxvk.conf answers the "game thinks it has 512 MB" case."""
        analysis32 = replace(self.mock_analysis, architecture="x86", is_64bit=False)
        build_output = self.temp_dir / "DxvkBuild"
        result = ModInstaller.prepare_build_folder(
            analysis=analysis32,
            strategy_override=STRATEGY_DXVK_DX9,
            custom_output_dir=build_output,
        )
        self.assertTrue(result.success)
        conf = (build_output / "build_files" / "dxvk.conf").read_text(encoding="utf-8")
        self.assertIn("d3d9.maxAvailableMemory = 4096", conf)

    def test_dgvoodoo_watermark_expires_instead_of_running_all_session(self):
        """The watermark proves dgVoodoo loaded; it should not stay for the whole session."""
        conf = ConfigGenerator.generate_dgvoodoo_conf(None, ModInstaller._dgvoodoo_template())
        duration = [
            line for line in conf.splitlines()
            if line.strip().startswith("WatermarkDisplayDuration")
        ]
        self.assertTrue(duration, "WatermarkDisplayDuration must be written")
        self.assertNotIn("= 0", duration[0], "0 means the watermark never goes away")

    def test_32bit_plan_uses_host64_and_addon32(self):
        """A 32-bit game gets the x86 add-on plus the 64-bit helper folder.

        NGX is 64-bit only, so nothing of the neural stack may be staged next to a
        32-bit executable - it belongs in host64/, and the game gets .addon32.
        """
        analysis = replace(
            self.mock_analysis,
            architecture="x86",
            is_64bit=False,
            recommended_strategy=STRATEGY_32BIT_FEEDER,
        )
        plan = StrategyEngine.build_plan(analysis)
        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}

        self.assertTrue(plan.uses_host64)
        # The feeder runs in the 32-bit game, so it goes in the add-on folder the game's
        # own ReShade searches; the neural stack runs in the helper, which has host64\\.
        self.assertIn("dlss5-addons/dlss5-feed.addon32", destinations)
        self.assertNotIn("dlss5-addons/dlss5-feed.addon64", destinations)
        self.assertIn("dlss5-addons/host64/renodx-dlss5.addon64", destinations)
        self.assertIn("dlss5-addons/host64/nvngx_dlssnr.dll", destinations)
        self.assertIn("dlss5-addons/host64/dlss5-feed-host64.exe", destinations)
        self.assertNotIn("renodx-dlss5.addon64", destinations)

    def test_64bit_plan_stays_in_process(self):
        plan = StrategyEngine.build_plan(self.mock_analysis)
        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}

        self.assertFalse(plan.uses_host64)
        self.assertIn("dlss5-addons/dlss5-feed.addon64", destinations)
        self.assertIn("dlss5-addons/renodx-dlss5.addon64", destinations)
        self.assertFalse(any(d.startswith("host64/") for d in destinations))

    def test_emulator_vulkan_plan_registers_layer_instead_of_copying_reshade(self):
        """RPCS3 has no D3D renderer: ReShade must not be copied in as a local DLL."""
        analysis = replace(
            self.mock_analysis,
            exe_name="rpcs3.exe",
            emulator=EMULATOR_PROFILES["rpcs3"],
            emulator_api="vulkan",
            recommended_strategy=STRATEGY_EMULATOR,
        )
        with self.downloaded_vulkan_layer():
            plan = StrategyEngine.build_plan(analysis)
        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}

        self.assertTrue(plan.needs_vulkan_layer)
        self.assertNotIn("dxgi.dll", destinations)
        self.assertNotIn("opengl32.dll", destinations)
        self.assertIn("feed-vk-layer", destinations)
        # x64 host: the add-on runs in the emulator, never in a helper process.
        self.assertIn("dlss5-addons/dlss5-feed.addon64", destinations)
        self.assertFalse(any(d.startswith("host64/") for d in destinations))

    def test_a_vulkan_plan_warns_when_the_layer_has_not_been_downloaded(self):
        """The state every fresh install is in, and it must say so rather than go quiet.

        Without the component there is nothing to stage, so the plan has to carry a
        warning naming what is missing - otherwise a Vulkan host that needs the fallback
        gets an install with no layer and no explanation of why.
        """
        analysis = replace(
            self.mock_analysis,
            exe_name="rpcs3.exe",
            emulator=EMULATOR_PROFILES["rpcs3"],
            emulator_api="vulkan",
            recommended_strategy=STRATEGY_EMULATOR,
        )
        missing_feeder = self.temp_dir / "empty_components" / "feeder"
        missing_feeder.mkdir(parents=True, exist_ok=True)
        original = ComponentManager.get_component_dir

        def routed(comp_id):
            return missing_feeder if comp_id == COMP_FEEDER else original(comp_id)

        with mock.patch.object(ComponentManager, "get_component_dir", side_effect=routed):
            plan = StrategyEngine.build_plan(analysis)

        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}
        self.assertNotIn("feed-vk-layer", destinations)
        self.assertTrue(
            any("layer-x64" in w for w in plan.warnings),
            f"a missing interop layer has to be reported; got {plan.warnings}",
        )

    def test_shadps4_is_vulkan_only_and_never_gets_a_local_dll(self):
        """shadPS4 has no D3D backend at all, so a dxgi.dll here would be loaded by nothing."""
        profile = EMULATOR_PROFILES["shadps4"]
        self.assertEqual(profile.apis, ["vulkan"])

        analysis = replace(
            self.mock_analysis,
            exe_name="shadPS4.exe",
            emulator=profile,
            emulator_api="vulkan",
            recommended_strategy=STRATEGY_EMULATOR,
        )
        plan = StrategyEngine.build_plan(analysis)
        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}

        self.assertTrue(plan.needs_vulkan_layer)
        self.assertNotIn("dxgi.dll", destinations)
        self.assertNotIn("opengl32.dll", destinations)
        self.assertIn("dlss5-addons/dlss5-feed.addon64", destinations)
        self.assertFalse(any(d.startswith("host64/") for d in destinations))

        # One backend means no renderer to pick: the instructions must not send the reader
        # hunting for a dropdown shadPS4 does not have.
        instructions = ConfigGenerator.generate_readme_guide(
            analysis, STRATEGY_EMULATOR, plan=plan
        )
        self.assertNotIn("SET THE RENDERER FIRST", instructions)
        self.assertIn("no renderer", instructions)

    def test_emulator_d3d12_plan_attaches_reshade_as_dxgi(self):
        analysis = replace(
            self.mock_analysis,
            exe_name="pcsx2-qt.exe",
            emulator=EMULATOR_PROFILES["pcsx2"],
            emulator_api="d3d12",
            recommended_strategy=STRATEGY_EMULATOR,
        )
        plan = StrategyEngine.build_plan(analysis)
        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}

        self.assertFalse(plan.needs_vulkan_layer)
        self.assertIn("dxgi.dll", destinations)
        self.assertNotIn("feed-vk-layer", destinations)

    def test_emulator_opengl_plan_attaches_reshade_as_opengl32(self):
        analysis = replace(
            self.mock_analysis,
            exe_name="rpcs3.exe",
            emulator=EMULATOR_PROFILES["rpcs3"],
            emulator_api="opengl",
            recommended_strategy=STRATEGY_EMULATOR,
        )
        plan = StrategyEngine.build_plan(analysis)
        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}

        self.assertIn("opengl32.dll", destinations)
        self.assertNotIn("dxgi.dll", destinations)

    def test_direct_install_and_restore(self):
        # 1. Install
        install_res = ModInstaller.install_to_game(analysis=self.mock_analysis)
        self.assertTrue(install_res.success)
        self.assertTrue((self.temp_dir / "ReShade.ini").exists())
        self.assertTrue((self.temp_dir / "DLSS5_Preset.ini").exists())
        self.assertTrue((self.temp_dir / "launch_with_dlss5.bat").exists())
        self.assertTrue((self.temp_dir / ".dlss5_backup" / "dlss5_manifest.json").exists())

        # 2. Restore
        ok, msg, removed = ModInstaller.restore_and_uninstall(self.temp_dir)
        self.assertTrue(ok)
        self.assertFalse((self.temp_dir / "ReShade.ini").exists())
        self.assertFalse((self.temp_dir / "DLSS5_Preset.ini").exists())
        self.assertFalse((self.temp_dir / ".dlss5_backup").exists())
        self.assertTrue(self.mock_exe.exists())  # Original exe preserved


class TestMotionVectorProvider(unittest.TestCase):
    """The profile chooses which shader estimates motion; everything else has to follow.

    Getting this wrong is silent. DLSS5_Feed.fx declares whichever texture
    DLSS5_MV_PROVIDER names, so a preset that selects a provider without enabling its
    technique - or stages the number without the .fx file - produces an install that
    loads, logs a delivered frame, and reconstructs from vectors of exactly zero.
    """

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dlss5_mv_"))
        self.mock_exe = self.temp_dir / "MockGame.exe"
        self.mock_exe.write_text("dummy exe content", encoding="utf-8")
        self.analysis = GameAnalysis(
            exe_path=self.mock_exe,
            game_dir=self.temp_dir,
            exe_name="MockGame.exe",
            file_size_bytes=1024,
            architecture="x64",
            is_64bit=True,
            detected_apis=["DirectX 11"],
            primary_api="DirectX 11",
            recommended_strategy=STRATEGY_FEEDER_DX11_12,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _techniques(self, preset: str) -> list:
        line = next(l for l in preset.splitlines() if l.startswith("Techniques="))
        return line.split("=", 1)[1].split(",")

    def test_default_profile_is_unchanged_by_the_provider_being_selectable(self):
        """The shipped default still is, and still generates, LumeniteFX Kernel."""
        self.assertEqual(DEFAULT_PROFILE["mv_provider"], MV_PROVIDER_LUMENITE)

        preset = ConfigGenerator.generate_preset_ini({})
        self.assertEqual(
            self._techniques(preset)[0], "Lumenite_Kernel@lumenite_Kernel.fx"
        )
        self.assertIn("DLSS5_MV_PROVIDER=3", preset)

    def test_choosing_vort_switches_the_technique_and_the_definition_together(self):
        """The number and the enabled technique are one choice, not two."""
        profile = {"mv_provider": MV_PROVIDER_VORT}
        preset = ConfigGenerator.generate_preset_ini(profile)
        techniques = self._techniques(preset)

        self.assertEqual(techniques[0], "vort_MotionEffects@vort_Motion.fx")
        self.assertIn("DLSS5_MV_PROVIDER=2", preset)
        self.assertLess(
            techniques.index("vort_MotionEffects@vort_Motion.fx"),
            techniques.index("DLSS5_Feed@DLSS5_Feed.fx"),
            "the provider has to write its vectors before the feed reads them",
        )
        self.assertIn(
            "DLSS5_MV_PROVIDER=2",
            ConfigGenerator.generate_reshade_ini(self.analysis, profile),
            "the global definition has to agree with the preset",
        )

    def test_vort_is_pinned_to_vectors_only(self):
        """vort_Motion.fx also carries motion blur and a TAA pass.

        Both would be applied to the frame *before* DLSS ran on it - blurring and
        re-resolving the neural pass's own input. They are off in vort's defaults, but a
        global definition left over from another preset would turn either back on, so the
        preset states all of it explicitly.
        """
        preset = ConfigGenerator.generate_preset_ini({"mv_provider": MV_PROVIDER_VORT})
        self.assertIn("[vort_Motion.fx]", preset)
        section = preset.split("[vort_Motion.fx]", 1)[1]
        self.assertIn("V_MV_MODE=1", section)
        self.assertIn("V_ENABLE_MOT_BLUR=0", section)
        self.assertIn("V_ENABLE_TAA=0", section)

    def test_lumenite_effects_keep_their_own_flow_writer(self):
        """RTAO, LSAO, SSSR and TRAA read Kernel::tFlow, whoever feeds DLSS.

        Re-declaring a texture is legal in ReShade - both declarations name the same
        resource - so with Kernel not enabled these effects compile and run perfectly on
        a texture of zeroes. The only symptom is AO that never settles, which reads as
        'the effect does nothing' rather than as a missing dependency.
        """
        preset = ConfigGenerator.generate_preset_ini({
            "mv_provider": MV_PROVIDER_VORT,
            "lumenite_effects": ["rtao", "traa"],
        })
        techniques = self._techniques(preset)

        self.assertIn("Lumenite_Kernel@lumenite_Kernel.fx", techniques)
        self.assertLess(
            techniques.index("Lumenite_Kernel@lumenite_Kernel.fx"),
            techniques.index("Lumenite_RTAO@lumenite_RTAO.fx"),
        )

    def test_quantao_pulls_in_the_quant_flow_writer(self):
        """QuantAO reads QuantMotion::tFlow, which only Lumenite_QuantMotion writes."""
        techniques = self._techniques(
            ConfigGenerator.generate_preset_ini({"lumenite_effects": ["quantao"]})
        )
        self.assertIn("Lumenite_QuantMotion@lumenite_QuantMotion.fx", techniques)
        self.assertLess(
            techniques.index("Lumenite_QuantMotion@lumenite_QuantMotion.fx"),
            techniques.index("Lumenite_QuantAO@lumenite_QuantAO.fx"),
        )

    def test_an_unknown_provider_falls_back_instead_of_failing(self):
        """A profile from a future version, or hand-edited, still has to build."""
        preset = ConfigGenerator.generate_preset_ini({"mv_provider": 99})
        self.assertIn("DLSS5_MV_PROVIDER=3", preset)
        self.assertEqual(
            self._techniques(preset)[0], "Lumenite_Kernel@lumenite_Kernel.fx"
        )

    def test_selecting_vort_stages_its_shader_headers_and_texture(self):
        """The .fx is useless without Includes/ and the blue noise it samples."""
        vort_dir = self.temp_dir / "vort_component"
        (vort_dir / "Shaders" / "Includes").mkdir(parents=True)
        (vort_dir / "Textures").mkdir(parents=True)
        (vort_dir / "Shaders" / "vort_Motion.fx").write_text("fx", encoding="utf-8")
        (vort_dir / "Shaders" / "vort_Static.fx").write_text("fx", encoding="utf-8")
        (vort_dir / "Shaders" / "Includes" / "vort_Defs.fxh").write_text("h", encoding="utf-8")
        (vort_dir / "Textures" / "vort_BlueNoise.png").write_bytes(b"png")

        plan = self._plan_with_vort_dir(vort_dir)
        destinations = {item.relative_dest.replace("\\", "/") for item in plan.items}

        self.assertIn("reshade-shaders/Shaders/vort_Motion.fx", destinations)
        self.assertIn("reshade-shaders/Shaders/Includes", destinations)
        self.assertIn("reshade-shaders/Textures/vort_BlueNoise.png", destinations)

        # ReShade compiles every .fx it finds, enabled or not: staging the rest of the
        # pack would buy a longer load and compile errors for effects nobody asked for.
        self.assertNotIn("reshade-shaders/Shaders/vort_Static.fx", destinations)

    def test_selecting_a_provider_that_is_not_downloaded_warns(self):
        """Silence here is the failure mode - the build succeeds and DLSS sees nothing."""
        plan = self._plan_with_vort_dir(self.temp_dir / "empty_component")
        self.assertTrue(
            any("vort_Motion.fx" in w for w in plan.warnings),
            f"expected a warning naming the missing shader, got {plan.warnings}",
        )

    def _plan_with_vort_dir(self, vort_dir: Path):
        """Build a plan with the vort component pointed at `vort_dir`.

        The component cache is a real folder under the install, so a test that read it
        would pass or fail on whether this machine had run a download.
        """
        real = strategy_module.ComponentManager.get_component_dir

        def fake(comp_id):
            return vort_dir if comp_id == COMP_VORT else real(comp_id)

        with mock.patch.object(
            strategy_module.ComponentManager, "get_component_dir", side_effect=fake
        ):
            return StrategyEngine.build_plan(
                self.analysis, profile={"mv_provider": MV_PROVIDER_VORT}
            )


if __name__ == "__main__":
    unittest.main()
