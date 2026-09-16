"""
Installation, Staging Build & Backup/Restore Pipeline for DLSS5-Anywhere.
Handles direct game folder injection, standalone staging builds, file backups, and clean uninstallation.
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import (
    ADDON_DIR_NAME,
    BUILDS_DIR,
    RESHADE_PROXY_FALLBACK_ORDER,
    USER_SUPPLIED_DIR,
    uses_dfc,
    COMP_DGVOODOO,
    COMPONENTS_DIR,
    COMPONENTS_REGISTRY,
    STRATEGY_FSR_BRIDGE,
)
from .config_gen import ConfigGenerator
from .detector import GameAnalysis
from .strategy import HOST64_DEST, StrategyEngine, StrategyPlan


@dataclass
class BuildResult:
    """Outcome of an installation or build staging operation."""
    success: bool
    mode: str  # 'direct_install' or 'prepare_build'
    target_directory: Path
    files_copied: List[str] = field(default_factory=list)
    configs_written: List[str] = field(default_factory=list)
    backup_manifest: Optional[Path] = None
    message: str = ""
    warnings: List[str] = field(default_factory=list)


class ModInstaller:
    """Core pipeline executing file transfers, config generation, backup manifests, and clean uninstalls."""

    BACKUP_DIR_NAME = ".dlss5_backup"
    MANIFEST_FILE_NAME = "dlss5_manifest.json"

    @classmethod
    def _dgvoodoo_template(cls) -> Optional[Path]:
        """The stock dgVoodoo.conf, patched rather than replaced so unrelated keys survive."""
        template = COMPONENTS_DIR / COMPONENTS_REGISTRY[COMP_DGVOODOO].target_subdir / "dgVoodoo.conf"
        return template if template.exists() else None

    # Chicken shares the NGX feature-1 entry points with these, and its documentation for
    # that situation is "one or the other, never both".
    RIVAL_NEURAL_ADDONS = (
        "renodx-dlss5.addon64",
        "renodx-dlss.addon64",
        "alexs-toolkit.addon64",
        "dlss5-dx11-bridge.addon64",
    )

    @classmethod
    def _install_dfc_config(cls, game_dir: Path, profile: Optional[Dict[str, Any]]) -> List[str]:
        """Copy Chicken's own config in and patch the keys this tool owns.

        Its config is 346 lines across thirty layer blocks. Generating that from a partial
        idea of the schema is how alexs-toolkit.cfg lost five keys, so the vendor file is
        carried through and only layers, arm and enabled are set.
        """
        source = USER_SUPPLIED_DIR / "deep-fried-chicken.cfg"
        if not source.is_file():
            return [
                "deep-fried-chicken.cfg NOT written: import it from the release zip - this "
                "tool patches that file rather than writing one from scratch"
            ]
        target = game_dir / ADDON_DIR_NAME / "deep-fried-chicken.cfg"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            vendor = source.read_text(encoding="utf-8", errors="replace")
            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write(ConfigGenerator.patch_dfc_cfg(vendor, profile))
        except OSError as exc:
            return [f"deep-fried-chicken.cfg could not be written ({exc.strerror or exc})"]
        return [f"{ADDON_DIR_NAME}/deep-fried-chicken.cfg"]

    @classmethod
    def _remove_rival_neural_addons(cls, game_dir: Path, backup_dir: Path) -> List[str]:
        """Move the other neural consumers out of the add-on folder, into the backup.

        Not deleted. These are add-ons the user chose at some point, and one of them is
        this tool's own default - a later install without Chicken selected puts RenoDX
        straight back. Leaving them in place is the one thing that cannot be allowed:
        Chicken and RenoDX both claim the same entry points, and the loser reports itself
        ready while doing nothing.
        """
        addon_dir = game_dir / ADDON_DIR_NAME
        if not addon_dir.is_dir():
            return []
        moved: List[str] = []
        for name in cls.RIVAL_NEURAL_ADDONS:
            path = addon_dir / name
            if not path.is_file():
                continue
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_dir / name)
                path.unlink()
                moved.append(name)
            except OSError:
                pass
        return moved

    @classmethod
    def _remove_superseded_proxy(
        cls, game_dir: Path, backup_dir: Path, plan: StrategyPlan
    ) -> List[str]:
        """Delete a ReShade proxy DLL from a previous install under a different name.

        ReShade attaches by being named after a DLL the process loads, and the right name
        is a property of the executable - so a reinstall can legitimately change it, as it
        does the first time a game is found not to import the name the API implied. What
        must not happen is both files being left in the folder: two copies of ReShade in
        one process is a worse failure than the one being fixed, and it only shows up as
        a duplicated overlay long after the install said it succeeded.

        Only proxy names the previous manifest claims are touched, and never the one this
        install just placed.
        """
        keeping = {
            item.relative_dest.lower()
            for item in plan.items
            if item.relative_dest
        }
        manifest = backup_dir / cls.MANIFEST_FILE_NAME
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8")).get("injected_files")
        except (OSError, ValueError, AttributeError):
            return []
        if not isinstance(previous, list):
            return []

        removed: List[str] = []
        for entry in previous:
            name = str(entry).replace("/", os.sep)
            if os.sep in name or name.lower() not in RESHADE_PROXY_FALLBACK_ORDER:
                continue
            if name.lower() in keeping:
                continue
            stale = game_dir / name
            if not stale.is_file():
                continue
            try:
                # It was ours, so it goes back to the backup rather than into thin air.
                shutil.copy2(stale, backup_dir / name)
                stale.unlink()
                removed.append(name)
            except OSError:
                pass
        return removed

    @classmethod
    def _remove_superseded_root_addons(cls, game_dir: Path, backup_dir: Path) -> List[str]:
        """Delete add-ons an older version of this tool put next to the executable.

        Before the add-on folder existed, our two add-ons installed into the game folder
        and AddonPath was ".\\". Installing the new layout on top leaves those files
        behind: harmless while AddonPath points elsewhere, but they are a second copy of
        the same add-on waiting for anything that resets that key, and they show up in
        every listing of what is in the folder.

        Only files the *previous manifest* claims are removed. That distinction is the
        whole safety property here - "renodx-dlss5.addon64 sits in this folder" is not
        evidence that this tool put it there, and deleting somebody's own install of the
        same add-on because it shares a name would be exactly the kind of thing this tool
        refuses to do to the other add-ons.
        """
        manifest = backup_dir / cls.MANIFEST_FILE_NAME
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8")).get("injected_files")
        except (OSError, ValueError, AttributeError):
            return []
        if not isinstance(previous, list):
            return []

        # Names recorded at the top level of the game folder, not in a subdirectory.
        claimed = {
            str(entry).replace("/", os.sep)
            for entry in previous
            if entry and os.sep not in str(entry).replace("/", os.sep)
        }

        removed: List[str] = []
        for name in sorted(claimed):
            if not name.lower().endswith((".addon64", ".addon32")):
                continue
            if not (game_dir / ADDON_DIR_NAME / name).is_file():
                continue  # superseded only if the new layout actually has it
            stale = game_dir / name
            if not stale.is_file():
                continue
            try:
                stale.unlink()
                removed.append(name)
            except OSError:
                pass
        return removed

    @staticmethod
    def _locked_targets(game_dir: Path, plan: StrategyPlan) -> List[str]:
        """Files this install would overwrite that something else currently holds open.

        Opening for append is the test, because it asks the filesystem the same question
        the copy is about to ask - can this be written - without changing a byte. A file
        that does not exist yet cannot be locked, and anything that fails for a reason
        other than a lock is left for the copy to report in context.
        """
        locked: List[str] = []
        for item in plan.items:
            if not item.relative_dest:
                continue
            target = game_dir / item.relative_dest
            if not target.is_file():
                continue
            try:
                with open(target, "ab"):
                    pass
            except PermissionError:
                locked.append(item.relative_dest)
            except OSError:
                pass
        return locked

    # The two runtimes are looked for in two different places by two different pieces of
    # code, and both are right.
    RUNTIMES_BESIDE_ADDONS = ("nvngx_dlssnr.dll", "nvngx_dlss.dll")

    @classmethod
    def _mirror_runtimes_beside_addons(cls, game_dir: Path) -> List[str]:
        """Put the nvngx runtimes in the add-on folder as well as the game folder.

        renodx-dlss5 resolves nvngx_dlssnr.dll against its own module directory - the
        string it prints when it fails is "was not found beside the addon" - while NGX
        looks for nvngx_dlss.dll next to the executable. Moving the add-on into a folder
        of its own therefore breaks the first lookup, and leaving the runtimes only in
        the folder would break the second.

        These are 165 MB and 59 MB, so a second copy is not something to do casually.
        A hard link is the same file under two names on one NTFS volume: no extra bytes,
        no sync problem, and deleting either name is just a name going away. The add-on
        folder is always a child of the game folder, so the two are always on the same
        volume and the link always works - but a copy is the fallback anyway, because a
        working install is worth 225 MB and an exotic filesystem is not worth failing on.

        Returns the relative paths written, for the manifest.
        """
        addon_dir = game_dir / ADDON_DIR_NAME
        if not addon_dir.is_dir():
            return []

        written: List[str] = []
        for name in cls.RUNTIMES_BESIDE_ADDONS:
            source = game_dir / name
            target = addon_dir / name
            if not source.is_file():
                continue
            try:
                # Always replaced rather than reused: an existing name here is a link to
                # whatever the last install put in the game folder, and telling that apart
                # from a link to the current one is not worth getting wrong.
                if target.exists():
                    target.unlink()
                os.link(source, target)
            except OSError:
                try:
                    shutil.copy2(source, target)
                except OSError:
                    continue
            written.append(f"{ADDON_DIR_NAME}/{name}")
        return written

    @staticmethod
    def _existing_vulkan_layer() -> Optional[str]:
        """The ReShade implicit Vulkan layer already registered on this machine, if any.

        Read-only, and silent on every failure: a missing key, a locked registry or a
        machine with no Vulkan at all all mean "no layer found", which is the state that
        makes the installer go and register one.
        """
        try:
            import winreg
        except ImportError:
            return None
        for root, key in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Khronos\Vulkan\ImplicitLayers"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Khronos\Vulkan\ImplicitLayers"),
        ):
            try:
                with winreg.OpenKey(root, key) as handle:
                    index = 0
                    while True:
                        try:
                            name, _, _ = winreg.EnumValue(handle, index)
                        except OSError:
                            break
                        if "reshade" in name.lower() and name.lower().endswith(".json"):
                            return name
                        index += 1
            except OSError:
                continue
        return None

    # The layer is registered once for the machine, but it only attaches to executables
    # named in this file, which sits beside the layer DLL that the registry entry points at.
    RESHADE_APPS_FILE = "ReShadeApps.ini"

    @classmethod
    def _reshade_vulkan_apps_file(cls, layer_json: Optional[str]) -> Optional[Path]:
        """Where ReShade keeps the list, derived from the layer registration itself.

        Taking it from the registry entry rather than assuming C:\\ProgramData means a
        ReShade installed somewhere else is still found.
        """
        if layer_json:
            beside = Path(layer_json).parent
            if beside.is_dir():
                return beside / cls.RESHADE_APPS_FILE
        program_data = os.environ.get("ProgramData")
        if program_data:
            return Path(program_data) / "ReShade" / cls.RESHADE_APPS_FILE
        return None

    @classmethod
    def _add_to_reshade_vulkan_apps(
        cls, exe: Path, layer_json: Optional[str]
    ) -> Tuple[bool, str]:
        """Put one executable on the layer's allow-list, leaving the rest of it alone.

        The file is one `Apps=` line of comma-separated full paths, written by ReShade's
        own setup with a UTF-8 BOM. Other entries are other games, so this appends rather
        than rewrites, and it compares case-insensitively because these are Windows paths
        that the user may well have typed in a different case than Windows stores them.
        """
        path = cls._reshade_vulkan_apps_file(layer_json)
        if path is None:
            return False, "could not work out where ReShade keeps its Vulkan application list"

        try:
            raw = path.read_text(encoding="utf-8-sig") if path.is_file() else "Apps=\n"
        except OSError as exc:
            return False, f"could not read {path.name}: {exc}"

        try:
            target = str(exe.resolve())
        except OSError:
            target = str(exe)

        lines = raw.splitlines(True)
        for index, line in enumerate(lines):
            if not line.startswith("Apps="):
                continue
            body = line.rstrip("\r\n")
            ending = line[len(body):] or "\n"
            entries = [entry for entry in body[len("Apps="):].split(",") if entry.strip()]
            if any(entry.strip().lower() == target.lower() for entry in entries):
                return True, f"{exe.name} was already listed in {path.name}"
            entries.append(target)
            lines[index] = "Apps=" + ",".join(entries) + ending
            break
        else:
            lines.append(f"Apps={target}\n")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # utf-8-sig: ReShade's setup leaves a BOM on this file and reads it back.
            path.write_text("".join(lines), encoding="utf-8-sig")
        except PermissionError:
            # The usual home for this file is C:\ProgramData\ReShade, which an ordinary
            # user cannot write to. Worth saying so plainly: the fix is one elevated
            # command, not a reinstall of anything.
            return False, (
                f"{path} is not writable without administrator rights. Add this line to it "
                f"from an elevated PowerShell, keeping the entries already there:\n"
                f"    Apps=<existing entries>,{target}"
            )
        except OSError as exc:
            return False, f"could not write {path.name}: {exc}"
        return True, f"added {exe.name} to {path.name}"

    @classmethod
    def register_reshade_vulkan_layer(cls, target_exe: str | Path) -> Tuple[bool, str]:
        """Register ReShade as a Vulkan layer for one executable.

        Vulkan has no local-DLL injection point, so ReShade attaches through the layer
        mechanism instead. This runs the ReShade setup headlessly against the executable
        that actually renders - for an RTX Remix install that is .trex/NvRemixBridge.exe,
        not the game. It writes a machine-level layer registration, which the same setup
        can undo.
        """
        exe = Path(target_exe)
        if not exe.exists():
            return False, f"Executable not found: {exe}"

        # The implicit-layer entry under HKLM is machine-wide, and for a long time this
        # read that as "one is registered, so this game is covered". It is not: the layer
        # loads into every Vulkan application and then checks the executable against
        # ReShadeApps.ini beside the layer DLL. A game missing from that list gets a
        # registered layer and no ReShade, which looks identical to the layer not working.
        #
        # The setup cannot simply be run again - it answers "Existing ReShade installation
        # found. Please uninstall it first" - so the entry is added directly instead.
        existing = cls._existing_vulkan_layer()
        if existing:
            listed, detail = cls._add_to_reshade_vulkan_apps(exe, existing)
            if not listed:
                return False, (
                    f"ReShade's Vulkan layer is registered ({existing}), but {exe.name} could "
                    f"not be added to its application list: {detail}. Until it is, the layer "
                    f"will load beside {exe.name} and attach to nothing."
                )
            return True, (
                f"ReShade's Vulkan layer was already registered ({existing}); {detail}."
            )

        setup = COMPONENTS_DIR / "reshade" / "ReShade_Setup_Addon.exe"
        if not setup.exists():
            return False, (
                "components/reshade/ReShade_Setup_Addon.exe is missing - download the ReShade "
                "component first."
            )

        try:
            result = subprocess.run(
                [str(setup), str(exe), "--api", "vulkan", "--headless"],
                capture_output=True, text=True, timeout=300,
            )
        except Exception as e:
            return False, f"Could not run the ReShade setup: {e}"

        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        if result.returncode != 0:
            return False, f"ReShade setup failed (exit {result.returncode}): {output}"

        # The setup writes the allow-list entry itself; this confirms it rather than
        # trusting it, because a layer registered without the entry is the silent failure
        # this whole path exists to avoid.
        listed, detail = cls._add_to_reshade_vulkan_apps(exe, cls._existing_vulkan_layer())
        suffix = f" ({detail})" if listed else f" - but {detail}"
        return True, f"ReShade registered as a Vulkan layer for {exe.name}{suffix}. {output}"

    @classmethod
    def prepare_build_folder(
        cls,
        analysis: GameAnalysis,
        strategy_override: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
        custom_output_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> BuildResult:
        """Create a self-contained, standalone build folder containing all files and 1-click batch scripts."""
        plan = StrategyEngine.build_plan(analysis, strategy_override, profile)
        
        # Determine build folder path
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sanitized_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in analysis.exe_name)
        
        if custom_output_dir:
            build_root = custom_output_dir
        else:
            build_root = BUILDS_DIR / f"DLSS5_Build_{sanitized_name}_{timestamp}"

        build_root.mkdir(parents=True, exist_ok=True)
        build_files_dir = build_root / "build_files"
        build_files_dir.mkdir(parents=True, exist_ok=True)

        files_copied: List[str] = []
        configs_written: List[str] = []
        warnings = list(plan.warnings)

        total_items = len(plan.items) + 6
        current_step = 0

        # 1. Copy plan binaries and shaders into build_files/
        for item in plan.items:
            current_step += 1
            if progress_callback:
                progress_callback(current_step / total_items, f"Staging {item.relative_dest}...")

            if not item.source_path or not item.source_path.exists():
                warnings.append(f"Source missing for {item.relative_dest} (file will not be included)")
                continue

            dest_path = build_files_dir / item.relative_dest
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            if item.source_path.is_file():
                shutil.copy2(item.source_path, dest_path)
                files_copied.append(item.relative_dest)
            elif item.source_path.is_dir():
                shutil.copytree(item.source_path, dest_path, dirs_exist_ok=True)
                files_copied.append(f"{item.relative_dest}/")

        # 2/3. ReShade configuration. Under Remix the game process does not render at all,
        #      so the only ReShade.ini that matters is the one beside the bridge renderer.
        current_step += 2
        if progress_callback:
            progress_callback(current_step / total_items, "Generating ReShade configuration...")
        if plan.uses_remix:
            bridge_ini = build_files_dir / ".trex" / "ReShade.ini"
            bridge_ini.parent.mkdir(parents=True, exist_ok=True)
            bridge_ini.write_text(ConfigGenerator.generate_bridge_reshade_ini(), encoding="utf-8")
            configs_written.append(".trex/ReShade.ini")
        else:
            # The neural add-on reads its settings from the ReShade.ini in its OWN folder.
            # For a 32-bit game that folder is host64/, so the game's ini gets the effect
            # setup and host64/ReShade.ini gets the neural settings.
            (build_files_dir / "ReShade.ini").write_text(
                ConfigGenerator.generate_reshade_ini(
                    analysis, profile,
                    neural_in_process=not plan.uses_host64 and not uses_dfc(profile),
                ),
                encoding="utf-8",
            )
            (build_files_dir / "DLSS5_Preset.ini").write_text(
                ConfigGenerator.generate_preset_ini(profile), encoding="utf-8"
            )
            configs_written.extend(["ReShade.ini", "DLSS5_Preset.ini"])

            if plan.uses_host64:
                host_dir = build_files_dir / HOST64_DEST
                host_dir.mkdir(parents=True, exist_ok=True)
                (host_dir / "ReShade.ini").write_text(
                    ConfigGenerator.generate_host64_reshade_ini(profile), encoding="utf-8"
                )
                configs_written.append("host64/ReShade.ini")

            toolkit_dir = build_files_dir / (HOST64_DEST if plan.uses_host64 else "")
            if (toolkit_dir / "alexs-toolkit.addon64").exists():
                (toolkit_dir / "alexs-toolkit.cfg").write_text(
                    ConfigGenerator.generate_toolkit_cfg(profile), encoding="utf-8"
                )
                configs_written.append(
                    (f"{HOST64_DEST}/" if plan.uses_host64 else "") + "alexs-toolkit.cfg"
                )

        # 4. Write dlss5-feed.cfg, then dgVoodoo.conf when the game presents through D3D9
        current_step += 1
        if not plan.uses_remix:
            (build_files_dir / "dlss5-feed.cfg").write_text(
                ConfigGenerator.generate_feed_cfg(analysis, profile), encoding="utf-8"
            )
            configs_written.append("dlss5-feed.cfg")

        if plan.uses_dgvoodoo:
            dgvoodoo_conf = ConfigGenerator.generate_dgvoodoo_conf(profile, cls._dgvoodoo_template())
            (build_files_dir / "dgVoodoo.conf").write_text(dgvoodoo_conf, encoding="utf-8")
            configs_written.append("dgVoodoo.conf")

        if plan.uses_dxvk:
            (build_files_dir / "dxvk.conf").write_text(
                ConfigGenerator.generate_dxvk_conf(analysis, profile), encoding="utf-8"
            )
            configs_written.append("dxvk.conf")

        # 5. Write OptiScaler.ini if FSR bridge
        if plan.strategy_id == STRATEGY_FSR_BRIDGE:
            optiscaler_ini = ConfigGenerator.generate_optiscaler_ini(profile)
            (build_files_dir / "OptiScaler.ini").write_text(optiscaler_ini, encoding="utf-8")
            configs_written.append("OptiScaler.ini")

        # 5b. Lift the RAGE video memory restrictions (GTA IV and friends behind dgVoodoo2)
        if plan.needs_rage_commandline:
            (build_files_dir / "commandline.txt").write_text(
                ConfigGenerator.generate_rage_commandline(profile), encoding="utf-8"
            )
            configs_written.append("commandline.txt")

        # 6. Write Batch scripts & README in root of build folder
        current_step += 1
        apply_bat = ConfigGenerator.generate_apply_batch(analysis)
        (build_root / "apply_dlss5.bat").write_text(apply_bat, encoding="utf-8")
        
        uninstall_bat = ConfigGenerator.generate_uninstall_batch(analysis)
        (build_root / "uninstall_dlss5.bat").write_text(uninstall_bat, encoding="utf-8")

        launcher_bat = ConfigGenerator.generate_launcher_batch(analysis, plan, profile)
        (build_files_dir / "launch_with_dlss5.bat").write_text(launcher_bat, encoding="utf-8")

        # A direct install writes this; a build folder did not, so "Prepare Build Only"
        # in Chicken mode shipped without its config and it started on the vendor
        # defaults. The add-on files were all there, which is what made it look complete.
        if uses_dfc(profile):
            configs_written.extend(cls._install_dfc_config(build_files_dir, profile))

        emulator = getattr(analysis, "emulator", None)
        if ConfigGenerator.emulator_can_hold_consumer(emulator):
            (build_files_dir / ConfigGenerator.HOLD_SCRIPT_NAME).write_text(
                ConfigGenerator.generate_hold_script(
                    emulator, ConfigGenerator.consumer_addon_name(profile)
                ),
                encoding="utf-8",
            )

        readme_text = ConfigGenerator.generate_readme_guide(analysis, plan.display_name, plan, profile)
        (build_root / "README_INSTALL.txt").write_text(readme_text, encoding="utf-8")
        configs_written.extend(["apply_dlss5.bat", "uninstall_dlss5.bat", "README_INSTALL.txt"])

        if progress_callback:
            progress_callback(1.0, "Build staging complete!")

        return BuildResult(
            success=True,
            mode="prepare_build",
            target_directory=build_root,
            files_copied=files_copied,
            configs_written=configs_written,
            message=f"Successfully generated build folder at: {build_root}",
            warnings=warnings,
        )

    @classmethod
    def install_to_game(
        cls,
        analysis: GameAnalysis,
        strategy_override: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> BuildResult:
        """Inject DLSS 5 mod directly into the game directory with a complete backup manifest."""
        game_dir = analysis.game_dir
        plan = StrategyEngine.build_plan(analysis, strategy_override, profile)
        
        backup_dir = game_dir / cls.BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = backup_dir / cls.MANIFEST_FILE_NAME

        files_copied: List[str] = []
        configs_written: List[str] = []
        backed_up_files: List[str] = []
        injected_files: List[str] = []
        warnings = list(plan.warnings)

        # Nothing is written until every file we intend to replace can actually be
        # replaced. Windows holds an exclusive lock on a loaded DLL, so installing over a
        # running game fails partway through - after some files have been copied and
        # before any config is written, which is the worst place to stop.
        locked = cls._locked_targets(game_dir, plan)
        if locked:
            return BuildResult(
                success=False,
                mode="direct_install",
                target_directory=game_dir,
                message=(
                    "Close the game first - these files are in use and cannot be replaced: "
                    + ", ".join(locked[:6])
                    + (f", and {len(locked) - 6} more" if len(locked) > 6 else "")
                    + ". Nothing has been changed."
                ),
                warnings=list(plan.warnings),
            )

        total_items = len(plan.items) + 7
        current_step = 0

        # Backup manifest structure
        manifest_data = {
            "timestamp": datetime.now().isoformat(),
            "target_exe": str(analysis.exe_path),
            "strategy": plan.strategy_id,
            "backed_up_files": [],
            "injected_files": [],
        }

        # 1. Process files from plan
        for item in plan.items:
            current_step += 1
            if progress_callback:
                progress_callback(current_step / total_items, f"Installing {item.relative_dest}...")

            if not item.source_path or not item.source_path.exists():
                warnings.append(f"Source missing for {item.relative_dest}")
                continue

            target_path = game_dir / item.relative_dest
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # If target already exists in game directory, back it up first. Some plan
            # items are whole directories (the shader includes), and copy2 raises
            # PermissionError on a directory - which turned every reinstall over an
            # existing include/ folder into a failed install partway through.
            if target_path.exists() and not str(target_path).startswith(str(backup_dir)):
                backup_dest = backup_dir / item.relative_dest
                backup_dest.parent.mkdir(parents=True, exist_ok=True)
                if not backup_dest.exists():
                    try:
                        if target_path.is_dir():
                            shutil.copytree(target_path, backup_dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(target_path, backup_dest)
                        backed_up_files.append(item.relative_dest)
                    except OSError as exc:
                        # A file we cannot read is a file we must not silently replace.
                        warnings.append(
                            f"Could not back up {item.relative_dest} ({exc.strerror or exc}); "
                            "installing over it anyway."
                        )

            # Copy file into place
            if item.source_path.is_file():
                shutil.copy2(item.source_path, target_path)
                files_copied.append(item.relative_dest)
                injected_files.append(item.relative_dest)
            elif item.source_path.is_dir():
                shutil.copytree(item.source_path, target_path, dirs_exist_ok=True)
                files_copied.append(f"{item.relative_dest}/")
                injected_files.append(f"{item.relative_dest}/")

        # 2. Write ReShade.ini
        current_step += 1
        if progress_callback:
            progress_callback(current_step / total_items, "Configuring ReShade.ini...")
        reshade_ini_path = game_dir / "ReShade.ini"
        if reshade_ini_path.exists() and "ReShade.ini" not in backed_up_files:
            shutil.copy2(reshade_ini_path, backup_dir / "ReShade.ini")
            backed_up_files.append("ReShade.ini")
        
        if plan.uses_remix:
            bridge_ini = game_dir / ".trex" / "ReShade.ini"
            bridge_ini.parent.mkdir(parents=True, exist_ok=True)
            bridge_ini.write_text(ConfigGenerator.generate_bridge_reshade_ini(), encoding="utf-8")
            configs_written.append(".trex/ReShade.ini")
            injected_files.append(".trex/ReShade.ini")
        else:
            reshade_ini_path.write_text(
                ConfigGenerator.generate_reshade_ini(
                    analysis, profile,
                    neural_in_process=not plan.uses_host64 and not uses_dfc(profile),
                ),
                encoding="utf-8",
            )
            configs_written.append("ReShade.ini")
            injected_files.append("ReShade.ini")

            # host64/ is a folder this tool creates, so there is nothing of the user's to
            # back up in it - but it is also the only ReShade a 32-bit game's neural
            # add-on ever reads, so without this file none of the settings above apply.
            if plan.uses_host64:
                host_dir = game_dir / HOST64_DEST
                host_dir.mkdir(parents=True, exist_ok=True)
                (host_dir / "ReShade.ini").write_text(
                    ConfigGenerator.generate_host64_reshade_ini(profile), encoding="utf-8"
                )
                configs_written.append("host64/ReShade.ini")
                injected_files.append("host64/ReShade.ini")

            # The feeder looks for the toolkit beside itself, so its settings file goes
            # wherever the add-on went - host64\\ for a 32-bit game, the add-on folder
            # otherwise.
            toolkit_rel = HOST64_DEST if plan.uses_host64 else ADDON_DIR_NAME
            toolkit_dir = game_dir / toolkit_rel
            if (toolkit_dir / "alexs-toolkit.addon64").exists():
                (toolkit_dir / "alexs-toolkit.cfg").write_text(
                    ConfigGenerator.generate_toolkit_cfg(profile), encoding="utf-8"
                )
                rel = toolkit_rel + "/alexs-toolkit.cfg"
                configs_written.append(rel)
                injected_files.append(rel)

        # 3. Write DLSS5_Preset.ini (in-process ReShade layouts only)
        current_step += 1
        if not plan.uses_remix:
            preset_ini = ConfigGenerator.generate_preset_ini(profile)
            (game_dir / "DLSS5_Preset.ini").write_text(preset_ini, encoding="utf-8")
            configs_written.append("DLSS5_Preset.ini")
            injected_files.append("DLSS5_Preset.ini")

        # 4. Write dlss5-feed.cfg, then dgVoodoo.conf when the game presents through D3D9.
        #    The add-on owns this file at runtime, so an existing one is backed up first.
        current_step += 1
        feed_cfg_path = game_dir / "dlss5-feed.cfg"
        if plan.uses_remix:
            feed_cfg_path = None
        if feed_cfg_path is not None and feed_cfg_path.exists() and "dlss5-feed.cfg" not in backed_up_files:
            shutil.copy2(feed_cfg_path, backup_dir / "dlss5-feed.cfg")
            backed_up_files.append("dlss5-feed.cfg")
        if feed_cfg_path is not None:
            feed_cfg_path.write_text(
                ConfigGenerator.generate_feed_cfg(analysis, profile), encoding="utf-8"
            )
            configs_written.append("dlss5-feed.cfg")
            injected_files.append("dlss5-feed.cfg")

            # The add-on now sits in a subfolder, and which of the two directories it
            # resolves this relative to is its business, not ours. The file is 128 bytes;
            # writing it to both costs nothing and removes the question.
            addon_dir = game_dir / ADDON_DIR_NAME
            if addon_dir.is_dir():
                (addon_dir / "dlss5-feed.cfg").write_text(
                    ConfigGenerator.generate_feed_cfg(analysis, profile), encoding="utf-8"
                )
                injected_files.append(f"{ADDON_DIR_NAME}/dlss5-feed.cfg")

        if plan.uses_dgvoodoo:
            dgvoodoo_path = game_dir / "dgVoodoo.conf"
            if dgvoodoo_path.exists() and "dgVoodoo.conf" not in backed_up_files:
                shutil.copy2(dgvoodoo_path, backup_dir / "dgVoodoo.conf")
                backed_up_files.append("dgVoodoo.conf")
            dgvoodoo_conf = ConfigGenerator.generate_dgvoodoo_conf(profile, cls._dgvoodoo_template())
            dgvoodoo_path.write_text(dgvoodoo_conf, encoding="utf-8")
            configs_written.append("dgVoodoo.conf")
            injected_files.append("dgVoodoo.conf")

        if plan.uses_dxvk:
            # An existing dxvk.conf is the player's own - they may already be running DXVK
            # with tuning of their own, and this file is small enough to lose silently.
            dxvk_path = game_dir / "dxvk.conf"
            if dxvk_path.exists() and "dxvk.conf" not in backed_up_files:
                shutil.copy2(dxvk_path, backup_dir / "dxvk.conf")
                backed_up_files.append("dxvk.conf")
            dxvk_path.write_text(
                ConfigGenerator.generate_dxvk_conf(analysis, profile), encoding="utf-8"
            )
            configs_written.append("dxvk.conf")
            injected_files.append("dxvk.conf")

        # 5. Write OptiScaler.ini if FSR bridge
        if plan.strategy_id == STRATEGY_FSR_BRIDGE:
            optiscaler_path = game_dir / "OptiScaler.ini"
            optiscaler_ini = ConfigGenerator.generate_optiscaler_ini(profile)
            optiscaler_path.write_text(optiscaler_ini, encoding="utf-8")
            configs_written.append("OptiScaler.ini")
            injected_files.append("OptiScaler.ini")

        # 5b. Lift the RAGE video memory restrictions. An existing commandline.txt is the
        #     player's own and may carry unrelated switches, so it is backed up first.
        if plan.needs_rage_commandline:
            cmdline_path = game_dir / "commandline.txt"
            if cmdline_path.exists() and "commandline.txt" not in backed_up_files:
                shutil.copy2(cmdline_path, backup_dir / "commandline.txt")
                backed_up_files.append("commandline.txt")
            cmdline_path.write_text(
                ConfigGenerator.generate_rage_commandline(profile), encoding="utf-8"
            )
            configs_written.append("commandline.txt")
            injected_files.append("commandline.txt")

        # 5a1b. Chicken's own config, and the add-ons it cannot share a process with.
        if uses_dfc(profile):
            for line in cls._install_dfc_config(game_dir, profile):
                configs_written.append(line)
            for removed in cls._remove_rival_neural_addons(game_dir, backup_dir):
                configs_written.append(f"removed {removed} (conflicts with Deep Fried Chicken)")

        # 5a2. The nvngx runtimes have to exist in two places at once, and an install
        #      that predates the add-on folder left its add-ons in the game folder.
        current_step += 1
        injected_files.extend(cls._mirror_runtimes_beside_addons(game_dir))
        for removed in cls._remove_superseded_root_addons(game_dir, backup_dir):
            configs_written.append(f"removed superseded {removed}")
        for removed in cls._remove_superseded_proxy(game_dir, backup_dir, plan):
            configs_written.append(f"removed superseded {removed}")

        # 5b. Vulkan hosts get no local DLL - ReShade attaches through the loader's layer
        #     mechanism, registered against this one executable.
        if getattr(plan, "needs_vulkan_layer", False):
            # The layer is enrolled per executable, so it has to name the one that
            # renders. A launcher does not: Prince of Persia's PrinceOfPersia.exe imports
            # no graphics API at all and hands off to POP3.EXE. The detector already works
            # out which siblings do render, so those are enrolled alongside the selection
            # rather than the user being told afterwards that they picked the wrong file.
            targets = [analysis.exe_path]
            for candidate in getattr(analysis, "renderer_candidates", []):
                sibling = analysis.game_dir / candidate
                if sibling.is_file():
                    targets.append(sibling)

            ok, message = True, ""
            for target in targets:
                target_ok, target_message = cls.register_reshade_vulkan_layer(target)
                ok = ok and target_ok
                message = f"{message} {target_message}".strip()
            configs_written.append(
                f"ReShade Vulkan layer: {'registered' if ok else 'FAILED'} - {message}"
            )
            if not ok:
                warnings_out = manifest_data.setdefault("warnings", [])
                warnings_out.append(
                    "ReShade could not be registered as a Vulkan layer automatically: "
                    f"{message} Run components/reshade/ReShade_Setup_Addon.exe manually, point it "
                    f"at {analysis.exe_name} and choose Vulkan."
                )

        # 6. Write helper launcher and uninstaller scripts
        launcher_bat = ConfigGenerator.generate_launcher_batch(analysis, plan, profile)
        (game_dir / "launch_with_dlss5.bat").write_text(launcher_bat, encoding="utf-8")

        uninstall_bat = ConfigGenerator.generate_uninstall_batch(analysis)
        (game_dir / "uninstall_dlss5.bat").write_text(uninstall_bat, encoding="utf-8")
        configs_written.extend(["launch_with_dlss5.bat", "uninstall_dlss5.bat"])
        injected_files.extend(["launch_with_dlss5.bat", "uninstall_dlss5.bat"])

        # The launcher drives this rather than the emulator directly, on emulators whose
        # log says when emulation stops. Without it a title that restarts emulation loses
        # the neural consumer for the whole session.
        emulator = getattr(analysis, "emulator", None)
        if ConfigGenerator.emulator_can_hold_consumer(emulator):
            hold_ps1 = ConfigGenerator.generate_hold_script(
                emulator, ConfigGenerator.consumer_addon_name(profile)
            )
            name = ConfigGenerator.HOLD_SCRIPT_NAME
            (game_dir / name).write_text(hold_ps1, encoding="utf-8")
            configs_written.append(name)
            injected_files.append(name)

        # Write manifest file
        manifest_data["backed_up_files"] = backed_up_files
        manifest_data["injected_files"] = list(set(injected_files))
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        if progress_callback:
            progress_callback(1.0, "Installation completed successfully!")

        return BuildResult(
            success=True,
            mode="direct_install",
            target_directory=game_dir,
            files_copied=files_copied,
            configs_written=configs_written,
            backup_manifest=manifest_path,
            message=f"DLSS 5 Neural Rendering successfully injected into {analysis.exe_name}!",
            warnings=warnings,
        )

    @classmethod
    def restore_and_uninstall(
        cls,
        game_dir: str | Path,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Tuple[bool, str, List[str]]:
        """Restore game directory to original clean state and remove all mod artifacts."""
        target = Path(game_dir).resolve()
        if not target.exists() or not target.is_dir():
            return False, f"Game directory not found: {target}", []

        backup_dir = target / cls.BACKUP_DIR_NAME
        manifest_path = backup_dir / cls.MANIFEST_FILE_NAME
        restored_files: List[str] = []
        removed_files: List[str] = []

        if progress_callback:
            progress_callback(0.2, "Reading backup manifest...")

        # 1. Known injected files list to remove
        known_mod_files = [
            "dlss5-feed.addon64",
            "dlss5-feed.addon32",
            "dlss5-feed.cfg",
            "dlss5-feed.log",
            "renodx-dlss5.addon64",
            "nvngx_dlssnr.dll",
            "nvngx_dlss.dll",
            "DLSS5_Feed.fx",
            "DLSS5_Preset.ini",
            "ReShade.ini",
            "ReShade.log",
            "dxgi.dll",
            "opengl32.dll",
            "ReShade64.dll",
            "ReShade32.dll",
            "D3D9.dll",
            "dgVoodoo.conf",
            "dgVoodooCpl.exe",
            "OptiScaler.ini",
            "commandline.txt",
            "d3d9.dll",
            "d3d8to9.dll",
            "NvRemixLauncher32.exe",
            "launch_with_dlss5.bat",
            "uninstall_dlss5.bat",
            ConfigGenerator.HOLD_SCRIPT_NAME,
            "dlss5-launch-state.ini",
        ]

        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                for inj in manifest_data.get("injected_files", []):
                    if inj not in known_mod_files:
                        known_mod_files.append(inj)
            except Exception:
                pass

        if progress_callback:
            progress_callback(0.5, "Removing mod files...")

        # Remove injected mod files
        for fname in known_mod_files:
            file_path = target / fname
            if file_path.exists() and file_path.is_file():
                try:
                    file_path.unlink()
                    removed_files.append(fname)
                except Exception:
                    pass

        # Remove the folders the installer creates
        for dir_name in (ADDON_DIR_NAME, "reshade-shaders", "host64", ".trex", "feed-vk-layer"):
            mod_dir = target / dir_name
            if mod_dir.exists() and mod_dir.is_dir():
                try:
                    shutil.rmtree(mod_dir)
                    removed_files.append(f"{dir_name}/")
                except Exception:
                    pass

        if progress_callback:
            progress_callback(0.8, "Restoring original backups...")

        # Restore backed up original files
        if backup_dir.exists():
            for item in backup_dir.rglob("*"):
                if item.is_file() and item.name != cls.MANIFEST_FILE_NAME:
                    rel = item.relative_to(backup_dir)
                    dest = target / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
                    restored_files.append(str(rel))

            try:
                shutil.rmtree(backup_dir)
            except Exception:
                pass

        if progress_callback:
            progress_callback(1.0, "Restore completed.")

        msg = f"Successfully restored vanilla game state! Removed {len(removed_files)} mod items, restored {len(restored_files)} backups."
        return True, msg, removed_files
