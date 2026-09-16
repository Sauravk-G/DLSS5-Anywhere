"""
Tests for GameDetector module.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from dlss5_anywhere.config import (
    STRATEGY_32BIT_FEEDER,
    STRATEGY_DGVOODOO_DX9,
    STRATEGY_DXVK_DX9,
    STRATEGY_FEEDER_DX11_12,
    STRATEGY_FSR_BRIDGE,
    STRATEGY_NATIVE_DLSS,
)
from dlss5_anywhere.core.detector import GameAnalysis, GameDetector


class TestGameDetector(unittest.TestCase):

    def test_strategy_decision_rules(self):
        # 1. 32-bit test
        strat, _ = GameDetector._determine_strategy(
            is_64bit=False,
            primary_api="DirectX 11",
            detected_apis=["DirectX 11"],
            has_native_dlss=False,
            has_native_streamline=False,
            has_native_fsr=False,
            has_native_xess=False,
        )
        self.assertEqual(strat, STRATEGY_32BIT_FEEDER)

        # 2. Pure DX9 -> DXVK. The add-on has no D3D9 path, so something must translate;
        #    DXVK does it in-process to Vulkan, which the add-on names as supported, and
        #    without a permanent watermark or an emulated card's VRAM figure.
        strat, _ = GameDetector._determine_strategy(
            is_64bit=True,
            primary_api="DirectX 9",
            detected_apis=["DirectX 9"],
            has_native_dlss=False,
            has_native_streamline=False,
            has_native_fsr=False,
            has_native_xess=False,
        )
        self.assertEqual(strat, STRATEGY_DXVK_DX9)

        # 2b. DirectX 8 stays on dgVoodoo2: DXVK reaches D3D8 through a shim over its D3D9
        #     path, which is not what the feeder is tested against.
        strat, _ = GameDetector._determine_strategy(
            is_64bit=False,
            primary_api="DirectX 8",
            detected_apis=["DirectX 8"],
            has_native_dlss=False,
            has_native_streamline=False,
            has_native_fsr=False,
            has_native_xess=False,
        )
        self.assertEqual(strat, STRATEGY_DGVOODOO_DX9)

        # 3. Native DLSS test
        strat, _ = GameDetector._determine_strategy(
            is_64bit=True,
            primary_api="DirectX 12",
            detected_apis=["DirectX 12"],
            has_native_dlss=True,
            has_native_streamline=True,
            has_native_fsr=False,
            has_native_xess=False,
        )
        self.assertEqual(strat, STRATEGY_NATIVE_DLSS)

        # 4. FSR Bridge test
        strat, _ = GameDetector._determine_strategy(
            is_64bit=True,
            primary_api="DirectX 11",
            detected_apis=["DirectX 11"],
            has_native_dlss=False,
            has_native_streamline=False,
            has_native_fsr=True,
            has_native_xess=False,
        )
        self.assertEqual(strat, STRATEGY_FSR_BRIDGE)

        # 5. Standard Feeder DX11/12 test
        strat, _ = GameDetector._determine_strategy(
            is_64bit=True,
            primary_api="DirectX 11",
            detected_apis=["DirectX 11"],
            has_native_dlss=False,
            has_native_streamline=False,
            has_native_fsr=False,
            has_native_xess=False,
        )
        self.assertEqual(strat, STRATEGY_FEEDER_DX11_12)

    def test_real_executable_analysis(self):
        """End-to-end analysis of an actual game binary, if one is offered.

        The path comes from the environment rather than being written in, for two
        reasons. A hard-coded path is one developer's drive layout: it names a game they
        own and a library they keep it in, and it silently does nothing on every other
        machine - the previous version of this test was an `if path.exists()` that passed
        without running anywhere but the author's PC. Set DLSS5_TEST_EXE to any game
        executable to exercise the real PE-reading path:

            set DLSS5_TEST_EXE=D:\\Games\\Something\\game.exe
        """
        sample = os.environ.get("DLSS5_TEST_EXE", "").strip('"')
        if not sample:
            self.skipTest("set DLSS5_TEST_EXE to a game .exe to run this")
        sample_path = Path(sample)
        if not sample_path.is_file():
            self.skipTest(f"DLSS5_TEST_EXE does not point at a file: {sample_path}")

        report = GameDetector.analyze(sample_path)
        self.assertIsInstance(report, GameAnalysis)
        self.assertEqual(report.exe_name, sample_path.name)
        self.assertIn(report.architecture.lower(), {"x64", "x86"})
        self.assertTrue(report.primary_api)


class TestRenderingApiEvidence(unittest.TestCase):
    """The API decides where ReShade attaches, so a wrong one wastes a whole install."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_api_"))
        # A stand-in for the game binary. It is deliberately not a real PE: these tests
        # drive _detect_rendering_apis with the import lists directly, so what is under
        # test is how the evidence is weighed, not the PE parser.
        self.exe = self.tmp / "game.exe"
        self.exe.write_bytes(b"MZ" + b"\x00" * 512)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _detect(self, imports=None, delay=None, mods=None, engine=""):
        return GameDetector._detect_rendering_apis(
            self.exe, self.tmp, imports or [], delay or [], mods or [], engine
        )

    def test_a_wrapper_in_the_folder_is_not_the_games_api(self):
        """DXVK's d3d9.dll used to be read as the game shipping a DirectX 9 renderer."""
        (self.tmp / "d3d9.dll").write_bytes(b"junk" * 64 + b"DXVK: setting up d3d9" + b"junk" * 64)
        apis, primary, evidence, _, wrappers = self._detect(imports=["d3d11.dll"])

        self.assertEqual(primary, "DirectX 11")
        self.assertIn("DXVK (d3d9.dll)", wrappers)
        self.assertNotIn("DirectX 9", apis)

    def test_an_unidentified_sibling_is_recorded_but_flagged(self):
        """An unknown d3d11.dll may still be an injector, so it must not outrank an import."""
        (self.tmp / "d3d11.dll").write_bytes(b"nothing recognisable here" * 100)
        apis, primary, evidence, _, wrappers = self._detect(imports=["d3d9.dll"])

        self.assertEqual(primary, "DirectX 9")
        self.assertEqual(wrappers, [])
        self.assertTrue(
            any("could be a wrapper" in line for line in evidence.get("DirectX 11", [])),
            "an unidentified graphics-named sibling has to carry that caveat",
        )

    def test_delay_loaded_renderer_is_seen(self):
        """Reading only the import table reports whichever backend was linked eagerly."""
        _, primary, _, _, _ = self._detect(imports=["dxgi.dll"], delay=["d3d12.dll"])
        self.assertEqual(primary, "DirectX 12")

    def test_agility_sdk_breaks_the_d3d11_d3d12_tie(self):
        """A D3D12 game commonly links d3d11 too; only D3D12 gains from the Agility SDK."""
        (self.tmp / "D3D12Core.dll").write_bytes(b"\x00" * 128)
        apis, primary, _, _, _ = self._detect(imports=["d3d11.dll", "dxgi.dll"])
        self.assertEqual(primary, "DirectX 12")
        self.assertIn("DirectX 11", apis, "the D3D11 path is still real and worth reporting")

    def test_this_tools_own_files_are_ignored(self):
        """Re-analysing an already-modded game must not read the mod as the game."""
        (self.tmp / "dxgi.dll").write_bytes(b"unknown injector")
        apis, primary, _, _, _ = self._detect(imports=["vulkan-1.dll"], mods=["dxgi.dll"])
        self.assertEqual(primary, "Vulkan")
        self.assertEqual(apis, ["Vulkan"])

    def test_no_evidence_falls_back_without_claiming_confidence(self):
        apis, primary, evidence, confidence, _ = self._detect()
        self.assertEqual(primary, "DirectX 11")
        self.assertEqual(confidence, "low")
        self.assertEqual(apis, [])


