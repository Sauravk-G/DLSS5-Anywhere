"""
Command Line Interface for DLSS5-Anywhere.
Provides full terminal automation for detection, building, installation, uninstallation, scanning, and component management.
"""

import argparse
from pathlib import Path
import sys
from typing import Optional

from .config import (
    APP_NAME,
    APP_VERSION,
    STRATEGY_DESCRIPTIONS,
    STRATEGY_DISPLAY_NAMES,
    uses_dfc,
)
from .core.components import ComponentManager
from .core.detector import GameDetector
from .core.downloader import ComponentDownloader
from .core.installer import ModInstaller
from .core.library_scanner import GameLibraryScanner
from .core.profiles import ProfileManager
from .core.strategy import StrategyEngine


# Ensure Windows stdout supports UTF-8 cleanly
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_banner():
    """Print ASCII header banner."""
    print("=" * 72)
    print(f"  [*] {APP_NAME} v{APP_VERSION} - DLSS 5 Neural Rendering Automation Tool")
    print("=" * 72)


def cmd_detect(args):
    """Analyze game executable and print diagnostic report."""
    print_banner()
    exe_path = Path(args.executable).resolve()
    print(f"[*] Analyzing target executable: {exe_path}\n")

    try:
        report = GameDetector.analyze(exe_path)
    except Exception as e:
        print(f"[!] Error during analysis: {e}")
        return 1

    print(f"  Game Title:            {report.exe_name}")
    print(f"  Directory:             {report.game_dir}")
    print(f"  Architecture:          {report.architecture.upper()} ({'64-bit' if report.is_64bit else '32-bit (x86)'})")
    print(f"  Primary Rendering API: {report.primary_api}  [{report.api_confidence} confidence]")
    print(f"  Detected APIs:         {', '.join(report.detected_apis) or 'none found'}")
    for reason in (report.api_evidence.get(report.primary_api) or [])[:3]:
        print(f"     read from:          {reason}")
    if report.detected_wrappers:
        print(f"  Wrapper present:       {', '.join(report.detected_wrappers)}")
        print("     (a wrapper, not the game's own renderer)")
    print(f"  Game Engine:           {report.detected_engine}")
    print(f"  Native DLSS / SL:      {'YES' if report.has_native_dlss else 'No'}")
    print(f"  Native FSR / XeSS:     {'YES' if report.has_native_fsr or report.has_native_xess else 'No'}")
    print(f"  Existing Mod Status:   {'DLSS 5 Mod Installed' if report.has_existing_dlss5_mod else ('ReShade Found' if report.has_existing_reshade else 'Vanilla')}")
    
    if report.anti_cheat_detected:
        banner = "!" * 72
        print("\n" + banner)
        if report.risk_level == "online_competitive":
            print("  BAN RISK - this is a known online / anti-cheat-protected title.")
        else:
            print("  ANTI-CHEAT DETECTED: " + ", ".join(report.anti_cheat_names))
        for w in report.anti_cheat_warnings:
            print(f"     - {w}")
        print("  An injected DLL is indistinguishable from a cheat. Single-player only.")
        print(banner)

    print("\n" + "-" * 72)
    print(f"  RECOMMENDED STRATEGY:  {STRATEGY_DISPLAY_NAMES.get(report.recommended_strategy, report.recommended_strategy)}")
    print(f"  RATIONALE:             {report.strategy_rationale}")
    print("-" * 72)
    return 0


def _profile_for(args):
    """The profile a build runs under, named on the command line or the default.

    The GUI has always passed the profile the user picked; the CLI imported
    ProfileManager and then passed nothing, so `--strategy emulator` builds quietly came
    out with the default neural consumer no matter what the profile said. A build that
    ignores the setting it was given is worse than one that cannot read it.
    """
    name = getattr(args, "profile", None)
    if not name:
        return None
    profile = ProfileManager.load_profile(name)
    print(f"[*] Profile: {name} (neural consumer: {profile.get('neural_consumer', 'renodx')})")
    return profile


