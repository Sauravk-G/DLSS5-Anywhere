"""
Installation Strategy Engine for DLSS5-Anywhere.
Calculates file mapping plans, wrapper configurations, and shader pipelines for any target game.

The layouts here follow the DLSS5-Feeder install guide:
https://github.com/jlrouzies-fr/DLSS5-Feeder#readme

Two facts drive every plan:

* NGX is 64-bit only. A 32-bit game therefore cannot host the neural rendering stack in
  process at all - `dlss5-feed.addon32` runs in the game and talks to a 64-bit helper in
  `host64\\`, which is where ReShade x64, renodx-dlss5.addon64 and the nvngx runtimes live.
* D3D9 is not a supported presentation path. dgVoodoo2 translates it to D3D11 first, and
  ReShade is then installed as dxgi.dll - never as d3d9.dll, which dgVoodoo owns.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import (
    ADDON_DIR_NAME,
    COMP_DFC,
    COMP_MFG_UNLOCK,
    MFG_ADDON_NAME,
    MFG_MIN_DRIVER,
    MFG_SECTION,
    dfc_same_device_target,
    mfg_settings,
    uses_dfc,
    wants_mfg_unlock,
    COMP_DGVOODOO,
    COMP_DXVK,
    DEFAULT_PROFILE,
    RESHADE_ATTACH_BY_API,
    choose_reshade_proxy,
    COMP_FEEDER,
    COMP_LUMENITE,
    COMP_NVNGX_DLSS,
    COMP_NVNGX_DLSSNR,
    COMP_OPTISCALER,
    COMP_RENODX,
    COMP_RESHADE,
    COMP_REMIX,
    COMP_RESHADE_SHADERS,
    COMP_IMMERSE,
    COMP_VORT,
    MV_PROVIDER_VORT,
    RENODX_PINNED_VERSION,
    STRATEGY_32BIT_FEEDER,
    STRATEGY_DESCRIPTIONS,
    STRATEGY_DGVOODOO_DX9,
    STRATEGY_DISPLAY_NAMES,
    STRATEGY_DXVK_DX9,
    STRATEGY_FEEDER_DX11_12,
    STRATEGY_FSR_BRIDGE,
    STRATEGY_NATIVE_DLSS,
    STRATEGY_REMIX_VULKAN,
    USER_SUPPLIED_DIR,
    mv_provider_for,
)
from .components import ComponentManager
from .config_gen import IMMERSE_EFFECTS
from .detector import GameAnalysis, GameDetector

# Where ReShade looks for effects and textures, relative to the game executable.
SHADERS_DEST = os.path.join("reshade-shaders", "Shaders")
TEXTURES_DEST = os.path.join("reshade-shaders", "Textures")

# Where our add-ons go, and the only folder the generated ReShade.ini searches.
# See ADDON_DIR_NAME in config.py for why they do not sit next to the .exe.
ADDON_DEST = ADDON_DIR_NAME

# The 64-bit helper folder a 32-bit game needs.
HOST64_DIR = "host64"

# ...and where it goes, which is not next to the executable. The 32-bit add-on resolves
# host64\ against its own module directory: with the add-on in dlss5-addons\ and the
# folder in the game root, it reported "the 64-bit host is not installed" while a complete
# host64\ sat beside the .exe. Moved here, the same build logs the path it spawns -
# "...\dlss5-addons\host64\dlss5-feed-host64.exe" - which is where this came from.
HOST64_DEST = os.path.join(ADDON_DEST, HOST64_DIR)

# RTX Remix keeps its 64-bit renderer, and everything that renderer loads, in .trex.
REMIX_RENDERER_DIR = ".trex"

# Where the Vulkan interop fallback layer is staged for emulator installs.
VK_LAYER_DIR = "feed-vk-layer"


@dataclass
class FilePlanItem:
    """Represents a single file transfer or configuration generation in the build plan."""
    source_path: Optional[Path]
    relative_dest: str
    description: str
    is_generated: bool = False
    generated_content: Optional[str] = None


@dataclass
class StrategyPlan:
    """Comprehensive blueprint for staging a build or injecting into game directory."""
    strategy_id: str
    display_name: str
    description: str
    target_exe: Path
    game_dir: Path
    is_64bit: bool
    items: List[FilePlanItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    steps_summary: List[str] = field(default_factory=list)
    # Layout facts the config generator and the README need.
    uses_dgvoodoo: bool = False
    uses_dxvk: bool = False
    uses_host64: bool = False
    needs_rage_commandline: bool = False
    uses_remix: bool = False
    # Emulator installs: which renderer the plan was built for, and whether ReShade has to
    # be registered as a Vulkan layer instead of dropped in as a local DLL.
    emulator_api: str = ""
    needs_vulkan_layer: bool = False


class StrategyEngine:
    """Engine that designs the precise installation plan based on game diagnostics and user profile."""

    @staticmethod
    def _warn_about_same_device_d3d12(
        analysis,
        profile: Dict[str, Any],
        emulator,
        emulator_api: str,
        in_process: bool,
        warnings: List[str],
    ) -> None:
        """Say when this build puts Deep Fried Chicken on the one transport it cannot use.

        See DFC_BLOCKED_TRANSPORT_API. The feeder hands DLSS the game's own D3D12 device
        whenever the thing it attached to is already presenting through Direct3D 12, and
        Chicken's FP16 codec acquisition then fails on device identity and disables its
        neural path before the first frame.

        This has to be said here because nothing else will. Such an install loads, arms,
        reports interception state 2 (ARMED) in dlss5-feed.log and delivers DLAA frames
        for as long as you play - the only record that the neural consumer did nothing is
        two lines in its own log, in a folder nobody opens when the install looks fine.
        """
        if emulator:
            if not dfc_same_device_target(profile, emulator_api=emulator_api):
                return
            warnings.append(
                f"{emulator.name} is set to Direct3D 12, and that is the one renderer Deep "
                "Fried Chicken cannot run on: the feeder then feeds DLSS the emulator's own "
                "device, and Chicken answers with \"standalone FP16 codec allocation failed: "
                "game-output device identity failed\" and switches its neural path off for "
                "the session. Nothing else reports a problem - the add-on still loads, still "
                "arms, and DLAA frames keep being delivered. Set the renderer to Vulkan and "
                "install again, or stay on Direct3D 12 and use RenoDX as the consumer."
            )
            return

        if dfc_same_device_target(
            profile, primary_api=analysis.primary_api, is_64bit=in_process
        ):
            warnings.append(
                "This game presents through Direct3D 12, which is the one case Deep Fried "
                "Chicken cannot run in: the feeder uses the game's own device, and Chicken "
                "answers with \"standalone FP16 codec allocation failed: game-output device "
                "identity failed\" and switches its neural path off before the first frame. "
                "The overlay, its tabs and the pass count all still work, so the install "
                "looks correct - deep-fried-chicken.log is the only place that says "
                "otherwise. If the game has a DirectX 11 mode, build for that; otherwise "
                "use RenoDX as the neural consumer here."
            )

    @classmethod
    def _mfg_unlock_items(
        cls, analysis, profile: Dict[str, Any], addon_prefix: str,
        is_64bit: bool, warnings: List[str], steps: List[str],
    ) -> List[FilePlanItem]:
        """Stage the RTX 40 multi-frame-generation unlock, and say what it needs.

        This add-on is the odd one out in this tool. Everything else here exists for a
        game with no DLSS, where the feeder builds the contract from ReShade's depth and
        estimated motion vectors. This one multiplies frame generation the game already
        ships - so on the games this tool is usually pointed at there is nothing for it
        to multiply, and it will load, report itself, and do nothing at all.

        The requirements it cannot check for itself are the card and the driver. Neither
        is knowable from a game folder, so both are said rather than tested.
        """
        if not wants_mfg_unlock(profile):
            return []

        if not is_64bit:
            warnings.append(
                "The multi frame generation unlock is not deployed here: this executable is "
                "32-bit, and DLSS Frame Generation is x64 only - there is no 32-bit DLSS-G "
                "for it to multiply."
            )
            return []

        src = ComponentManager.get_component_dir(COMP_MFG_UNLOCK) / MFG_ADDON_NAME
        if not src.exists():
            warnings.append(
                f"'{MFG_ADDON_NAME}' is missing and the profile asks for multi frame "
                "generation. Download it on the Components tab, or turn the option off."
            )

        if not getattr(analysis, "has_frame_generation", False):
            warnings.append(
                "Multi frame generation is switched on, but nothing in this game folder ships "
                "DLSS Frame Generation - no nvngx_dlssg.dll and no Streamline sl.dlss_g. The "
                "unlock multiplies frame generation a game already has; it cannot add it. On "
                "this game the add-on will load and have nothing to work on. It is deployed "
                "anyway because a game can carry DLSS-G in a subfolder this scan does not "
                "reach, but if the overlay reports no frame generation, that is why."
            )

        warnings.append(
            "Multi frame generation needs an RTX 40 card and NVIDIA driver "
            f"{MFG_MIN_DRIVER} or newer, neither of which can be read from a game folder - "
            "check them yourself. NVIDIA has blocked this unlock once already and the mod "
            "was updated to restore it, so expect a driver update to break it. And it is "
            "frame generation: never take it into a game with anti-cheat."
        )

        force, count = mfg_settings(profile)
        if not force:
            warnings.append(
                "Multi frame generation is set to follow the game's own multiplier. That is "
                "right for a game whose menu offers 2x/3x/4x, and does nothing at all for one "
                "that only offers Frame Generation on/off - which is most titles on an RTX 40, "
                "because the driver never advertised MFG to them. Pick a multiplier on the "
                f"Builder tab, or set Force frame multiplier in the overlay's {MFG_SECTION} "
                "panel once in game."
            )

        steps.append(
            f"Deploy {MFG_ADDON_NAME} and its [{MFG_SECTION}] settings "
            f"(multi frame generation on RTX 40, "
            f"{'game-driven' if not force else f'forced {force}x'}, ceiling {count}x)"
        )
        items = [FilePlanItem(
            src if src.exists() else None,
            os.path.join(addon_prefix, MFG_ADDON_NAME),
            "MFG Ada Unlock - 3x/4x frame generation on an RTX 40 card",
        )]

        # The multi-frame code is in this runtime, not in the game. A title still on the
        # DLSS 3 snippet has none of it, and the unlock then has nothing to raise - so the
        # add-on lands, reports itself, and changes nothing. Beside the executable, which
        # is where the game's own loader looks, not in the private add-on folder.
        dlssg = USER_SUPPLIED_DIR / "nvngx_dlssg.dll"
        if not dlssg.exists():
            warnings.append(
                "'nvngx_dlssg.dll' is not imported, so this build stages no frame generation "
                "runtime. If the game is still on the DLSS 3 snippet (3.5.x) it carries no "
                "multi-frame code at all and the unlock will have nothing to raise. Import a "
                "310.x or newer copy on the Components tab - TechPowerUp's DLSS archive or "
                "DLSS Swapper - or leave it if the game already ships one."
            )
        else:
            steps.append("Stage nvngx_dlssg.dll beside the game (frame generation runtime)")
            items.append(FilePlanItem(
                dlssg, "nvngx_dlssg.dll",
                "DLSS Frame Generation runtime - the multi-frame code lives here",
            ))
        return items

    @staticmethod
    def _warn_about_foreign_addons(analysis, warnings: List[str]) -> None:
        """Say what else is already loading into this game.

        ReShade loads every add-on in the game folder. An install into a folder that
        already holds a dozen of them is not the install anybody thinks they are getting:
        the symptoms are a flickering overlay, a frame that has been tone-mapped three
        times, and a DLSS path that reports ready while another add-on holds the hooks.

        The install gives our add-ons a folder of their own and points AddonPath at it,
        so ReShade never enumerates the game folder and never opens these files at all -
        which is why this reports what was done rather than what to go and do.

        ReShade's own DisabledAddons key cannot do this job. It matches on the name an
        add-on registers at runtime ("Adjust Depth" for a file called
        ReShade64-AdjustDepth-By-seri14.addon64), and an installer looking at files on
        disk cannot know that name until the add-on has loaded, which is already too late.
        An earlier version of this listed file stems there and silently matched nothing.

        Nothing on disk is touched either way: these are the user's other mods, and moving
        them out of the way is not something an installer gets to do - only declining to
        look at them is.
        """
        foreign = list(getattr(analysis, "foreign_addons", []) or [])
        if not foreign:
            return

        known = [(name, GameDetector.describe_addon_conflict(name)) for name in foreign]
        clashing = [(n, why) for n, why in known if why]

        warnings.append(
            f"{len(foreign)} other ReShade add-on(s) are in this folder and will not be "
            f"loaded: ReShade is pointed at {ADDON_DEST}" + chr(92) + " instead, so it never "
            "opens them. Nothing is moved, renamed or deleted - set AddonPath=." + chr(92) +
            " in ReShade.ini to go back to loading everything. Not loaded: "
            + ", ".join(foreign[:8])
            + (f", and {len(foreign) - 8} more" if len(foreign) > 8 else "")
        )
        for name, why in clashing:
            warnings.append(f"'{name}' would have conflicted - {why}.")

    @classmethod
    def build_plan(
        cls,
        analysis: GameAnalysis,
        strategy_override: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> StrategyPlan:
        """Create a complete, executable deployment plan for the given game and profile."""
        items: List[FilePlanItem] = []
        warnings: List[str] = list(analysis.anti_cheat_warnings) + list(analysis.advisories)
        cls._warn_about_foreign_addons(analysis, warnings)
        steps: List[str] = []
        merged_profile: Dict[str, Any] = {**DEFAULT_PROFILE, **(profile or {})}

        strategy_id = strategy_override or analysis.recommended_strategy

        # The profile picks which of the two legacy-D3D paths is used, but only when the
        # caller did not name a strategy outright - an explicit override from the GUI
        # dropdown or the CLI's --strategy is the user saying it in the more specific
        # place, and must win. D3D8 is not offered to DXVK either way: DXVK reaches D3D8
        # through a shim over its own D3D9 path, which is not what the feeder is tested
        # against, so a D3D8 title stays on dgVoodoo2 whatever the profile says.
        if strategy_override is None and strategy_id in (STRATEGY_DXVK_DX9, STRATEGY_DGVOODOO_DX9):
            wanted = str(merged_profile.get("d3d9_translation", "dxvk")).lower()
            work_resolution = int(merged_profile.get("feed_work_resolution", 100))

            if analysis.primary_api.startswith("DirectX 8"):
                strategy_id = STRATEGY_DGVOODOO_DX9
            elif work_resolution < 100 and wanted != "dgvoodoo":
                # The profile is asking DLSS to reconstruct from a smaller internal
                # render - the setting people actually use to buy frames. The add-on
                # honours it on its D3D11 transport and pins it to 100% on OpenGL and
                # Vulkan, so a D3D9 game that goes through DXVK cannot have it at all.
                # dgVoodoo lands on D3D11 and can. Silently taking the Vulkan route here
                # would leave the slider greyed out in the overlay with nothing anywhere
                # explaining why, which is how this was found.
                strategy_id = STRATEGY_DGVOODOO_DX9
                warnings.append(
                    f"Work resolution is set to {work_resolution}%, so this build uses dgVoodoo2 "
                    "rather than DXVK: the DLSS 5 add-on only exposes that control on its D3D11 "
                    "transport, and fixes it at 100% on Vulkan. Set the work resolution back to "
                    "100% to use DXVK (no watermark, real video memory reporting), or choose the "
                    "strategy outright to override this."
                )
            elif wanted == "dgvoodoo":
                strategy_id = STRATEGY_DGVOODOO_DX9
            elif wanted == "dxvk":
                strategy_id = STRATEGY_DXVK_DX9

        display_name = STRATEGY_DISPLAY_NAMES.get(strategy_id, strategy_id)
        description = STRATEGY_DESCRIPTIONS.get(strategy_id, "")

        if strategy_id == STRATEGY_FSR_BRIDGE:
            return cls._build_optiscaler_plan(analysis, strategy_id, display_name, description, warnings)

        if strategy_id == STRATEGY_REMIX_VULKAN:
            return cls._build_remix_plan(analysis, strategy_id, display_name, description, warnings)

        is_64bit = analysis.is_64bit
        uses_dgvoodoo = strategy_id == STRATEGY_DGVOODOO_DX9
        uses_dxvk = strategy_id == STRATEGY_DXVK_DX9
        # NGX only exists as 64-bit code, so every 32-bit game needs the helper process,
        # whatever its rendering API is.
        uses_host64 = not is_64bit

        # An emulator picks its renderer at runtime from its own settings, and that choice -
        # not the PE imports - decides ReShade's attach point.
        emulator = getattr(analysis, "emulator", None)
        emulator_api = ""
        needs_vulkan_layer = False
        if emulator:
            emulator_api = (
                getattr(analysis, "emulator_api", "") or emulator.recommended
            )
            needs_vulkan_layer = emulator_api == "vulkan"
            warnings.extend(emulator.caveats)
            if not is_64bit:
                warnings.append(
                    f"{emulator.name} was detected but this executable is 32-bit. Emulator builds "
                    "are x64; you have most likely picked a launcher or an old build."
                )

        # ------------------------------------------------------------------
        # 1. dgVoodoo2, when the game presents through D3D9/D3D8
        # ------------------------------------------------------------------
        if uses_dgvoodoo:
            dgv_dir = ComponentManager.get_component_dir(COMP_DGVOODOO)
            arch_suffix = "x64" if is_64bit else "x86"
            d3d9 = cls._first_existing(
                dgv_dir / f"D3D9_{arch_suffix}.dll",
                dgv_dir / "MS" / arch_suffix / "D3D9.dll",
            )
            if d3d9 is None:
                warnings.append(
                    f"dgVoodoo2 {arch_suffix} wrapper (D3D9.dll) is missing. Download the dgVoodoo2 "
                    "component, or the game will keep running on plain D3D9 and nothing else will load."
                )
            steps.append(f"Deploy dgVoodoo2 {arch_suffix} D3D9 -> D3D11 wrapper (D3D9.dll) + dgVoodoo.conf")
            items.append(FilePlanItem(d3d9, "D3D9.dll", f"dgVoodoo2 D3D9 to D3D11 wrapper ({arch_suffix})"))

            cpl = dgv_dir / "dgVoodooCpl.exe"
            if cpl.exists():
                items.append(FilePlanItem(cpl, "dgVoodooCpl.exe", "dgVoodoo2 control panel"))

        # ------------------------------------------------------------------
        # 1b. DXVK, the other way out of D3D9 - straight to Vulkan, in-process.
        #
        # Only d3d9.dll is deployed. DXVK's own dxgi.dll and d3d11.dll would take the
        # filename ReShade uses on every other path here, and a D3D9 game never loads them
        # anyway. Because the game then presents through Vulkan, ReShade has no local-DLL
        # entry point at all and is registered as a layer further down.
        # ------------------------------------------------------------------
        if uses_dxvk:
            arch_dir = "x64" if is_64bit else "x32"
            dxvk_d3d9 = ComponentManager.get_component_dir(COMP_DXVK) / arch_dir / "d3d9.dll"
            if not dxvk_d3d9.exists():
                warnings.append(
                    f"DXVK's {arch_dir}/d3d9.dll is missing. Download the DXVK component, or the "
                    "game keeps running on the system D3D9 runtime and nothing downstream loads."
                )
            steps.append(f"Deploy DXVK {arch_dir} d3d9.dll (D3D9 -> Vulkan) + dxvk.conf")
            items.append(FilePlanItem(
                dxvk_d3d9 if dxvk_d3d9.exists() else None,
                "d3d9.dll",
                f"DXVK D3D9 to Vulkan translation ({arch_dir})",
            ))
            # dgVoodoo announces itself with a watermark; DXVK has no such tell, so the
            # log it writes on request is the only way to confirm it is the runtime in use.
            warnings.append(
                "DXVK leaves no watermark, so to confirm it is actually loaded set DXVK_HUD=version "
                "(or DXVK_LOG_LEVEL=info) for one launch - the overlay/log names the DXVK build. "
                "If the game still starts with the system d3d9.dll, nothing downstream will work."
            )
            warnings.append(
                "On this path the DLSS 5 add-on's 'Work resolution' control is fixed at 100% - it "
                "is only adjustable on the D3D11 transport, and DXVK presents through Vulkan. If "
                "you rely on rendering below native for frames, use the dgVoodoo2 strategy instead."
            )
            warnings.append(
                "DXVK reads its config from the working directory ($PWD/dxvk.conf), not from next "
                "to the executable. launch_with_dlss5.bat points DXVK_CONFIG_FILE straight at the "
                "generated file; launching another way only picks it up when the working directory "
                "happens to be the game folder."
            )
            if analysis.supports_rage_commandline:
                warnings.append(
                    "DXVK ships a built-in profile for GTA IV that pins dxgi.maxDeviceMemory to "
                    "128 MB on purpose, to work around how the engine handles large values - which "
                    "is why the game reports a fraction of the card's memory here. Set "
                    "'dxvk_max_device_memory_mb' in the profile to override it, remembering that "
                    "the 128 MB is a workaround you are then switching off."
                )

        # ------------------------------------------------------------------
        # 2. ReShade runtime in the game process (always named dxgi.dll)
        # ------------------------------------------------------------------
        reshade_dir = ComponentManager.get_component_dir(COMP_RESHADE)
        game_reshade = reshade_dir / ("ReShade64.dll" if is_64bit else "ReShade32.dll")
        if not game_reshade.exists():
            warnings.append(
                f"ReShade {'x64' if is_64bit else 'x86'} runtime "
                f"({game_reshade.name}) is missing from components/reshade/. "
                "A 32-bit game cannot load a 64-bit ReShade and vice versa - the game would simply "
                "start with no overlay."
            )

        # dxgi.dll covers D3D11/D3D12; OpenGL needs opengl32.dll; Vulkan has no local-DLL
        # entry point at all and goes through the loader's layer mechanism instead. Under
        # DXVK the game presents through Vulkan just as an emulator on its Vulkan renderer
        # does, so it takes the same layer route - a dxgi.dll next to a DXVK'd D3D9 game
        # would be loaded by nothing.
        if uses_dxvk:
            reshade_dest = None
            needs_vulkan_layer = True
        elif emulator:
            reshade_dest = RESHADE_ATTACH_BY_API.get(emulator_api, "dxgi.dll")
        else:
            reshade_dest = "dxgi.dll"

        # The API says which name should work; the import table says which one will.
        reshade_dest, proxy_note = choose_reshade_proxy(
            reshade_dest, getattr(analysis, "imported_dlls", None)
        )
        if proxy_note:
            warnings.append(proxy_note)

        if reshade_dest is None:
            steps.append(
                "Register ReShade as a Vulkan layer for this executable (Vulkan has no local-DLL "
                "injection point - nothing is copied into the emulator folder)"
            )
        else:
            steps.append(
                f"Deploy {'64' if is_64bit else '32'}-bit ReShade (add-on support) as {reshade_dest}"
            )
            items.append(FilePlanItem(
                game_reshade if game_reshade.exists() else None,
                reshade_dest,
                f"ReShade {'x64' if is_64bit else 'x86'} add-on runtime",
            ))

        # ------------------------------------------------------------------
        # 3. DLSS5-Feeder add-on, matching the game's bitness
        # ------------------------------------------------------------------
        feeder_dir = ComponentManager.get_component_dir(COMP_FEEDER)
        addon_name = "dlss5-feed.addon64" if is_64bit else "dlss5-feed.addon32"
        addon_src = feeder_dir / addon_name
        if not addon_src.exists():
            warnings.append(
                f"'{addon_name}' is missing. This is the add-on that actually builds the DLSS "
                "contract - without it nothing appears in ReShade's Add-ons tab. Download the "
                "DLSS5-Feeder component."
            )
        steps.append(f"Deploy DLSS5-Feeder add-on ({addon_name})")
        items.append(FilePlanItem(
            addon_src if addon_src.exists() else None,
            os.path.join(ADDON_DEST, addon_name),
            "DLSS5-Feeder add-on (synthetic DLAA contract)",
        ))

        # ------------------------------------------------------------------
        # 4. Effects: DLSS5_Feed.fx, the motion-vector provider, and their includes
        # ------------------------------------------------------------------
        provider = mv_provider_for(merged_profile)
        steps.append(
            f"Deploy DLSS5_Feed.fx, {provider.name} and the ReShade includes into reshade-shaders/"
        )
        feed_fx = feeder_dir / "DLSS5_Feed.fx"
        if not feed_fx.exists():
            warnings.append("'DLSS5_Feed.fx' is missing - the effects list will have nothing to show.")
        items.append(FilePlanItem(
            feed_fx if feed_fx.exists() else None,
            os.path.join(SHADERS_DEST, "DLSS5_Feed.fx"),
            "DLSS5-Feeder effect",
        ))

        items.extend(cls._motion_vector_items(merged_profile, warnings))
        items.extend(cls._reshade_include_items(warnings))

        # ------------------------------------------------------------------
        # 5. The neural rendering stack - in process for x64, in host64\ for x86
        # ------------------------------------------------------------------
        renodx_src = USER_SUPPLIED_DIR / "renodx-dlss5.addon64"
        dlssnr_src = USER_SUPPLIED_DIR / "nvngx_dlssnr.dll"
        dlss_src = USER_SUPPLIED_DIR / "nvngx_dlss.dll"
        toolkit_src = USER_SUPPLIED_DIR / "alexs-toolkit.addon64"

        if not renodx_src.exists():
            warnings.append(
                "'renodx-dlss5.addon64' is missing. It is distributed through the RenoDX Discord "
                f"#DLSS5 channel and must be pinned to {RENODX_PINNED_VERSION} - newer builds "
                "conflict with DLSS5-Feeder."
            )
        if not dlssnr_src.exists():
            warnings.append("'nvngx_dlssnr.dll' is missing. Import it via the Components tab.")
        if not dlss_src.exists():
            warnings.append(
                "'nvngx_dlss.dll' is missing. DLSS5-Feeder wants a Super Resolution runtime next to "
                "the game (or in host64/); copy one from any DLSS game or DLSS Swapper."
            )

        # A 32-bit game hosts the neural add-on in host64\\, which is a folder this tool
        # creates and nobody else writes to - so there it is already the only add-on
        # ReShade can see, and only the in-process layout needs the private folder.
        addon_prefix = HOST64_DEST if uses_host64 else ADDON_DEST
        runtime_prefix = HOST64_DEST if uses_host64 else ""
        # Deep Fried Chicken replaces renodx-dlss5 rather than joining it: both intercept
        # the same NGX feature-1 entry points, and Chicken's own documentation for that
        # situation is "one or the other, never both". Alex's Toolkit goes with RenoDX, so
        # selecting Chicken drops the cascade too.
        wants_dfc = uses_dfc(merged_profile)
        if wants_dfc:
            cls._warn_about_same_device_d3d12(
                analysis, merged_profile, emulator, emulator_api,
                is_64bit and not uses_dgvoodoo and not uses_dxvk, warnings,
            )
            stack = cls._dfc_items(addon_prefix, warnings)
            steps.append("Deploy Deep Fried Chicken as the neural consumer (replaces RenoDX)")
        else:
            stack = [
                (renodx_src, os.path.join(addon_prefix, "renodx-dlss5.addon64"),
                 f"RenoDX DLSS 5 neural rendering add-on ({RENODX_PINNED_VERSION})"),
            ]
        stack.extend([
            (dlssnr_src, os.path.join(runtime_prefix, "nvngx_dlssnr.dll") if runtime_prefix
             else "nvngx_dlssnr.dll", "NVIDIA neural rendering runtime"),
            (dlss_src, os.path.join(runtime_prefix, "nvngx_dlss.dll") if runtime_prefix
             else "nvngx_dlss.dll", "DLSS Super Resolution runtime"),
        ])

        # Alex's Toolkit is optional, and it goes wherever the neural add-on goes - the
        # feeder looks for it next to itself and logs which cascade is running. Without it
        # DLSS 5 runs a single neural pass, which is the flatter of the two looks people
        # compare against each other.
        wants_cascade = bool(merged_profile.get("toolkit_enabled", True)) and (
            merged_profile.get("toolkit_two_pass", True) or merged_profile.get("toolkit_three_pass", False)
        )
        if toolkit_src.exists() and not wants_dfc:
            stack.append((
                toolkit_src, os.path.join(addon_prefix, "alexs-toolkit.addon64"),
                "Alex's Toolkit (multi-pass DLSS 5 cascade)",
            ))
        elif toolkit_src.exists() and wants_dfc:
            warnings.append(
                "Alex's Toolkit is installed here but is not being deployed: it cascades on "
                "top of RenoDX, and Deep Fried Chicken replaces RenoDX. Chicken runs its own "
                "multi-pass cascade instead, set with dfc_layers."
            )
            steps.append("Deploy Alex's Toolkit and its cascade settings (alexs-toolkit.cfg)")
        elif wants_cascade:
            warnings.append(
                "The profile asks for the multi-pass DLSS 5 cascade, but "
                "'alexs-toolkit.addon64' is not in components/user_supplied/. DLSS 5 will run a "
                "single neural pass - everything still works, it just looks flatter than the "
                "two- and three-pass setups. Import the add-on to enable it."
            )

        if uses_host64:
            steps.append(
                "Deploy the 64-bit helper (host64/dlss5-feed-host64.exe) - NGX is 64-bit only, so a "
                "32-bit game runs the DLSS work in a separate process"
            )
            host_exe = feeder_dir / "dlss5-feed-host64.exe"
            if not host_exe.exists():
                warnings.append(
                    "'dlss5-feed-host64.exe' is missing. Without the helper, a 32-bit game has no way "
                    "to reach the neural rendering runtime at all."
                )
            items.append(FilePlanItem(
                host_exe if host_exe.exists() else None,
                os.path.join(HOST64_DEST, "dlss5-feed-host64.exe"),
                "64-bit feeder host helper",
            ))

            host_reshade = reshade_dir / "ReShade64.dll"
            if not host_reshade.exists():
                warnings.append(
                    "ReShade x64 (ReShade64.dll) is missing, and host64/ needs it even though the game "
                    "is 32-bit - the helper process hosts the DLSS 5 add-on in its own ReShade."
                )
            items.append(FilePlanItem(
                host_reshade if host_reshade.exists() else None,
                os.path.join(HOST64_DEST, "dxgi.dll"),
                "ReShade x64 runtime for the helper process",
            ))
            steps.append(
                f"Populate {HOST64_DEST}\\ with ReShade x64, the neural add-on and the nvngx "
                "runtimes (the 32-bit add-on looks for it beside itself, not beside the .exe)"
            )
        else:
            steps.append(
                f"Deploy renodx-dlss5.addon64 into {ADDON_DEST}\\ and the nvngx runtimes "
                "next to the game"
            )

        items.extend(cls._mfg_unlock_items(
            analysis, merged_profile, addon_prefix, is_64bit, warnings, steps))

        for src, dest, desc in stack:
            # A source of None is not a mistake here. _dfc_items has already reported the
            # file it could not find and hands back None so the plan still carries the row,
            # and every other producer in this list hands back a Path. Calling .exists() on
            # the None turned "Chicken is not imported yet" into an AttributeError out of
            # build_plan - which is the first thing a clean checkout does when the profile
            # asks for Chicken, so it was every new install and no existing one.
            items.append(FilePlanItem(src if src and src.exists() else None, dest, desc))

        # A 32-bit build of the RenoDX add-on does not exist; if one is sitting in the
        # user_supplied folder it is a misnamed 64-bit file and would be loaded by nothing.
        stray_addon32 = USER_SUPPLIED_DIR / "renodx-dlss5.addon32"
        if stray_addon32.exists():
            warnings.append(
                "components/user_supplied/renodx-dlss5.addon32 is ignored: RenoDX DLSS 5 is 64-bit "
                "only. 32-bit games reach it through host64/, not through an .addon32."
            )

        if analysis.has_native_dlss or analysis.has_native_streamline:
            if is_64bit:
                warnings.append(
                    "This game already ships DLSS/Streamline. DLSS5-Feeder is for games with no DLSS "
                    "at all; where a game has its own, renodx-dlss5 can hook it directly and the "
                    "synthetic contract is not needed."
                )
            else:
                warnings.append(
                    "DLSS/Streamline files were found in the game folder, but this executable is "
                    "32-bit and those runtimes are x64 - the game process cannot load them. They are "
                    "inert here; the feeder's host64 helper is what actually reaches DLSS."
                )

        # A previous attempt often leaves 64-bit files in the root of a 32-bit game, where
        # they are inert - and their presence is what makes the Add-ons tab look broken.
        if uses_host64:
            stale = [
                f.name for f in analysis.game_dir.glob("*")
                if f.is_file() and f.name.lower() in (
                    "renodx-dlss5.addon64", "dlss5-feed.addon64", "nvngx_dlssnr.dll", "nvngx_dlss.dll",
                )
            ]
            if stale:
                warnings.append(
                    "These 64-bit files are sitting next to the 32-bit game executable and can never "
                    f"load there: {', '.join(sorted(stale))}. The install puts them in host64/ - delete "
                    "the copies in the game root once it is done."
                )

        # A RAGE game sizes its graphics presets from the video memory the device reports.
        # Behind dgVoodoo2 that is the wrapper's configured VRAM on a card the game has never
        # heard of, so it clamps everything to the lowest preset - commandline.txt is the
        # documented way to lift that.
        # The clamp fires whenever the engine dislikes the video memory the device reports,
        # which is any translation layer, not just dgVoodoo.
        rage_candidate = analysis.supports_rage_commandline and (uses_dgvoodoo or uses_dxvk)
        needs_rage_commandline = rage_candidate and bool(
            merged_profile.get("rage_write_commandline", False)
        )
        if needs_rage_commandline:
            steps.append("Generate commandline.txt with -norestrictions (RAGE settings clamp)")
        elif rage_candidate:
            warnings.append(
                "This engine sizes its graphics presets from the video memory the device "
                "reports, and behind a translation layer that number is not the card's - expect "
                "it to start on a low preset with the settings greyed out. A commandline.txt "
                "containing '-norestrictions' next to the .exe lifts that clamp; this profile has "
                "'rage_write_commandline' turned off, so it will not be written."
            )

        # The feeder's Vulkan transport imports D3D12 fences and textures into the host's
        # VkDevice, which needs four KHR external-interop extensions enabled at
        # vkCreateDevice. The add-on normally appends them itself from inside the process;
        # where its hook cannot see the call - Ryujinx creates its device through .NET
        # bindings - this launcher-scoped layer does the same job from outside. Staged as a
        # fallback rather than used by default: it is only needed when the log says so.
        if needs_vulkan_layer:
            # The fallback layer has to match the process it is loaded into: a 32-bit game
            # under DXVK needs layer-x86, not the x64 build every emulator uses.
            layer_name = "layer-x64" if is_64bit else "layer-x86"
            layer_dir = ComponentManager.get_component_dir(COMP_FEEDER) / layer_name
            if layer_dir.is_dir():
                steps.append(
                    "Stage the VK_LAYER_feed_vk fallback launcher (only needed if the log reports "
                    "missing Vulkan interop entry points)"
                )
                items.append(FilePlanItem(
                    layer_dir, VK_LAYER_DIR, "DLSS5-Feeder Vulkan interop layer (fallback launcher)"
                ))
            else:
                warnings.append(
                    f"components/feeder/{layer_name}/ is missing. It is the fallback for Vulkan "
                    "hosts whose vkCreateDevice the add-on cannot hook; re-download the "
                    "DLSS5-Feeder component to get it."
                )

        steps.append(
            "Generate ReShade.ini (with the [RenoDX.DLSS5] neural rendering settings) and "
            "DLSS5_Preset.ini with DLSS5_MV_PROVIDER=3 and the effect order"
        )
        if uses_host64:
            steps.append(
                "Generate host64/ReShade.ini - on a 32-bit game that is the ReShade the neural "
                "add-on reads its settings from"
            )

        return StrategyPlan(
            strategy_id=strategy_id,
            display_name=display_name,
            description=description,
            target_exe=analysis.exe_path,
            game_dir=analysis.game_dir,
            is_64bit=is_64bit,
            items=items,
            warnings=warnings,
            steps_summary=steps,
            uses_dgvoodoo=uses_dgvoodoo,
            uses_dxvk=uses_dxvk,
            uses_host64=uses_host64,
            needs_rage_commandline=needs_rage_commandline,
            emulator_api=emulator_api,
            needs_vulkan_layer=needs_vulkan_layer,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _first_existing(*candidates: Path) -> Optional[Path]:
        """Return the first path that exists, or None."""
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @classmethod
    def _dfc_items(cls, addon_prefix: str, warnings: List[str]):
        """The three files Deep Fried Chicken needs, beside the add-on like everything else.

        Chicken's own layout diagram puts these next to the game executable, because it
        assumes ReShade's default add-on path. This tool points ReShade at a folder of its
        own instead, so they go there - which is also where the nvngx runtimes already are,
        and Chicken wants a trusted nvngx_dlssnr.dll on its side of the transport.
        """
        dfc_dir = USER_SUPPLIED_DIR
        wanted = [
            ("deep-fried-chicken.addon64", "Deep Fried Chicken add-on"),
            ("deep-fried-chicken-nvngx.dll", "Deep Fried Chicken NGX interposer"),
        ]
        items = []
        for name, description in wanted:
            src = dfc_dir / name
            if not src.exists():
                warnings.append(
                    f"'{name}' is missing and the profile selects Deep Fried Chicken as the "
                    "neural consumer. Import the files from its release zip via the "
                    "Components tab, or set the neural consumer back to RenoDX."
                )
            items.append((src if src.exists() else None,
                          os.path.join(addon_prefix, name), description))
        return items

    @classmethod
    def _motion_vector_items(
        cls, profile: Dict[str, Any], warnings: List[str]
    ) -> List[FilePlanItem]:
        """Stage the shaders that estimate motion: LumeniteFX, plus vort if it is selected.

        LumeniteFX is staged either way. It is not only a motion vector provider - RTAO,
        LSAO, SSSR and TRAA come from the same pack and read `Kernel::tFlow`, so the
        Kernel effect still has to be installed and enabled when the *feed* is reading
        somebody else's vectors.
        """
        provider = mv_provider_for(profile)
        lum_dir = ComponentManager.get_component_dir(COMP_LUMENITE)
        shaders = lum_dir / "Shaders"
        textures = lum_dir / "Textures"
        items: List[FilePlanItem] = []

        kernel = shaders / "lumenite_Kernel.fx"
        if not kernel.exists():
            warnings.append(
                "LumeniteFX 'lumenite_Kernel.fx' is missing. It provides the default motion "
                "vectors (DLSS5_MV_PROVIDER=3) and the flow the RTAO, LSAO, SSSR and TRAA "
                "passes read; without it DLSS runs on zero motion vectors."
                if provider.id != MV_PROVIDER_VORT else
                "LumeniteFX 'lumenite_Kernel.fx' is missing. The feed is reading vort's "
                "vectors so DLSS itself is fine, but the RTAO, LSAO, SSSR and TRAA passes "
                "read Kernel's flow and will run on a texture of zeroes."
            )
        else:
            for fx in sorted(shaders.glob("lumenite_*.fx")):
                items.append(FilePlanItem(
                    fx, os.path.join(SHADERS_DEST, fx.name), f"LumeniteFX effect ({fx.name})"
                ))

        include_dir = shaders / "include"
        if include_dir.is_dir():
            items.append(FilePlanItem(
                include_dir,
                os.path.join(SHADERS_DEST, "include"),
                "LumeniteFX shader includes",
            ))
        elif kernel.exists():
            warnings.append("LumeniteFX Shaders/include/ is missing - lumenite_Kernel.fx will not compile.")

        noise = textures / "lumenite_bluenoise256.png"
        if noise.exists():
            items.append(FilePlanItem(
                noise, os.path.join(TEXTURES_DEST, noise.name), "LumeniteFX blue noise texture"
            ))
        elif kernel.exists():
            warnings.append("LumeniteFX texture 'lumenite_bluenoise256.png' is missing.")

        if provider.component == COMP_VORT:
            items.extend(cls._vort_items(warnings))

        # iMMERSE is staged when it provides the vectors *or* when any of its visible
        # effects is ticked. The two are separate choices: SMAA, Sharpen and Film Grain
        # care about neither provider, and MXAO only wants Launchpad enabled alongside it.
        ticked = {str(key).lower() for key in (profile.get("lumenite_effects") or [])}
        if provider.component == COMP_IMMERSE or ticked & set(IMMERSE_EFFECTS):
            items.extend(cls._immerse_items(profile, warnings))

        return items

    @classmethod
    def _immerse_items(
        cls, profile: Dict[str, Any], warnings: List[str]
    ) -> List[FilePlanItem]:
        """Stage the iMMERSE suite: all six effects, the headers, and every texture.

        Launchpad is Pascal Gilcher's optical flow, and it is the provider to reach for
        when the others come back empty: the feed's probe reading "0% non-zero" is the
        normal outcome on an emulator, where ReShade never finds a depth buffer worth
        having and a depth-assisted estimator has nothing to work from.

        This used to copy Launchpad and nothing else, on the reasoning that every .fx under
        the search path is a compile at startup whether or not it is enabled. That stopped
        being true when ReShade.ini started shipping SkipLoadingDisabledEffects=1, and the
        cost of the old rule was that choosing iMMERSE gave you one shader: MXAO, SOLARIS,
        SMAA, Sharpen and Film Grain were downloaded, then left behind, and every visible
        pass in the build came from LumeniteFX regardless.

        `MartysMods` is load-bearing as a folder name. The effects include their headers as
        ".\\MartysMods\\mmx_global.fxh", which resolves against the effect search path, so
        they have to sit under reshade-shaders/Shaders with exactly that name.

        Nothing here needs a companion effect for the flow request. Launchpad only computes
        optical flow when a consumer asked for it during the previous frame, and DLSS5_Feed.fx
        files that request itself through Launchpad's IPC buffer - which is why the generated
        preset puts Launchpad above the feed, matching its own "enable and move to the top".
        """
        immerse_dir = ComponentManager.get_component_dir(COMP_IMMERSE)
        shaders = immerse_dir / "Shaders"
        items: List[FilePlanItem] = []

        effects = sorted(shaders.glob("MartysMods_*.fx"))
        if not (shaders / "MartysMods_LAUNCHPAD.fx").exists():
            is_provider = mv_provider_for(profile).component == COMP_IMMERSE
            warnings.append(
                "iMMERSE 'MartysMods_LAUNCHPAD.fx' is missing, and the profile selects it as "
                "the motion vector provider (DLSS5_MV_PROVIDER=1). DLSS5_Feed.fx would sample "
                "Deferred::MotionVectorsTex with nothing writing it, which is the same as no "
                "motion vectors at all. Download the 'iMMERSE' component, or switch the "
                "provider back to LumeniteFX Kernel."
                if is_provider else
                "iMMERSE 'MartysMods_LAUNCHPAD.fx' is missing. MXAO reads the normals and "
                "motion Launchpad writes, so it would shade an empty G-buffer. Download the "
                "'iMMERSE' component."
            )
        if not effects:
            return items

        for fx in effects:
            items.append(FilePlanItem(
                fx,
                os.path.join(SHADERS_DEST, fx.name),
                f"iMMERSE effect ({fx.name})",
            ))

        headers = shaders / "MartysMods"
        if headers.is_dir():
            items.append(FilePlanItem(
                headers,
                os.path.join(SHADERS_DEST, "MartysMods"),
                "iMMERSE shader includes (MartysMods/)",
            ))
        else:
            warnings.append(
                "iMMERSE Shaders/MartysMods/ is missing - Launchpad includes eight headers "
                "from that folder and will not compile without it."
            )

        # Three textures, one per effect that samples one, and a missing one is not a
        # compile error that names a header - ReShade logs "Source 'iMMERSE_bluenoise_opt.png'
        # for texture 'V__BlueNoiseJitterTex' was not found in any of the texture search
        # paths" and the effect simply does not run, which reads as the effect being
        # installed and doing nothing. Launchpad wants iMMERSE_bluenoise_opt.png, MXAO wants
        # iMMERSE_bluenoise_temporal.png, and SMAA wants AreaLUT.png; the last two were never
        # staged at all while only Launchpad was being copied.
        textures = immerse_dir / "Textures"
        for png in sorted(textures.glob("*.png")):
            items.append(FilePlanItem(
                png,
                os.path.join(TEXTURES_DEST, png.name),
                f"iMMERSE texture ({png.name})",
            ))
        for name in ("iMMERSE_bluenoise_opt.png", "iMMERSE_bluenoise_temporal.png",
                     "AreaLUT.png"):
            if not (textures / name).exists():
                warnings.append(
                    f"iMMERSE texture '{name}' is missing, so the effect that samples it will "
                    "not run. Re-download the iMMERSE component."
                )
        return items

    @classmethod
    def _vort_items(cls, warnings: List[str]) -> List[FilePlanItem]:
        """Stage vort_Motion.fx, its Includes folder and its blue noise texture.

        Only vort_Motion.fx is copied, not the whole pack. ReShade.ini is written with
        SkipLoadingDisabledEffects=0, so every .fx under the search path is compiled at
        startup whether or not it is enabled - staging vort_Static.fx as well would buy
        a longer load and a second set of compile errors for an effect nobody asked for.

        The folder name `Includes` is load-bearing: vort's headers include each other as
        "Includes/vort_Defs.fxh", which resolves against the effect search path, so it has
        to sit directly under reshade-shaders/Shaders with that exact name.
        """
        vort_dir = ComponentManager.get_component_dir(COMP_VORT)
        shaders = vort_dir / "Shaders"
        items: List[FilePlanItem] = []

        motion_fx = shaders / "vort_Motion.fx"
        if not motion_fx.exists():
            warnings.append(
                "vort 'vort_Motion.fx' is missing, and the profile selects it as the motion "
                "vector provider (DLSS5_MV_PROVIDER=2). DLSS5_Feed.fx will sample a texture "
                "nothing writes, which is the same as no motion vectors at all. Download the "
                "'vort_Shaders (Motion Effects)' component, or switch the provider back to "
                "LumeniteFX Kernel."
            )
            return items

        items.append(FilePlanItem(
            motion_fx,
            os.path.join(SHADERS_DEST, motion_fx.name),
            "vort motion vector effect (vort_Motion.fx)",
        ))

        includes = shaders / "Includes"
        if includes.is_dir():
            items.append(FilePlanItem(
                includes, os.path.join(SHADERS_DEST, "Includes"), "vort shader includes"
            ))
        else:
            warnings.append("vort Shaders/Includes/ is missing - vort_Motion.fx will not compile.")

        noise = vort_dir / "Textures" / "vort_BlueNoise.png"
        if noise.exists():
            items.append(FilePlanItem(
                noise, os.path.join(TEXTURES_DEST, noise.name), "vort blue noise texture"
            ))
        else:
            warnings.append(
                "vort texture 'vort_BlueNoise.png' is missing - vort_MotionVectors.fxh reads it."
            )

        return items

    @classmethod
    def _reshade_include_items(cls, warnings: List[str]) -> List[FilePlanItem]:
        """Stage ReShade.fxh / DrawText.fxh, which both effects #include."""
        inc_dir = ComponentManager.get_component_dir(COMP_RESHADE_SHADERS)
        items: List[FilePlanItem] = []
        found = sorted(inc_dir.glob("*.fxh"))

        if not any(f.name == "ReShade.fxh" for f in found):
            warnings.append(
                "'ReShade.fxh' is missing. DLSS5_Feed.fx and lumenite_Kernel.fx both #include it, so "
                "neither compiles without it and the effects list stays empty. Download the "
                "'ReShade Base Shader Includes' component, or install the standard shaders through "
                "the ReShade setup."
            )
        if not any(f.name == "DrawText.fxh" for f in found):
            warnings.append("'DrawText.fxh' is missing - lumenite_Kernel.fx #includes it.")

        for fxh in found:
            items.append(FilePlanItem(
                fxh, os.path.join(SHADERS_DEST, fxh.name), f"ReShade include ({fxh.name})"
            ))
        return items

    @classmethod
    def _build_remix_plan(
        cls,
        analysis: GameAnalysis,
        strategy_id: str,
        display_name: str,
        description: str,
        warnings: List[str],
    ) -> StrategyPlan:
        """RTX Remix bridge: the game keeps a 32-bit client, rendering moves to a 64-bit process.

        Remix replaces the D3D9 runtime with a bridge - `d3d9.dll` in the game folder is a thin
        x86 client, and `.trex\\NvRemixBridge.exe` is an x64 process running dxvk-remix on Vulkan.

        That inverts the usual 32-bit problem. The neural rendering add-on is x64 and needs a
        64-bit host, and here one exists: the bridge renderer. Remix also issues real DLSS calls
        of its own (it ships nvngx_dlss and nvngx_dlssd), which is precisely the condition where
        the add-on has something to hook - so DLSS5-Feeder, its .addon32 and its host64 helper
        are not part of this layout at all.
        """
        items: List[FilePlanItem] = []
        steps = [
            "Deploy the RTX Remix bridge: d3d9.dll (x86 client) + .trex/ (x64 Vulkan renderer)",
            "Place renodx-dlss5.addon64 and nvngx_dlssnr.dll beside NvRemixBridge.exe in .trex/",
            "Register ReShade as a Vulkan layer against .trex/NvRemixBridge.exe",
        ]

        remix_root = ComponentManager.get_component_dir(COMP_REMIX) / "runtime"
        client = remix_root / "d3d9.dll"
        renderer = remix_root / REMIX_RENDERER_DIR

        if not client.exists() or not renderer.is_dir():
            warnings.append(
                "The RTX Remix runtime is missing. Download the RTX Remix component - without "
                "d3d9.dll and .trex/ there is no bridge and the game keeps running on plain D3D9."
            )

        items.append(FilePlanItem(
            client if client.exists() else None, "d3d9.dll", "RTX Remix bridge client (x86)"
        ))
        for extra in ("d3d8to9.dll", "NvRemixLauncher32.exe"):
            src = remix_root / extra
            if src.exists():
                items.append(FilePlanItem(src, extra, f"RTX Remix helper ({extra})"))
        if renderer.is_dir():
            items.append(FilePlanItem(
                renderer, REMIX_RENDERER_DIR, "RTX Remix Vulkan renderer + its DLSS runtimes"
            ))

        # The neural rendering stack goes where the *renderer* runs, not where the game runs.
        for name, desc in (
            ("renodx-dlss5.addon64", f"RenoDX DLSS 5 add-on ({RENODX_PINNED_VERSION})"),
            ("nvngx_dlssnr.dll", "NVIDIA neural rendering runtime"),
        ):
            src = USER_SUPPLIED_DIR / name
            if not src.exists():
                warnings.append(f"'{name}' is missing. Import it via the Components tab.")
            items.append(FilePlanItem(
                src if src.exists() else None,
                os.path.join(REMIX_RENDERER_DIR, name),
                f"{desc} - loaded by the bridge renderer",
            ))

        if not analysis.is_64bit:
            warnings.append(
                "ReShade cannot be dropped into this game folder for this layout: the game process "
                "no longer renders, and the bridge renderer uses Vulkan. Register ReShade as a "
                "Vulkan layer against .trex/NvRemixBridge.exe instead - the installer's "
                "'reshade-vulkan' step does this, or run the ReShade setup with --api vulkan."
            )

        warnings.append(
            "RTX Remix targets fixed-function DirectX 8/9. This engine uses programmable shaders, "
            "so Remix's own path tracing may not engage even when the bridge renders correctly - "
            "the bridge is being used here for its 64-bit Vulkan renderer and its DLSS calls."
        )

        return StrategyPlan(
            strategy_id=strategy_id,
            display_name=display_name,
            description=description,
            target_exe=analysis.exe_path,
            game_dir=analysis.game_dir,
            is_64bit=analysis.is_64bit,
            items=items,
            warnings=warnings,
            steps_summary=steps,
            uses_remix=True,
        )

    @classmethod
    def _build_optiscaler_plan(
        cls,
        analysis: GameAnalysis,
        strategy_id: str,
        display_name: str,
        description: str,
        warnings: List[str],
    ) -> StrategyPlan:
        """OptiScaler bridge for games that already expose FSR2/3 or XeSS."""
        items: List[FilePlanItem] = []
        steps = [
            "Deploy OptiScaler bridge DLLs",
            "Generate OptiScaler.ini mapped to the nvngx runtimes",
            f"Deploy renodx-dlss5.addon64 into {ADDON_DEST}\\ and the nvngx runtimes",
        ]

        if not analysis.is_64bit:
            warnings.append(
                "OptiScaler is 64-bit only; this executable is 32-bit. Use the 32-bit feeder path instead."
            )

        opti_dir = ComponentManager.get_component_dir(COMP_OPTISCALER)
        reshade_dir = ComponentManager.get_component_dir(COMP_RESHADE)

        opti_dxgi = cls._first_existing(opti_dir / "dxgi.dll", reshade_dir / "ReShade64.dll")
        items.append(FilePlanItem(opti_dxgi, "dxgi.dll", "OptiScaler / ReShade DXGI hook"))

        opti_nvngx = opti_dir / "nvngx.dll"
        if opti_nvngx.exists():
            items.append(FilePlanItem(opti_nvngx, "nvngx.dll", "OptiScaler NVNGX interposer"))

        # The add-on goes in the private folder the generated ReShade.ini searches; the
        # runtimes stay beside the .exe where NGX and OptiScaler both look for them, and
        # the installer hard-links them in next to the add-on as well.
        for name, dest, desc in (
            ("renodx-dlss5.addon64", os.path.join(ADDON_DEST, "renodx-dlss5.addon64"),
             f"RenoDX DLSS 5 add-on ({RENODX_PINNED_VERSION})"),
            ("nvngx_dlssnr.dll", "nvngx_dlssnr.dll", "NVIDIA neural rendering runtime"),
            ("nvngx_dlss.dll", "nvngx_dlss.dll", "DLSS Super Resolution runtime"),
        ):
            src = USER_SUPPLIED_DIR / name
            if not src.exists():
                warnings.append(f"'{name}' is missing. Import it via the Components tab.")
            items.append(FilePlanItem(src if src.exists() else None, dest, desc))

        return StrategyPlan(
            strategy_id=strategy_id,
            display_name=display_name,
            description=description,
            target_exe=analysis.exe_path,
            game_dir=analysis.game_dir,
            is_64bit=analysis.is_64bit,
            items=items,
            warnings=warnings,
            steps_summary=steps,
        )