class TestAntiCheatRisk(unittest.TestCase):
    """The ban lands on the player's account, so this warning has to fire before an install."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_ac_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_known_online_title_is_flagged_by_name_alone(self):
        """Elden Ring ships no anti-cheat files next to the exe, and bans for DLLs anyway."""
        has_ac, warnings, is_mp, names, level = GameDetector._detect_anticheat_and_risks(
            "eldenring.exe", self.tmp
        )
        self.assertTrue(has_ac)
        self.assertTrue(is_mp)
        self.assertEqual(level, "online_competitive")
        self.assertTrue(any("banned" in w for w in warnings))

    def test_anti_cheat_is_found_in_a_subfolder(self):
        """EAC and BattlEye install into their own directory, not beside the executable."""
        (self.tmp / "EasyAntiCheat").mkdir()
        (self.tmp / "EasyAntiCheat" / "EasyAntiCheat_x64.dll").write_bytes(b"")
        has_ac, warnings, is_mp, names, level = GameDetector._detect_anticheat_and_risks(
            "somegame.exe", self.tmp
        )
        self.assertTrue(has_ac)
        self.assertEqual(level, "anti_cheat")
        self.assertIn("Easy Anti-Cheat", names)
        self.assertTrue(any("kernel-level" in w for w in warnings))

    def test_a_clean_single_player_game_is_not_nagged(self):
        """A warning that fires on everything is a warning nobody reads."""
        (self.tmp / "game.exe").write_bytes(b"")
        (self.tmp / "data").mkdir()
        has_ac, warnings, is_mp, names, level = GameDetector._detect_anticheat_and_risks(
            "game.exe", self.tmp
        )
        self.assertFalse(has_ac)
        self.assertEqual(level, "none")
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()


class TestOurOwnFilesAreNotTheGames(unittest.TestCase):
    """A game this tool has already modded must not be misread as shipping DLSS.

    `nvngx_dlss.dll` next to a game .exe is genuinely ambiguous - it is either something
    the game shipped, or something an install put there. Reading it as the former made the
    detector recommend the Streamline path for a game with no native DLSS at all, on the
    strength of a file the tool had written itself.

    The manifest settles the copy this tool staged, and that is what these cover: a file
    named in it is ours, and a file that is not stays visible. Whether a file that stays
    visible means the game has DLSS is the separate question below - it does not, on its
    own, which is why `has_dlss` is False throughout here and the assertions are about
    the runtime still being *reported*.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_selfdetect_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "nvngx_dlss.dll").write_bytes(b"MZ")

    def _write_manifest(self, injected):
        backup = self.tmp / ".dlss5_backup"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "dlss5_manifest.json").write_text(
            json.dumps({"strategy": "feeder_dx11_12", "injected_files": injected}),
            encoding="utf-8",
        )

    def _detect(self):
        _reshade, _dlss5, mods = GameDetector._detect_existing_mods(self.tmp)
        has_dlss, _sl, has_fsr, _xess, _files, loose, _fg = (
            GameDetector._detect_upscalers(self.tmp, [], mods)
        )
        return has_dlss, has_fsr, loose

    def test_a_runtime_we_installed_is_not_reported_at_all(self):
        self._write_manifest(["nvngx_dlss.dll", "nvngx_dlssnr.dll", "dlss5-feed.addon64"])
        has_dlss, _, loose = self._detect()
        self.assertFalse(has_dlss, "our own nvngx_dlss.dll was read as the game's")
        self.assertEqual(loose, [], "and it should not be raised with the user either")

    def test_a_runtime_that_is_not_ours_is_still_seen(self):
        """The manifest must not become a way of not looking: no manifest means nothing
        is ours, and the file has to reach the report."""
        has_dlss, _, loose = self._detect()
        self.assertEqual(loose, ["nvngx_dlss.dll"])
        self.assertFalse(has_dlss, "one loose runtime is not proof the game ships DLSS")

    def test_a_manifest_naming_other_files_leaves_it_alone(self):
        self._write_manifest(["dlss5-feed.addon64", "ReShade.ini"])
        self.assertEqual(self._detect()[2], ["nvngx_dlss.dll"])

    def test_an_unreadable_manifest_is_not_fatal(self):
        backup = self.tmp / ".dlss5_backup"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "dlss5_manifest.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(
            self._detect()[2], ["nvngx_dlss.dll"],
            "a broken manifest must not change the answer",
        )

    def test_full_paths_in_the_manifest_are_matched_by_name(self):
        self._write_manifest([r"C:\Games\Thing\nvngx_dlss.dll"])
        self.assertEqual(self._detect()[2], [])