def cmd_build(args):
    """Stage a standalone mod build folder for a target game."""
    print_banner()
    exe_path = Path(args.executable).resolve()
    print(f"[*] Preparing standalone build for: {exe_path}")

    try:
        report = GameDetector.analyze(exe_path)
        out_dir = Path(args.output).resolve() if args.output else None
        
        def progress(frac, msg):
            print(f"  [{frac*100:3.0f}%] {msg}")

        result = ModInstaller.prepare_build_folder(
            analysis=report,
            strategy_override=args.strategy,
            profile=_profile_for(args),
            custom_output_dir=out_dir,
            progress_callback=progress,
        )

        print("\n" + "=" * 72)
        if result.success:
            print(f"[OK] Build folder staged successfully at:")
            print(f"     {result.target_directory}")
            print(f"     Included files: {len(result.files_copied)} binaries/shaders, {len(result.configs_written)} config scripts.")
            print(f"\nTo install: Run 'apply_dlss5.bat' inside the build folder or copy files manually.")
        else:
            print(f"[!] Staging failed: {result.message}")
        print("=" * 72)
        return 0 if result.success else 1
    except Exception as e:
        print(f"[!] Build error: {e}")
        return 1


def cmd_install(args):
    """Directly inject DLSS 5 mod into game directory with automatic backup."""
    print_banner()
    exe_path = Path(args.executable).resolve()
    print(f"[*] Injecting DLSS 5 Neural Rendering into: {exe_path}")

    try:
        report = GameDetector.analyze(exe_path)

        if report.anti_cheat_detected and not args.force:
            print("\n[!] WARNING: Anti-cheat or competitive multiplayer markers detected!")
            for w in report.anti_cheat_warnings:
                print(f"    - {w}")
            confirm = input("\nDo you really want to proceed with injection? (y/N): ")
            if confirm.lower() != "y":
                print("Aborted by user.")
                return 1

        def progress(frac, msg):
            print(f"  [{frac*100:3.0f}%] {msg}")

        profile = _profile_for(args)
        result = ModInstaller.install_to_game(
            analysis=report,
            strategy_override=args.strategy,
            profile=profile,
            progress_callback=progress,
        )

        print("\n" + "=" * 72)
        if result.success:
            print(f"[OK] {result.message}")
            print(f"     Backup manifest saved to: {result.backup_manifest}")
            print("\nIn-Game Instructions:")
            print("  1. Launch game normally or via 'launch_with_dlss5.bat'")
            consumer = "Deep Fried Chicken" if uses_dfc(profile) else "RenoDX DLSS 5"
            print(f"  2. Press [Home] to open ReShade overlay and verify the {consumer} add-on")
            print("  3. Press [F2] to toggle Neural Rendering pipeline")
        else:
            print(f"[!] Injection failed: {result.message}")
        print("=" * 72)
        return 0 if result.success else 1
    except Exception as e:
        print(f"[!] Installation error: {e}")
        return 1


def cmd_uninstall(args):
    """Restore game directory to vanilla state."""
    print_banner()
    target_path = Path(args.path).resolve()
    if target_path.is_file():
        target_dir = target_path.parent
    else:
        target_dir = target_path

    print(f"[*] Restoring game directory to vanilla state: {target_dir}")

    def progress(frac, msg):
        print(f"  [{frac*100:3.0f}%] {msg}")

    success, msg, removed = ModInstaller.restore_and_uninstall(target_dir, progress)
    print("\n" + "=" * 72)
    if success:
        print(f"[OK] {msg}")
        if removed:
            print(f"     Removed items: {', '.join(removed[:10])}{'...' if len(removed) > 10 else ''}")
    else:
        print(f"[!] Uninstall failed: {msg}")
    print("=" * 72)
    return 0 if success else 1


def cmd_scan(args):
    """Scan game libraries across Steam, Epic Games, and GOG."""
    print_banner()
    print("[*] Scanning storage drives for installed games...\n")

    games = GameLibraryScanner.scan_all()
    print(f"Found {len(games)} installed games:\n")
    print(f"{'Source':<12} | {'Game Title':<36} | {'Executable Path'}")
    print("-" * 80)
    for g in games:
        print(f"{g.source:<12} | {g.name[:34]:<36} | {g.exe_path}")
    print("-" * 80)
    return 0


def cmd_components(args):
    """Display status of all mod components."""
    print_banner()
    print("[*] Checking status of DLSS 5 components in local repository:\n")

    statuses = ComponentManager.get_all_statuses()
    for comp_id, status in statuses.items():
        state_icon = "[READY]  " if status.is_installed else "[MISSING]"
        kind = "(User-Supplied)" if status.meta.is_user_supplied else "(Public)       "
        print(f"  {state_icon} {kind} {status.meta.name:<46} | {status.details}")
    
    print("\nTip: Run 'dlss5-anywhere download-all' to fetch missing public components.")
    print("     Run 'dlss5-anywhere auto-import' to scan system for proprietary user files.")
    return 0


