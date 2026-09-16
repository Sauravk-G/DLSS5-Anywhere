"""
Configuration Generator for DLSS5-Anywhere.
Generates ReShade.ini, the DLSS 5 preset, dgVoodoo.conf, OptiScaler.ini, and batch scripts.

Key names and effect names here match what ReShade 6 and DLSS5-Feeder actually read:
technique selection lives in the *preset*, not in ReShade.ini, and the effect names are
`Lumenite_Kernel` (in lumenite_Kernel.fx) and `DLSS5_Feed` (in DLSS5_Feed.fx).
"""

from pathlib import Path
import re
from typing import Any, Dict, Optional, Tuple

from ..config import (
    ADDON_DIR_NAME,
    uses_dfc,
    wants_mfg_unlock,
    mfg_settings,
    MFG_SECTION,
    API_DISPLAY_NAMES,
    COMP_DGVOODOO,
    COMP_VORT,
    DEFAULT_PROFILE,
    RENODX_PINNED_VERSION,
    mv_provider_for,
)
from .detector import GameAnalysis

# Effect order: the motion vector provider must run before the feed reads it, and the
# add-on runs DLSS + neural rendering right after DLSS5_Feed - so anything listed after
# the feed is applied on top of the neural output. Which effect provides the vectors is
# the profile's choice; see MV_PROVIDERS in config.py.
FEED_TECHNIQUE = "DLSS5_Feed@DLSS5_Feed.fx"

# LumeniteFX is a whole effect suite, not just the motion vector kernel, and the installer
# already stages all of it. Only Kernel and DLSS5_Feed were ever enabled, and neither draws
# anything the player can see - Kernel writes optical flow into a texture, DLSS5_Feed writes
# the DLSS guide textures - so a build that worked perfectly rendered no new pixels of its
# own and looked like a failed install. These are the technique names as declared inside
# each .fx file; keys are what a profile's "lumenite_effects" list refers to.
LUMENITE_EFFECTS = {
    "rtao": "Lumenite_RTAO@lumenite_RTAO.fx",
    "quantao": "Lumenite_QuantAO@lumenite_QuantAO.fx",
    "lsao": "Lumenite_LSAO@lumenite_LSAO.fx",
    # SSSR is staged with the rest of the suite but is off in the shipped defaults - its
    # reflections break up badly on a depth buffer ReShade has not found cleanly, which is
    # most wrapper paths. Opt in per profile.
    "sssr": "LUMENITE_SSSR@lumenite_SSSR.fx",
    "bloom": "Lumenite_AnamorphicBloom@lumenite_AnamorphicBloom.fx",
    # TRAA is temporal anti-aliasing on top of DLSS's own, so two history buffers are
    # resolving the same frame. It is on in the defaults anyway: in practice it settles the
    # edges the AO passes leave, and it is a tick-box away if a game smears under it.
    "traa": "Lumenite_TRAA@lumenite_TRAA.fx",
}

# iMMERSE is a suite too, and for a long time only Launchpad was ever installed from it:
# choosing iMMERSE as the motion vector provider still left every visible pass coming from
# LumeniteFX, because this catalogue had no iMMERSE entries and the installer copied one
# .fx. The other five shipped in the component folder and never reached a game.
#
# Technique names are as declared inside each .fx, and two of them are missing an "s" in
# "Martys" upstream - `MartyMods_Sharpen` and `MartyMods_FilmGrain` really are spelled that
# way. SMAA declares two techniques and the prepass has to run before the resolve, so the
# values here are tuples rather than single names.
IMMERSE_EFFECTS: Dict[str, Tuple[str, ...]] = {
    "mxao": ("MartysMods_MXAO@MartysMods_MXAO.fx",),
    "solaris": ("MartysMods_SOLARIS@MartysMods_SOLARIS.fx",),
    "smaa": (
        "MartysMods_AntiAliasing_Prepass@MartysMods_SMAA.fx",
        "MartysMods_AntiAliasing@MartysMods_SMAA.fx",
    ),
    "sharpen": ("MartyMods_Sharpen@MartysMods_SHARPEN.fx",),
    "filmgrain": ("MartyMods_FilmGrain@MartysMods_FILMGRAIN.fx",),
}

# The one catalogue _technique_list reads. A profile's "lumenite_effects" list names keys
# from either suite; the name of that profile key is kept as it is so profiles saved by
# older builds still load.
VISIBLE_EFFECTS: Dict[str, Tuple[str, ...]] = {
    **{key: (technique,) for key, technique in LUMENITE_EFFECTS.items()},
    **IMMERSE_EFFECTS,
}

# Which technique writes the flow each LumeniteFX effect reads.
#
# RTAO, LSAO, SSSR and TRAA all re-declare `Kernel::tFlow`; QuantAO re-declares
# `QuantMotion::tFlow`. Re-declaring is not an error in ReShade - both declarations name
# the same resource - so an effect whose writer is not enabled compiles and runs happily
# on a texture of zeroes, and the only symptom is an AO or AA pass that never settles.
#
# This is a separate question from which provider feeds DLSS. Choosing vort_MotionEffects
# for the feed does not give these effects their flow, so whichever writer they need is
# enabled alongside it.
LUMENITE_FLOW_SOURCE = {
    "rtao": "Lumenite_Kernel@lumenite_Kernel.fx",
    "lsao": "Lumenite_Kernel@lumenite_Kernel.fx",
    "sssr": "Lumenite_Kernel@lumenite_Kernel.fx",
    "traa": "Lumenite_Kernel@lumenite_Kernel.fx",
    "quantao": "Lumenite_QuantMotion@lumenite_QuantMotion.fx",
    # Not flow this time but the same shape of dependency: MXAO calls Deferred::get_normals
    # and Deferred::get_motion, and Launchpad is what writes both. Ticked on its own it
    # would shade an empty G-buffer.
    "mxao": "MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx",
}

# renodx-dlss5.addon64 stores its settings through ReShade's own config API
# (ReShadeGetConfigValue / ReShadeSetConfigValue), so they live in ReShade.ini under this
# section rather than in a file of its own.
RENODX_SECTION = "RenoDX.DLSS5"


def _technique_list(profile: Optional[Dict[str, Any]] = None) -> str:
    """Build the ordered technique list: flow writers, the feed, then the visible effects.

    Order is the whole point of this list. ReShade runs techniques top to bottom, so
    anything that writes a texture has to sit above everything that reads it, and
    everything below DLSS5_Feed is drawn on top of the neural output instead of into the
    frame that fed it.
    """
    p = {**DEFAULT_PROFILE, **(profile or {})}
    effects = [str(key).lower() for key in (p.get("lumenite_effects") or [])]

    # The feed's provider first, then any other flow writer the enabled effects need -
    # which is nothing extra when they share the feed's provider, the usual case.
    order = [mv_provider_for(p).technique]
    for key in effects:
        source = LUMENITE_FLOW_SOURCE.get(key)
        if source and source not in order:
            order.append(source)

    order.append(FEED_TECHNIQUE)
    for key in effects:
        for technique in VISIBLE_EFFECTS.get(key, ()):
            if technique not in order:
                order.append(technique)
    return ",".join(order)


def _renodx_section(profile: Optional[Dict[str, Any]] = None) -> str:
    """Render the [RenoDX.DLSS5] block that switches neural rendering on and shapes it.

    Written only into the ReShade.ini that sits beside renodx-dlss5.addon64 - the game
    folder for a 64-bit game, host64/ for a 32-bit one. The add-on rewrites these values
    itself when the user moves a slider in the overlay, so this only decides what the
    first launch looks like.
    """
    p = {**DEFAULT_PROFILE, **(profile or {})}

    def flag(key: str, default: bool) -> str:
        return "1" if p.get(key, default) else "0"

    def scalar(key: str, default: float) -> str:
        return f"{float(p.get(key, default)):.3f}"

    return f"""
[{RENODX_SECTION}]
NeuralUplift={flag("nr_enabled", True)}
NREnableUpscaling={flag("nr_upscaling", False)}
NRPreset={int(p.get("nr_preset", 0))}
NRStyle={int(p.get("nr_style", 0))}
NRIntensity={scalar("nr_intensity", 1.0)}
NRLocalTone={scalar("nr_local_tone", 1.0)}
NRLocalStructure={scalar("nr_local_structure", 1.0)}
NRSkinStructure={scalar("nr_skin_structure", 1.0)}
NRColorStrength={scalar("nr_color_strength", 1.0)}
NRAutoMask={flag("nr_auto_mask", True)}
NRUICorrection={flag("nr_ui_correction", True)}
"""


