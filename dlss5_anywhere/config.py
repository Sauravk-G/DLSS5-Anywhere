"""
Configuration and constants for DLSS5-Anywhere.
"""

from dataclasses import dataclass, field
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Base Application Paths
APP_NAME = "DLSS5-Anywhere"
APP_VERSION = "1.0.0"

# Who wrote it. Kept here rather than typed into each view, so the nav rail, the
# credits page and the generated build notes cannot drift apart.
APP_AUTHOR = "Saurav.G"
APP_AUTHOR_URL = "https://github.com/Sauravk-G"

ROOT_DIR = Path(__file__).resolve().parent.parent
COMPONENTS_DIR = ROOT_DIR / "components"
USER_SUPPLIED_DIR = COMPONENTS_DIR / "user_supplied"
PROFILES_DIR = ROOT_DIR / "profiles"
BUILDS_DIR = ROOT_DIR / "builds"

# The application's own artwork, which unlike everything under components\ ships with
# the repository. The .ico carries eight sizes; assets/build_icon.py rebuilds it and the
# PNGs from the two SVGs beside it.
ASSETS_DIR = ROOT_DIR / "assets"
APP_ICON = ASSETS_DIR / "dlss5-anywhere.ico"
APP_MARK_PNG = ASSETS_DIR / "dlss5-anywhere-png" / "64.png"

# The user's own list of games, kept out of PROFILES_DIR on purpose: ProfileManager globs
# that directory for *.json and would offer this file as a tuning profile called
# "Custom Games".
LIBRARY_DIR = ROOT_DIR / "library"
CUSTOM_GAMES_FILE = LIBRARY_DIR / "custom_games.json"

