"""
Game Library Scanner for DLSS5-Anywhere.
Discovers installed games across Steam, Epic Games Launcher, GOG Galaxy, and custom game directories.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional, Set

if sys.platform == "win32":
    import winreg


@dataclass
class DiscoveredGame:
    """Represents an installed game detected on the user's system."""
    name: str
    exe_path: Path
    install_dir: Path
    source: str  # 'Steam', 'Epic Games', 'GOG Galaxy', 'Custom'
    app_id: Optional[str] = None
    icon_path: Optional[Path] = None
    # Only ever true for a game the user added by hand: a scan cannot turn up an
    # executable that is not there, but a stored entry outlives the file it points at -
    # the game gets uninstalled, or the drive it lives on is unplugged. Such an entry is
    # shown and marked rather than dropped, because deleting the user's list on their
    # behalf because a USB disk was not connected is not a decision this tool gets to
    # make.
    missing: bool = False


class GameLibraryScanner:
    """Scans Windows storage drives, registries, and launcher manifests for games."""

    IGNORE_EXE_KEYWORDS = {
        "crash", "report", "unins", "setup", "launcher", "helper",
        "redist", "vcredist", "dxsetup", "update", "patch",
        "prereq", "install", "config", "editor", "tool", "server",
        "benchmark", "cleanup", "cefprocess", "webview", "support",
        "steamerrorreporter", "unitycrashhandler", "eac", "easyanticheat",
        "battleye", "beservice", "goggame", "galaxyclient"
    }

    @classmethod
    def scan_all(cls, include_custom: bool = True) -> List[DiscoveredGame]:
        """The whole library: everything the launchers know about, plus the user's own.

        The manual entries go on last, so a game that a launcher also reports keeps the
        launcher's name and source - if it has since become detectable, the detected
        answer is the better one.
        """
        games: List[DiscoveredGame] = []
        seen_paths: Set[str] = set()

        # 1. Steam Games
        try:
            for g in cls.scan_steam():
                key = str(g.exe_path).lower()
                if key not in seen_paths:
                    seen_paths.add(key)
                    games.append(g)
        except Exception:
            pass

        # 2. Epic Games
        try:
            for g in cls.scan_epic():
                key = str(g.exe_path).lower()
                if key not in seen_paths:
                    seen_paths.add(key)
                    games.append(g)
        except Exception:
            pass

        # 3. GOG Galaxy Games
        try:
            for g in cls.scan_gog():
                key = str(g.exe_path).lower()
                if key not in seen_paths:
                    seen_paths.add(key)
                    games.append(g)
        except Exception:
            pass

        # 4. Whatever the user added by hand.
        #
        # Imported here rather than at the top of the module: custom_library needs
        # DiscoveredGame from this file, so a module-level import in this direction would
        # close the loop and neither module would load.
        if include_custom:
            try:
                from .custom_library import CustomLibrary

                for g in CustomLibrary.load():
                    key = str(g.exe_path).lower()
                    if key not in seen_paths:
                        seen_paths.add(key)
                        games.append(g)
            except Exception:
                pass

        games.sort(key=lambda x: x.name.lower())
        return games

    @classmethod
    def scan_steam(cls) -> List[DiscoveredGame]:
        """Discover games installed in Steam libraries across all drives."""
        games: List[DiscoveredGame] = []
        steam_paths: List[Path] = []

        # Find Steam base path via Registry
        if sys.platform == "win32":
            for reg_root in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
                for subkey in [r"Software\Valve\Steam", r"SOFTWARE\Valve\Steam", r"SOFTWARE\WOW6432Node\Valve\Steam"]:
                    try:
                        with winreg.OpenKey(reg_root, subkey) as k:
                            val, _ = winreg.QueryValueEx(k, "SteamPath")
                            if val:
                                p = Path(val)
                                if p.exists() and p not in steam_paths:
                                    steam_paths.append(p)
                    except Exception:
                        pass

        # Common default paths
        for default in [
            Path(r"C:\Program Files (x86)\Steam"),
            Path(r"C:\Program Files\Steam"),
            Path(r"D:\Steam"),
            Path(r"E:\Steam"),
        ]:
            if default.exists() and default not in steam_paths:
                steam_paths.append(default)

        # Collect all library folders
        library_folders: List[Path] = []
        for s_path in steam_paths:
            vdf_file = s_path / "steamapps" / "libraryfolders.vdf"
            if vdf_file.exists():
                try:
                    content = vdf_file.read_text(encoding="utf-8", errors="ignore")
                    matches = re.findall(r'"path"\s+"([^"]+)"', content)
                    for m in matches:
                        p = Path(m.replace("\\\\", "\\"))
                        if p.exists() and p not in library_folders:
                            library_folders.append(p)
                except Exception:
                    pass
            if s_path not in library_folders:
                library_folders.append(s_path)

        # Check common SteamLibrary locations on all drives
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive_lib = Path(f"{letter}:\\SteamLibrary")
            if drive_lib.exists() and drive_lib not in library_folders:
                library_folders.append(drive_lib)

        # Read manifests in each library folder
        for lib in library_folders:
            steamapps = lib / "steamapps"
            if not steamapps.exists():
                continue

            for acf in steamapps.glob("appmanifest_*.acf"):
                try:
                    content = acf.read_text(encoding="utf-8", errors="ignore")
                    name_m = re.search(r'"name"\s+"([^"]+)"', content)
                    dir_m = re.search(r'"installdir"\s+"([^"]+)"', content)
                    appid_m = re.search(r'"appid"\s+"([^"]+)"', content)

                    if name_m and dir_m:
                        game_name = name_m.group(1).strip()
                        install_dir_name = dir_m.group(1).strip()
                        app_id = appid_m.group(1).strip() if appid_m else None
                        
                        # Skip Steamworks / runtime runtimes
                        if any(k in game_name.lower() for k in ["steamlinux", "steam controller", "steamworks shared", "proton"]):
                            continue

                        game_dir = steamapps / "common" / install_dir_name
                        if game_dir.exists():
                            exe = cls._find_primary_game_exe(game_dir, game_name)
                            if exe:
                                games.append(DiscoveredGame(
                                    name=game_name,
                                    exe_path=exe,
                                    install_dir=game_dir,
                                    source="Steam",
                                    app_id=app_id,
                                ))
                except Exception:
                    continue

        return games

    @classmethod
    def scan_epic(cls) -> List[DiscoveredGame]:
        """Discover games installed via Epic Games Launcher."""
        games: List[DiscoveredGame] = []
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        manifests_dir = Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"

        if not manifests_dir.exists():
            return games

        for item_file in manifests_dir.glob("*.item"):
            try:
                data = json.loads(item_file.read_text(encoding="utf-8", errors="ignore"))
                display_name = data.get("DisplayName")
                install_loc = data.get("InstallLocation")
                launch_exe = data.get("LaunchExecutable")
                app_name = data.get("AppName")

                if display_name and install_loc:
                    install_dir = Path(install_loc)
                    if install_dir.exists():
                        exe_path = None
                        if launch_exe:
                            candidate = install_dir / launch_exe
                            if candidate.exists():
                                exe_path = candidate
                        if not exe_path:
                            exe_path = cls._find_primary_game_exe(install_dir, display_name)

                        if exe_path:
                            games.append(DiscoveredGame(
                                name=display_name,
                                exe_path=exe_path,
                                install_dir=install_dir,
                                source="Epic Games",
                                app_id=app_name,
                            ))
            except Exception:
                continue

        return games

    @classmethod
    def scan_gog(cls) -> List[DiscoveredGame]:
        """Discover games installed via GOG Galaxy."""
        games: List[DiscoveredGame] = []
        if sys.platform != "win32":
            return games

        for root_key in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
            for subpath in [r"SOFTWARE\GOG.com\Games", r"SOFTWARE\WOW6432Node\GOG.com\Games"]:
                try:
                    with winreg.OpenKey(root_key, subpath) as games_key:
                        num_subkeys, _, _ = winreg.QueryInfoKey(games_key)
                        for i in range(num_subkeys):
                            game_id = winreg.EnumKey(games_key, i)
                            try:
                                with winreg.OpenKey(games_key, game_id) as g_key:
                                    name, _ = winreg.QueryValueEx(g_key, "gameName")
                                    path_str, _ = winreg.QueryValueEx(g_key, "path")
                                    exe_name, _ = winreg.QueryValueEx(g_key, "exe")
                                    
                                    if path_str:
                                        install_dir = Path(path_str)
                                        if install_dir.exists():
                                            exe_path = None
                                            if exe_name:
                                                cand = install_dir / exe_name
                                                if cand.exists():
                                                    exe_path = cand
                                            if not exe_path:
                                                exe_path = cls._find_primary_game_exe(install_dir, name)
                                            if exe_path:
                                                games.append(DiscoveredGame(
                                                    name=name,
                                                    exe_path=exe_path,
                                                    install_dir=install_dir,
                                                    source="GOG Galaxy",
                                                    app_id=game_id,
                                                ))
                            except Exception:
                                pass
                except Exception:
                    pass

        return games

    @classmethod
    def scan_custom_directory(cls, root_dir: str | Path, max_depth: int = 2) -> List[DiscoveredGame]:
        """Scan a custom directory for game folders containing executables."""
        games: List[DiscoveredGame] = []
        root = Path(root_dir).resolve()
        if not root.exists() or not root.is_dir():
            return games

        # Find all direct subdirectories
        for item in root.iterdir():
            if item.is_dir():
                exe = cls._find_primary_game_exe(item, item.name)
                if exe:
                    games.append(DiscoveredGame(
                        name=item.name,
                        exe_path=exe,
                        install_dir=item,
                        source="Custom",
                    ))
        return games

    @classmethod
    def _find_primary_game_exe(cls, game_dir: Path, game_name: str) -> Optional[Path]:
        """Find the most probable primary game executable in a directory."""
        candidates: List[Tuple[int, Path]] = []
        name_clean = re.sub(r"[^\w]", "", game_name.lower())

        # Check binaries in root and 1 level of subdirectories (Binaries/Win64, etc.)
        search_dirs = [game_dir]
        for sub in ["Binaries/Win64", "bin/x64", "bin/win64", "bin", "x64", "Win64", "GTAIV"]:
            d = game_dir / sub
            if d.is_dir():
                search_dirs.append(d)

        for s_dir in search_dirs:
            try:
                for exe in s_dir.glob("*.exe"):
                    exe_lower = exe.name.lower()
                    
                    # Skip common utilities
                    if any(k in exe_lower for k in cls.IGNORE_EXE_KEYWORDS):
                        continue

                    # Score candidate
                    score = 0
                    exe_clean = re.sub(r"[^\w]", "", exe.stem.lower())

                    # Match with game name
                    if exe_clean == name_clean:
                        score += 50
                    elif exe_clean in name_clean or name_clean in exe_clean:
                        score += 30
                    
                    # Prefer 64-bit or Win64-Shipping names
                    if "win64" in exe_lower or "x64" in exe_lower:
                        score += 20
                    
                    # Prefer larger executables (game code is usually 10MB+)
                    try:
                        size_mb = exe.stat().st_size / (1024 * 1024)
                        if size_mb > 5:
                            score += min(int(size_mb), 25)
                    except Exception:
                        pass

                    candidates.append((score, exe))
            except Exception:
                pass

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

        return None