def _mfg_section(profile: Optional[Dict[str, Any]] = None) -> str:
    """Render the [RenoDX.MFGUnlock] block that drives the RTX 40 frame-generation unlock.

    Read by renodx-mfgunlock.addon64 out of the game folder's ReShade.ini, the same file
    and the same mechanism as the neural add-on's own section, and rewritten by the add-on
    when a control is moved in the overlay's Add-ons tab. So this decides the first launch
    and nothing after it.

    MaxCount is the ceiling the add-on offers, not what the game runs at: ForceMultiplier
    stays 0 by default, which leaves the multiplier to the game's own frame generation
    setting. Forcing a number the game's UI does not know about is the configuration most
    likely to look broken from inside the game.
    """
    p = {**DEFAULT_PROFILE, **(profile or {})}
    force, count = mfg_settings(profile)

    return f"""
[{MFG_SECTION}]
Enabled=1
MaxCount={count}
ForceMultiplier={force}
DynamicMFG={1 if p.get("mfg_dynamic", False) else 0}
HDRCompatibilityMode={int(p.get("mfg_hdr_compat", 2))}
"""


class ConfigGenerator:
    """Generates structured configuration files for game injection or standalone builds."""

    @classmethod
    def generate_reshade_ini(
        cls,
        analysis: GameAnalysis,
        profile: Optional[Dict[str, Any]] = None,
        neural_in_process: Optional[bool] = None,
    ) -> str:
        """Generate a ReShade.ini matching the game and the selected profile.

        `neural_in_process` says whether renodx-dlss5.addon64 loads into *this* process.
        It does for a 64-bit game, and it does not for a 32-bit one - there the add-on
        lives in host64/ and reads host64/ReShade.ini, so writing its settings here would
        put them in a file it never opens. Defaults to the game's own bitness.
        """
        p = {**DEFAULT_PROFILE, **(profile or {})}
        if neural_in_process is None:
            neural_in_process = analysis.is_64bit

        mv_provider = mv_provider_for(p).id
        depth_reversed = 1 if p.get("depth_reversed", False) else 0
        depth_upsidedown = 1 if p.get("depth_upsidedown", False) else 0

        # Global definitions apply to every effect, which is how DLSS5_Feed.fx picks up
        # its provider. RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN is spelled with the underscore
        # ReShade 6 uses; the other spelling is silently ignored.
        preprocessor_defs = [
            f"DLSS5_MV_PROVIDER={mv_provider}",
            f"RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN={depth_upsidedown}",
            f"RESHADE_DEPTH_INPUT_IS_REVERSED={depth_reversed}",
            "RESHADE_DEPTH_INPUT_IS_LOGARITHMIC=0",
            "RESHADE_DEPTH_LINEARIZATION_FAR_PLANE=1000.0",
        ]
        preprocessor_str = ",".join(preprocessor_defs)

        overlay_key = p.get("reshade_overlay_key", "36")
        effects_key = p.get("reshade_effect_toggle_key", "113")

        # ReShade picks the depth buffer with two heuristics that assume a game rendering
        # at the size of its own window, and an emulator is not that.
        #
        # UseAspectRatioHeuristics rejects buffers whose shape does not match the back
        # buffer. An emulator renders at an emulated internal resolution - PCSX2 at 4x a
        # PS2 frame, PPSSPP at 10x a PSP one - so the buffer that matters is exactly the
        # one this throws away.
        #
        # DepthCopyBeforeClears takes a copy before the depth target is cleared. Plenty of
        # renderers clear depth before ReShade sees the end of the frame, and an emulator
        # driving an emulated GPU's clear pattern does it more often than most.
        #
        # Measured on a PCSX2 install with neither set: "Depth probe: min 0, max 0,
        # variance 0" on every sample, for the whole session, while the motion vectors
        # underneath were fine. Both are live in ReShade's Depth tab, so this is a starting
        # point rather than a decision - but starting at the values that cannot work is
        # what made an install look broken.
        # Deliberately always empty, which is not where this started.
        #
        # Deep Fried Chicken writes [ADDON] LoadFromDllMain=deep-fried-chicken.addon64
        # into a ReShade.ini beside itself, in the add-on folder, and ReShade reads the one
        # next to the executable - so it looked like a request landing in a file nothing
        # opens. Writing it into the real ini made things worse, immediately:
        #
        #   without it   Chicken initialised and armed        (two emulators, three runs)
        #   with it      "No add-on was registered by deep-fried-chicken.addon64.
        #                 Unloading again", and its own log not written at all
        #
        # LoadFromDllMain loads an add-on from DllMain, which is to say inside the loader
        # lock, and Chicken's first log line is "initialized outside loader lock" - it
        # cares where it starts. Its copy of that key is presumably for something other
        # than being copied into ReShade's config, and guessing otherwise cost a working
        # install. The key is still written, empty, so a value left by an older install
        # does not survive into a new one.
        load_from_dll_main = ""

        emulated = getattr(analysis, "emulator", None) is not None
        depth_before_clears = 1 if emulated else 0
        aspect_heuristics = 0 if emulated else 1

        return f"""[GENERAL]
EffectSearchPaths=.\\reshade-shaders\\Shaders,.\\reshade-shaders\\Shaders\\**
TextureSearchPaths=.\\reshade-shaders\\Textures,.\\reshade-shaders\\Textures\\**
PreprocessorDefinitions={preprocessor_str}
PresetPath=.\\DLSS5_Preset.ini
PerformanceMode=0
NoReloadOnInit=0
SkipLoadingDisabledEffects=1

[ADDON]
AddonPath=.\\{ADDON_DIR_NAME}\\
DisabledAddons=
LoadFromDllMain={load_from_dll_main}

[INPUT]
KeyOverlay={overlay_key},0,0,0
KeyEffects={effects_key},0,0,0
InputProcessing=2

[DEPTH]
DepthCopyBeforeClears={depth_before_clears}
DepthCopyAtClearIndex=0
UseAspectRatioHeuristics={aspect_heuristics}

[OVERLAY]
TutorialProgress=4
""" + (_renodx_section(p) if neural_in_process else "") \
    + (_mfg_section(p) if wants_mfg_unlock(profile) else "")

    @classmethod
    def generate_host64_reshade_ini(
        cls,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """ReShade.ini for host64/, the helper process" a 32-bit game feeds.

        The helper renders no game frames and runs no effects, so this carries no effect
        search paths - only the add-on path and the neural rendering settings, because
        host64/ is where renodx-dlss5.addon64 actually lives on a 32-bit install. Without
        this file the add-on there starts at its own defaults every time and a 32-bit game
        can never be configured from the tool at all.
        """
        return """[ADDON]
AddonPath=.\\
DisabledAddons=

[GENERAL]
NoReloadOnInit=0
PerformanceMode=0

[INPUT]
KeyOverlay=36,0,0,0
InputProcessing=2

[OVERLAY]
TutorialProgress=4
""" + _renodx_section(profile)

    @classmethod
    def generate_toolkit_cfg(
        cls,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate alexs-toolkit.cfg, the multi-pass cascade's settings.

        DLSS5-Feeder probes for alexs-toolkit.addon64 next to itself and reports the
        cascade in dlss5-feed.log; with the toolkit absent or its cascade off, DLSS 5 runs
        a single neural pass. Each extra pass roughly multiplies the temporal history, so
        two-pass is the safe setting and three-pass trades smearing behind fast motion and
        a slow settle after a camera cut for a heavier look. The add-on re-reads this file
        while the game runs.
        """
        p = {**DEFAULT_PROFILE, **(profile or {})}
        three_pass = bool(p.get("toolkit_three_pass", False))
        # The cascades are B->A and B->C->A: the three-pass chain is the two-pass chain
        # with a stage inserted, so DEPLOY-DEV.md gives three_pass as requiring
        # two_pass=1. A profile asking for three passes without two is asking for a
        # cascade that does not exist, and writing it out would leave the add-on to
        # resolve a contradiction this tool can settle before the file is saved.
        two_pass = bool(p.get("toolkit_two_pass", True)) or three_pass
        def flag(key: str, default: bool) -> int:
            return 1 if p.get(key, default) else 0

        def scalar(key: str, default: float) -> str:
            try:
                return f"{float(p.get(key, default)):.2f}"
            except (TypeError, ValueError):
                return f"{default:.2f}"

        # All eight keys the add-on ships, in its own order. The five below the cascade are
        # undocumented and are written at the add-on's own defaults unless a profile says
        # otherwise - the alternative is deleting them from a file this tool does not own.
        return (
            f"enabled={flag('toolkit_enabled', True)}\n"
            f"two_pass={1 if two_pass else 0}\n"
            f"three_pass={1 if three_pass else 0}\n"
            f"adaptive_extreme={flag('toolkit_adaptive_extreme', False)}\n"
            f"stage_b_blend={scalar('toolkit_stage_b_blend', 1.00)}\n"
            f"stage_c_blend={scalar('toolkit_stage_c_blend', 1.00)}\n"
            f"texture_boost={flag('toolkit_texture_boost', False)}\n"
            f"texture_boost_strength={scalar('toolkit_texture_boost_strength', 1.00)}\n"
        )

    @staticmethod
    def patch_dfc_cfg(vendor_cfg: str, profile: Optional[Dict[str, Any]] = None) -> str:
        """Return Chicken's own config with the few keys this tool owns set.

        Its config is 346 lines: sixteen globals and thirty layer blocks of eleven keys
        each. Writing that from scratch would mean inventing values for settings nobody
        here understands, and the last time this tool generated a third-party add-on's
        config from a partial idea of its schema it silently deleted five keys from
        alexs-toolkit.cfg. So the vendor file is carried through verbatim and three keys
        are patched:

          layers   how many neural passes to run, from the profile
          arm      1, because arm=0 is a restart-only hard disarm under which Chicken
                   claims nothing and installs no interception at all - the feeder would
                   find it present and idle
          enabled  1, the live switch that lets the neural work actually run

        Anything the file already carries and this tool has no opinion on is left alone.
        """
        p = {**DEFAULT_PROFILE, **(profile or {})}
        try:
            layers = max(1, min(30, int(p.get("dfc_layers", 1))))
        except (TypeError, ValueError):
            layers = 1

        wanted = {"layers": str(layers), "arm": "1", "enabled": "1"}
        seen = set()
        out = []
        for line in vendor_cfg.splitlines(True):
            key, sep, _ = line.partition("=")
            name = key.strip()
            if sep and name in wanted and name not in seen:
                ending = line[len(line.rstrip("\r\n")):]
                out.append(f"{name}={wanted[name]}{ending}")
                seen.add(name)
            else:
                out.append(line)

        # A config that never mentioned them is not one this tool understands, but a
        # missing key is better appended than left to a default nobody checked.
        # Appended in the file's own line ending, so a CRLF config is not left mixed.
        ending = "\r\n" if "\r\n" in vendor_cfg else "\n"
        for name in ("layers", "arm", "enabled"):
            if name not in seen:
                out.append(f"{name}={wanted[name]}{ending}")
        return "".join(out)

    @classmethod
    def generate_preset_ini(
        cls,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate DLSS5_Preset.ini: which techniques run, in which order.

        ReShade takes the enabled technique list from the preset, not from ReShade.ini.
        The motion vector provider must sit above `DLSS5_Feed` or the feed reads vectors
        that have not been written yet, and the profile's visible effects go below it so
        they land on the neural output rather than on the frame that fed it.

        The per-effect sections underneath matter as much as the order. They are written
        for every build, including with empty values, because ReShade keeps whatever a
        preset does not mention: a definition left over from a previous install of a
        different provider would otherwise survive into this one.
        """
        p = {**DEFAULT_PROFILE, **(profile or {})}
        provider = mv_provider_for(p)
        techniques = _technique_list(p)

        # vort_Motion.fx is a suite - vectors, motion blur, TAA - so it is pinned to
        # vectors only. lumenite_Kernel.fx needs no definitions but is always cleared.
        provider_section = ""
        if provider.effect_file != "lumenite_Kernel.fx":
            provider_section = f"""
[{provider.effect_file}]
PreprocessorDefinitions={provider.definitions}
"""

        return f"""Techniques={techniques}
TechniqueSorting={techniques}

[DLSS5_Feed.fx]
PreprocessorDefinitions=DLSS5_MV_PROVIDER={provider.id}

[lumenite_Kernel.fx]
PreprocessorDefinitions=
{provider_section}"""

    @classmethod
    def generate_dgvoodoo_conf(
        cls,
        profile: Optional[Dict[str, Any]] = None,
        template_path: Optional[Path] = None,
    ) -> str:
        """Generate dgVoodoo.conf for D3D9 translation.

        dgVoodoo ships `DisableAndPassThru = true`, which turns the wrapper off entirely -
        the single most common reason a D3D9 install appears to do nothing. The stock
        VRAM of 256 MB also causes 'ran out of video memory' crashes, and 2 GB breaks some
        old engines, so 1 GB is the value the DLSS5-Feeder guide settles on.
        """
        p = {**DEFAULT_PROFILE, **(profile or {})}
        overrides_directx = {
            "DisableAndPassThru": "false",
            "VRAM": str(p.get("dgvoodoo_vram_mb", 1024)),
            "VideoCard": str(p.get("dgvoodoo_video_card", "internal3D")),
            "Filtering": "max",
            "Antialiasing": str(p.get("dgvoodoo_antialiasing", "appdriven")),
            "Resolution": str(p.get("dgvoodoo_resolution_scaling", "unforced")),
            "dgVoodooWatermark": "true" if p.get("dgvoodoo_watermark", True) else "false",
            "FastVideoMemoryAccess": "true",
        }
        overrides_general = {"OutputAPI": "d3d11_fl11_0"}
        # WatermarkDisplayDuration lives in [GeneralExt] and ships as 0, which means "for
        # ever". That is why the watermark is still there hours later: nothing was ever
        # written to stop it. A few seconds still proves dgVoodoo loaded - which is the
        # only reason to have it on - and then it goes away on its own.
        watermark_seconds = int(p.get("dgvoodoo_watermark_seconds", 15))
        overrides_general_ext = {"WatermarkDisplayDuration": str(max(0, watermark_seconds))}

        if template_path and Path(template_path).exists():
            return cls._patch_ini_sections(
                Path(template_path).read_text(encoding="utf-8", errors="replace"),
                {
                    "General": overrides_general,
                    "GeneralExt": overrides_general_ext,
                    "DirectX": overrides_directx,
                },
            )

        lines = ["[General]"]
        lines += [f"{k} = {v}" for k, v in overrides_general.items()]
        lines += ["Adapter = all", "", "[GeneralExt]"]
        lines += [f"{k} = {v}" for k, v in overrides_general_ext.items()]
        lines += ["", "[DirectX]"]
        lines += [f"{k} = {v}" for k, v in overrides_directx.items()]
        lines += ["AppAppearance = borderless", ""]
        return "\n".join(lines)

    @staticmethod
    def _patch_ini_sections(text: str, overrides: Dict[str, Dict[str, str]]) -> str:
        """Rewrite selected keys inside selected sections, leaving the rest of the file intact."""
        out = []
        section = ""
        applied: Dict[str, set] = {name: set() for name in overrides}

        for line in text.splitlines():
            header = re.match(r"\s*\[([^\]]+)\]", line)
            if header:
                section = header.group(1)
                out.append(line)
                continue

            keys = overrides.get(section)
            if keys:
                match = re.match(r"(\s*)([A-Za-z_][\w]*)(\s*)=(\s*)(.*)$", line)
                if match and match.group(2) in keys:
                    indent, key, before, after = match.group(1), match.group(2), match.group(3), match.group(4)
                    out.append(f"{indent}{key}{before}={after}{keys[key]}")
                    applied[section].add(key)
                    continue
            out.append(line)

        # Append any key the template did not carry.
        for section_name, keys in overrides.items():
            missing = {k: v for k, v in keys.items() if k not in applied[section_name]}
            if missing:
                out.append("")
                out.append(f"[{section_name}]")
                out.extend(f"{k} = {v}" for k, v in missing.items())

        return "\n".join(out) + "\n"

    @classmethod
    def generate_dxvk_conf(
        cls,
        analysis: GameAnalysis,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate dxvk.conf for the D3D9 -> Vulkan path.

        Deliberately short. DXVK carries per-game profiles of its own and applies them
        automatically, so every line written here is a line that overrides one of them -
        only the settings a profile actually asked for are emitted.

        Reporting video memory takes two keys, not one. DXVK computes

            GetAvailableTextureMem = min(deviceMemory + systemMemory,
                                         d3d9.maxAvailableMemory) - 8 MB

        so `d3d9.maxAvailableMemory` is a ceiling - it lowers the figure and can never
        raise it. `deviceMemory` is what `dxgi.maxDeviceMemory` caps, and DXVK's own
        built-in profile pins that to 128 MB for GTAIV.exe and EFLC.exe on purpose. Both
        keys have to be set to move the number a game actually sees.
        """
        p = {**DEFAULT_PROFILE, **(profile or {})}
        lines = [
            "# Generated by DLSS5-Anywhere.",
            "# DXVK loads this file first and merges its own per-game profile in with",
            "# insert(), which does not overwrite - so anything set here wins over the",
            "# built-in profile for this game. Keys are left out unless the mod profile",
            "# asked for them, because the built-in profiles are usually right.",
            "",
        ]

        device_mem = p.get("dxvk_max_device_memory_mb")
        if device_mem is not None:
            lines += [
                "# Caps the video memory the adapter reports. DXVK's built-in profile pins",
                "# this to 128 MB for GTA IV specifically - which is why a 12 GB card can",
                "# come up as a few hundred MB in that game. Raising it is the only way to",
                "# raise what the engine sees; it is also the workaround being switched off,",
                "# so if the game starts misbehaving around textures, put it back.",
                f"dxgi.maxDeviceMemory = {int(device_mem)}",
                "",
            ]

        max_mem = p.get("dxvk_max_available_memory_mb")
        if max_mem is not None:
            lines += [
                "# The CEILING on what GetAvailableTextureMem returns, in MB:",
                "#   min(deviceMemory + systemMemory, this) - 8 MB",
                "# It cannot raise the figure on its own - that is dxgi.maxDeviceMemory",
                "# above - but it has to be at least as large as the figure you want.",
                f"d3d9.maxAvailableMemory = {int(max_mem)}",
                "",
            ]

        track = p.get("dxvk_memory_track_test")
        if track is not None:
            lines += [f"d3d9.memoryTrackTest = {'True' if track else 'False'}", ""]

        tex_mem = p.get("dxvk_texture_memory_mb")
        if tex_mem is not None:
            lines += [
                "# Virtual address space DXVK will map for textures, in MB (0 = no limit).",
                "# A 32-bit game has about 2 GB of address space in total, so 0 is a way to",
                "# run out of it rather than a way to get more textures.",
                f"d3d9.textureMemory = {int(tex_mem)}",
                "",
            ]

        return "\n".join(lines)

    @classmethod
    def generate_bridge_reshade_ini(cls) -> str:
        """ReShade.ini for the RTX Remix renderer process (.trex/, beside NvRemixBridge.exe).

        ReShade attaches to that process as a Vulkan layer rather than as a local DLL, so this
        file exists only to point it at the add-ons sitting next to the renderer.
        """
        return """[ADDON]
AddonPath=.\\
DisabledAddons=

[GENERAL]
NoReloadOnInit=0
PerformanceMode=0

[INPUT]
KeyOverlay=36,0,0,0
InputProcessing=2

[OVERLAY]
TutorialProgress=4
"""

    @classmethod
    def generate_feed_cfg(
        cls,
        analysis: GameAnalysis,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate dlss5-feed.cfg, the add-on's own settings file.

        The add-on creates this itself with working defaults and re-reads it while the
        game runs, so writing it here only seeds the profile's choices. `work_resolution`
        is honoured on the 64-bit D3D11 path only - every other path stays at 100%.
        """
        p = {**DEFAULT_PROFILE, **(profile or {})}

        # The helper window is hidden by default, and for RenoDX that costs nothing: the
        # feeder mirrors the consumer's settings onto the "DLSS 5 Feed" page in the game's
        # own overlay, and that page is written from the [RenoDX.DLSS5] keys.
        #
        # Deep Fried Chicken is not mirrored anywhere. On a 32-bit install it runs in
        # host64\, its panel is a ReShade tab in that process, and with the window hidden
        # there is no way to reach the neural consumer at all - no pass count, no per-pass
        # guides, no way to see whether it armed. Measured on Prince of Persia: 4,800
        # neural frames in host64\deep-fried-chicken.log, delivered by a panel sitting in
        # a window behind the game with no taskbar button to raise it with.
        #
        # The window is only shown for the consumer that needs it. It takes focus when it
        # appears, and an engine that blocks its update loop on lost focus stalls - which
        # is why hidden is the default in the first place.
        host_window = bool(p.get("feed_host_window", True))
        if uses_dfc(profile) and not analysis.is_64bit:
            host_window = True

        return (
            "enabled=1\n"
            f"mode={int(p.get('feed_mode', 2))}\n"
            f"hdr={int(p.get('feed_hdr', -1))}\n"
            f"depth_inverted={1 if p.get('depth_reversed') else -1}\n"
            f"preset={int(p.get('feed_preset', 0))}\n"
            f"work_resolution={int(p.get('feed_work_resolution', 100))}\n"
            f"mv_scale_x={float(p.get('feed_mv_scale_x', 1.0)):.3f}\n"
            f"mv_scale_y={float(p.get('feed_mv_scale_y', 1.0)):.3f}\n"
            f"host_window={1 if host_window else 0}\n"
        )

    @classmethod
    def generate_rage_commandline(
        cls,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate commandline.txt for a RAGE game running behind dgVoodoo2.

        Only `-norestrictions`, which lifts the clamp on the graphics settings. The
        engine also accepts `-availablevidmem <MB>` ("[MEMORY] Override available video
        memory (in megabytes)"), but overriding the streamer's memory budget was observed
        to hang GTA IV on its loading screen - with the renderer still presenting frames,
        so it does not even look like a hang from the log. It is not written here.
        """
        return "-norestrictions\n"

    @classmethod
    def generate_optiscaler_ini(
        cls,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate OptiScaler.ini for the FSR/XeSS bridge path."""
        return """[Upscalers]
Dx11Upscaler=fsr2
Dx12Upscaler=dlss
VulkanUpscaler=fsr2

[DLSS]
LibraryPath=auto
RenderPresetOverride=true

[Hooks]
HookDxgi=true
HookD3D11=true
HookD3D12=true
HookVulkan=true

[Framerate]
FramerateLimit=0
"""

    @classmethod
    def generate_launcher_batch(
        cls,
        analysis: GameAnalysis,
        plan=None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate a convenience launcher that documents the in-game keys.

        On the DXVK path this is not just a convenience. DXVK looks for its config as
        `$PWD/dxvk.conf` - the current working directory, not the folder the executable is
        in - or at whatever `DXVK_CONFIG_FILE` points to. Launch the game any way that
        does not happen to set the working directory to the game folder and the generated
        dxvk.conf is silently not read, with no log line and no symptom beyond the
        settings in it not applying. Naming the file outright removes the question.

        On an emulator with a verified direct-boot flag it is not a convenience either -
        see `_emulator_launcher`, and `EmulatorProfile.direct_boot_flag` for why the
        front-end costs the install its neural consumer.
        """
        emulator = getattr(analysis, "emulator", None)
        if emulator is not None and emulator.direct_boot_flag:
            return cls._emulator_launcher(analysis, emulator, plan, profile)

        exe_name = analysis.exe_name
        dxvk_env = ""
        dxvk_note = ""
        if getattr(plan, "uses_dxvk", False):
            dxvk_env = (
                'set "DXVK_CONFIG_FILE=%~dp0dxvk.conf"\n'
                'cd /d "%~dp0"\n'
            )
            dxvk_note = (
                "echo   dxvk.conf is being passed to DXVK by path - launching the game\n"
                "echo   another way only picks it up if the working directory happens to\n"
                "echo   be this folder.\n"
            )

        return f"""@echo off
title Launching {exe_name} with DLSS 5 Neural Rendering
{dxvk_env}echo ========================================================
echo   Launching {exe_name}
echo ========================================================
echo In-game:
echo   [Home]  ReShade overlay (Home tab = effects, Add-ons tab = DLSS 5 Feed)
echo   [F2]    Toggle the effects on/off for comparison
{dxvk_note}echo ========================================================
start "" "{exe_name}"
"""

    # Implicit Vulkan layers that wrap the swapchain ReShade wraps. Measured on one PC
    # with six of them live: 28 full effect reloads in three minutes, seen as black
    # flashing, against 3 in sixteen minutes with them off. Each reload is another chance
    # for the add-on registration below to be lost, so an emulator launcher turns them off
    # for the process it starts - and only for that process, because this is an
    # environment variable and not an install-wide setting.
    _RIVAL_VULKAN_LAYERS = (
        "VK_LAYER_EOS_Overlay",
        "GalaxyOverlayVkLayer",
        "GalaxyOverlayVkLayer_VERBOSE",
        "GalaxyOverlayVkLayer_DEBUG",
        "VK_LAYER_OW_OVERLAY",
        "VK_LAYER_OW_OBS_HOOK",
        "VK_LAYER_VALVE_steam_overlay",
        "VK_LAYER_VALVE_steam_fossilize",
    )

    @classmethod
    def generate_hold_script(cls, emulator, consumer_file: str) -> str:
        """The watcher that keeps the neural consumer out of the first emulation start.

        A neural consumer pins itself in the process once it has initialised, because its
        NGX detours have to outlive the graphics device they were installed under. That is
        also what breaks it: the emulator destroys and recreates that device every time
        emulation starts, ReShade's layer is reloaded and re-scans the add-on folder, and
        the consumer is already resident - so LoadLibrary returns the handle it has, the
        entry point never runs again, and ReShade logs "No add-on was registered".

        Titles that need two emulation starts are the ones this hits, and they cannot be
        avoided by booting differently. Measured on God of War: Ascension - its EBOOT.BIN
        is a launcher, so RPCS3 boots the game folder, logs "Stopping emulator..." three
        seconds later, and boots USRDIR/GOWA.SELF with direct=1. Booting that .SELF from
        the command line instead does not work at all: "Failed to decrypt SELF metadata
        info", because the decryption belongs to the boot chain.

        So instead of chasing one attach, this makes the consumer miss the first one. A
        file that is not in the folder cannot be loaded, and cannot pin itself; put it
        back while emulation is stopping and the next attach loads it for the first time
        and registers it properly. Measured end to end: consumer restored 7.5s after
        launch, ReShade "Registered add-on Deep Fried Chicken" at 10.0s, feeder reporting
        "interception state 2 (ARMED)" and 1200 neural frames.

        Whether a title restarts emulation is a property of the title, so the first run of
        a new one is a guess. The guess is recorded afterwards from the emulator's own log
        and reused, which makes it wrong at most once.
        """
        addon_dir = ADDON_DIR_NAME
        return f'''<#
    Holds {consumer_file} out of {addon_dir}\\ across the first emulation start, and
    puts it back before the next one. Generated by DLSS5-Anywhere - see
    ConfigGenerator.generate_hold_script for why this exists.

    -Game    the game to boot; omitted opens the emulator's front end
    -Mode    auto (default, decided by the last run), hold, or nohold
#>
param(
    [string] $Game = '',
    [ValidateSet('auto', 'hold', 'nohold')]
    [string] $Mode = 'auto'
)

$ErrorActionPreference = 'Stop'
$here     = $PSScriptRoot
$exe      = Join-Path $here '{emulator.exe_names[0]}'
$consumer = Join-Path $here '{addon_dir}\\{consumer_file}'
$held     = "$consumer.held"
$emuLog   = Join-Path $here '{emulator.log_path}'
$state    = Join-Path $here 'dlss5-launch-state.ini'

function Restore-Consumer {{
    if (Test-Path -LiteralPath $held) {{
        Move-Item -LiteralPath $held -Destination $consumer -Force
        return $true
    }}
    return $false
}}

# A session that was killed before it could put the file back leaves it here. Undoing
# that first means a crash costs one launch, not a silently missing consumer.
if (Restore-Consumer) {{ Write-Host '  recovered {consumer_file} from a previous run' }}

if (-not (Test-Path -LiteralPath $exe))      {{ Write-Host "  not found: $exe"; exit 1 }}
if (-not (Test-Path -LiteralPath $consumer)) {{ Write-Host "  no neural consumer at $consumer - launching unchanged"; $Mode = 'nohold' }}

$restarts = ''
if (Test-Path -LiteralPath $state) {{
    $restarts = (Get-Content -LiteralPath $state | Where-Object {{ $_ -match '^restarts_emulation=' }} | Select-Object -Last 1)
}}
$hold = switch ($Mode) {{
    'hold'   {{ $true }}
    'nohold' {{ $false }}
    default  {{ $restarts -eq 'restarts_emulation=1' }}
}}

if ($hold) {{
    Move-Item -LiteralPath $consumer -Destination $held -Force
    Write-Host '  {consumer_file} held back: this title restarts emulation, and the'
    Write-Host '  consumer only registers with ReShade on an attach that loads it fresh.'
}}
elseif ($Mode -eq 'auto' -and -not $restarts) {{
    Write-Host '  first run of this launcher: if the game boots twice and the neural'
    Write-Host '  consumer does not come on, run it once more - this run records whether'
    Write-Host '  the title restarts emulation and the next one acts on it.'
}}

$logLength = 0
if (Test-Path -LiteralPath $emuLog) {{ $logLength = (Get-Item -LiteralPath $emuLog).Length }}

# Quoted here, not by Start-Process: -ArgumentList joins an array with spaces and quotes
# nothing, so an unquoted "...\\GOW Ascension\\PS3_GAME" reaches the emulator as two
# arguments and it boots "H:\\Emulation\\roms\\ps3\\GOW" - "Invalid file or folder".
$emuArgs = @()
if ($Game) {{ $emuArgs = @('{emulator.direct_boot_flag}', ('"' + $Game.Trim('"') + '"')) }}

try {{
    $proc = Start-Process -FilePath $exe -WorkingDirectory $here -ArgumentList $emuArgs -PassThru
    $started = Get-Date
    $booted  = $null

    while (-not $proc.HasExited) {{
        Start-Sleep -Milliseconds 250

        $text = ''
        try {{
            # The emulator holds its log open, and truncates it at startup - so this reads
            # with full sharing, and ignores anything shorter than what was already there.
            $fs = [System.IO.File]::Open($emuLog, 'Open', 'Read', 'ReadWrite, Delete')
            $sr = New-Object System.IO.StreamReader($fs)
            $text = $sr.ReadToEnd()
            $sr.Close(); $fs.Close()
        }} catch {{ continue }}

        if (-not $booted -and $text -match '{emulator.boot_marker}') {{ $booted = Get-Date }}

        if ($hold) {{
            if ($text -match '{emulator.stop_marker}') {{
                $null = Restore-Consumer
                $hold = $false
                Write-Host ('  {consumer_file} restored ' + [math]::Round(((Get-Date) - $started).TotalSeconds, 1) + 's in, before the next attach')
            }}
            elseif ($booted -and ((Get-Date) - $booted).TotalSeconds -gt 120) {{
                $null = Restore-Consumer
                $hold = $false
                Write-Host '  no restart after two minutes of play, so this title does not need'
                Write-Host '  the hold: it is back in place, and the next run will not hold it.'
            }}
        }}
    }}

    # What the log says now is the answer for next time: one boot means the hold is not
    # needed, two or more means it is.
    try {{
        $boots = ([regex]::Matches($text, '{emulator.boot_marker}')).Count
        if ($boots -ge 1) {{
            $value = '0'
            if ($boots -ge 2) {{ $value = '1' }}
            Set-Content -LiteralPath $state -Encoding UTF8 -Value @(
                '# Written by launch_with_dlss5.bat. Delete this file to forget it.',
                ('restarts_emulation=' + $value)
            )
        }}
    }} catch {{ }}
}}
finally {{
    if (Restore-Consumer) {{ Write-Host '  {consumer_file} put back' }}
}}
'''

    HOLD_SCRIPT_NAME = "dlss5-hold-consumer.ps1"

    @staticmethod
    def consumer_addon_name(profile: Optional[Dict[str, Any]] = None) -> str:
        """The neural consumer's file name, whichever one this profile selected."""
        return "deep-fried-chicken.addon64" if uses_dfc(profile) else "renodx-dlss5.addon64"

    @staticmethod
    def emulator_can_hold_consumer(emulator) -> bool:
        """Whether this emulator's log has been read well enough to watch it.

        All three are needed: the log to read, the line that means emulation started, and
        the line that means it stopped. A marker that never matches would hold the
        consumer back for an entire session, which is worse than not trying.
        """
        return bool(
            emulator is not None
            and emulator.log_path
            and emulator.boot_marker
            and emulator.stop_marker
        )

    @classmethod
    def _emulator_launcher(
        cls,
        analysis: GameAnalysis,
        emulator,
        plan=None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """The launcher for an emulator that can boot a game without its front-end.

        An emulator creates a graphics device when emulation starts and destroys it when
        emulation stops, so one session holds several: a game whose launcher executable
        chain-boots the real one makes two, and every stop and restart from the game list
        makes another. ReShade's Vulkan layer is loaded and unloaded with each of them and
        re-scans the add-on folder every time.

        Loading an add-on that is already in the process does nothing: LoadLibrary hands
        back the handle it has, the entry point does not run, nothing calls
        ReShadeRegisterAddon, and ReShade logs "No add-on was registered by ...  Unloading
        again". Add-ons that pin themselves do that to keep hooks alive across exactly
        this kind of teardown, so the ones worth having are the ones this breaks - a
        neural consumer ends up holding its NGX hooks with no ReShade registration behind
        them, which reads in dlss5-feed.log as a consumer that never leaves CLAIMING.

        Booting the game directly removes the attaches the front end would have added.
        The ones the title itself forces are handled by the hold script instead.
        """
        exe_name = analysis.exe_name
        flag = emulator.direct_boot_flag
        consumer = cls.consumer_addon_name(profile)
        holding = cls.emulator_can_hold_consumer(emulator)

        if holding:
            run_with_game = (
                f'powershell -NoProfile -ExecutionPolicy Bypass -File '
                f'"%~dp0{cls.HOLD_SCRIPT_NAME}" -Game "%GAME%" %MODE%'
            )
            run_bare = (
                f'powershell -NoProfile -ExecutionPolicy Bypass -File '
                f'"%~dp0{cls.HOLD_SCRIPT_NAME}" %MODE%'
            )
            hold_note = (
                f"rem  A title that boots twice is handled by {cls.HOLD_SCRIPT_NAME},\n"
                f"rem  which holds {consumer} out of the folder for the first\n"
                "rem  attach so it is loaded fresh on the second. That is why this window\n"
                "rem  stays open while you play: closing it early only costs one launch,\n"
                "rem  because the next one puts the file back before doing anything else.\n"
                "rem\n"
                "rem  Pass /hold or /nohold after the game path to overrule what the last\n"
                "rem  run recorded about this title.\n"
                "rem\n"
            )
        else:
            run_with_game = f'start "" "{exe_name}" {flag} "%GAME%"'
            run_bare = f'start "" "{exe_name}"'
            hold_note = ""

        layer_env = ""
        if getattr(plan, "needs_vulkan_layer", False):
            layer_env = (
                "rem  Keep other overlays out of the swapchain ReShade is wrapping.\n"
                'set "VK_LOADER_LAYERS_DISABLE='
                + ",".join(cls._RIVAL_VULKAN_LAYERS)
                + '"\n'
                "rem  Older Vulkan loaders ignore the list above but honour these.\n"
                'set "DISABLE_VK_LAYER_VALVE_steam_overlay_1=1"\n'
                'set "DISABLE_VK_LAYER_VALVE_steam_fossilize_1=1"\n'
                'set "DISABLE_VK_LAYER_OW_OVERLAY_1=1"\n'
                'set "DISABLE_VK_LAYER_OW_OBS_HOOK_1=1"\n'
                "\n"
            )

        return f"""@echo off
title Launching {exe_name} with DLSS 5 Neural Rendering
setlocal
cd /d "%~dp0"

rem  ---------------------------------------------------------------------------
rem  This boots the game itself instead of opening {emulator.name}'s front-end,
rem  and that is not a convenience.
rem
rem  The emulator makes a graphics device every time emulation starts and destroys
rem  it every time emulation stops, and ReShade's layer is loaded, unloaded and
rem  re-scanned with it. An add-on that pins itself in the process - the neural
rem  consumer does, to keep its NGX hooks alive - is already loaded by the next
rem  attach, so its entry point never runs again and ReShade.log says
rem
rem      No add-on was registered by '...\\deep-fried-chicken.addon64'.
rem      Unloading again ...
rem
rem  It then has its hooks but no ReShade registration: no entry in the Add-ons
rem  tab, and dlss5-feed.log reporting a consumer stuck at CLAIMING.
rem
rem  Booting straight in removes the starts the front end would have added. The
rem  ones the title forces on you cannot be removed: a launcher EBOOT that
rem  chain-boots the real executable stops and restarts emulation by itself, and
rem  the real executable usually cannot be booted on its own - RPCS3 answers
rem  "Failed to decrypt SELF metadata info", because that belongs to the chain.
rem
{hold_note}rem  Put a path between the quotes below to make it the default:
rem  ---------------------------------------------------------------------------
set "GAME="

rem  Read one argument at a time rather than looping over %*, which would split a game
rem  path at its spaces.
set "MODE="
if /i "%~1"=="/hold" (set "MODE=-Mode hold") else if /i "%~1"=="/nohold" (set "MODE=-Mode nohold") else if not "%~1"=="" (set "GAME=%~1")
if /i "%~2"=="/hold" set "MODE=-Mode hold"
if /i "%~2"=="/nohold" set "MODE=-Mode nohold"

rem  Both spelled out: a Git-for-Windows or Cygwin bin folder ahead of System32 on
rem  PATH turns a bare "find" into the Unix one, which does not take /i, fails, and
rem  quietly answers "not running" to the question below.
"%SystemRoot%\\System32\\tasklist.exe" /fi "imagename eq {exe_name}" 2>nul | "%SystemRoot%\\System32\\find.exe" /i "{exe_name}" >nul
if not errorlevel 1 (
    echo.
    echo   {exe_name} is already running - close it first, including a leftover
    echo   crash or error dialog, which keeps the process alive and counts:
    echo.
    "%SystemRoot%\\System32\\tasklist.exe" /fi "imagename eq {exe_name}"
    echo.
    pause
    exit /b 1
)

{layer_env}echo ========================================================
echo   Launching {exe_name}
echo ========================================================
echo In-game:
echo   [Home]  ReShade overlay (Home tab = effects, Add-ons tab = DLSS 5 Feed)
echo   [F2]    Toggle the effects on/off for comparison
echo ========================================================

if defined GAME (
    if not exist "%GAME%" (
        echo.
        echo   Not found: %GAME%
        echo   Pass the game's executable instead:
        echo       launch_with_dlss5.bat "X:\\path\\to\\the\\game"
        echo.
        pause
        exit /b 1
    )
    {run_with_game}
    exit /b 0
)

echo.
echo   No game path was given, so this opens the front-end. Booting from the
echo   game list works, but every stop and restart re-attaches ReShade.
echo.
echo   To boot straight in:
echo       launch_with_dlss5.bat "X:\\path\\to\\the\\game"
echo.
{run_bare}
"""

    @classmethod
    def generate_apply_batch(cls, analysis: GameAnalysis) -> str:
        """Generate the 1-click apply script used by 'Prepare Build Only'."""
        return """@echo off
setlocal EnableDelayedExpansion
title DLSS 5 Mod - 1-Click Installer
echo ========================================================
echo   Applying DLSS 5 Neural Rendering to this folder
echo ========================================================
set "GAME_DIR=%~dp0"

echo Copying mod files...
xcopy /E /Y /I "%~dp0build_files\\*" "%GAME_DIR%"

echo.
echo [OK] Files applied. Read README_INSTALL.txt before launching.
pause
"""

    @classmethod
    def generate_uninstall_batch(cls, analysis: GameAnalysis) -> str:
        """Generate the uninstall script for the game folder."""
        return """@echo off
title DLSS 5 Mod - Uninstaller
echo ========================================================
echo   Removing DLSS 5 Neural Rendering Mod
echo ========================================================

for %%F in (
    dlss5-feed.addon64 dlss5-feed.addon32 dlss5-feed.cfg dlss5-feed.log
    renodx-dlss5.addon64 nvngx_dlssnr.dll nvngx_dlss.dll
    DLSS5_Preset.ini ReShade.ini ReShade.log ReShadePreset.ini
    alexs-toolkit.addon64 alexs-toolkit.cfg
    dxgi.dll opengl32.dll D3D9.dll dgVoodoo.conf dgVoodooCpl.exe dxvk.conf
    OptiScaler.ini launch_with_dlss5.bat
    dlss5-hold-consumer.ps1 dlss5-launch-state.ini
) do if exist "%%F" del /f /q "%%F"

rem  The launcher moves the neural consumer aside for one emulation start and moves it
rem  back. A session killed in between leaves the held name behind, and deleting the
rem  add-on folder below would take the consumer with it - so put it back first.
for %%F in ("dlss5-addons\\*.addon64.held") do (
    if exist "%%~dpnF" (del /f /q "%%F") else (move /y "%%F" "%%~dpnF" >nul)
)

rem  host64\ moved inside the add-on folder; the older location is still removed
rem  so an uninstall run against a build made before that leaves nothing behind.
if exist "host64" rmdir /s /q "host64"
if exist "reshade-shaders" rmdir /s /q "reshade-shaders"
if exist "feed-vk-layer" rmdir /s /q "feed-vk-layer"

echo Restoring original backups if available...
if exist ".dlss5_backup" (
    xcopy /E /Y /I ".dlss5_backup\\*" ".\\"
    rmdir /s /q ".dlss5_backup"
    echo Backup files restored.
)

echo.
echo [OK] Game directory restored to vanilla state!
pause
"""

    @classmethod
    def generate_readme_guide(
        cls,
        analysis: GameAnalysis,
        strategy: str,
        plan=None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate install instructions specific to this game's layout."""
        uses_host64 = getattr(plan, "uses_host64", not analysis.is_64bit)
        uses_dgvoodoo = getattr(plan, "uses_dgvoodoo", False)
        uses_dxvk = getattr(plan, "uses_dxvk", False)
        emulator = getattr(analysis, "emulator", None)
        emulator_api = getattr(plan, "emulator_api", "")
        chicken = uses_dfc(profile)
        consumer_addon = cls.consumer_addon_name(profile)

        arch_label = "x64" if analysis.is_64bit else "x86"
        # Vulkan hosts get no local DLL at all - saying otherwise here is what sends people
        # hunting for a dxgi.dll that was deliberately never written.
        if emulator_api == "vulkan" or uses_dxvk:
            reshade_line = (
                f"     (no local DLL)            ReShade {arch_label} is registered as a Vulkan layer"
            )
        elif emulator_api == "opengl":
            reshade_line = f"     opengl32.dll              ReShade {arch_label} (add-on support)"
        else:
            reshade_line = f"     dxgi.dll                  ReShade {arch_label} (add-on support)"

        p = {**DEFAULT_PROFILE, **(profile or {})}
        effects = [k for k in (p.get("lumenite_effects") or []) if k in VISIBLE_EFFECTS]
        provider = mv_provider_for(p)

        layout = [
            "   Next to the game .exe:",
            reshade_line,
            f"     {'dlss5-feed.addon64' if analysis.is_64bit else 'dlss5-feed.addon32'}        DLSS5-Feeder add-on",
            "     reshade-shaders\\Shaders\\  DLSS5_Feed.fx, lumenite_*.fx, include\\, ReShade.fxh",
            "     reshade-shaders\\Textures\\ lumenite_bluenoise256.png",
            "     ReShade.ini, DLSS5_Preset.ini",
        ]
        if provider.component == COMP_VORT:
            # vort_Motion.fx resolves its own headers as Includes\..., which ReShade
            # finds through the Shaders search path - so the folder name matters.
            layout[4:5] = [
                "     reshade-shaders\\Shaders\\  vort_Motion.fx + Includes\\ (motion vectors)",
                "     reshade-shaders\\Textures\\ lumenite_bluenoise256.png, vort_BlueNoise.png",
            ]
        if emulator_api == "vulkan" or uses_dxvk:
            layout.append(
                "     feed-vk-layer\\           VK_LAYER_feed_vk fallback launcher"
            )
        if uses_dxvk:
            layout[1:1] = [
                "     d3d9.dll                  DXVK (D3D9 -> Vulkan)",
                "     dxvk.conf                 how much video memory the game is told about",
            ]
        if uses_dgvoodoo:
            layout[1:1] = [
                "     D3D9.dll                  dgVoodoo2 wrapper (D3D9 -> D3D11)",
                "     dgVoodoo.conf             wrapper settings (watermark ON until verified)",
            ]
        # The two consumers are alternatives and never both, so naming the one this build
        # deliberately left out sends people looking for a file that is not there.
        if chicken:
            consumer_lines = [
                "     deep-fried-chicken.addon64   DLSS 5 neural rendering add-on",
                "     deep-fried-chicken-nvngx.dll its private NGX bridge - it cannot attach without this",
                "     deep-fried-chicken.cfg       its settings, carried from the vendor file",
            ]
            consumer_ini = "     ReShade.ini               ReShade and add-on paths"
        else:
            consumer_lines = [
                f"     renodx-dlss5.addon64      DLSS 5 neural rendering add-on ({RENODX_PINNED_VERSION})",
            ]
            consumer_ini = "     ReShade.ini               neural rendering settings ([RenoDX.DLSS5])"

        runtimes = [
            "     nvngx_dlssnr.dll          neural rendering runtime",
            "     nvngx_dlss.dll            super resolution runtime",
        ]

        # The pin is a RenoDX rule - newer RenoDX builds construct part of the DLSS
        # contract themselves and fight the feeder. Chicken has its own rule instead:
        # it and RenoDX go inert in each other's presence, so only one may be installed.
        if chicken:
            version_pin = (
                "   Deep Fried Chicken and renodx-dlss5.addon64 must never be installed\n"
                "   together: Chicken goes completely inert for the whole process while a\n"
                "   RenoDX neural provider is loaded, with no error anywhere. This build\n"
                "   staged Chicken, so there is no renodx-dlss5.addon64 to remove."
            )
        else:
            version_pin = (
                f"   renodx-dlss5.addon64 must be {RENODX_PINNED_VERSION}. Newer builds construct\n"
                "   part of the DLSS contract themselves and conflict with the feeder."
            )
        if uses_host64:
            layout += [
                "",
                "   In host64\\ (the 64-bit helper - NGX has no 32-bit build):",
                "     dlss5-feed-host64.exe     helper process",
                "     dxgi.dll                  ReShade x64",
                consumer_ini,
            ] + consumer_lines + runtimes
        else:
            layout += consumer_lines + runtimes

        dxvk_steps = ""
        if uses_dxvk:
            dxvk_steps = """
2. VERIFY DXVK FIRST (D3D9 game):
   DXVK leaves no watermark, so confirm it another way before blaming anything
   downstream: set DXVK_HUD=version for one launch (a command prompt in the game folder,
   `set DXVK_HUD=version` then start the .exe) and the DXVK build appears in the corner.
   DXVK_LOG_LEVEL=info writes the same to a log beside the executable. If neither shows
   up the game is still on the system d3d9.dll and nothing else can work.

   ReShade is NOT a file in this folder. Under DXVK the game presents through Vulkan,
   which has no local-DLL injection point, so ReShade is registered as a layer for this
   executable - copying a dxgi.dll in here does nothing. If the overlay never appears,
   re-run components/reshade/ReShade_Setup_Addon.exe, point it at the .exe and pick
   Vulkan.

   CONFIG FILE: DXVK reads $PWD/dxvk.conf - the WORKING DIRECTORY, not this folder. The
   generated launch_with_dlss5.bat points DXVK_CONFIG_FILE at the file by full path;
   launching the game another way only picks it up if the working directory happens to be
   this folder. If a setting below appears to do nothing, check that first.

   VIDEO MEMORY: DXVK works it out as

       reported = min(deviceMemory + systemMemory, d3d9.maxAvailableMemory) - 8 MB

   so d3d9.maxAvailableMemory is a CEILING - it can lower the figure and never raise it.
   deviceMemory is what dxgi.maxDeviceMemory caps, and DXVK ships a built-in profile that
   pins that to 128 MB for GTAIV.exe and EFLC.exe deliberately, to work around how the
   engine handles large values. That is why a 12 GB card can report a few hundred MB in
   that game, and it is not a fault in this install. To override it, set
   'dxvk_max_device_memory_mb' in the profile - and put it back if textures start
   misbehaving, because the 128 MB is the workaround you are switching off.

   WORK RESOLUTION: fixed at 100% on this path. The add-on offers that control on its
   D3D11 transport only and pins it at 100% on OpenGL and Vulkan, so the slider in the
   overlay is greyed out here by design. If you render below native for frames, use the
   dgVoodoo2 strategy - setting work resolution below 100% in a profile selects it
   automatically.
"""

        dgvoodoo_steps = ""
        if uses_dgvoodoo:
            dgvoodoo_steps = """
2. VERIFY DGVOODOO FIRST (D3D9 game):
   Launch the game. The dgVoodoo watermark MUST appear in the corner. If it does not,
   nothing downstream can work - the game is still on plain D3D9. Check that
   dgVoodoo.conf has DisableAndPassThru = false and that D3D9.dll is the right
   architecture. It clears itself after a few seconds (WatermarkDisplayDuration in
   dgVoodoo.conf); set dgvoodoo_watermark to false in the profile to stop it appearing
   at all.

   VIDEO MEMORY: the engine reads back dgVoodoo's configured VRAM on a video card it has
   never heard of, and old engines clamp their presets from that. If the game reports far
   less memory than the card has, raise VRAM in dgVoodoo.conf. A RAGE game (GTA IV) also
   clamps its presets outright when it does not recognise the device - that is what the
   generated commandline.txt with -norestrictions lifts.

   This path keeps the add-on's 'Work resolution' control, which the DXVK path does not:
   dgVoodoo lands on D3D11, and that is the only transport the add-on offers it on.
"""

        emulator_steps = ""
        if emulator:
            api_label = API_DISPLAY_NAMES.get(emulator_api, emulator_api)
            if emulator_api == "vulkan":
                attach = (
                    "   Vulkan has no local-DLL injection point. ReShade was registered as a Vulkan\n"
                    f"   layer for {analysis.exe_name} instead - nothing named dxgi.dll or opengl32.dll\n"
                    "   is copied into the emulator folder, and copying one there does nothing.\n"
                    "\n"
                    "   If dlss5-feed.log reports missing Vulkan interop entry points, the add-on's\n"
                    "   in-process vkCreateDevice hook did not catch the emulator's device creation.\n"
                    "   Launch through feed-vk-layer\\run-with-feed-layer.bat in that case - it sets\n"
                    f"   VK_LAYER_feed_vk for that one launch and does not touch the registry.\n"
                )
            elif emulator_api == "opengl":
                attach = "   ReShade attaches as opengl32.dll, not dxgi.dll.\n"
            else:
                attach = "   ReShade attaches as dxgi.dll, the same as any Direct3D game.\n"

            # An emulator with one backend has no renderer to set. Printing the "set it
            # first" step anyway is an instruction that cannot be followed, and the reader
            # has no way to tell it apart from a step they have missed.
            if len(getattr(emulator, "apis", [])) > 1:
                renderer_heading = f"""2. SET THE RENDERER FIRST ({emulator.name}):
   This install is built for the {api_label} renderer. Open the emulator's own graphics
   settings and select it BEFORE launching with the mod. Switching renderer afterwards
   moves ReShade's attach point and silently disables the whole stack."""
            else:
                renderer_heading = f"""2. RENDERER ({emulator.name}):
   It presents through {api_label} and nothing else, so there is no renderer setting to
   change and nothing to get wrong here - the attach point below is fixed."""

            emulator_steps = f"""
{renderer_heading}

{attach}
   The emulated console issues no DLSS calls of its own, so DLSS5-Feeder synthesises the
   DLAA contract exactly as it does for an old PC game. The emulator is 64-bit, so the
   add-on runs inside it - there is no host64 helper here.
"""

        host_steps = ""
        if uses_host64:
            host_steps = """
   32-BIT NOTE: the first fed frame spawns host64\\dlss5-feed-host64.exe, which opens a
   window titled "32-bit DLSS 5 Feeder". That window is where the DLSS 5 add-on's own
   panel lives; the game's own overlay only shows the "DLSS 5 Feed" add-on page.
"""
            if chicken:
                host_steps += """   The window is shown for this build (host_window=1 in dlss5-feed.cfg) because it is
   the only place Deep Fried Chicken exists. Press [Home] in THAT window, not in the
   game, to reach its tabs. The feeder mirrors a consumer's settings onto the game's
   own overlay page, but it mirrors RenoDX's - Chicken's are not there. Its log is
   host64\\deep-fried-chicken.log, next to the helper rather than next to the game.
"""

        # The failure that looks exactly like a working install: everything registers,
        # arms and delivers frames, and the consumer quietly does nothing. Worth a
        # troubleshooting line of its own because no other log in the folder mentions it.
        # Multi frame generation is configured in its own overlay panel, and the single
        # most likely way to get nothing out of it is to install it and never open that
        # panel - the default follows the game's multiplier, and a game with an on/off
        # Frame Generation switch has no multiplier to follow.
        mfg_steps = ""
        if wants_mfg_unlock(profile):
            force, ceiling = mfg_settings(profile)
            chosen = (
                f"forcing {force}x" if force
                else "following the game's own multiplier"
            )
            mfg_steps = f"""
   MULTI FRAME GENERATION (RTX 40): this build wrote {chosen}, ceiling {ceiling}x, into
   [{MFG_SECTION}] in ReShade.ini. Change it in game from the overlay:
     [Home] -> Add-ons tab -> MFG Unlock
       Force frame multiplier   what actually runs. 0 lets the game choose - correct only
                                if the game's own menu offers 2x/3x/4x. If it offers a
                                plain Frame Generation on/off, set this to 3 or 4 or
                                nothing changes.
       Max count                the ceiling reported to the runtime, not what runs.
   It multiplies the game's own frame generation and cannot add it, and the multi-frame
   code lives in nvngx_dlssg.dll rather than in the game - a title still on the DLSS 3
   snippet (3.5.x) needs a 310.x runtime beside the .exe before there is anything to
   raise. Never take this into a game with anti-cheat.
"""

        chicken_check = ""
        chicken_trouble = ""
        if chicken:
            # Chicken writes its log beside its own .addon64, which is host64\ on a
            # 32-bit install and the private add-on folder everywhere else - not beside
            # the game, where dlss5-feed.log is and where people look first.
            dfc_log = ("host64" if uses_host64 else ADDON_DIR_NAME) + "\\deep-fried-chicken.log"
            chicken_check = f"""
   That is the feeder's own log, and for this consumer it is not enough on its own: it
   reports Chicken ARMED and keeps delivering frames even when Chicken's neural path has
   been switched off. Open {dfc_log} too, and look for
   "standalone neural frame succeeded: count=" climbing. If the log stops at an FP16 line
   instead, see the first entry under troubleshooting."""
            chicken_trouble = """
   - deep-fried-chicken.log says "standalone FP16 codec allocation failed: game-output
     device identity failed (0x80070057)", then "standalone neural path disabled at FP16
     codec acquisition": the neural consumer is off for the session, however healthy
     dlss5-feed.log looks. It means the feeder handed DLSS the game's own Direct3D 12
     device - which happens for a Direct3D 12 game, and for an emulator set to Direct3D
     12 - and Chicken 1.4.8-alpha cannot work on that one. Move the renderer to Vulkan
     (or Direct3D 11) and rebuild, or use RenoDX as the consumer here. The line to look
     for when it is right is "FP16 output codec contract cached", then "standalone
     neural frame succeeded: count=..." climbing."""

        # The single most common report about this tool was "it installed fine and looks
        # like nothing happened", and it was true: the preset enabled two techniques that
        # draw nothing, and the neural add-on's own switch was never written. Spelling the
        # resulting look out here makes the difference checkable before launching.
        effect_names = {
            "rtao": "ray-traced ambient occlusion",
            "quantao": "ambient occlusion (cheap)",
            "lsao": "large-scale ambient occlusion",
            "sssr": "screen-space reflections (glitchy - off by default)",
            "bloom": "anamorphic bloom",
            "traa": "temporal AA (runs alongside DLSS's own)",
        }
        neural_ini = "host64/ReShade.ini" if uses_host64 else "the game folder's ReShade.ini"
        # The two consumers keep their settings in different places: RenoDX imports
        # ReShade's config API, Chicken keeps its own file. Pointing someone at a
        # [RenoDX.DLSS5] section that this build never wrote is a dead end.
        if chicken:
            settings_home = f"{ADDON_DIR_NAME}/deep-fried-chicken.cfg"
        else:
            settings_home = f"[RenoDX.DLSS5] in {neural_ini}"
        if p.get("nr_enabled", True):
            cascade = "single neural pass"
            if chicken:
                # The cascade attaches to RenoDX only, and Chicken's own notes ask for the
                # toolkit to be removed rather than stacked on it.
                cascade = f"{max(1, min(30, int(p.get('dfc_layers', 1) or 1)))}-layer neural pass"
            elif p.get("toolkit_enabled", True) and p.get("toolkit_three_pass", False):
                cascade = "three-pass cascade (needs alexs-toolkit.addon64)"
            elif p.get("toolkit_enabled", True) and p.get("toolkit_two_pass", True):
                cascade = "two-pass cascade (needs alexs-toolkit.addon64)"
            look_lines = [
                f"   Neural rendering:  ON  ({'Cinematic' if int(p.get('nr_style', 0)) == 1 else 'Natural'} "
                f"style, {cascade})",
                f"                      written to {settings_home}",
            ]
        else:
            look_lines = [
                "   Neural rendering:  OFF - this build is DLAA only. Clean edges, and very",
                "                      little else. Pick a profile with it enabled, or tick",
                "                      'Enable DLSS Neural Rendering' in the Add-ons tab.",
            ]
        if effects:
            look_lines.append(
                "   Effects on top:    " + ", ".join(effect_names.get(k, k) for k in effects)
            )
        else:
            look_lines.append(
                "   Effects on top:    none - only the DLSS pass itself changes the image."
            )
        look_summary = chr(10).join(look_lines)

        mv_id = provider.id
        mv_name = provider.name

        return f"""================================================================================
  DLSS 5 NEURAL RENDERING - INSTALL & USAGE
  Target: {analysis.exe_name} ({analysis.architecture}, {analysis.primary_api})
  Strategy: {strategy}
================================================================================

1. WHAT GETS INSTALLED:
{chr(10).join(layout)}
{dxvk_steps}{dgvoodoo_steps}{emulator_steps}
3. WHAT THIS BUILD IS SET TO:
{look_summary}

4. IN-GAME:
   a. Launch the game and press [Home] for the ReShade overlay.
   b. Home tab: the techniques above are already ticked, in that order. The motion
      vector technique ({mv_name}) has to stay ABOVE "DLSS 5 Feed": it writes
      the vectors the feed reads, and a feed that runs first reads last frame's -
      or nothing at all. Everything below the feed lands on the neural output.
   c. Add-ons tab: "DLSS 5 Neural Rendering" should already be on - the install wrote it
      into ReShade.ini. If the checkbox is off, the settings did not reach the add-on:
      check that ReShade.ini is in the same folder as {consumer_addon}.
   d. Turn the game's own MSAA/SSAA off.
   e. [F2] toggles the effects for A/B comparison.
{host_steps}{mfg_steps}
5. CHECK IT IS ACTUALLY RUNNING:
   Open dlss5-feed.log next to the game .exe. You want:
     - "feature ready ... DLAA"
     - "frame N delivered"
     - "DLSS5_MV_PROVIDER={mv_id}" naming {mv_name}, with its technique enabled
   The overlay's "Motion vectors" section says the same thing, in red when the shader
   and the enabled provider disagree. An MV probe logs the share of non-zero vectors
   every 600 frames - while you move, that must not be 0%.{chicken_check}

6. VERSION PIN:
{version_pin}

7. TROUBLESHOOTING:
   - "It installed but looks like nothing happened": open the Add-ons tab and check
     "DLSS 5 Neural Rendering" is ticked, then the Home tab and check the effects listed
     in section 3 are ticked. With neural rendering off and no effects enabled, a fully
     working install is a DLAA pass and nothing more - which is very close to invisible.
   - Effects list empty: ReShade.fxh / DrawText.fxh missing from reshade-shaders\\Shaders,
     or EffectSearchPaths does not point there. ReShade.log names the failing include.
   - Nothing in the Add-ons tab: the add-on file next to the .exe is the wrong bitness.
     A 32-bit ReShade loads only .addon32; a 64-bit one only .addon64.
   - ReShade.log says "No add-on was registered by '...addon64'. Unloading again":
     the add-on was already in the process and pinned there by an earlier attach, so
     its entry point did not run a second time and nothing registered. ReShade attaches
     once per graphics device, and an emulator makes one every time emulation starts -
     one per boot, one more when a game's launcher executable chain-boots the real one,
     another for every stop and restart. The neural consumer keeps its NGX hooks and
     loses its ReShade
     side, which reads as a consumer stuck at CLAIMING in dlss5-feed.log while frames
     are still delivered and nothing reports an error. Boot the game from the command
     line instead of the front-end - launch_with_dlss5.bat does - point it at the
     executable the game really runs rather than a launcher, and close every other copy
     of the emulator first.{chicken_trouble}
   - Depth wrong / halos: overlay -> Edit global preprocessor definitions ->
     RESHADE_DEPTH_INPUT_IS_REVERSED 0 <-> 1, then Reload.
   - Crash on startup: disable RivaTuner / MSI Afterburner / Discord overlays.
   - Nvidia Smooth Motion and OptiScaler are not compatible with the feeder; turn them off.

8. UNINSTALL:
   Run uninstall_dlss5.bat, or use the Restore button in DLSS5-Anywhere.
================================================================================
"""