# Ensure essential directories exist
for d in [COMPONENTS_DIR, USER_SUPPLIED_DIR, PROFILES_DIR, BUILDS_DIR, LIBRARY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Installation Strategies
STRATEGY_FEEDER_DX11_12 = "feeder_dx11_12"
STRATEGY_DGVOODOO_DX9 = "dgvoodoo_dx9"
STRATEGY_DXVK_DX9 = "dxvk_dx9"
STRATEGY_NATIVE_DLSS = "native_dlss"
STRATEGY_FSR_BRIDGE = "fsr_bridge"
STRATEGY_32BIT_FEEDER = "32bit_feeder"
STRATEGY_REMIX_VULKAN = "remix_vulkan"
STRATEGY_EMULATOR = "emulator"

STRATEGY_DESCRIPTIONS = {
    STRATEGY_FEEDER_DX11_12: (
        "Feeder Mode (DirectX 11 / 12 / Vulkan / OpenGL):\n"
        "Injects ReShade with Add-on support, RenoDX DLSS 5 addon, nvngx_dlssnr.dll runtime, "
        "and DLSS5-Feeder paired with LumeniteFX Kernel motion vector optical flow shader. "
        "Ideal for modern games without native DLSS."
    ),
    STRATEGY_DXVK_DX9: (
        "DXVK + Feeder (DirectX 9 -> Vulkan):\n"
        "Replaces d3d9.dll with DXVK, which translates D3D9 to Vulkan in the game's own process. "
        "The DLSS5-Feeder add-on names this path itself - it feeds '32-bit D3D11, OpenGL and "
        "Vulkan (DXVK) games' - so the frame arrives over the add-on's Vulkan transport. There is "
        "no watermark and no emulated video card: DXVK reports the real GPU, and how much of it "
        "the game is told about is a line in dxvk.conf. ReShade has no local-DLL entry point on "
        "Vulkan and is registered as a layer for the executable instead."
    ),
    STRATEGY_DGVOODOO_DX9: (
        "dgVoodoo2 Wrapper + Feeder (DirectX 9 / DirectX 8):\n"
        "Translates legacy D3D9/D3D8 API calls into DirectX 11 via dgVoodoo2 wrapper DLLs, "
        "enabling ReShade Addon and DLSS 5 Feeder to capture frames and depth buffers."
    ),
    STRATEGY_NATIVE_DLSS: (
        "Direct / Streamline Upgrade Path:\n"
        "Game already includes native DLSS or NVIDIA Streamline. Replaces/supplements "
        "the native pipeline with nvngx_dlssnr.dll neural rendering runtime and RenoDX addon."
    ),
    STRATEGY_FSR_BRIDGE: (
        "FSR / XeSS Bridge Path (OptiScaler):\n"
        "Game includes AMD FSR2/3 or Intel XeSS. Intercepts engine motion vectors and depth buffers "
        "via OptiScaler bridge and feeds them cleanly into DLSS 5 Neural Rendering."
    ),
    STRATEGY_REMIX_VULKAN: (
        "RTX Remix Bridge (DirectX 9 -> Vulkan):\n"
        "Replaces the D3D9 runtime with NVIDIA's RTX Remix bridge. The game keeps a small 32-bit "
        "client (d3d9.dll) and all rendering moves into NvRemixBridge.exe, a 64-bit process running "
        "the dxvk-remix Vulkan renderer. Remix issues real DLSS calls of its own, so the neural "
        "rendering add-on has something to hook and DLSS5-Feeder is not used at all - no .addon32, "
        "no host64 helper, no cross-process fences."
    ),
    STRATEGY_EMULATOR: (
        "Emulator Mode (PCSX2 / RPCS3 / Switch / others):\n"
        "Emulators are 64-bit, so dlss5-feed.addon64 runs inside the emulator itself - no "
        "host64 helper, no cross-process fences. What changes per emulator is the renderer "
        "you pick in its own settings, because that decides where ReShade attaches. A "
        "Vulkan renderer has no local-DLL entry point at all - ReShade is registered as a "
        "Vulkan layer for the executable. On D3D and OpenGL the name ReShade is installed "
        "under is read from the emulator's import table rather than assumed from the API: "
        "PPSSPP renders through D3D11 but never imports dxgi.dll, so a dxgi.dll beside it "
        "would be loaded by nothing and it takes dinput8.dll instead."
    ),
    STRATEGY_32BIT_FEEDER: (
        "32-Bit Host Helper Path:\n"
        "For 32-bit executables (x86), utilizes dgVoodoo2 translation and Feeder host helper "
        "to communicate with 64-bit neural rendering runtime."
    ),
}

STRATEGY_DISPLAY_NAMES = {
    STRATEGY_FEEDER_DX11_12: "Feeder Mode (DX11 / DX12 / Vulkan / OpenGL)",
    STRATEGY_DGVOODOO_DX9: "dgVoodoo2 Wrapper + Feeder (DX9 / DX8)",
    STRATEGY_DXVK_DX9: "DXVK + Feeder (DX9 -> Vulkan, no wrapper watermark)",
    STRATEGY_NATIVE_DLSS: "Direct / Streamline Upgrade Path",
    STRATEGY_FSR_BRIDGE: "FSR / XeSS Bridge Path (OptiScaler)",
    STRATEGY_32BIT_FEEDER: "32-Bit Host Helper Path (x86)",
    STRATEGY_REMIX_VULKAN: "RTX Remix Bridge (DX9 -> Vulkan, 64-bit renderer)",
    STRATEGY_EMULATOR: "Emulator Mode (in-process addon64)",
}

# --------------------------------------------------------------------------------------
# Emulators
#
# Every emulator worth targeting is x64, which removes the whole 32-bit problem: the
# neural stack runs in the emulator process itself. The variable is the renderer the
# user selects inside the emulator, because that decides ReShade's attach point. The
# feeder's addon64 carries four transports - "same-device D3D12", "D3D11->D3D12",
# "Vulkan" and "OpenGL" - so all four renderers are viable; D3D12 is cheapest because
# no interop copy is needed.
#
# `apis` lists what the emulator offers, `recommended` is what to select in its own
# graphics settings, and `caveats` are the things that actually bite in practice.
# --------------------------------------------------------------------------------------

# How ReShade attaches for a given renderer. Vulkan has no local-DLL form.
RESHADE_ATTACH_BY_API = {
    "d3d12": "dxgi.dll",
    "d3d11": "dxgi.dll",
    "opengl": "opengl32.dll",
    "vulkan": None,      # global layer, registered per-executable
}

# ReShade gets into a process by being named after a DLL that process loads, and then
# forwarding the real exports on. Which name works is a property of the executable, not of
# the render API - and the two come apart more often than the API mapping above suggests.
#
# PPSSPP is the case that proved it. It renders through Direct3D 11, so the mapping picked
# dxgi.dll - but PPSSPP does not import dxgi.dll at all. It loads d3d11.dll at runtime,
# and DXGI arrives as a dependency of the System32 copy, resolved against System32. The
# dxgi.dll sitting next to the executable was opened by nobody. There was no error: the
# install was complete and correct and simply never loaded, with no ReShade.log to say so.
#
# A static import cannot fail this way. The loader resolves it at process start, searching
# the application directory, before any code in the program runs. PPSSPP statically imports
# dinput8.dll, which is why ReShade documents that name for exactly this problem.
#
# Order matters below: dinput8.dll first because it is not a graphics API, so standing in
# for it cannot make ReShade attach to a renderer the game is not using. Every name here
# is one our ReShade build actually exports an entry point for.
RESHADE_PROXY_FALLBACK_ORDER = (
    "dinput8.dll",
    "d3d9.dll",
    "d3d11.dll",
    "opengl32.dll",
    "dxgi.dll",
)


def choose_reshade_proxy(
    preferred: Optional[str], imported_dlls: Optional[List[str]]
) -> "tuple[Optional[str], Optional[str]]":
    """Pick the DLL name to install ReShade as, and say why if it is not the obvious one.

    Returns (name, note). `note` is None when nothing surprising happened; otherwise it is
    a sentence for the user explaining the substitution or the risk.

    An empty import list means the PE could not be read, in which case no substitution is
    made - a guess based on no evidence is worse than the API default.
    """
    if preferred is None:
        return None, None  # Vulkan: no local DLL at all

    imports = {name.lower() for name in (imported_dlls or [])}
    if not imports:
        return preferred, None
    if preferred.lower() in imports:
        return preferred, None

    for candidate in RESHADE_PROXY_FALLBACK_ORDER:
        if candidate in imports:
            return candidate, (
                f"This executable does not import {preferred}, so a {preferred} next to it "
                f"would never be loaded - ReShade is installed as {candidate} instead, "
                "which it does import."
            )

    return preferred, (
        f"This executable imports none of the DLL names ReShade can stand in for, so "
        f"{preferred} is a guess. If the ReShade overlay does not open, ReShade never "
        "attached and this game needs a different injection method."
    )


# Every LumeniteFX pass in the shipped defaults carries a history from the previous frame,
# and every one of them reprojects it with the same texture:
#
#     float2 flow = tex2D(Kernel::sFlow, input.uv).xy;
#     float2 rawHistory = tex2D(sPrevAO, input.uv + flow).rg;
#     ao = lerp(ao, prevAO, alpha);          // RTAO: alpha up to confidence * 0.98
#
# So when that flow is zero, `input.uv + flow` is `input.uv` and each pass blends the last
# frame onto this one unreprojected. Lumenite_TRAA holds 0.9 of its history and the two AO
# passes up to 0.98, which is what a second, ghosted copy of the image looks like.
#
# There was briefly a rule here that switched the temporal pass off for emulators. It was
# wrong twice over: it removed Lumenite_TRAA and left the heavier RTAO and LSAO running,
# and it was keyed on the wrong thing entirely. Whether these passes work is a property of
# the flow field, not of the host - measured on one machine, RPCS3 reported 99-100% of its
# motion vectors non-zero and looked excellent, while PPSSPP on the same defaults reported
# 0%. No blanket rule about emulators could have got both of those right.
#
# Nothing here can know which it will be before the game runs, so the defaults are the same
# everywhere and dlss5-feed.log answers it afterwards. See "Emulators" in the README.

API_DISPLAY_NAMES = {
    "d3d12": "Direct3D 12",
    "d3d11": "Direct3D 11",
    "opengl": "OpenGL",
    "vulkan": "Vulkan",
}


@dataclass
class EmulatorProfile:
    id: str
    name: str
    exe_names: List[str]
    apis: List[str]
    recommended: str
    caveats: List[str] = field(default_factory=list)

    # Where the emulator records the renderer it is actually set to, so the install can
    # read it rather than assume it. `recommended` is what this tool would like the
    # renderer to be; these three fields are how it finds out what the renderer *is*.
    #
    # This exists because of a completely silent failure. An install into PPSSPP put a
    # dxgi.dll next to the executable and reported success, having printed "set its
    # renderer to Direct3D 11" as one caveat among four. That PPSSPP was set to Vulkan,
    # which never loads a dxgi.dll - so ReShade never attached, the add-ons never ran, and
    # there was no error anywhere to explain it. The information needed to say so
    # precisely was sitting in the emulator's own ini the whole time.
    #
    # Left empty for emulators whose config layout is not verified against a real file.
    # Saying nothing is correct; guessing at an enum and announcing the wrong renderer
    # with confidence is worse than the caveat it would replace.
    config_paths: List[str] = field(default_factory=list)
    config_key: str = ""
    config_api_values: Dict[str, str] = field(default_factory=dict)

    # The flag that boots a game straight into the renderer, with no front-end in front of
    # it. This is not a convenience: an emulator's front-end creates a graphics device of
    # its own before any game exists, ReShade's layer attaches to it, and every add-on in
    # the folder is loaded into a runtime that is about to be thrown away. An add-on that
    # pins itself in the process is then already resident when the run that matters
    # starts, its entry point does not run a second time, and ReShade registers nothing -
    # measured on RPCS3, where the front-end's "Vulkan Device Enumeration Thread" is the
    # first of three instances in a single session.
    #
    # Empty for emulators whose flag has not been run against a real install. A launcher
    # that passes a flag the emulator does not recognise is worse than one that does not
    # try, because the emulator refuses to start at all.
    direct_boot_flag: str = ""

    # The emulator's own log, and the two lines in it that mean emulation started and
    # stopped. The launcher watches these to hold the neural consumer out of the add-on
    # folder across the first emulation start - see ConfigGenerator.generate_hold_script.
    # Paths are relative to the emulator's folder. Empty for emulators whose log has not
    # been read on a real install: a marker that never matches would leave the consumer
    # held for a whole session.
    log_path: str = ""
    boot_marker: str = ""
    stop_marker: str = ""


# Depth is the make-or-break detail everywhere here. The feeder needs ReShade's depth
# buffer, and an emulator's depth target is produced by emulated GPU code, so ReShade's
# heuristics find it far less reliably than in a native game. dlss5-feed.log reports
# "DLSS5_Depth MISSING" when that happens, and the fix is always the same: the emulator's
# own "disable depth emulation / accurate depth" style option, then ReShade's Depth tab.
_DEPTH_CAVEAT = (
    "Check dlss5-feed.log for 'DLSS5_Depth found'. Emulated depth buffers are the usual "
    "failure point - if it says MISSING, cycle the buffer in ReShade's Depth tab and turn "
    "off any 'fast/approximate depth' option in the emulator."
)

_INTERNAL_RES_CAVEAT = (
    "Set the emulator's internal resolution first and leave it there. DLAA runs on the "
    "presented frame, so changing internal resolution changes what DLSS is reconstructing."
)

EMULATOR_PROFILES: Dict[str, EmulatorProfile] = {
    "pcsx2": EmulatorProfile(
        id="pcsx2",
        name="PCSX2 (PlayStation 2)",
        # EmuDeck ships pcsx2-qtx64.exe, which matched none of the older names - so that
        # install was never recognised as PCSX2 at all and got none of the advice below.
        exe_names=[
            "pcsx2-qt.exe", "pcsx2.exe", "pcsx2x64.exe", "pcsx2x64-avx2.exe",
            "pcsx2-qtx64.exe", "pcsx2-qtx64-avx2.exe",
        ],
        apis=["d3d12", "d3d11", "vulkan", "opengl"],
        recommended="d3d12",
        # PCSX2 keeps this beside the executable in a portable layout (EmuDeck) and under
        # Documents otherwise. Renderer lives in [EmuCore/GS]; the name appears nowhere
        # else in the file as a whole key.
        config_paths=[
            "inis/PCSX2.ini",
            "~/Documents/PCSX2/inis/PCSX2.ini",
        ],
        config_key="Renderer",
        # Both values seen on a real install: Renderer=15, whose feed log then reported
        # "session open (same-device D3D12)", and Renderer=14, which PCSX2 writes when its
        # renderer dropdown is set to Vulkan. Any other number reads back as unknown, and
        # no claim is made about it.
        config_api_values={"14": "vulkan", "15": "d3d12"},
        caveats=[
            "PCSX2 offers all four renderers in Settings -> Graphics -> Renderer. Direct3D 12 "
            "is the best match: the add-on then uses its same-device D3D12 transport with no "
            "interop copy, and ReShade attaches as a plain dxgi.dll.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
    "rpcs3": EmulatorProfile(
        id="rpcs3",
        name="RPCS3 (PlayStation 3)",
        exe_names=["rpcs3.exe"],
        apis=["vulkan", "opengl"],
        recommended="vulkan",
        direct_boot_flag="--no-gui",
        log_path="log/RPCS3.log",
        boot_marker="Emulator::BootGame",
        stop_marker="Stopping emulator",
        caveats=[
            "RPCS3 has no Direct3D renderer at all, so this is a Vulkan install: ReShade has "
            "to be registered as a Vulkan layer for rpcs3.exe, not copied into its folder.",
            # Measured on two sessions of one title, from RPCS3.log. Booting it from the
            # game list boots the game folder, stops emulation four seconds later, and
            # boots the game's own .SELF with direct=1, because that EBOOT.BIN is a
            # launcher for another executable - two Vulkan devices, at +6.5s and +14s in
            # the second session. deep-fried-chicken.log is stamped 0.2s after the first
            # of them and the surviving ReShade.log is the second, which registered
            # nothing. ReShade truncates its log on every attach, so an add-on's own log
            # is the only record of the earlier ones.
            #
            # The front end's own "Vulkan Device Enumeration Thread" instance, a second
            # earlier at +0.9s, is not one of these: if ReShade loaded the add-ons there,
            # Chicken's log would be stamped +1s rather than +6.7s. A surfaceless
            # enumeration instance is not something ReShade attaches to, so skipping the
            # front end is not what matters - skipping the extra emulation start is.
            "RPCS3 creates a Vulkan device every time emulation starts and destroys it every "
            "time emulation stops, and ReShade's layer is loaded, unloaded and re-scanned "
            "with it. Booting is one; a game whose EBOOT.BIN chain-boots a second executable "
            "is two; stopping and restarting from the game list is another each time. An "
            "add-on that pins itself in the process - Deep Fried Chicken does, to keep its "
            "NGX hooks alive - is already loaded by the next attach, so its entry point "
            "never runs again and ReShade logs 'No add-on was registered by "
            "deep-fried-chicken.addon64. Unloading again'. It keeps its hooks, has no "
            "Add-ons tab, and never leaves CLAIMING while the feeder waits for it. Boot the "
            "executable the game really runs - the .SELF, not a launcher EBOOT.BIN - "
            "straight from the command line with --no-gui, which is what "
            "launch_with_dlss5.bat does, and do not stop and restart emulation.",
            "RPCS3's 'Write Depth Buffer' / 'Read Depth Buffer' settings change whether a "
            "depth target exists for ReShade to find.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
    "shadps4": EmulatorProfile(
        id="shadps4",
        name="shadPS4 (PlayStation 4)",
        exe_names=["shadps4.exe"],
        apis=["vulkan"],
        recommended="vulkan",
        caveats=[
            "shadPS4 presents through Vulkan and nothing else - there is no renderer setting "
            "to change, and no dxgi.dll or opengl32.dll is copied into its folder. ReShade is "
            "registered as a Vulkan layer for shadPS4.exe instead.",
            "Install next to shadPS4.exe, not next to a front-end. Launchers like BB Launcher "
            "patch the game's files and then spawn shadPS4.exe as a child process, so the "
            "add-on has to sit beside the emulator; a launcher's Mods folder is for game-data "
            "mods and will ignore all of this. Layer registration is per-executable, so "
            "launching through the front-end still picks it up.",
            "shadPS4 has no internal resolution scaler - resolution and framerate come from "
            "per-title patches (BB Launcher applies Bloodborne's). Settle those first and "
            "leave them alone: DLAA reconstructs the frame it is presented, so changing the "
            "patch changes what DLSS is working from.",
            "shadPS4 moves fast and its Vulkan device creation changes between builds. Confirm "
            "the ReShade overlay opens on [Home] before believing anything downstream - if it "
            "never appears, the layer is not registered for this executable.",
            _DEPTH_CAVEAT,
        ],
    ),
    "ryujinx": EmulatorProfile(
        id="ryujinx",
        name="Ryujinx / Switch emulators",
        exe_names=["ryujinx.exe", "citron.exe", "sudachi.exe", "suyu.exe", "yuzu.exe"],
        apis=["vulkan", "opengl"],
        recommended="vulkan",
        caveats=[
            "Vulkan install: register ReShade as a Vulkan layer for the emulator executable.",
            "Ryujinx creates its Vulkan device through .NET bindings. If dlss5-feed.log says "
            "the Vulkan interop entry points are missing, the add-on's in-process hook did not "
            "catch vkCreateDevice - launch through the staged run-with-feed-layer.bat, which "
            "adds VK_LAYER_feed_vk for that launch only.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
    "dolphin": EmulatorProfile(
        id="dolphin",
        name="Dolphin (GameCube / Wii)",
        exe_names=["dolphin.exe"],
        apis=["d3d12", "d3d11", "vulkan", "opengl"],
        recommended="d3d12",
        caveats=[
            "Pick Direct3D 12 in Graphics -> Backend so ReShade can attach as dxgi.dll.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
    "cemu": EmulatorProfile(
        id="cemu",
        name="Cemu (Wii U)",
        exe_names=["cemu.exe"],
        apis=["vulkan", "opengl"],
        recommended="vulkan",
        caveats=[
            "Vulkan install: register ReShade as a Vulkan layer for Cemu.exe.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
    "duckstation": EmulatorProfile(
        id="duckstation",
        name="DuckStation (PlayStation 1)",
        exe_names=["duckstation-qt-x64-releaseltcg.exe", "duckstation-qt.exe", "duckstation.exe"],
        apis=["d3d12", "d3d11", "vulkan", "opengl"],
        recommended="d3d12",
        caveats=[
            "Pick Direct3D 12 as the renderer so ReShade attaches as dxgi.dll.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
    "xenia": EmulatorProfile(
        id="xenia",
        name="Xenia (Xbox 360)",
        exe_names=["xenia_canary.exe", "xenia.exe"],
        apis=["d3d12", "vulkan"],
        recommended="d3d12",
        caveats=[
            "Xenia's D3D12 backend is the default and the one to keep.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
    "ppsspp": EmulatorProfile(
        id="ppsspp",
        name="PPSSPP (PSP)",
        exe_names=["ppssppwindows64.exe"],
        apis=["d3d11", "vulkan", "opengl"],
        recommended="d3d11",
        # PPSSPP writes "GraphicsBackend = 3 (VULKAN)" - the number is what it reads back,
        # the name in brackets is a comment for humans. Portable installs keep this under
        # the executable; an installed-mode PPSSPP keeps it in %APPDATA%.
        config_paths=[
            "memstick/PSP/SYSTEM/ppsspp.ini",
            "~/AppData/Roaming/PPSSPP/PSP/SYSTEM/ppsspp.ini",
        ],
        config_key="GraphicsBackend",
        config_api_values={"0": "opengl", "1": "d3d9", "2": "d3d11", "3": "vulkan"},
        caveats=[
            "PPSSPP has no D3D12 backend; Direct3D 11 keeps ReShade on the simple dxgi.dll path.",
            _DEPTH_CAVEAT,
            _INTERNAL_RES_CAVEAT,
        ],
    ),
}


# There is deliberately no counterpart that writes this back. An emulator's own settings
# are the user's, and this tool installs DLSS 5 - it does not configure somebody's
# emulator. When the renderer does not match what the install attaches to, that is said
# plainly and left to the user to change.
def read_emulator_backend(
    profile: Optional[EmulatorProfile], game_dir: "Path"
) -> Optional[str]:
    """The renderer this emulator is actually set to, from its own config file.

    Returns an api id from the profile's `config_api_values`, or None when the profile
    declares no config layout, no file is found, or the value is not one this tool knows.
    None means "no claim made" everywhere it is used - the caller keeps whatever it would
    have done without this.
    """
    if profile is None or not profile.config_paths or not profile.config_key:
        return None

    for relative in profile.config_paths:
        candidate = (
            Path(relative).expanduser()
            if relative.startswith("~")
            else Path(game_dir) / relative
        )
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if not sep or key.strip() != profile.config_key:
                continue
            # "3 (VULKAN)" - the value is the leading token; the rest is a comment.
            token = value.strip().split()[0] if value.strip() else ""
            return profile.config_api_values.get(token)
    return None


# Which add-on actually runs the neural pass. The two cannot share a process - both
# intercept the same NGX feature-1 entry points, and Deep Fried Chicken's own wording for
# that is "one or the other, never both" - so this is one choice, not two switches. Alex's
# Toolkit cascades on top of RenoDX, so choosing Chicken retires the cascade and Chicken's
# own layer count takes over.
NEURAL_CONSUMER_CHOICES = {
    "RenoDX + Alex's Toolkit": "renodx",
    "Deep Fried Chicken": "dfc",
}
NEURAL_CONSUMER_LABELS = {value: label for label, value in NEURAL_CONSUMER_CHOICES.items()}

# Chicken runs one to thirty passes over the same frame; its quick start says to start at 1.
DFC_LAYER_RANGE = range(1, 31)


def uses_dfc(profile: Optional[Dict[str, Any]]) -> bool:
    """Whether this profile asks for Deep Fried Chicken instead of renodx-dlss5."""
    merged = {**DEFAULT_PROFILE, **(profile or {})}
    return str(merged.get("neural_consumer", "renodx")).lower() == "dfc"


# The feeder's four transports are not interchangeable to the neural consumer.
#
# Three of them do the neural work on a D3D12 device the feeder created for itself: the
# "Vulkan transport", the plain "D3D12 session" it bridges a D3D11 game onto, and the
# host64 helper a 32-bit game spawns. The fourth, "same-device D3D12", hands DLSS the
# game's own device and copies nothing - which is why it was the recommended renderer
# here. A native Direct3D 12 game gets it, and so does an emulator set to Direct3D 12.
#
# Deep Fried Chicken 1.4.8-alpha cannot use that fourth one. Four installs on one
# machine, same add-on build, from each one's deep-fried-chicken.log:
#
#   RPCS3, PCSX2       Vulkan transport    "FP16 output codec contract cached" -> frames
#   a D3D11 title      D3D11 -> D3D12      "FP16 output codec contract cached" -> frames
#   Prince of Persia   host64 helper       cached -> 4800 neural frames succeeded
#   Resident Evil 4    same-device D3D12   "standalone FP16 codec allocation failed:
#                                           game-output device identity failed
#                                           (0x80070057)", then "standalone neural path
#                                           disabled at FP16 codec acquisition" - and not
#                                           one neural frame for the rest of the session
#
# Everything else about that install looks right, which is the trap: ReShade registers the
# add-on, its panel and its per-pass tabs are all there, the feeder reports interception
# state 2 (ARMED) and goes on delivering DLAA frames. The pass count can be changed in the
# overlay and the log will happily record the change. Only Chicken's own log says the
# neural path was switched off before the first frame.
DFC_BLOCKED_TRANSPORT_API = "d3d12"


def dfc_on_same_device(api: str, profile: Optional[Dict[str, Any]]) -> bool:
    """Whether this build would put Chicken on the transport it cannot run on."""
    return uses_dfc(profile) and str(api or "").lower() == DFC_BLOCKED_TRANSPORT_API


# Multi Frame Generation on Ada, which NVIDIA ships gated to Blackwell.
#
# DLSS 4 generates up to three frames per rendered one; NVIDIA restricts that to RTX 50
# and gives RTX 40 single frame generation. MFGAdaUnlock-RenoDx lifts the gate from
# inside the process and corrects the temporal midpoint so the extra frames carry new
# motion rather than repeating one. It is a ReShade add-on, MIT licensed, and it patches
# only the mapped image at runtime - nothing on disk is modified, and every patch is
# reverted when the add-on unloads.
#
# What it is NOT: a way to add frame generation to a game that has none. It multiplies
# what the game's own DLSS-G already produces, so the game has to ship frame generation
# through Streamline with a 310.x nvngx_dlssg.dll. That is the opposite population to the
# rest of this tool, which exists for games with no DLSS at all.
MFG_ADDON_NAME = "renodx-mfgunlock.addon64"
MFG_SECTION = "RenoDX.MFGUnlock"

# Dynamic MFG is the one setting with a floor under it, from the add-on's own README.
MFG_MIN_DRIVER = "595.41"

# The add-on's own range. 1 would be "no generation", which is what turning it off means.
MFG_COUNT_RANGE = range(2, 7)

# What the multiplier control offers, and what each choice writes to ForceMultiplier.
#
# 0 is the add-on's "let the game decide", and it is the right answer only for a game that
# has a multiplier selector of its own - a DLSS 4 title showing 2x/3x/4x in its menu. Most
# of what an RTX 40 runs shows a single Frame Generation on/off instead, because the driver
# never advertised MFG to it, and there a forced value is the only way to reach 3x at all.
# Leaving it at 0 on such a game installs the add-on and changes nothing.
MFG_MULTIPLIER_CHOICES: Dict[str, int] = {
    "Game's own setting": 0,
    "2x (one generated frame)": 2,
    "3x (two generated frames)": 3,
    "4x (three generated frames)": 4,
    "5x": 5,
    "6x": 6,
}
MFG_MULTIPLIER_LABELS = {value: label for label, value in MFG_MULTIPLIER_CHOICES.items()}


def mfg_settings(profile: Optional[Dict[str, Any]]) -> Tuple[int, int]:
    """Return (force_multiplier, max_count) as a pair that cannot contradict itself.

    MaxCount is the ceiling reported to the runtime and ForceMultiplier is what actually
    runs, so a forced 6x under a ceiling of 4 is a configuration asking for something it
    has just said is unavailable. Whichever way the two are set, the ceiling is raised to
    cover the forced value rather than the forced value being quietly clamped - the number
    the user picked is the one they will look for in the overlay.
    """
    merged = {**DEFAULT_PROFILE, **(profile or {})}

    force = int(merged.get("mfg_force_multiplier", 0) or 0)
    if force and force not in MFG_COUNT_RANGE:
        force = 0

    count = int(merged.get("mfg_max_count", 4))
    if count not in MFG_COUNT_RANGE:
        count = 4
    return force, max(count, force)


def wants_mfg_unlock(profile: Optional[Dict[str, Any]]) -> bool:
    """Whether this profile asks for the RTX 40 multi-frame-generation unlock."""
    merged = {**DEFAULT_PROFILE, **(profile or {})}
    return bool(merged.get("mfg_unlock", False))


def dfc_same_device_target(
    profile: Optional[Dict[str, Any]],
    *,
    emulator_api: str = "",
    primary_api: str = "",
    is_64bit: bool = True,
    translated: bool = False,
) -> bool:
    """Whether a build for this target would land Chicken on the same-device transport.

    One function because two places have to give the same answer: the plan warns about it
    at build time, and the Builder tab has to say it at the moment the consumer is picked,
    which is before any plan exists. A game that reads as fine in one and broken in the
    other is worse than either message on its own.

    `translated` means dgVoodoo2 or DXVK is in front of the game - the feeder then sees
    D3D11 or Vulkan whatever the game was written against, so the game's own API is not
    what decides this. A 32-bit game reaches DLSS through host64, which owns its device.
    """
    if not uses_dfc(profile):
        return False
    if emulator_api:
        return emulator_api.lower() == DFC_BLOCKED_TRANSPORT_API
    return is_64bit and not translated and str(primary_api or "").startswith("DirectX 12")


def find_emulator_profile(exe_name: str) -> Optional[EmulatorProfile]:
    """Return the emulator profile matching this executable name, if any."""
    name = exe_name.lower()
    for profile in EMULATOR_PROFILES.values():
        if name in profile.exe_names:
            return profile
    return None

# Component IDs
COMP_RESHADE = "reshade_addon"
COMP_FEEDER = "dlss5_feeder"
COMP_LUMENITE = "lumenitefx"
COMP_VORT = "vort_shaders"
COMP_IMMERSE = "immerse"
COMP_DGVOODOO = "dgvoodoo2"
COMP_DXVK = "dxvk"
COMP_OPTISCALER = "optiscaler"
COMP_MFG_UNLOCK = "mfg_unlock"
COMP_RESHADE_SHADERS = "reshade_shaders"
COMP_REMIX = "rtx_remix"
COMP_RENODX = "renodx_addon"
COMP_TOOLKIT = "alexs_toolkit"
COMP_DFC = "deep_fried_chicken"
COMP_NVNGX_DLSSNR = "nvngx_dlssnr"
COMP_NVNGX_DLSS = "nvngx_dlss"
COMP_NVNGX_DLSSG = "nvngx_dlssg"

# Our add-ons install into this subfolder of the game, and the generated ReShade.ini
# points AddonPath at it alone.
#
# ReShade searches exactly the directories AddonPath names and loads every .addon64 it
# finds in them, so an AddonPath of ".\" means "load everything sitting next to the game
# executable". A game folder that already holds somebody's ReShade add-on pack therefore
# does not get a two-add-on install: it gets a twenty-four-add-on install, and the
# twenty-two nobody asked for are the ones that break it.
#
# ReShade's own DisabledAddons key cannot be used for this. It matches on the add-on's
# registered *name*, and that name is whatever the add-on passes to register_addon() at
# runtime - "Adjust Depth" for ReShade64-AdjustDepth-By-seri14.addon64, and nothing at all
# derivable from the file for several others. An installer reading files on disk cannot
# know those names before the add-ons have loaded, which is exactly too late.
#
# Giving our add-ons a folder of their own sidesteps the whole problem: ReShade never
# enumerates the game directory, so the other add-ons are never opened, never loaded and
# never listed. Nothing of the user's is moved, renamed or deleted - restoring the old
# behaviour is one line, AddonPath=.\ - which matters, because those files are the user's
# other mods and an installer does not get to throw them away.
ADDON_DIR_NAME = "dlss5-addons"

# The DLSS5-Feeder project detours renodx-dlss5; builds past v4.55 conflict with it.
RENODX_PINNED_VERSION = "v4.55"
RENODX_DISCORD_URL = "https://discord.com/channels/1408098019194310818/1542647972695904317"

# Component Definitions
@dataclass
class ComponentMeta:
    id: str
    name: str
    description: str
    is_user_supplied: bool
    expected_files: List[str]
    target_subdir: str
    download_url: Optional[str] = None
    repo_api_url: Optional[str] = None
    notes: Optional[str] = None

COMPONENTS_REGISTRY: Dict[str, ComponentMeta] = {
    COMP_RESHADE: ComponentMeta(
        id=COMP_RESHADE,
        name="ReShade with Add-on Support",
        description="Modified ReShade runtime allowing third-party binary add-ons (RenoDX, Feeder).",
        is_user_supplied=False,
        expected_files=["ReShade64.dll", "ReShade32.dll"],
        target_subdir="reshade",
        download_url="https://reshade.me/downloads",
        repo_api_url="https://api.github.com/repos/crosire/reshade/releases/latest",
        notes="ReShade 6.8+ with add-on support. Both architectures are required: x86 for 32-bit "
              "games, x64 for 64-bit games and for the host64\ helper folder."
    ),
    COMP_FEEDER: ComponentMeta(
        id=COMP_FEEDER,
        name="DLSS5-Feeder",
        description="ReShade add-on that builds a synthetic DLSS DLAA contract from ReShade depth "
                    "and motion vectors, so the DLSS 5 add-on has something to hook.",
        is_user_supplied=False,
        expected_files=[
            "dlss5-feed.addon64",
            "dlss5-feed.addon32",
            "dlss5-feed-host64.exe",
            "DLSS5_Feed.fx",
            # Fallback for Vulkan hosts whose vkCreateDevice the add-on's in-process hook
            # misses (Ryujinx and other .NET/wrapper-created devices). Launcher-scoped, so
            # it never touches the registry.
            os.path.join("layer-x64", "VkLayer_feed_vk.dll"),
            os.path.join("layer-x64", "VkLayer_feed_vk.json"),
            os.path.join("layer-x64", "run-with-feed-layer.bat"),
        ],
        target_subdir="feeder",
        download_url="https://github.com/jlrouzies-fr/DLSS5-Feeder",
        repo_api_url="https://api.github.com/repos/jlrouzies-fr/DLSS5-Feeder/releases/latest",
        notes="Created by jlrouzies-fr and community contributors."
    ),
    COMP_LUMENITE: ComponentMeta(
        id=COMP_LUMENITE,
        name="LumeniteFX Shader Suite",
        description="LumeniteFX effects for ReShade. lumenite_Kernel.fx is the optical flow motion "
                    "vector provider the feeder needs; the rest of the suite - RTAO, SSSR, the AO "
                    "variants, the anamorphic bloom - is what a profile can run on top of the "
                    "neural output.",
        is_user_supplied=False,
        expected_files=[
            "lumenite_Kernel.fx",
            "lumenite_Compute.fxh",
            "lumenite_bluenoise256.png",
        ],
        target_subdir="lumenitefx",
        download_url="https://github.com/umar-afzaal/LumeniteFX",
        repo_api_url="https://api.github.com/repos/umar-afzaal/LumeniteFX/releases/latest",
        notes="Kernel sets DLSS5_MV_PROVIDER=3 and draws nothing itself; the visible effects are "
              "the other files in the pack, enabled per profile."
    ),
    COMP_VORT: ComponentMeta(
        id=COMP_VORT,
        name="vort_Shaders (Motion Effects)",
        description="Vortigern's ReShade pack. vort_Motion.fx's 'vort_MotionEffects' technique is "
                    "the second motion vector provider the feeder accepts (DLSS5_MV_PROVIDER=2), "
                    "and the one its own documentation recommends: full-resolution optical flow "
                    "against LumeniteFX Kernel's 1/8-resolution estimate.",
        is_user_supplied=False,
        expected_files=[
            os.path.join("Shaders", "vort_Motion.fx"),
            os.path.join("Shaders", "Includes", "vort_MotionVectors.fxh"),
            os.path.join("Textures", "vort_BlueNoise.png"),
        ],
        target_subdir="vort",
        download_url="https://github.com/vortigern11/vort_Shaders",
        notes="Only the motion vectors are wanted here, so the preset pins V_ENABLE_MOT_BLUR=0 "
              "and V_ENABLE_TAA=0: motion blur and a second TAA applied before the DLSS pass "
              "would both be fighting the neural output. Optional - LumeniteFX Kernel is the "
              "default provider and needs no extra download."
    ),
    COMP_DGVOODOO: ComponentMeta(
        id=COMP_DGVOODOO,
        name="dgVoodoo2 Graphics Wrapper",
        description="DirectX 9 / 8 to DirectX 11 wrapper for running ReShade & DLSS 5 on legacy games.",
        is_user_supplied=False,
        expected_files=["D3D9.dll", "D3DImm.dll", "DDraw.dll", "dgVoodoo.conf"],
        target_subdir="dgvoodoo2",
        download_url="https://github.com/dege-diosg/dgVoodoo2/releases",
        repo_api_url="https://api.github.com/repos/dege-diosg/dgVoodoo2/releases/latest",
        notes="Essential for DirectX 9 games like Oblivion, NFS Most Wanted, GTA San Andreas."
    ),
    COMP_DXVK: ComponentMeta(
        id=COMP_DXVK,
        name="DXVK (D3D9 -> Vulkan)",
        description="Translates Direct3D 9 to Vulkan inside the game process. The alternative to "
                    "dgVoodoo2 for legacy games: no watermark, and the video memory the game is "
                    "told about comes from dxvk.conf rather than from an emulated video card.",
        is_user_supplied=False,
        expected_files=[
            os.path.join("x32", "d3d9.dll"),
            os.path.join("x64", "d3d9.dll"),
        ],
        target_subdir="dxvk",
        download_url="https://github.com/doitsujin/dxvk/releases",
        repo_api_url="https://api.github.com/repos/doitsujin/dxvk/releases/latest",
        notes="Only d3d9.dll is used. DXVK's own dxgi.dll/d3d11.dll are NOT installed - ReShade "
              "attaches as a Vulkan layer on this path, and a DXVK dxgi.dll next to the game would "
              "take the name ReShade uses everywhere else."
    ),
    COMP_OPTISCALER: ComponentMeta(
        id=COMP_OPTISCALER,
        name="OptiScaler Upscaler Bridge",
        description="Universal upscaler middleware translating FSR2/XeSS in-engine calls to DLSS.",
        is_user_supplied=False,
        expected_files=["dxgi.dll", "nvngx.dll", "OptiScaler.ini"],
        target_subdir="optiscaler",
        download_url="https://github.com/optiscaler/OptiScaler/releases",
        repo_api_url="https://api.github.com/repos/optiscaler/OptiScaler/releases/latest",
        notes="Extracts clean native engine motion vectors from games with FSR 2/3 or XeSS."
    ),
    COMP_MFG_UNLOCK: ComponentMeta(
        id=COMP_MFG_UNLOCK,
        name="MFG Ada Unlock (multi frame generation on RTX 40)",
        description="ReShade add-on that lifts NVIDIA's RTX 50-only gate on DLSS Multi Frame "
                    "Generation, so a game's own DLSS-G runs at 3x/4x/6x on an Ada card, and "
                    "corrects the temporal midpoint so the generated frames carry new motion.",
        is_user_supplied=False,
        expected_files=[MFG_ADDON_NAME],
        target_subdir="mfg_unlock",
        download_url="https://github.com/mavismmg/MFGAdaUnlock-RenoDx/releases",
        repo_api_url="https://api.github.com/repos/mavismmg/MFGAdaUnlock-RenoDx/releases/latest",
        notes="MIT licensed. Patches in memory only - nothing on disk is modified, and every "
              "patch is reverted when the add-on unloads. Needs an RTX 40 card, driver "
              f"{MFG_MIN_DRIVER}+ for Dynamic MFG, and a game that ALREADY ships DLSS Frame "
              "Generation through Streamline: it multiplies frame generation rather than "
              "adding it, so it does nothing on the no-DLSS games the rest of this tool "
              "targets. NVIDIA has blocked this unlock once already and the mod was updated "
              "to restore it - expect it to break on a driver update.",
    ),
    COMP_REMIX: ComponentMeta(
        id=COMP_REMIX,
        name="RTX Remix Runtime",
        description="NVIDIA's D3D9 bridge: a 32-bit client (d3d9.dll) plus a 64-bit renderer "
                    "(.trex/NvRemixBridge.exe) running dxvk-remix on Vulkan, with its own DLSS "
                    "and Ray Reconstruction runtimes.",
        is_user_supplied=False,
        expected_files=["d3d9.dll", "NvRemixBridge.exe"],
        target_subdir="remix",
        download_url="https://github.com/NVIDIAGameWorks/rtx-remix/releases",
        repo_api_url="https://api.github.com/repos/NVIDIAGameWorks/rtx-remix/releases/latest",
        notes="Unpacked under components/remix/runtime/."
    ),
    COMP_RESHADE_SHADERS: ComponentMeta(
        id=COMP_RESHADE_SHADERS,
        name="ReShade Base Shader Includes",
        description="ReShade.fxh / DrawText.fxh from crosire's shader repository. Both DLSS5_Feed.fx "
                    "and lumenite_Kernel.fx #include these; without them neither effect compiles.",
        is_user_supplied=False,
        expected_files=["ReShade.fxh", "DrawText.fxh"],
        target_subdir="reshade_shaders",
        download_url="https://github.com/crosire/reshade-shaders",
        notes="Fetched from the crosire/reshade-shaders 'slim' branch."
    ),
    COMP_RENODX: ComponentMeta(
        id=COMP_RENODX,
        name="RenoDX DLSS 5 Add-on",
        description="RenoDX binary add-on (renodx-dlss5.addon64) connecting ReShade to Neural Rendering.",
        is_user_supplied=True,
        expected_files=["renodx-dlss5.addon64"],
        target_subdir="user_supplied",
        notes=f"Proprietary / community add-on, distributed through the RenoDX Discord #DLSS5 channel. "
              f"Pin to {RENODX_PINNED_VERSION} - newer builds conflict with DLSS5-Feeder."
    ),
    COMP_DFC: ComponentMeta(
        id=COMP_DFC,
        name="Deep Fried Chicken (alternative neural consumer)",
        description="An add-on that runs one to thirty DLSS Neural Rendering passes after the "
                    "DLSS Super Resolution call. Replaces renodx-dlss5 rather than adding to it, "
                    "and consumes DLSS5-Feeder's synthetic contract on games with no native DLSS - "
                    "which is what makes it work on emulators.",
        is_user_supplied=True,
        expected_files=[
            "deep-fried-chicken.addon64",
            "deep-fried-chicken-nvngx.dll",
            "deep-fried-chicken.cfg",
        ],
        target_subdir="user_supplied",
        notes="Not redistributed: its licence asks that the author's official release link be "
              "shared rather than the archive rehosted. Import all three files from the release "
              "zip, including its own .cfg - that file has 346 lines across thirty layer blocks "
              "and this tool patches the few keys it owns rather than writing it from scratch. "
              "Cannot coexist with renodx-dlss5, renodx-dlss or Alex's Toolkit; the install "
              "removes them when this is selected. Its own guidance is to start at one layer.",
    ),
    COMP_IMMERSE: ComponentMeta(
        id=COMP_IMMERSE,
        name="iMMERSE (Launchpad motion vectors + effect suite)",
        description="Pascal Gilcher's iMMERSE shader suite. Launchpad prepares optical flow and "
                    "normals for the others, and DLSS5_Feed.fx can read its "
                    "Deferred::MotionVectorsTex as DLSS5_MV_PROVIDER=1; MXAO, SOLARIS, SMAA, "
                    "Sharpen and Film Grain are tickable per profile alongside LumeniteFX.",
        is_user_supplied=False,
        expected_files=[
            "Shaders\\MartysMods_LAUNCHPAD.fx",
            "Shaders\\MartysMods_MXAO.fx",
            "Shaders\\MartysMods_SMAA.fx",
            "Shaders\\MartysMods\\mmx_deferred.fxh",
            "Shaders\\MartysMods\\mmx_global.fxh",
            # Launchpad samples the first, MXAO the second, SMAA the third. Without one
            # ReShade logs "Source 'iMMERSE_bluenoise_opt.png' for texture
            # 'V__BlueNoiseJitterTex' was not found in any of the texture search paths" and
            # that effect does not run.
            "Textures\\iMMERSE_bluenoise_opt.png",
            "Textures\\iMMERSE_bluenoise_temporal.png",
            "Textures\\AreaLUT.png",
        ],
        target_subdir="immerse",
        download_url="https://github.com/martymcmodding/iMMERSE",
        notes="Copyright (c) Pascal Gilcher, all rights reserved - so this is downloaded from "
              "the author's repository rather than bundled here. Launchpad pulls in eight "
              "MartysMods/mmx_*.fxh headers and they must keep that folder name; its includes "
              "are written as \".\\MartysMods\\mmx_global.fxh\" and resolve against the "
              "effect search path.",
    ),
    COMP_TOOLKIT: ComponentMeta(
        id=COMP_TOOLKIT,
        name="Alex's Toolkit (DLSS 5 multi-pass cascade)",
        description="Optional add-on that runs the DLSS 5 neural pass two or three times over the "
                    "same frame. DLSS5-Feeder looks for it by name and logs which cascade is active; "
                    "without it DLSS 5 runs a single pass, which is the flat look most first installs "
                    "end up with.",
        is_user_supplied=True,
        expected_files=["alexs-toolkit.addon64"],
        target_subdir="user_supplied",
        notes="Optional community add-on - everything works without it. Chains the neural pass "
              "two or three times over the same frame ('Extreme' and 'Extreme Plus' in "
              "ReShade's Add-ons tab); alexs-toolkit.cfg is re-read live, so the cascade can be "
              "changed without restarting the game. Distribution is informal and this tool does "
              "not name a source: neither the DLSS5-Feeder README nor dlss5feeder.com says where "
              "to get it, and an earlier note here pointed at a RenoDX Discord channel that does "
              "not carry it. The cascade multiplies temporal history, so expect more smearing "
              "behind fast motion and a slower settle after a camera cut - two-pass is the safe "
              "setting, three-pass is for slow, cinematic scenes. Do not use it where the "
              "motion-vector probe in dlss5-feed.log reads near zero: multiplying a history that "
              "cannot be reprojected only multiplies the ghosting."
    ),
    COMP_NVNGX_DLSSNR: ComponentMeta(
        id=COMP_NVNGX_DLSSNR,
        name="NVIDIA Neural Rendering Runtime (nvngx_dlssnr.dll)",
        description="The DLSS 5 Neural Rendering engine runtime DLL (~158MB leaked or community patched).",
        is_user_supplied=True,
        expected_files=["nvngx_dlssnr.dll"],
        target_subdir="user_supplied",
        notes="Proprietary NVIDIA library (v310.8.0.0 or RTX 20/30 FP16 patched builds). Must be user-supplied."
    ),
    COMP_NVNGX_DLSS: ComponentMeta(
        id=COMP_NVNGX_DLSS,
        name="DLSS Super Resolution Runtime (nvngx_dlss.dll)",
        description="A DLSS Super Resolution runtime placed next to the game. Required by DLSS5-Feeder; "
                    "without it the driver copy is used, which is not always present.",
        is_user_supplied=True,
        expected_files=["nvngx_dlss.dll"],
        target_subdir="user_supplied",
        notes="Copy from any DLSS game or DLSS Swapper (https://github.com/beeradmoore/dlss-swapper)."
    ),
    COMP_NVNGX_DLSSG: ComponentMeta(
        id=COMP_NVNGX_DLSSG,
        name="DLSS Frame Generation Runtime (nvngx_dlssg.dll)",
        description="The frame generation runtime, staged beside the executable for the RTX 40 "
                    "multi frame generation unlock. The multi-frame code lives in this file, not "
                    "in the game.",
        is_user_supplied=True,
        expected_files=["nvngx_dlssg.dll"],
        target_subdir="user_supplied",
        notes="Needed at 310.x or newer. A game still on the DLSS 3 snippet (3.5.x) carries no "
              "multi-frame code at all, so the unlock has nothing to raise until this is dropped "
              "in beside the game - which is most titles, because a 40-series was never offered "
              "MFG and had no reason to ship a runtime that does it. Get one from TechPowerUp's "
              "DLSS DLL archive or DLSS Swapper. Only used when multi frame generation is on."
    ),
}

# --------------------------------------------------------------------------------------
# Motion vector providers
#
# DLSS needs to know where every pixel was last frame. A game that never shipped DLSS
# never tells anyone, so the vectors are estimated from the frame itself by a ReShade
# effect and DLSS5_Feed.fx reads the result: it re-declares the chosen provider's OUTPUT
# texture exactly as that provider declares it, so ReShade binds the same resource.
#
# A provider is therefore three things that have to agree, which is why they live in one
# record rather than being spelled out separately at each use:
#
#   * a number - DLSS5_MV_PROVIDER, which decides which texture DLSS5_Feed.fx declares
#   * a technique, which has to be enabled ABOVE DLSS5_Feed in the preset, or the feed
#     samples a texture nothing wrote this frame and DLSS runs on zero motion
#   * the component supplying the .fx file, which the installer has to stage
#
# Providers 0 (the shared texMotionVectors) and 1 (iMMERSE Launchpad) exist in the shader
# as well. They are not offered here because this tool neither ships nor fetches either
# pack, and an option that silently produces zero vectors is worse than no option.
# --------------------------------------------------------------------------------------
MV_PROVIDER_LAUNCHPAD = 1
MV_PROVIDER_VORT = 2
MV_PROVIDER_LUMENITE = 3
DEFAULT_MV_PROVIDER = MV_PROVIDER_LUMENITE


@dataclass(frozen=True)
class MotionVectorProvider:
    id: int
    name: str
    component: str
    technique: str          # "Technique@file.fx" - how a ReShade preset names one
    effect_file: str
    definitions: str        # per-effect preprocessor pins written into the preset
    summary: str


MV_PROVIDERS: Dict[int, MotionVectorProvider] = {
    MV_PROVIDER_LUMENITE: MotionVectorProvider(
        id=MV_PROVIDER_LUMENITE,
        name="LumeniteFX Kernel 2.0",
        component=COMP_LUMENITE,
        technique="Lumenite_Kernel@lumenite_Kernel.fx",
        effect_file="lumenite_Kernel.fx",
        definitions="",
        summary="Pyramidal optical flow with per-level median filtering and previous-frame "
                "seeding, computed at 1/8 resolution and upsampled. Needs no depth buffer, "
                "which is what makes it the safe default on wrapper paths where ReShade "
                "never finds a clean one. Already installed - the RTAO, LSAO, TRAA and SSSR "
                "passes read its flow too.",
    ),
    MV_PROVIDER_LAUNCHPAD: MotionVectorProvider(
        id=MV_PROVIDER_LAUNCHPAD,
        name="iMMERSE Launchpad",
        component=COMP_IMMERSE,
        technique="MartysMods_Launchpad@MartysMods_LAUNCHPAD.fx",
        effect_file="MartysMods_LAUNCHPAD.fx",
        definitions="",
        summary="Pascal Gilcher's optical flow, the most capable provider here and the one "
                "to reach for when the others report an empty flow field - emulators in "
                "particular, where ReShade never finds a usable depth buffer and the probe "
                "in dlss5-feed.log reads '0% non-zero'. Its own label says 'enable and move "
                "to the top', which is where the generated preset puts it. Launchpad only "
                "computes flow when something asks for it during the previous frame, and "
                "DLSS5_Feed.fx files that request itself through Launchpad's IPC buffer, so "
                "no extra effect is needed. Not redistributed - download the component.",
    ),
    MV_PROVIDER_VORT: MotionVectorProvider(
        id=MV_PROVIDER_VORT,
        name="vort_MotionEffects",
        component=COMP_VORT,
        technique="vort_MotionEffects@vort_Motion.fx",
        effect_file="vort_Motion.fx",
        # vort_Motion.fx is a suite: motion vectors, motion blur and a TAA pass. Only the
        # first is wanted. Blur and TAA are off in its own defaults, but a leftover global
        # definition would turn either on, and both would then be applied to the frame
        # *before* DLSS ran on it - blurring and re-resolving the input to the neural pass.
        definitions="V_MV_MODE=1,V_ENABLE_MOT_BLUR=0,V_ENABLE_TAA=0,V_MV_DEBUG=0",
        summary="Full-resolution optical flow, and the provider DLSS5_Feed.fx's own "
                "documentation recommends. Finer vectors than a 1/8-resolution estimate, "
                "at a higher cost per frame. Worth trying when fine detail smears or thin "
                "geometry ghosts under the default provider.",
    ),
}

# Label -> id, for the provider pickers. Insertion order is the order they are offered.
MV_PROVIDER_CHOICES: Dict[str, int] = {
    f"{prov.id} - {prov.name}": prov.id for prov in MV_PROVIDERS.values()
}


def mv_provider_for(profile: Optional[Dict[str, Any]] = None) -> MotionVectorProvider:
    """The motion vector provider a profile selects.

    Anything unrecognised falls back to the default rather than raising: a profile saved
    by a future version, or hand-edited to a provider this tool does not stage, should
    still build - with the provider whose shader is certainly present.
    """
    try:
        key = int((profile or {}).get("mv_provider", DEFAULT_MV_PROVIDER))
    except (TypeError, ValueError):
        key = DEFAULT_MV_PROVIDER
    return MV_PROVIDERS.get(key, MV_PROVIDERS[DEFAULT_MV_PROVIDER])


def mv_provider_label(provider_id: int) -> str:
    """The picker label for a provider id, for setting a combo box from a saved profile."""
    for label, value in MV_PROVIDER_CHOICES.items():
        if value == provider_id:
            return label
    return next(iter(MV_PROVIDER_CHOICES))


# Anti-Cheat Patterns (Dangerous to inject mods into online multiplayer games)
# --------------------------------------------------------------------------------------
# Credits
#
# One source of truth for attribution, rendered by the Credits view and mirrored in
# README.md. This tool installs other people's work and generates configuration for it -
# it renders nothing itself - so the people below are the reason any of it works, and the
# list belongs somewhere the app can show it rather than only in a file on GitHub.
#
# (project, authors, what it does here, url)
# --------------------------------------------------------------------------------------
CREDITS = [
    (
        "ReShade & the Add-on framework",
        "crosire and the ReShade team",
        "The runtime everything else loads into: injection, depth buffer access, the "
        "effect pipeline and the add-on API this tool configures.",
        "https://github.com/crosire/reshade",
    ),
    (
        "DLSS5-Feeder",
        "jlrouzies-fr and contributors",
        "Synthesises the DLAA contract for games that never shipped DLSS, and provides "
        "DLSS5_Feed.fx.",
        "https://github.com/jlrouzies-fr/DLSS5-Feeder",
    ),
    (
        "LumeniteFX",
        "umar-afzaal",
        "Kernel 2.0 optical-flow motion vectors (DLSS5_MV_PROVIDER=3), plus the RTAO, "
        "LSAO, TRAA, SSSR and bloom passes this tool enables.",
        "https://github.com/umar-afzaal/LumeniteFX",
    ),
    (
        "vort_Shaders",
        "Vortigern",
        "vort_MotionEffects - the full-resolution optical flow alternative "
        "(DLSS5_MV_PROVIDER=2), which DLSS5-Feeder names as its recommended provider.",
        "https://github.com/vortigern11/vort_Shaders",
    ),
    (
        "RenoDX",
        "Reno (clshortfuse) and the RenoDX community",
        "renodx-dlss5.addon64 - the add-on that runs neural rendering inference inside "
        "the game process.",
        "https://github.com/clshortfuse/renodx",
    ),
    (
        "dgVoodoo2",
        "Dege",
        "Translates legacy Direct3D 8/9 and Glide to Direct3D 11 so old games can host "
        "ReShade add-ons at all.",
        "http://dege.freeweb.hu",
    ),
    (
        "DXVK",
        "Philip Rebohle and contributors",
        "The other legacy path: D3D9 to Vulkan in the game's own process, with no "
        "watermark and no emulated video card.",
        "https://github.com/doitsujin/dxvk",
    ),
    (
        "OptiScaler",
        "cdozdil and the OptiScaler team",
        "Intercepts real in-engine motion vectors from FSR 2/3 and XeSS titles, which "
        "beats anything a screen-space estimator can produce.",
        "https://github.com/optiscaler/OptiScaler",
    ),
    (
        "iMMERSE / Launchpad",
        "Marty McFly (Pascal Gilcher)",
        "Foundational work on depth linearization, aspect-ratio heuristics and optical "
        "flow that the motion vector path is built on.",
        "https://github.com/martymcmodding",
    ),
    (
        "FP16 kernel research",
        "The community modders and researchers",
        "The patches that brought DLSS 5 neural rendering to RTX 20 and RTX 30 cards.",
        "",
    ),
    (
        "DLSS and neural graphics",
        "NVIDIA Corporation",
        "DLSS, Tensor Core acceleration and the NGX runtimes. No NVIDIA binaries are "
        "redistributed by this tool - you supply your own.",
        "https://www.nvidia.com/en-us/geforce/technologies/dlss/",
    ),
]

# Shown under the credits list and in the generated docs. This tool is an installer and a
# config generator; saying so plainly is both accurate and the licensing position.
CREDITS_NOTE = (
    "DLSS5-Anywhere automates installation and generates configuration. It renders "
    "nothing itself and redistributes no proprietary NVIDIA binaries - renodx-dlss5.addon64 "
    "and the nvngx_*.dll runtimes are supplied by you. Every effect you see on screen is "
    "the work of the projects listed here."
)


# --------------------------------------------------------------------------------------
# Anti-cheat and online-play risk
#
# Everything this tool installs is a DLL injected into the game process: ReShade takes a
# system DLL's name so the loader picks it up first, and the add-ons hook graphics entry
# points at runtime. That is indistinguishable from what a cheat does, and every
# kernel-level anti-cheat treats it accordingly. The consequence lands on the player's
# account, not on the tool, so the detector's job is to be loud before the install rather
# than accurate afterwards.
#
# Signatures are file-name fragments, matched against the game folder and one level of
# subfolders - which is where these install themselves (EasyAntiCheat\, BattlEye\).
# --------------------------------------------------------------------------------------

# Fragments are matched on token boundaries, never as bare substrings - see
# anti_cheat_fragment_matches below for why.
#
# (display name, filename fragments, runs in kernel)
ANTI_CHEAT_SIGNATURES = [
    ("Easy Anti-Cheat", ("easyanticheat", "eac_server", "start_protected_game"), True),
    ("BattlEye", ("battleye", "beservice", "beclient", "bedaisy"), True),
    ("Riot Vanguard", ("vgk.sys", "vgc.exe", "vanguard"), True),
    ("Denuvo Anti-Cheat", ("denuvo", "anticheat_win64"), True),
    ("nProtect GameGuard", ("gameguard", "npggnt", "nprotect"), True),
    ("XIGNCODE3", ("xigncode", "xhunter"), True),
    ("HoYoverse Anti-Cheat", ("mhypbase", "mhyprot", "hoyokprotect"), True),
    # Named in full rather than as "pnkbstr": the suffix here is a letter, not a
    # version number, and the token rule only forgives digits.
    ("PunkBuster", ("punkbuster", "pnkbstra", "pnkbstrb", "pnkbstrk", "pbsvc", "pbcl"), False),
    ("FACEIT Anti-Cheat", ("faceit",), True),
    ("ESEA Client", ("esea",), True),
    ("Treyarch Anti-Cheat", ("t7ac", "codac"), False),
    ("Ricochet", ("ricochet",), True),
]

# What an anti-cheat file can actually be. A driver, a service, a library - not an asset.
# Folders are matched whatever they are called; this gates files only.
ANTI_CHEAT_MODULE_SUFFIXES = frozenset({".exe", ".dll", ".sys", ".des"})

_ANTI_CHEAT_SEPARATORS = re.compile(r"[^a-z0-9]+")


def _anti_cheat_tokens(text: str) -> List[str]:
    return [token for token in _ANTI_CHEAT_SEPARATORS.split(text.lower()) if token]


def anti_cheat_fragment_matches(file_name: str, fragment: str) -> bool:
    """Does this file name really name an anti-cheat file, or merely contain the letters?

    Both sides are split on anything that is not a letter or a digit, and the fragment has
    to line up with whole tokens. A trailing version number is allowed on the last one, so
    "xhunter" still finds xhunter1.sys and "mhyprot" still finds mhyprot2.sys.

    What it no longer does is match inside a token. RPCS3 was reported as shipping Treyarch
    Anti-Cheat because its PPU cache holds

        ppu-qrgfcgxdku16t7acnf5r8j4s4owp-libm4aacdec.sprx

    and "t7ac" is in there. So are "esea", "eac" and "faceit", sooner or later, in any
    folder that stores content-addressed files - which every emulator does. A warning that
    fires on a hash is a warning nobody reads the next time it is right.
    """
    words = _anti_cheat_tokens(fragment)
    tokens = _anti_cheat_tokens(file_name)
    if not words or len(tokens) < len(words):
        return False

    head, last = words[:-1], words[-1]
    for start in range(len(tokens) - len(words) + 1):
        if tokens[start:start + len(head)] != head:
            continue
        tail = tokens[start + len(head)]
        if tail == last or (tail.startswith(last) and tail[len(last):].isdigit()):
            return True
    return False


# Titles where a foreign DLL in the process is known to risk the account, not just a
# crash - competitive shooters, live-service games, and the single-player games whose
# anti-cheat runs anyway (the FromSoftware titles are on this list for exactly that
# reason: people mod them constantly and the bans are real).
COMPETITIVE_MULTIPLAYER_EXES = [
    # Competitive shooters / live service
    "apexlegends.exe", "r5apex.exe", "r5apexdx12.exe",
    "cs2.exe", "csgo.exe", "valorant.exe", "valorant-win64-shipping.exe",
    "fortniteclient-win64-shipping.exe", "destiny2.exe",
    "rainbowsix.exe", "rainbowsixgame.exe", "rainbowsix_vulkan.exe",
    "overwatch.exe", "thefinals.exe", "deltaforce.exe",
    "pubg.exe", "tslgame.exe", "huntgame.exe", "hll.exe", "squad.exe",
    "escapefromtarkov.exe", "eft.exe", "rust.exe", "dayz.exe", "dayz_x64.exe",
    "marvelrivals.exe", "marvelrivals-win64-shipping.exe",
    "deadbydaylight-win64-shipping.exe", "helldivers2.exe",
    "paladins.exe", "smite.exe", "battlebit.exe", "splitgate2.exe",
    # Battlefield / Call of Duty
    "bf2042.exe", "bf1.exe", "bfv.exe", "battlefield.exe",
    "cod.exe", "modernwarfare.exe", "blackopscoldwar.exe", "warzone.exe",
    # Rockstar online
    "gta5.exe", "gtav.exe", "playgtav.exe", "rdr2.exe",
    # HoYoverse / kernel anti-cheat
    "genshinimpact.exe", "yuanshen.exe", "honkaistarrail.exe",
    "zenlesszonezero.exe", "wutheringwaves.exe",
    # Riot
    "leagueclient.exe", "league of legends.exe",
    # FromSoftware - single-player, but EAC runs and bans for injected DLLs
    "eldenring.exe", "nightreign.exe", "armoredcore6.exe",
    "darksouls3.exe", "darksoulsremastered.exe", "sekiro.exe",
    # EA sports / other EAC titles
    "fc24.exe", "fc25.exe", "fifa23.exe", "madden24.exe",
    "starcitizen.exe", "warframe.x64.exe", "thefirstdescendant.exe",
]

# The wording used everywhere the risk is surfaced - GUI, CLI and generated docs - so the
# warning reads the same however the user arrives at it.
ANTI_CHEAT_HEADLINE = (
    "This game is protected by anti-cheat. Injecting ReShade or any DLL mod can be "
    "detected as tampering and get the account banned."
)
ANTI_CHEAT_GUIDANCE = (
    "Anti-cheat cannot tell a graphics mod from a cheat - both are foreign code in the "
    "game's process. Bans are applied to the account by the publisher, are usually "
    "permanent, and are not appealable on the grounds that the DLL was only a shader "
    "injector.\n\n"
    "Only continue if this is a single-player or offline installation you are willing to "
    "risk the account on. Never take this online."
)

# Default profile settings
# These map 1:1 onto keys DLSS5-Feeder actually reads (dlss5-feed.cfg) or onto
# ReShade / dgVoodoo settings. Nothing here is decorative: a knob with no consumer
# is a slider that silently does nothing.
DEFAULT_PROFILE = {
    # Which effect estimates the motion vectors DLSS reconstructs from. See MV_PROVIDERS.
    "mv_provider": MV_PROVIDER_LUMENITE,
    "depth_reversed": False,
    "depth_upsidedown": False,
    # dlss5-feed.cfg
    "feed_mode": 2,               # 0 inert, 1 transport test, 2 full DLSS path
    # 50-100%. Honoured on the D3D11 transport at either bitness; the add-on fixes it at
    # 100% on the OpenGL and Vulkan transports ("DLSS runs at the game's native resolution
    # there"), which includes a D3D9 game reaching Vulkan through DXVK. Below 100 this is
    # the lever that buys frames, so the D3D9 path is chosen to match it - see
    # StrategyEngine.build_plan.
    "feed_work_resolution": 100,
    "feed_preset": 0,             # 0 default, 5/6 legacy CNN E/F, 10/11 transformer J/K
    "feed_mv_scale_x": 1.0,
    "feed_mv_scale_y": 1.0,
    "feed_hdr": -1,               # -1 auto, 0 force SDR, 1 force HDR
    # 32-bit games: the helper's window. Hidden by default - it is a normal window that
    # takes focus when it appears, and an engine that blocks its update loop on lost focus
    # (GTA IV does, hence its own -noBlockOnLostFocus switch) then stops advancing while
    # its present loop keeps running, which looks exactly like a frozen loading screen.
    # Its settings are mirrored on the overlay's DLSS 5 Feed page anyway.
    "feed_host_window": False,
    # RAGE games (GTA IV): write a commandline.txt containing -norestrictions, which lifts
    # the clamp the engine puts on its graphics settings when it does not like the video
    # memory the device reports. Behind any translation layer it does not like it, so this
    # is on: the clamp is the "why is it capped" people actually run into.
    # -availablevidmem is still never written. That one changes the streamer's memory
    # budget, and a wrong value hangs the game on its loading screen while the renderer
    # keeps presenting frames - which does not even look like a hang.
    "rage_write_commandline": True,
    # ------------------------------------------------------------------
    # How a D3D9/D3D8 game reaches an API the feeder supports.
    #
    # The add-on has no D3D9 code path at all - its own text says it feeds "32-bit D3D11,
    # OpenGL and Vulkan (DXVK) games" - so something has to translate first. Two options:
    #
    #   "dxvk"     d3d9.dll -> Vulkan, in the game's own process. No watermark, and the
    #              video memory the game is told about is a line in dxvk.conf instead of
    #              whatever an emulated video card claims. ReShade attaches as a Vulkan
    #              layer rather than as a local DLL.
    #   "dgvoodoo" d3d9.dll -> D3D11 through dgVoodoo2. The older, more forgiving option
    #              for very early D3D8/D3D9 titles, and the one to fall back to when a
    #              game will not start under DXVK.
    # ------------------------------------------------------------------
    "d3d9_translation": "dxvk",

    # dxvk.conf. Only keys set here are written: DXVK ships per-game profiles of its own,
    # and a config file that contradicts them is worse than no config file.
    #   d3d9.maxAvailableMemory - what GetAvailableTextureMem reports, in MB. This is the
    #     number an old engine sizes its texture budget and graphics presets from, and the
    #     reason a game can come up claiming 512 MB on a 12 GB card. DXVK's own default is
    #     4096.
    #   d3d9.memoryTrackTest / d3d9.textureMemory - left at DXVK's defaults unless set to
    #     something other than None, because DXVK already special-cases the games that
    #     need them.
    #
    # Reporting video memory under DXVK takes two keys, not one:
    #
    #   GetAvailableTextureMem = min(deviceMemory + systemMemory,
    #                                d3d9.maxAvailableMemory) - 8 MB
    #
    # so d3d9.maxAvailableMemory is a CEILING. It can only ever lower the figure - on its
    # own it cannot raise a game above what the adapter reports. What the adapter reports
    # is capped by dxgi.maxDeviceMemory, and DXVK ships a built-in profile that pins that
    # to 128 MB for GTAIV.exe and EFLC.exe, deliberately, to work around GTA IV's own
    # handling of large values. A game showing a few hundred MB on a 12 GB card is that
    # profile, not a bug.
    #
    # The user's dxvk.conf does win over the built-in profile - DXVK loads it first and
    # merges the profile with insert(), which does not overwrite - but only if the file is
    # found at all. See dxvk_config_file_note in config_gen: DXVK reads $PWD/dxvk.conf,
    # not the file next to the executable.
    "dxvk_max_available_memory_mb": 4096,
    "dxvk_max_device_memory_mb": None,
    "dxvk_memory_track_test": None,
    "dxvk_texture_memory_mb": None,

    "dgvoodoo_resolution_scaling": "unforced",
    "dgvoodoo_vram_mb": 1024,          # 256 MB default crashes; 2GB breaks old engines
    "dgvoodoo_video_card": "internal3D",
    # The watermark is the only proof dgVoodoo is actually loaded, so it stays on - but it
    # now clears itself after a few seconds instead of sitting in the corner for the whole
    # session. Set the duration to 0 for the old always-on behaviour, or turn the watermark
    # off entirely once the pipeline is verified.
    "dgvoodoo_watermark": True,
    "dgvoodoo_watermark_seconds": 15,
    "dgvoodoo_antialiasing": "appdriven",
    "reshade_effect_toggle_key": "113",  # F2
    "reshade_overlay_key": "36",         # Home

    # ------------------------------------------------------------------
    # DLSS 5 neural rendering look - [RenoDX.DLSS5] in ReShade.ini
    #
    # renodx-dlss5.addon64 keeps its settings in ReShade's own config (it imports
    # ReShadeGetConfigValue / ReShadeSetConfigValue, not a private ini file), under the
    # section "RenoDX.DLSS5". Nothing here used to be written at all, so every install
    # started at the add-on's own defaults with the master switch off: the feeder built a
    # perfectly good DLAA contract and then nothing consumed it, which is exactly the
    # "installed fine, looks like almost nothing" result. Seeding the section turns the
    # neural pass on for the first launch instead of leaving it to a checkbox in the
    # Add-ons tab that most people never find.
    #
    # ReShade rewrites this file when the user changes a setting in the overlay, so these
    # are starting values, not a lock - tune in-game, and the tuned values survive.
    # ------------------------------------------------------------------
    "nr_enabled": True,             # NeuralUplift  - "Enable DLSS Neural Rendering"
    "nr_upscaling": False,          # NREnableUpscaling - the feeder publishes a 1:1 DLAA
                                    #   contract ("upscaling off"), so leave this off on
                                    #   feeder paths; it is for games with real DLSS.
    "nr_preset": 0,                 # NRPreset  0 Default, 1-3 Preset #1..#3
    "nr_style": 0,                  # NRStyle   0 Natural, 1 Cinematic
    "nr_intensity": 1.0,            # NRIntensity      - overall strength of the neural pass
    "nr_local_tone": 1.0,           # NRLocalTone      - local tone reconstruction
    "nr_local_structure": 1.0,      # NRLocalStructure - detail/structure recovery
    "nr_skin_structure": 1.0,       # NRSkinStructure  - the same, restrained on skin
    "nr_color_strength": 1.0,       # NRColorStrength
    "nr_auto_mask": True,           # NRAutoMask
    "nr_ui_correction": True,       # NRUICorrection   - keeps the HUD out of the neural pass

    # ------------------------------------------------------------------
    # Multi Frame Generation on an RTX 40 card. See MFG_ADDON_NAME.
    #
    # Off by default, and not because it is unsafe: it applies to a different kind of
    # game than the rest of this tool. Everything else here is for a game with no DLSS at
    # all, where the feeder synthesises the contract. This multiplies frame generation a
    # game already has, so on the titles this tool is usually pointed at there is nothing
    # for it to multiply. Turning it on is a deliberate act for a game that ships DLSS-G.
    # ------------------------------------------------------------------
    "mfg_unlock": False,            # Enabled            - deploy the add-on at all
    "mfg_max_count": 4,             # MaxCount           - highest multiplier offered, 2-6
    "mfg_force_multiplier": 0,      # ForceMultiplier    - 0 leaves the game's own choice
    "mfg_dynamic": False,           # DynamicMFG         - needs driver 595.41+
    "mfg_hdr_compat": 2,            # HDRCompatibilityMode

    # Which LumeniteFX effects run after the neural pass. The installer already stages the
    # whole LumeniteFX suite into reshade-shaders/Shaders - RTAO, SSSR, the AO variants and
    # the bloom - and until now the preset enabled none of it. Keys are from
    # config_gen.VISIBLE_EFFECTS, which covers both LumeniteFX and iMMERSE; order in this
    # list is the order they run in. The key is still called "lumenite_effects" so profiles
    # saved before iMMERSE was offered here still load.
    #
    # SSSR is staged but deliberately not on: its reflections are glitchy on enough of the
    # stack (wrapper paths especially) that shipping it on by default made healthy builds
    # look broken. Tick "Screen-space reflections" per profile if a game handles it. The
    # AO passes run first, TRAA last, so the AA pass resolves what the AO wrote.
    "lumenite_effects": ["rtao", "lsao", "traa"],

    # Which add-on actually runs the neural passes. Two exist and they cannot coexist:
    # each intercepts the same NGX feature-1 entry points, and Chicken's own words for the
    # situation are "one or the other, never both". renodx-dlss5 is the default because it
    # is what the rest of this tool is built around, and because Alex's Toolkit only
    # cascades on top of RenoDX.
    "neural_consumer": "renodx",     # "renodx" | "dfc"

    # Deep Fried Chicken runs one to thirty neural passes; its own quick start says to
    # begin at one. This is written into the layers key of its config.
    "dfc_layers": 1,

    # Alex's Toolkit cascade (alexs-toolkit.cfg), only meaningful when the add-on is present.
    #
    # DLSS5-Feeder's DEPLOY-DEV.md documents three keys, but v0.9.0-beta ships a cfg with
    # eight. Writing only the documented three would delete the other five from a file the
    # add-on owns, so all eight are written and the undocumented ones carry the values the
    # add-on itself ships. They are exposed here rather than hardcoded because they are the
    # user's to change - this tool has no basis for a better value, and no idea what most
    # of them do beyond what their names suggest.
    "toolkit_enabled": True,
    "toolkit_two_pass": True,
    "toolkit_three_pass": False,
    "toolkit_adaptive_extreme": False,
    "toolkit_stage_b_blend": 1.00,
    "toolkit_stage_c_blend": 1.00,
    "toolkit_texture_boost": False,
    "toolkit_texture_boost_strength": 1.00,
}