def cmd_download_all(args):
    """Download all missing public components."""
    print_banner()
    print("[*] Downloading public components (ReShade, Feeder, LumeniteFX, vort, dgVoodoo2, OptiScaler)...\n")

    def progress(name, frac, msg):
        print(f"  [{frac*100:3.0f}%] {name}: {msg}")

    results = ComponentDownloader.download_all_public_components(progress)
    print("\n" + "=" * 72)
    print("Download Summary:")
    for comp_id, (ok, msg) in results.items():
        icon = "[OK]  " if ok else "[FAIL]"
        print(f"  {icon} {comp_id}: {msg}")
    print("=" * 72)
    return 0


def cmd_import_file(args):
    """Import a user-supplied proprietary file."""
    print_banner()
    file_path = Path(args.file_path).resolve()
    print(f"[*] Importing user file: {file_path}")

    ok, msg, comp_id = ComponentManager.import_user_file(file_path)
    if ok:
        print(f"[OK] {msg}")
        return 0
    else:
        print(f"[!] Import failed: {msg}")
        return 1


def cmd_auto_import(args):
    """Scan user drives for proprietary files."""
    print_banner()
    print("[*] Scanning Downloads, Desktop, and game folders for renodx-dlss5.addon64 / nvngx_dlssnr.dll...")

    imported = ComponentManager.auto_scan_and_import_existing()
    if imported:
        print(f"\n[OK] Successfully imported {len(imported)} files:")
        for item in imported:
            print(f"     - {item}")
    else:
        print("\n[*] No new files found or components are already in place.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="dlss5-anywhere",
        description=f"{APP_NAME} v{APP_VERSION} - Automation tool for DLSS 5 Neural Rendering mod installation.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # detect
    p_detect = subparsers.add_parser("detect", help="Analyze game executable and detect architecture & rendering APIs")
    p_detect.add_argument("executable", help="Path to game .exe file")
    p_detect.set_defaults(func=cmd_detect)

    # build
    p_build = subparsers.add_parser("build", help="Create a clean standalone build folder without modifying game")
    p_build.add_argument("executable", help="Path to game .exe file")
    p_build.add_argument("--strategy", help="Override installation strategy", choices=list(STRATEGY_DISPLAY_NAMES.keys()))
    p_build.add_argument("--output", "-o", help="Custom output directory for build")
    p_build.add_argument("--profile", help="Build with this saved profile (see 'profiles'); without it the defaults are used")
    p_build.set_defaults(func=cmd_build)

    # install
    p_install = subparsers.add_parser("install", help="Directly inject DLSS 5 mod into game directory with backup")
    p_install.add_argument("executable", help="Path to game .exe file")
    p_install.add_argument("--strategy", help="Override installation strategy", choices=list(STRATEGY_DISPLAY_NAMES.keys()))
    p_install.add_argument("--force", "-f", action="store_true", help="Force install even if anti-cheat is detected")
    p_install.add_argument("--profile", help="Build with this saved profile (see 'profiles'); without it the defaults are used")
    p_install.set_defaults(func=cmd_install)

    # uninstall
    p_unins = subparsers.add_parser("uninstall", help="Restore game directory to vanilla state and remove mod files")
    p_unins.add_argument("path", help="Path to game directory or game .exe file")
    p_unins.set_defaults(func=cmd_uninstall)

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan system for installed Steam, Epic, and GOG games")
    p_scan.set_defaults(func=cmd_scan)

    # components
    p_comp = subparsers.add_parser("components", help="Check status of all required mod components")
    p_comp.set_defaults(func=cmd_components)

    # download-all
    p_dl = subparsers.add_parser("download-all", help="Download all missing public components")
    p_dl.set_defaults(func=cmd_download_all)

    # import-file
    p_imp = subparsers.add_parser("import-file", help="Import a proprietary user file (renodx addon or nvngx_dlssnr.dll)")
    p_imp.add_argument("file_path", help="Path to user file")
    p_imp.set_defaults(func=cmd_import_file)

    # auto-import
    p_auto = subparsers.add_parser("auto-import", help="Automatically scan system for proprietary user files")
    p_auto.set_defaults(func=cmd_auto_import)

    return parser


def main(argv=None):
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