class TestALooseRuntimeDoesNotPickTheStrategy(unittest.TestCase):
    """Resident Evil 4 has no DLSS. It offers FSR, and it was planned for Streamline.

    A hand-swapped nvngx_dlss.dll beside the executable is the most ordinary thing in a
    modded game folder, and it looks identical to one a game shipped. Acting on it picked
    the Direct / Streamline Upgrade Path, which installs the neural runtime and then waits
    for DLSS calls a game like that never makes - nothing happens, and nothing says why.

    The feeder works in both cases, so that is where the ambiguity now falls, with the
    file named in the report and the other path a dropdown away. Streamline files and an
    NGX import in the executable are not ambiguous and still decide it outright.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_loose_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _detect(self, imports=()):
        return GameDetector._detect_upscalers(self.tmp, list(imports), [])

    def test_a_lone_runtime_is_not_native_dlss(self):
        (self.tmp / "nvngx_dlss.dll").write_bytes(b"MZ")
        has_dlss, has_sl, _fsr, _xess, files, loose, _fg = self._detect()
        self.assertFalse(has_dlss)
        self.assertFalse(has_sl)
        self.assertEqual(loose, ["nvngx_dlss.dll"])
        self.assertIn("nvngx_dlss.dll", files, "it still has to appear in the report")

    def test_the_dlss_enabler_shim_is_not_either(self):
        """_nvngx.dll is what a DLSS-enabler mod drops in, not what a game ships."""
        (self.tmp / "_nvngx.dll").write_bytes(b"MZ")
        self.assertFalse(self._detect()[0])

    def test_streamline_still_decides_it(self):
        (self.tmp / "nvngx_dlss.dll").write_bytes(b"MZ")
        (self.tmp / "sl.interposer.dll").write_bytes(b"MZ")
        has_dlss, has_sl, _fsr, _xess, _files, loose, _fg = self._detect()
        self.assertTrue(has_dlss)
        self.assertTrue(has_sl)
        self.assertEqual(loose, [], "corroborated, so there is no ambiguity to report")

    def test_so_does_the_executable_importing_ngx(self):
        (self.tmp / "nvngx_dlss.dll").write_bytes(b"MZ")
        has_dlss, _sl, _fsr, _xess, _files, loose, _fg = self._detect(["nvngx.dll"])
        self.assertTrue(has_dlss)
        self.assertEqual(loose, [])

    def test_the_recommendation_that_follows_is_the_feeder(self):
        """The whole point: what RE4 is planned as now."""
        strategy, why = GameDetector._determine_strategy(
            is_64bit=True, primary_api="DirectX 12", detected_apis=["DirectX 12"],
            has_native_dlss=False, has_native_streamline=False,
            has_native_fsr=False, has_native_xess=False,
        )
        self.assertEqual(strategy, STRATEGY_FEEDER_DX11_12)
        self.assertIn("without native DLSS", why)
