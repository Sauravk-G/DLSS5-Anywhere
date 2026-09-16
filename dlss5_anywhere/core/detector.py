"""
Game Executable & Environment Detector for DLSS5-Anywhere.
Inspects PE binary headers, import tables, runtime DLLs, upscaler presence, and anti-cheat indicators.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import struct
from typing import Dict, List, Optional, Set, Tuple

try:
    import pefile
    HAVE_PEFILE = True
except ImportError:
    HAVE_PEFILE = False

from ..config import (
    ADDON_DIR_NAME,
    ANTI_CHEAT_HEADLINE,
    ANTI_CHEAT_MODULE_SUFFIXES,
    ANTI_CHEAT_SIGNATURES,
    anti_cheat_fragment_matches,
    API_DISPLAY_NAMES,
    COMPETITIVE_MULTIPLAYER_EXES,
    EmulatorProfile,
    STRATEGY_32BIT_FEEDER,
    STRATEGY_DGVOODOO_DX9,
    STRATEGY_DXVK_DX9,
    STRATEGY_EMULATOR,
    STRATEGY_FEEDER_DX11_12,
    STRATEGY_FSR_BRIDGE,
    STRATEGY_NATIVE_DLSS,
    find_emulator_profile,
    read_emulator_backend,
)


# --------------------------------------------------------------------------------------
# Rendering API evidence
#
# The first version of this detector read the import table and the game folder's DLL
# names and treated both as proof. Neither survives contact with a real library. A modern
# engine links dxgi.dll and reaches D3D12 through LoadLibrary, so the import table names
# the wrong API; and a graphics-named DLL sitting in a game folder is almost never
# something the game shipped - it is ReShade's dxgi.dll, DXVK's d3d9.dll, dgVoodoo's
# D3D9.dll. Counting those as the game's renderer is what reported "DirectX 11" for
# Vulkan-only titles and "DirectX 9" for anything this tool had already modded.
#
# Every signal is now weighted by how directly it proves the game calls that API, and the
# evidence trail is kept so the report can show its working instead of asserting.
# --------------------------------------------------------------------------------------

W_IMPORT = 100      # named in the import table - the game links it, no ambiguity
W_DELAY = 90        # delay-load import: still a hard link, resolved on first use
W_AGILITY = 100     # a D3D12 Agility SDK redist is useless without a D3D12 renderer,
                    # so shipping one proves as much as a hard import - and more than a
                    # d3d11 import, which a D3D12 game commonly keeps for video decode
W_RHI_CONFIG = 80   # the engine's own config file names the RHI it starts in
W_SYMBOL = 55       # an API entry point in the binary: the LoadLibrary path
W_DLL_STRING = 45   # the DLL's name as a string: weaker, could be a log line
W_SIBLING = 30      # a plausible sibling DLL that is not a known wrapper
W_ENGINE = 15       # the engine's usual default, for breaking ties only

D3D12, D3D11, VULKAN, D3D9, D3D8, OPENGL = (
    "DirectX 12", "DirectX 11", "Vulkan", "DirectX 9", "DirectX 8", "OpenGL",
)

# Import-table names. d3d10* maps to DirectX 11 deliberately: it is the same DXGI-era
# stack, ReShade attaches identically, and no game ships a D3D10-only renderer any more.
API_IMPORT_MAP = {
    "d3d12.dll": D3D12, "d3d12core.dll": D3D12,
    "d3d11.dll": D3D11, "d3d11_1.dll": D3D11, "d3d11_2.dll": D3D11,
    "d3d10.dll": D3D11, "d3d10_1.dll": D3D11, "d3d10core.dll": D3D11,
    "vulkan-1.dll": VULKAN, "vulkan.dll": VULKAN,
    "d3d9.dll": D3D9, "d3d9x.dll": D3D9, "d3dx9_43.dll": D3D9,
    "d3d8.dll": D3D8, "ddraw.dll": D3D8,
    "opengl32.dll": OPENGL,
}

# Entry points beat DLL names: a game that pulls d3d12.dll in with LoadLibrary still has
# to name D3D12CreateDevice to get a device out of it.
API_SYMBOL_MARKERS = [
    (b"D3D12CreateDevice", D3D12, W_SYMBOL),
    (b"D3D12GetDebugInterface", D3D12, W_SYMBOL),
    (b"d3d12.dll", D3D12, W_DLL_STRING),
    (b"D3D12Core.dll", D3D12, W_DLL_STRING),
    (b"D3D11CreateDeviceAndSwapChain", D3D11, W_SYMBOL),
    (b"D3D11CreateDevice", D3D11, W_SYMBOL),
    (b"d3d11.dll", D3D11, W_DLL_STRING),
    (b"vkCreateInstance", VULKAN, W_SYMBOL),
    (b"vkGetInstanceProcAddr", VULKAN, W_SYMBOL),
    (b"vkCreateSwapchainKHR", VULKAN, W_SYMBOL),
    (b"vulkan-1.dll", VULKAN, W_DLL_STRING),
    (b"Direct3DCreate9Ex", D3D9, W_SYMBOL),
    (b"Direct3DCreate9", D3D9, W_SYMBOL),
    (b"d3d9.dll", D3D9, W_DLL_STRING),
    (b"Direct3DCreate8", D3D8, W_SYMBOL),
    (b"DirectDrawCreateEx", D3D8, W_SYMBOL),
    (b"wglCreateContext", OPENGL, W_SYMBOL),
    (b"wglMakeCurrent", OPENGL, W_SYMBOL),
    (b"opengl32.dll", OPENGL, W_DLL_STRING),
]

# A DLL with one of these names in a game folder is an injection point, not a renderer:
# Windows resolves it from the application directory before system32, which is the whole
# reason wrappers and overlays take these names.
INJECTABLE_DLL_NAMES = {
    "dxgi.dll", "d3d8.dll", "d3d9.dll", "d3d10.dll", "d3d10core.dll", "d3d11.dll",
    "d3d12.dll", "opengl32.dll", "ddraw.dll", "dinput8.dll", "winmm.dll", "version.dll",
}

# Content markers that identify such a DLL as a known wrapper or injector. Cheap to look
# for and far more reliable than the file name, which is by definition borrowed.
WRAPPER_MARKERS = [
    (b"DXVK", "DXVK"),
    (b"dgVoodoo", "dgVoodoo2"),
    (b"ReShade", "ReShade"),
    (b"OptiScaler", "OptiScaler"),
    (b"SpecialK", "Special K"),
    (b"Wine builtin", "WineD3D"),
]

# Executables this tool (or a wrapper it installs) puts in a game folder. They are not
# the game, whatever they import, so the launcher check has to look past them.
COMPANION_EXECUTABLES = {
    "dgvoodoocpl.exe",
    "dlss5-feed-host64.exe",
    "nvremixlauncher32.exe",
    "nvremixbridge.exe",
    "reshade_setup_addon.exe",
}

# An emulator profile stores its renderer as a short id; the detector speaks in labels.
EMULATOR_API_LABELS = {"d3d12": D3D12, "d3d11": D3D11, "vulkan": VULKAN, "opengl": OPENGL}

# What each engine starts in when nothing else says otherwise. Only ever a tie-breaker.
ENGINE_DEFAULT_API = {
    "Unreal Engine 5": D3D12,
    "Unreal Engine 4": D3D11,
    "Unity": D3D11,
    "Source Engine": D3D9,
    "RAGE": D3D9,
    "id Tech": VULKAN,
    "Creation Engine": D3D11,
}


@dataclass
class GameAnalysis:
    """Complete diagnostic report for a selected game executable and directory."""
    exe_path: Path
    game_dir: Path
    exe_name: str
    file_size_bytes: int
    architecture: str  # 'x64' or 'x86'
    is_64bit: bool
    
    # Detected Graphics APIs
    detected_apis: List[str] = field(default_factory=list)
    primary_api: str = "Unknown"

    # Why the detector settled on primary_api, and how sure it is. A report that names an
    # API without saying what it read is impossible to argue with when it is wrong, and
    # the API decides where ReShade attaches - so a wrong one wastes a whole install.
    api_evidence: Dict[str, List[str]] = field(default_factory=dict)
    api_confidence: str = "low"          # high | medium | low
    detected_wrappers: List[str] = field(default_factory=list)
    
    # Native Upscaler Support (Original game code)
    has_native_dlss: bool = False
    has_native_streamline: bool = False
    has_native_fsr: bool = False
    has_native_xess: bool = False
    # Whether the game ships DLSS Frame Generation - nvngx_dlssg.dll, or Streamline's
    # sl.dlss_g. Separate from has_native_dlss, which is about the upscaler: the RTX 40
    # multi-frame-generation unlock multiplies frame generation that already exists, so
    # this is the one signal that says whether it has anything to work on.
    has_frame_generation: bool = False
    detected_upscaler_files: List[str] = field(default_factory=list)
    
    # Existing Mod Status
    has_existing_reshade: bool = False
    has_existing_dlss5_mod: bool = False
    existing_mod_files: List[str] = field(default_factory=list)
    # ReShade add-ons already in the game folder that this tool did not put there. They
    # load automatically - ReShade's AddonPath is the game directory - so they are part of
    # the install whether or not anyone chose them.
    foreign_addons: List[str] = field(default_factory=list)
    
    # Siblings that do import a graphics API when the selected .exe does not - the
    # renderer behind a launcher. Kept rather than only mentioned in an advisory, because
    # ReShade's Vulkan layer is enrolled per executable and enrolling a launcher enrols
    # something that never draws.
    renderer_candidates: List[str] = field(default_factory=list)

    # Engine & Anti-Cheat Heuristics
    detected_engine: str = "Unknown Engine"
    anti_cheat_detected: bool = False
    anti_cheat_warnings: List[str] = field(default_factory=list)
    is_multiplayer_risk: bool = False

    # What was found, and how bad it is. "online_competitive" means the title itself is
    # known to ban for injected DLLs; "anti_cheat" means a protection system is installed
    # beside the game. Either one has to stop a one-click install and ask.
    anti_cheat_names: List[str] = field(default_factory=list)
    risk_level: str = "none"             # none | anti_cheat | online_competitive

    # Non-fatal findings worth showing before an install (wrong exe picked, etc.)
    advisories: List[str] = field(default_factory=list)

    # RAGE-engine games (GTA IV, EFLC, Max Payne 3) read a commandline.txt next to the
    # executable, and that is the only way to lift their video-memory restrictions.
    supports_rage_commandline: bool = False

    # Emulators are handled as their own class of target: always x64, and the renderer
    # is a setting inside the emulator rather than a property of the binary, so the
    # install has to be planned around the renderer the user selects.
    emulator: Optional[EmulatorProfile] = None
    emulator_api: str = ""
    
    # Recommended Strategy
    recommended_strategy: str = STRATEGY_FEEDER_DX11_12
    strategy_rationale: str = ""
    
    # Detected PE Imports
    imported_dlls: List[str] = field(default_factory=list)


# Where an install records what it wrote. These mirror ModInstaller's own constants
# rather than importing them: installer.py imports this module, and importing back would
# close the loop.
BACKUP_DIR_NAME = ".dlss5_backup"
MANIFEST_FILE_NAME = "dlss5_manifest.json"


class GameDetector:
    """High-precision game executable and environment analyzer."""

    @classmethod
    def analyze(cls, exe_path: str | Path) -> GameAnalysis:
        """Run full diagnostic analysis on target game executable and its containing folder."""
        exe = Path(exe_path).resolve()
        if not exe.exists() or not exe.is_file():
            raise FileNotFoundError(f"Target executable does not exist: {exe}")

        game_dir = exe.parent
        file_size = exe.stat().st_size
        exe_name = exe.name.lower()

        # 1. Architecture Detection (x64 vs x86) & PE Imports
        is_64bit, imported_dlls, delay_imports = cls._inspect_pe_binary(exe)
        arch = "x64" if is_64bit else "x86"

        # 2. Existing Mod State Detection (Check this early to distinguish mod vs native)
        has_reshade, has_dlss5_mod, mod_files = cls._detect_existing_mods(game_dir)
        # Only the manifest decides what is ours. mod_files deliberately holds every
        # add-on in the folder - it is the "do not read this as the game's own" list - so
        # passing it here marked all of them as ours and nothing was ever foreign.
        foreign_addons = cls._detect_foreign_addons(
            game_dir, {n.lower() for n in cls._files_this_tool_installed(game_dir)}
        )

        # 3. Engine, which the API detector uses to break ties and to know where the
        #    engine keeps the config that names its RHI.
        engine = cls._detect_game_engine(exe, game_dir)

        # 4. Rendering API Detection. Everything found so far feeds it: the mod files so
        #    this tool's own dxgi.dll is not mistaken for the game's renderer, and the
        #    engine for the cases where two renderers are linked and only one is used.
        (
            detected_apis,
            primary_api,
            api_evidence,
            api_confidence,
            detected_wrappers,
        ) = cls._detect_rendering_apis(
            exe, game_dir, imported_dlls, delay_imports, mod_files, engine
        )

        # 5. Native Upscaler & Tech Detection (Filtering out mod files)
        (
            has_native_dlss,
            has_native_streamline,
            has_native_fsr,
            has_native_xess,
            upscaler_files,
            loose_nvngx,
            has_frame_generation,
        ) = cls._detect_upscalers(game_dir, imported_dlls, mod_files)

        # 6. Anti-Cheat Heuristics
        has_ac, ac_warnings, is_mp_risk, ac_names, risk_level = (
            cls._detect_anticheat_and_risks(exe_name, game_dir)
        )

        # 6. Launcher check - modding the launcher instead of the renderer is a silent no-op
        advisories, renderer_candidates = cls._check_for_launcher(exe, game_dir, imported_dlls)
        advisories.extend(cls._check_for_better_bitness(exe, is_64bit))
        advisories.extend(cls._check_for_global_reshade())
        supports_rage_commandline = cls._supports_rage_commandline(exe)

        # Say what was found and what was made of it, rather than quietly acting on it.
        # See _detect_upscalers for why a loose runtime does not decide the strategy.
        if loose_nvngx:
            advisories.append(
                f"{', '.join(sorted(set(loose_nvngx)))} is in this folder, with no Streamline "
                "files and no NGX import in the executable to say the game itself uses it. A "
                "runtime swapped in by hand looks exactly like one a game shipped, so this is "
                "planned as a game without DLSS: the feeder synthesises the contract, which "
                "works either way. If this game does have DLSS in its own settings, choose the "
                "Direct / Streamline Upgrade Path instead - it lets the engine provide the "
                "motion vectors."
            )

        # 7. Emulator check. An emulator's imports say almost nothing useful - it links
        #    every backend it was built with and picks one at runtime from its own
        #    settings - so the profile, not the PE, decides the renderer.
        emulator = find_emulator_profile(exe.name)
        emulator_api = emulator.recommended if emulator else ""

        # What the emulator is actually set to, when it will tell us. An install that
        # attaches somewhere the emulator does not render is not a broken install with an
        # error in it - it is a folder full of files that nothing ever loads, and no log
        # anywhere says why. This turns that into a sentence.
        # What it is actually set to wins over what this tool would have chosen. Reading
        # the renderer and then installing for the recommendation anyway is how a PCSX2 set
        # to Vulkan got a dxgi.dll it could never load: correct files, nothing loading them,
        # and an advisory telling the user to change a setting rather than an install that
        # matched it. The emulator's configuration belongs to the user; this follows it.
        configured_api = read_emulator_backend(emulator, game_dir)
        if emulator and configured_api and configured_api in emulator.apis:
            emulator_api = configured_api

        if emulator:
            api_label = API_DISPLAY_NAMES.get(emulator_api, emulator_api)
            if configured_api and configured_api not in emulator.apis:
                current = API_DISPLAY_NAMES.get(configured_api, configured_api)
                advisories.append(
                    f"{emulator.name} is set to {current}, which this tool has no install "
                    f"path for. Installing for {api_label} instead - if the overlay never "
                    "appears, that is why."
                )
            elif configured_api:
                current = API_DISPLAY_NAMES.get(configured_api, configured_api)
                note = (
                    f"{emulator.name} is set to {current}, and this install attaches through "
                    f"{current} to match."
                )
                if configured_api != emulator.recommended:
                    preferred = API_DISPLAY_NAMES.get(emulator.recommended, emulator.recommended)
                    note += (
                        f" This tool would otherwise have picked {preferred}; the renderer is "
                        "yours to choose and the install follows it. Change it in the emulator "
                        "and install again - the attach point changes with it."
                    )
                advisories.append(note)
            elif len(emulator.apis) > 1:
                advisories.append(
                    f"{emulator.name} detected. Set its renderer to {api_label} before "
                    "installing - the renderer decides where ReShade attaches, and changing it "
                    "afterwards silently disables the whole stack."
                )
            else:
                # A single-backend emulator has no renderer to set, and telling someone to go
                # and set one sends them looking through settings for a dropdown that is not
                # there. The attach point is still fixed - it was just never a choice.
                advisories.append(
                    f"{emulator.name} detected. It presents through {api_label} only, so there "
                    "is no renderer to choose - the attach point is fixed."
                )

        # An emulator picks its renderer at runtime from its own settings, and its binary
        # links every backend it was built with - shadPS4 carries D3D9, D3D11, D3D12 and
        # OpenGL strings while presenting only through Vulkan. Reporting what the PE
        # contains would be actively misleading here, so the profile wins outright.
        if emulator and emulator_api:
            forced = EMULATOR_API_LABELS.get(emulator_api, emulator_api)
            primary_api = forced
            api_confidence = "high"
            detected_apis = [
                EMULATOR_API_LABELS.get(a, a) for a in emulator.apis
            ]
            api_evidence = {
                forced: [
                    f"{emulator.name} presents through {forced}; its renderer is a setting "
                    "inside the emulator, so the binary's imports say nothing useful here"
                ]
            }

        # 8. Strategy Selection Matrix
        strategy, rationale = cls._determine_strategy(
            is_64bit=is_64bit,
            primary_api=primary_api,
            detected_apis=detected_apis,
            has_native_dlss=has_native_dlss,
            has_native_streamline=has_native_streamline,
            has_native_fsr=has_native_fsr,
            has_native_xess=has_native_xess,
            has_existing_dlss5=has_dlss5_mod,
            emulator=emulator,
        )

        return GameAnalysis(
            exe_path=exe,
            game_dir=game_dir,
            exe_name=exe.name,
            file_size_bytes=file_size,
            architecture=arch,
            is_64bit=is_64bit,
            detected_apis=detected_apis,
            primary_api=primary_api,
            api_evidence=api_evidence,
            api_confidence=api_confidence,
            detected_wrappers=detected_wrappers,
            has_native_dlss=has_native_dlss,
            has_native_streamline=has_native_streamline,
            has_native_fsr=has_native_fsr,
            has_native_xess=has_native_xess,
            has_frame_generation=has_frame_generation,
            detected_upscaler_files=upscaler_files,
            has_existing_reshade=has_reshade,
            has_existing_dlss5_mod=has_dlss5_mod,
            existing_mod_files=mod_files,
            foreign_addons=foreign_addons,
            detected_engine=engine,
            renderer_candidates=renderer_candidates,
            anti_cheat_detected=has_ac,
            anti_cheat_warnings=ac_warnings,
            is_multiplayer_risk=is_mp_risk,
            anti_cheat_names=ac_names,
            risk_level=risk_level,
            advisories=advisories,
            supports_rage_commandline=supports_rage_commandline,
            emulator=emulator,
            emulator_api=emulator_api,
            recommended_strategy=strategy,
            strategy_rationale=rationale,
            imported_dlls=imported_dlls,
        )

    @classmethod
    def _inspect_pe_binary(cls, exe_path: Path) -> Tuple[bool, List[str], List[str]]:
        """Inspect the PE header, the import table and the delay-load import table.

        Delay-load imports matter as much as ordinary ones here. An engine that ships two
        renderers almost always delay-loads at least one of them, so reading only
        DIRECTORY_ENTRY_IMPORT reports whichever backend happened to be linked eagerly -
        which is how a D3D12 title gets filed as D3D11.
        """
        imported_dlls: List[str] = []
        delay_dlls: List[str] = []
        is_64bit = True

        if HAVE_PEFILE:
            try:
                pe = pefile.PE(str(exe_path), fast_load=True)
                machine = pe.FILE_HEADER.Machine
                # 0x8664 is AMD64 (x64), 0xaa64 is ARM64
                is_64bit = machine in (0x8664, 0xAA64)

                pe.parse_data_directories(directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
                ])
                for attr, sink in (
                    ("DIRECTORY_ENTRY_IMPORT", imported_dlls),
                    ("DIRECTORY_ENTRY_DELAY_IMPORT", delay_dlls),
                ):
                    for entry in getattr(pe, attr, None) or []:
                        raw = getattr(entry, "dll", None) or b""
                        name = raw.decode("utf-8", errors="ignore").strip().lower()
                        if name:
                            sink.append(name)
                pe.close()
                return is_64bit, imported_dlls, delay_dlls
            except Exception:
                pass  # Fall through to the pure-Python parser

        # Fallback parser: architecture from the COFF header, and nothing else that can
        # be trusted as an import. The marker scan below covers what this misses.
        try:
            with open(exe_path, "rb") as f:
                header = f.read(1024)
                if len(header) > 0x40 and header[:2] == b"MZ":
                    pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
                    f.seek(pe_offset)
                    if f.read(4) == b"PE\x00\x00":
                        coff_header = f.read(20)
                        machine = struct.unpack_from("<H", coff_header, 0)[0]
                        # 0x014c is i386 (32-bit); everything else here is 64-bit
                        is_64bit = machine != 0x014C
        except Exception:
            pass

        return is_64bit, imported_dlls, delay_dlls

    @classmethod
    def _scan_binary_markers(cls, exe_path: Path, cap_bytes: int = 96 * 1024 * 1024) -> set:
        """Return which API markers appear in the binary, as ASCII *and* as UTF-16LE.

        LoadLibraryW takes a wide string, so a DLL reached dynamically is stored as UTF-16
        and an ASCII-only scan never sees it - which is most of the reason the old
        detector missed the API a modern game actually renders with. Read in chunks with
        an overlap so a marker straddling a boundary still matches, and matched needles
        are dropped as they are found so a long executable does not pay for them twice.
        """
        pending = []
        for marker, api, weight in API_SYMBOL_MARKERS:
            ascii_form = marker.lower()
            wide_form = marker.decode("ascii").encode("utf-16-le").lower()
            pending.append([ascii_form, marker, api, weight])
            pending.append([wide_form, marker, api, weight])

        found = set()
        overlap = max(len(entry[0]) for entry in pending)
        try:
            read = 0
            tail = b""
            with open(exe_path, "rb") as f:
                while read < cap_bytes and pending:
                    chunk = f.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    read += len(chunk)
                    window = (tail + chunk).lower()
                    still_pending = []
                    for entry in pending:
                        if entry[0] in window:
                            found.add((entry[1], entry[2], entry[3]))
                        else:
                            still_pending.append(entry)
                    pending = still_pending
                    tail = window[-overlap:]
        except Exception:
            pass
        return found

    @classmethod
    def _identify_wrapper(cls, dll_path: Path) -> Optional[str]:
        """Name the wrapper or injector a graphics-named DLL actually is, if it is one.

        The file name is borrowed by definition - that is how these DLLs get loaded - so
        it says nothing. The content does.
        """
        try:
            with open(dll_path, "rb") as f:
                head = f.read(2 * 1024 * 1024)
        except Exception:
            return None
        low = head.lower()
        for marker, label in WRAPPER_MARKERS:
            if marker.lower() in low:
                return label
        return None

    @classmethod
    def _rhi_from_engine_config(cls, exe_path: Path, game_dir: Path) -> Optional[Tuple[str, str]]:
        """Read the RHI out of Unreal's own config, where it is stated outright.

        UE4 and UE5 link both D3D11 and D3D12 in the same shipped binary, so imports and
        symbols cannot separate them - but the packaged config records which one the game
        starts in. When it is there it is better evidence than anything in the PE.
        """
        # <Project>/Binaries/Win64/Game.exe -> <Project>
        project = game_dir
        for _ in range(3):
            if (project / "Config").is_dir() or (project / "Saved").is_dir():
                break
            project = project.parent

        candidates = [
            project / "Config" / "DefaultEngine.ini",
            project / "Saved" / "Config" / "WindowsNoEditor" / "Engine.ini",
            project / "Saved" / "Config" / "Windows" / "Engine.ini",
        ]
        for cfg in candidates:
            try:
                if not cfg.is_file():
                    continue
                text = cfg.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                continue
            if "defaultgraphicsrhi_dx12" in text:
                return D3D12, f"{cfg.name} sets DefaultGraphicsRHI=DX12"
            if "defaultgraphicsrhi_dx11" in text:
                return D3D11, f"{cfg.name} sets DefaultGraphicsRHI=DX11"
        return None

    @classmethod
    def _detect_rendering_apis(
        cls,
        exe_path: Path,
        game_dir: Path,
        imported_dlls: List[str],
        delay_imports: Optional[List[str]] = None,
        mod_files: Optional[List[str]] = None,
        engine: str = "",
    ) -> Tuple[List[str], str, Dict[str, List[str]], str, List[str]]:
        """Work out which API the game actually renders with, and show the working.

        Returns (detected_apis, primary_api, evidence, confidence, wrappers). Scores are
        "best single piece of evidence, plus a little for corroboration", so one import
        beats any number of weak hints, and two independent weak hints still lose to it.
        """
        weights: Dict[str, List[int]] = {}
        evidence: Dict[str, List[str]] = {}
        wrappers: List[str] = []

        def record(api: str, weight: int, why: str) -> None:
            weights.setdefault(api, []).append(weight)
            if why not in evidence.setdefault(api, []):
                evidence[api].append(why)

        # --- the import table, and the delay-load table beside it ---------------------
        for dll in imported_dlls:
            api = API_IMPORT_MAP.get(dll)
            if api:
                record(api, W_IMPORT, f"imports {dll}")
        for dll in delay_imports or []:
            api = API_IMPORT_MAP.get(dll)
            if api:
                record(api, W_DELAY, f"delay-loads {dll}")

        # dxgi.dll is shared by D3D11 and D3D12 and proves only that one of them is in
        # use. It is recorded against both, weakly, so a binary that names nothing else
        # still lands in the right family instead of defaulting there silently.
        if "dxgi.dll" in imported_dlls or "dxgi.dll" in (delay_imports or []):
            record(D3D11, 40, "imports dxgi.dll (D3D11 or D3D12 - not decisive on its own)")
            record(D3D12, 40, "imports dxgi.dll (D3D11 or D3D12 - not decisive on its own)")

        # --- entry points and DLL names inside the binary -----------------------------
        for marker, api, weight in cls._scan_binary_markers(exe_path):
            label = marker.decode("ascii", errors="ignore")
            kind = "entry point" if weight == W_SYMBOL else "string"
            record(api, weight, f"{label} ({kind}) in the executable")

        # --- the D3D12 Agility SDK redistributable ------------------------------------
        # Only a D3D12 renderer gains anything from shipping this, so it is also the
        # tie-breaker when a game links D3D11 as well - which many do, for video decode
        # or a legacy fallback, while rendering through D3D12.
        agility_present = False
        for agility in (game_dir / "D3D12Core.dll", game_dir / "D3D12" / "D3D12Core.dll"):
            if agility.is_file():
                agility_present = True
                record(D3D12, W_AGILITY, f"ships the D3D12 Agility SDK ({agility.name})")
                break

        # --- the engine's own config --------------------------------------------------
        rhi = cls._rhi_from_engine_config(exe_path, game_dir)
        if rhi:
            record(rhi[0], W_RHI_CONFIG, rhi[1])

        # --- sibling DLLs, minus the wrappers -----------------------------------------
        mod_set = {m.lower() for m in (mod_files or [])}
        try:
            for item in game_dir.iterdir():
                if not item.is_file():
                    continue
                name = item.name.lower()
                if not (name in API_IMPORT_MAP or name == "dxgi.dll"):
                    continue
                if name in mod_set:
                    continue
                if name in INJECTABLE_DLL_NAMES:
                    # Nearly always a wrapper or an overlay taking a system DLL's name so
                    # the loader finds it first. Counting one of these as the game's own
                    # renderer is the single biggest source of wrong API reports.
                    label = cls._identify_wrapper(item)
                    if label:
                        entry = f"{label} ({item.name})"
                        if entry not in wrappers:
                            wrappers.append(entry)
                        continue
                    api = API_IMPORT_MAP.get(name)
                    if api:
                        record(api, W_SIBLING, f"{item.name} in the game folder (unidentified - could be a wrapper)")
                    continue
                api = API_IMPORT_MAP.get(name)
                if api:
                    record(api, W_SIBLING, f"ships {item.name}")
        except Exception:
            pass

        # --- the engine's usual default, for ties only --------------------------------
        engine_default = None
        for known, api in ENGINE_DEFAULT_API.items():
            if engine.startswith(known):
                engine_default = api
                record(api, W_ENGINE, f"{engine} normally starts in {api}")
                break

        # --- discount a framework that bundles every backend --------------------------
        # SDL and the emulators built on it link a renderer for D3D9, D3D11, D3D12,
        # OpenGL and Vulkan, so all five sets of symbols sit in the binary whether or not
        # a single one is ever called. Four or more APIs known only from strings is that
        # pattern, not a game with four renderers, and the strings have to stop carrying
        # the weight of a real link or the winner is decided by whichever marker sorted
        # first. Anything with a hard import, a config entry or the Agility SDK is
        # untouched.
        hard_sources = {W_IMPORT, W_DELAY, W_AGILITY, W_RHI_CONFIG}
        string_only = [
            api for api, w in weights.items()
            if not hard_sources.intersection(w)
        ]
        if len(string_only) >= 4:
            for api in string_only:
                weights[api] = [min(W_SIBLING, max(weights[api]))]
                evidence[api].append(
                    "discounted: this binary references every graphics backend, which is a "
                    "multi-backend framework rather than a game that uses them all"
                )

        if not weights:
            return (
                [],
                D3D11,
                {D3D11: ["no import, symbol or config evidence found - assuming DirectX 11"]},
                "low",
                wrappers,
            )

        # Best single piece of evidence, plus a small bonus for independent corroboration.
        scores = {
            api: max(w) + min(15, 5 * (len(set(w)) - 1))
            for api, w in weights.items()
        }
        canonical_order = [D3D12, D3D11, VULKAN, D3D9, D3D8, OPENGL]

        def rank(api: str) -> Tuple[int, int, int, int]:
            return (
                scores[api],
                1 if (agility_present and api == D3D12) else 0,
                1 if api == engine_default else 0,
                -canonical_order.index(api) if api in canonical_order else -99,
            )

        ordered = sorted(scores, key=rank, reverse=True)
        primary = ordered[0]
        top = scores[primary]
        runner_up = scores[ordered[1]] if len(ordered) > 1 else 0

        if top >= W_DELAY and (top - runner_up) >= 25:
            confidence = "high"
        elif top >= W_SYMBOL and (top - runner_up) >= 10:
            confidence = "medium"
        else:
            confidence = "low"

        detected = [api for api in ordered if scores[api] >= W_SIBLING]
        return detected, primary, evidence, confidence, wrappers

    # A 64-bit build of the same program, sitting in the same folder under a name that
    # differs only by "64".
    _BITNESS_SUFFIXES = ("64", "_64", "-64", "x64", "_x64", "-x64")

    @classmethod
    def _check_for_better_bitness(cls, exe: Path, is_64bit: bool) -> List[str]:
        """Say so when the 32-bit build was picked and a 64-bit one is right there.

        PPSSPP ships PPSSPPWindows.exe and PPSSPPWindows64.exe side by side. Installing
        for the 32-bit one is not an error and the tool will do it, but it is almost never
        what somebody wants: it drags in the whole out-of-process host64 layout for a
        program that has a 64-bit build in the same folder, and NGX is 64-bit only.

        It cost an afternoon here. The install went to the 64-bit executable and worked;
        the 32-bit one was being launched, picked up an unrelated system-wide ReShade with
        none of these add-ons in it, and looked from the outside exactly like an install
        that had done nothing.
        """
        if is_64bit:
            return []
        stem = exe.stem.lower()
        try:
            siblings = [s for s in exe.parent.iterdir() if s.suffix.lower() == ".exe"]
        except OSError:
            return []

        for sibling in sorted(siblings):
            if sibling.name.lower() == exe.name.lower():
                continue
            other = sibling.stem.lower()
            if not any(other == stem + suffix for suffix in cls._BITNESS_SUFFIXES):
                continue
            is_64, _, _ = cls._inspect_pe_binary(sibling)
            if not is_64:
                continue
            return [
                f"{exe.name} is the 32-bit build, and {sibling.name} is in the same folder. "
                "Install for that one instead: the neural rendering runtime is 64-bit only, "
                "so a 32-bit install has to run it in a separate helper process, and "
                "whichever build gets launched is the one that has to have been modded."
            ]
        return []

    # A ReShade the user installed for the whole machine, which is not ours and does not
    # know about any of this.
    GLOBAL_RESHADE_DIR = Path(r"C:\ProgramData\ReShade")

    @classmethod
    def _check_for_global_reshade(cls) -> List[str]:
        """Note a system-wide ReShade, because it attaches to games on its own.

        ReShade's setup tool can install once for the machine and register itself as an
        implicit Vulkan layer. That copy then loads into anything that initialises Vulkan -
        including an emulator that merely probes for it while rendering through Direct3D -
        and it brings its own configuration and none of the add-ons installed here.

        Observed writing its own ReShade.log over this install's, in the same folder, which
        makes the log say the opposite of what is happening.
        """
        try:
            present = any(
                (cls.GLOBAL_RESHADE_DIR / name).is_file()
                for name in ("ReShade64.dll", "ReShade32.dll")
            )
        except OSError:
            return []
        if not present:
            return []
        return [
            "A system-wide ReShade is installed in C:\\ProgramData\\ReShade. It registers "
            "itself as a Vulkan layer, so it can load into a game on its own - with its own "
            "settings and none of the add-ons installed here - and it writes its own "
            "ReShade.log into the game folder. If the overlay opens but the Add-ons tab is "
            "empty, that is the copy that attached."
        ]

    @classmethod
    def _detect_existing_mods(cls, game_dir: Path) -> Tuple[bool, bool, List[str]]:
        """Detect if ReShade or DLSS 5 mod components are already in the directory."""
        has_reshade = False
        has_dlss5 = False
        mod_files: List[str] = []

        try:
            for item in game_dir.iterdir():
                name = item.name.lower()
                if name in ("reshade.ini", "reshade.log", "reshade-shaders", "reshade64.dll", "reshade32.dll"):
                    has_reshade = True
                    mod_files.append(item.name)
                elif name in (
                    "renodx-dlss5.addon64",
                    "renodx-dlss5.addon32",
                    "nvngx_dlssnr.dll",
                    "dlss5_feed.fx",
                    "sl.dlss_nr.dll",
                    "feeder.ini",
                    "dlss5-feed.log"
                ):
                    has_dlss5 = True
                    mod_files.append(item.name)
                elif name.endswith(".addon64") or name.endswith(".addon32"):
                    has_reshade = True

            # Our add-ons live in their own folder now, so the game folder alone no longer
            # answers "is this installed".
            for addon in ("renodx-dlss5.addon64", "dlss5-feed.addon64", "dlss5-feed.addon32"):
                if (game_dir / ADDON_DIR_NAME / addon).is_file():
                    has_dlss5 = True
                    mod_files.append(f"{ADDON_DIR_NAME}\\{addon}")
                    mod_files.append(item.name)
        except Exception:
            pass

        mod_files.extend(cls._files_this_tool_installed(game_dir))
        return has_reshade, has_dlss5, mod_files

    # This tool's own add-ons. Named so they can never be reported as conflicting with
    # themselves - "renodx-dlss5" starts with "renodx-dlss", and a prefix match alone
    # accused our own file of clashing with us.
    OUR_ADDONS = {"renodx-dlss5", "dlss5-feed"}

    # Add-ons known to contend for the same hooks this tool installs. Matched on the whole
    # stem: these names differ from ours, and from each other, only by a suffix.
    CONFLICTING_ADDONS = {
        "renodx-dlss": "another RenoDX DLSS add-on - both hook the same NGX entry points",
        "renodx-dlssfix": "another RenoDX DLSS add-on - both hook the same NGX entry points",
        "renodx-upgrade": "RenoDX tone-mapping - applies its own grade on top of the output",
        "renodx-devkit": "RenoDX development build",
        "autohdr": "a second tone-mapper - stacks with the neural output",
        "frame_capture": "hooks Present; overlays and capture add-ons flicker against each other",
        "obs_capture": "hooks Present",
        "livesplit_overlay": "hooks Present",
        "window_transparency": "hooks Present",
    }

    # The same, for add-ons whose file name carries a version, so no exact stem exists to
    # match: "srReshade_v2.1.0.addon64".
    CONFLICTING_ADDON_PREFIXES = {
        "srreshade": "a second upscaler, competing for the same frame",
        "optiscaler": "a second upscaler bridge",
    }

    @classmethod
    def _detect_foreign_addons(cls, game_dir: Path, ours: Set[str]) -> List[str]:
        """ReShade add-ons in the folder that this tool did not install.

        ReShade's AddonPath is the game directory, so it loads every .addon64 it finds.
        Anything already there is part of the install whether or not the user thought
        about it, and a folder holding a couple of dozen of them is the difference between
        a working install and one where four separate add-ons are tone-mapping the same
        frame.

        Reported, never touched. These are the user's other mods and removing them is not
        this tool's decision to make.
        """
        found: List[str] = []
        try:
            for item in sorted(game_dir.iterdir()):
                name = item.name
                if not name.lower().endswith((".addon64", ".addon32")):
                    continue
                if name.lower() in ours:
                    continue
                found.append(name)
        except OSError:
            pass
        return found

    @classmethod
    def describe_addon_conflict(cls, addon_name: str) -> Optional[str]:
        """Why a particular add-on is a problem, if it is a known one."""
        stem = Path(addon_name).stem.lower()
        if stem in cls.OUR_ADDONS:
            return None
        reason = cls.CONFLICTING_ADDONS.get(stem)
        if reason:
            return reason
        for prefix, reason in cls.CONFLICTING_ADDON_PREFIXES.items():
            if stem.startswith(prefix):
                return reason
        return None

    @classmethod
    def _files_this_tool_installed(cls, game_dir: Path) -> List[str]:
        """Read back the manifest an install leaves, and name every file it wrote.

        This exists because of one filename. A previous install stages `nvngx_dlss.dll`
        into the game folder, and on the next analysis `_detect_upscalers` read that file
        and concluded the game ships native DLSS - so re-analysing a game this tool had
        already modded flipped the recommendation to the Streamline path, on the strength
        of a DLL the tool had put there itself. Observed on a game with no native DLSS at
        all, which was recommended the native path.

        A hardcoded exclusion list cannot fix it: `nvngx_dlss.dll` next to a game .exe is
        genuinely ambiguous, and skipping it by name would hide real native DLSS in every
        game that ships it. The manifest is the only thing that knows which of the two
        this is, because it is written at install time by the code that put the file
        there.

        Silent on every failure. A missing or unreadable manifest means "nothing known to
        be ours", which is exactly the state of a folder this tool has never touched.
        """
        manifest = game_dir / BACKUP_DIR_NAME / MANIFEST_FILE_NAME
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        installed = data.get("injected_files")
        if not isinstance(installed, list):
            return []
        # Only the file name matters: the callers compare against directory entries.
        return [Path(str(entry)).name for entry in installed if entry]

    @classmethod
    def _detect_upscalers(
        cls, game_dir: Path, imported_dlls: List[str], mod_files: List[str]
    ) -> Tuple[bool, bool, bool, bool, List[str], List[str], bool]:
        """Detect native DLSS, NVIDIA Streamline, AMD FSR, or Intel XeSS files (excluding mod files).

        Returns a sixth list: nvngx runtimes found lying in the game folder with nothing
        to corroborate them. Those do not count as native DLSS, and the reason is
        Resident Evil 4.

        RE4 ships no DLSS at all - it offers FSR - but a hand-swapped nvngx_dlss.dll next
        to re4.exe is the single most common thing in a modded game folder, and reading
        one was enough to recommend the Streamline path. Which does nothing whatsoever in
        a game that never calls NGX: it installs the neural runtime and waits for DLSS
        calls that are never made, with no error anywhere. The manifest check above
        catches the copy this tool staged; it cannot catch the copy a person dropped in
        last year, and no rule on the filename can - games that genuinely ship DLSS put
        that same file in that same place.

        So the ambiguity is resolved by which mistake is survivable. Choosing the feeder
        for a game that does have DLSS costs a synthetic contract it did not need, and it
        works - that path is what every emulator uses. Choosing the native path for a game
        that does not have DLSS produces nothing at all and says nothing about why. The
        analysis reports the file it found either way, and the strategy is one dropdown
        away.

        Streamline is not ambiguous and is unchanged: sl.interposer and friends are engine
        plumbing, not something anybody copies in by hand. Nor is a PE import of nvngx.
        """
        has_dlss = False
        has_streamline = False
        has_fsr = False
        has_xess = False
        upscaler_files: List[str] = []
        loose_nvngx: List[str] = []
        has_frame_gen = False
        mod_file_set = {m.lower() for m in mod_files}

        # Check game root directory and plugins subdirectories
        search_dirs = [game_dir]
        for sub in ["Plugins", "Binaries", "Engine", "r6"]:
            subdir = game_dir / sub
            if subdir.is_dir():
                search_dirs.append(subdir)

        seen_files = set()
        for s_dir in search_dirs:
            try:
                for file_path in s_dir.glob("*.dll"):
                    name = file_path.name.lower()
                    if name in seen_files or name in mod_file_set:
                        continue
                    # Skip neural rendering mod DLLs from native count
                    if name in ("nvngx_dlssnr.dll", "sl.dlss_nr.dll"):
                        continue
                    seen_files.add(name)

                    if name in ("nvngx_dlss.dll", "nvngx_dlssg.dll", "_nvngx.dll"):
                        # dlssg is the frame generation runtime, and its presence is the
                        # one thing the RTX 40 MFG unlock needs to be true - that add-on
                        # multiplies frame generation the game already has, so a game
                        # without this file has nothing for it to multiply. Recorded
                        # separately from the native-DLSS question, which is about whether
                        # the *upscaler* is the game's own and stays deliberately unproven.
                        if name == "nvngx_dlssg.dll":
                            has_frame_gen = True
                        loose_nvngx.append(file_path.name)
                        upscaler_files.append(file_path.name)
                    elif name.startswith("sl.") and (name.endswith(".dll") or "streamline" in name):
                        has_streamline = True
                        has_dlss = True
                        if name.startswith("sl.dlss_g"):
                            has_frame_gen = True
                        upscaler_files.append(file_path.name)
                    elif (
                        name.startswith("ffx_fsr2")
                        or name.startswith("ffx_fsr3")
                        or name.startswith("amd_fidelityfx")
                    ):
                        has_fsr = True
                        upscaler_files.append(file_path.name)
                    elif name in ("libxess.dll", "xefx.dll", "xefx_loader.dll"):
                        has_xess = True
                        upscaler_files.append(file_path.name)
            except Exception:
                pass

        # Also inspect PE imports
        dll_set = set(imported_dlls)
        if any("nvngx" in d for d in dll_set):
            has_dlss = True
        if any("sl.common" in d or "sl.dlss" in d for d in dll_set):
            has_streamline = True
            has_dlss = True
        if any("dlssg" in d or "sl.dlss_g" in d for d in dll_set):
            has_frame_gen = True
        if any("ffx_fsr" in d for d in dll_set):
            has_fsr = True
        if any("xess" in d for d in dll_set):
            has_xess = True

        # Corroborated after the fact: with Streamline present, or the executable importing
        # nvngx itself, the runtimes lying beside it are the game's own and there is nothing
        # ambiguous left to report.
        if has_dlss and loose_nvngx:
            loose_nvngx = []

        return (has_dlss, has_streamline, has_fsr, has_xess, upscaler_files,
                loose_nvngx, has_frame_gen)

    @classmethod
    def _detect_game_engine(cls, exe_path: Path, game_dir: Path) -> str:
        """Identify game engine to tune feeder and depth buffer heuristics."""
        dir_name = game_dir.name.lower()
        exe_name = exe_path.name.lower()

        # Unreal Engine
        if (
            (game_dir / "Engine").is_dir()
            or "win64-shipping" in exe_name
            or (game_dir.parent / "Engine").is_dir()
            or any((game_dir / sub).is_dir() for sub in ["Binaries", "Content"])
        ):
            return "Unreal Engine (UE4 / UE5)"

        # Unity Engine
        for item in game_dir.iterdir():
            if item.is_dir() and item.name.endswith("_Data"):
                return "Unity Engine"
            if item.name.lower() in ("unityplayer.dll", "unitycrashhandler64.exe"):
                return "Unity Engine"

        # Source / Source 2 Engine
        if (game_dir / "bin" / "win64").is_dir() or (game_dir / "hl2.exe").exists():
            return "Valve Source / Source 2"

        # Creation Engine (Bethesda)
        if any(f.name.lower() in ("tesv.exe", "skyrimse.exe", "fallout4.exe", "starfield.exe") for f in game_dir.glob("*.exe")):
            return "Bethesda Creation Engine"

        # RE Engine (Capcom)
        if any(f.name.startswith("re_chunk_") for f in game_dir.glob("*.pak")) or "re4.exe" in exe_name or "re8.exe" in exe_name:
            return "Capcom RE Engine"

        # REDengine (CD Projekt RED)
        if (game_dir / "r6").is_dir() or "cyberpunk2077" in exe_name or "witcher3" in exe_name:
            return "CD Projekt RED REDengine"

        # CryEngine / Lumberyard
        if (game_dir / "CrySystem.dll").exists() or (game_dir / "system.cfg").exists():
            return "CryEngine"

        # Glacier Engine (IO Interactive)
        if "hitman" in exe_name or "hitman" in dir_name:
            return "IO Interactive Glacier Engine"

        # RAGE (Rockstar Advanced Game Engine) - GTA IV, EFLC, Max Payne 3
        if exe_name in ("gtaiv.exe", "eflc.exe", "maxpayne3.exe") or (game_dir / "pc" / "data").is_dir():
            return "Rockstar RAGE"

        # RenderWare / EA Legacy
        if "gta" in exe_name or "nfsmw" in exe_name or "speed.exe" in exe_name:
            return "EA / RenderWare / Legacy Engine"

        return "Custom / Proprietary Engine"

    @classmethod
    def _detect_anticheat_and_risks(
        cls, exe_name: str, game_dir: Path
    ) -> Tuple[bool, List[str], bool, List[str], str]:
        """Find anti-cheat protection and known online titles before anything is injected.

        Scans the game folder and one level of subfolders, because that is where these
        systems install themselves. Returns the detail rather than a bare flag: naming the
        anti-cheat is what makes the warning credible enough to be read instead of
        clicked through.
        """
        warnings: List[str] = []
        found: List[str] = []
        is_mp_risk = exe_name.lower() in {e.lower() for e in COMPETITIVE_MULTIPLAYER_EXES}

        # An emulator has no account to ban, and its folder is the worst possible place to
        # run a file-name scan: save data, shader caches and content-addressed recompiler
        # output, all of it named by the games the user owns rather than by the emulator.
        # Whatever is found there says nothing about whether injecting into the emulator is
        # risky, so the scan is skipped rather than filtered.
        if find_emulator_profile(exe_name) is not None:
            return is_mp_risk, warnings, is_mp_risk, found, (
                "online_competitive" if is_mp_risk else "none"
            )

        if is_mp_risk:
            warnings.append(
                f"'{exe_name}' is a known online or anti-cheat-protected title. "
                + ANTI_CHEAT_HEADLINE
            )

        # One level down as well: EasyAntiCheat\, BattlEye\, Vanguard's own folder.
        #
        # Folders count whatever they are called, but a file only counts if it could be a
        # loadable module. Anti-cheat ships .exe/.dll/.sys (and nProtect's .des); a sound
        # bank called ricochet_ambience.bnk or a shader cache entry is content, and letting
        # content into this comparison is how the warning ends up firing on someone's
        # asset names.
        names: List[str] = []

        def consider(item: Path) -> None:
            name = item.name.lower()
            if item.is_dir() or Path(name).suffix in ANTI_CHEAT_MODULE_SUFFIXES:
                names.append(name)

        try:
            for item in game_dir.iterdir():
                consider(item)
                if item.is_dir():
                    try:
                        for child in item.iterdir():
                            consider(child)
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            pass

        for label, fragments, in_kernel in ANTI_CHEAT_SIGNATURES:
            if any(anti_cheat_fragment_matches(name, fragment)
                   for name in names for fragment in fragments):
                found.append(label)
                where = "kernel-level" if in_kernel else "user-mode"
                warnings.append(f"{label} ({where}) is installed with this game.")

        has_ac = bool(found) or is_mp_risk
        if is_mp_risk:
            risk_level = "online_competitive"
        elif found:
            risk_level = "anti_cheat"
        else:
            risk_level = "none"

        return has_ac, warnings, is_mp_risk, found, risk_level

    @classmethod
    def _check_for_launcher(
        cls, exe: Path, game_dir: Path, imported_dlls: List[str]
    ) -> Tuple[List[str], List[str]]:
        """Warn when the selected .exe never renders anything itself.

        Several games ship a small launcher next to the real renderer (Prince of Persia's
        PrinceOfPersia.exe next to POP3.EXE). Modding the launcher installs files that the
        rendering process may never load, and nothing reports an error.
        """
        advisories: List[str] = []
        graphics_markers = ("d3d9", "d3d11", "d3d12", "dxgi", "vulkan", "opengl32", "ddraw")
        if any(any(m in dll for m in graphics_markers) for dll in imported_dlls):
            return advisories, []

        candidates = []
        for sibling in game_dir.glob("*.exe"):
            if sibling.name.lower() == exe.name.lower():
                continue
            # Several of the things this tool installs are executables that link a
            # graphics API - dgVoodoo's control panel most of all - and naming one of
            # those as "probably the renderer" is worse than saying nothing.
            if sibling.name.lower() in COMPANION_EXECUTABLES:
                continue
            try:
                _, sibling_imports, _ = cls._inspect_pe_binary(sibling)
            except Exception:
                continue
            if any(any(m in dll for m in sibling_imports) for m in graphics_markers for dll in [dll for dll in sibling_imports]):
                candidates.append(sibling.name)

        if candidates:
            advisories.append(
                f"'{exe.name}' imports no graphics API - it looks like a launcher. The renderer is "
                f"probably {', '.join(sorted(candidates)[:3])}; analyse that executable instead, or "
                "the architecture and API detected here will not describe the process ReShade loads into."
            )
        return advisories, sorted(candidates)

    @staticmethod
    def _supports_rage_commandline(exe: Path) -> bool:
        """True when the executable parses a commandline.txt with video memory switches.

        Detected from the binary's own option strings rather than a name list, so it holds
        for any RAGE title. These games size their graphics presets from the video memory
        the device reports, and a wrapper such as dgVoodoo2 reports its own configured
        amount - which is how a 1440p game ends up clamped to its lowest settings.
        """
        try:
            data = exe.read_bytes()
        except Exception:
            return False
        return all(marker in data for marker in (b"commandline.txt", b"availablevidmem", b"norestrictions"))

    @classmethod
    def _determine_strategy(
        cls,
        is_64bit: bool,
        primary_api: str,
        detected_apis: List[str],
        has_native_dlss: bool,
        has_native_streamline: bool,
        has_native_fsr: bool,
        has_native_xess: bool,
        has_existing_dlss5: bool = False,
        emulator: Optional[EmulatorProfile] = None,
    ) -> Tuple[str, str]:
        """Determine optimal DLSS 5 installation strategy."""

        # 0. Emulators come first. They are x64 and modern-API, so the generic branches
        #    would reach the right family by accident, but they would also plan around the
        #    binary's imports instead of the renderer the emulator is actually set to.
        if emulator:
            api = API_DISPLAY_NAMES.get(emulator.recommended, emulator.recommended)
            return (
                STRATEGY_EMULATOR,
                f"{emulator.name} is a 64-bit host, so dlss5-feed.addon64 runs inside it directly - "
                "no host64 helper. The emulated title issues no DLSS calls of its own, so the feeder "
                f"synthesises the DLAA contract from ReShade's depth and estimated motion vectors. "
                f"Install planned for its {api} renderer."
            )

        is_legacy_d3d = (
            primary_api.startswith("DirectX 9")
            or primary_api.startswith("DirectX 8")
        ) and "DirectX 11" not in detected_apis and "DirectX 12" not in detected_apis

        # 1. D3D9 / D3D8 -> translate to something the add-on supports, at either bitness.
        #    This has to be checked before the bitness split: a 32-bit D3D9 game needs the
        #    translation layer *and* the 64-bit helper, and only this branch deploys one.
        #
        #    DXVK is the default of the two. The add-on's own description names it - it
        #    feeds "32-bit D3D11, OpenGL and Vulkan (DXVK) games" - and it costs the game
        #    neither a permanent watermark nor an emulated video card whose reported VRAM
        #    an old engine then sizes its texture budget from. dgVoodoo2 stays one click
        #    away for D3D8 and for the early titles DXVK will not start.
        if is_legacy_d3d:
            extra = "" if is_64bit else (
                " The executable is also 32-bit, so the neural rendering stack runs in a "
                "64-bit helper process under host64/ - NGX has no 32-bit build."
            )
            if primary_api.startswith("DirectX 8"):
                return (
                    STRATEGY_DGVOODOO_DX9,
                    f"Game presents through {primary_api}. DXVK's d3d8 support is a shim over its "
                    "D3D9 path and not what the feeder is tested against, so dgVoodoo2 translates "
                    "to DirectX 11 here instead." + extra
                )
            return (
                STRATEGY_DXVK_DX9,
                f"Game presents through {primary_api}. DXVK translates it to Vulkan in the game's "
                "own process, which is a path the DLSS5-Feeder add-on supports directly. No "
                "watermark, and the video memory the engine is told about comes from dxvk.conf "
                "rather than from an emulated video card. ReShade attaches as a Vulkan layer. "
                "Switch to the dgVoodoo2 strategy if the game will not start under DXVK." + extra
            )

        # 2. 32-bit, modern API -> add-on in the game, DLSS work in host64/
        if not is_64bit:
            return (
                STRATEGY_32BIT_FEEDER,
                "The game executable is 32-bit (x86). NGX is 64-bit only, so dlss5-feed.addon32 runs "
                "in the game and hands the work to dlss5-feed-host64.exe in host64/, which is where "
                "ReShade x64, renodx-dlss5.addon64 and the nvngx runtimes are installed."
            )

        # 3. Native DLSS / Streamline Game -> Direct / Streamline Upgrade Path
        if has_native_dlss or has_native_streamline:
            return (
                STRATEGY_NATIVE_DLSS,
                "Native DLSS or NVIDIA Streamline detected! In-engine motion vectors and depth buffers "
                "are already provided natively by the game engine. RenoDX DLSS 5 add-on and nvngx_dlssnr.dll "
                "can be integrated directly without synthetic optical flow."
            )

        # 4. AMD FSR 2/3 or Intel XeSS -> Bridge Path (OptiScaler)
        if has_native_fsr or has_native_xess:
            return (
                STRATEGY_FSR_BRIDGE,
                "AMD FSR2/3 or Intel XeSS detected in game files! OptiScaler bridge will intercept "
                "native game-engine motion vectors and depth buffers to feed DLSS 5 Neural Rendering with maximum stability."
            )

        # 5. General DirectX 11 / DirectX 12 / Vulkan / OpenGL -> Feeder Mode
        return (
            STRATEGY_FEEDER_DX11_12,
            f"Game runs on {primary_api} without native DLSS. DLSS5-Feeder paired with LumeniteFX Kernel "
            "(optical flow motion vector shader) and ReShade Add-on will synthesize the DLAA contract for Neural Rendering."
        )
