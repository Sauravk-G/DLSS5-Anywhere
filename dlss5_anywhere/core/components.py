"""
Component Repository & Validator for DLSS5-Anywhere.
Tracks status of public components and manages user-supplied proprietary files.
"""

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import struct
import shutil
from typing import Dict, List, Optional, Tuple

try:
    import pefile
    HAVE_PEFILE = True
except ImportError:
    HAVE_PEFILE = False

from ..config import (
    COMP_DGVOODOO,
    COMP_FEEDER,
    COMP_LUMENITE,
    COMP_NVNGX_DLSS,
    COMP_NVNGX_DLSSNR,
    COMP_OPTISCALER,
    COMP_RENODX,
    COMP_RESHADE,
    COMP_RESHADE_SHADERS,
    COMP_TOOLKIT,
    COMP_VORT,
    COMPONENTS_DIR,
    COMPONENTS_REGISTRY,
    ComponentMeta,
    USER_SUPPLIED_DIR,
)


@dataclass
class ComponentStatus:
    """Live status report of an individual mod component."""
    meta: ComponentMeta
    is_installed: bool = False
    found_files: List[Path] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    total_size_bytes: int = 0
    version_info: Optional[str] = None
    hash_sha256: Optional[str] = None
    detected_gpu_target: Optional[str] = None
    status_label: str = "Missing"
    status_color: str = "#FF5252"  # Red by default
    details: str = ""


class ComponentManager:
    """Manages local cache, validation, and status tracking for all DLSS 5 components."""

    @classmethod
    def get_component_dir(cls, comp_id: str) -> Path:
        """Return the target directory for a given component ID."""
        meta = COMPONENTS_REGISTRY.get(comp_id)
        if not meta:
            raise KeyError(f"Unknown component ID: {comp_id}")
        target_dir = COMPONENTS_DIR / meta.target_subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    @classmethod
    def check_component_status(cls, comp_id: str) -> ComponentStatus:
        """Check whether a specific component is installed and valid in local cache."""
        meta = COMPONENTS_REGISTRY[comp_id]
        comp_dir = cls.get_component_dir(comp_id)
        
        found_files: List[Path] = []
        missing_files: List[str] = []
        total_size = 0
        sha256_hash: Optional[str] = None
        gpu_target: Optional[str] = None

        # Check required files
        for expected in meta.expected_files:
            # Check directly in component directory or subdirectories
            matches = list(comp_dir.rglob(expected))
            if matches:
                found_files.append(matches[0])
                total_size += matches[0].stat().st_size
            else:
                missing_files.append(expected)

        # Also check fallback user_supplied directory if user-supplied
        if meta.is_user_supplied and missing_files:
            user_dir = USER_SUPPLIED_DIR
            for missing in list(missing_files):
                matches = list(user_dir.rglob(missing))
                if matches:
                    found_files.append(matches[0])
                    total_size += matches[0].stat().st_size
                    missing_files.remove(missing)

        # ReShade: both architectures matter, and each file has to really be that architecture.
        # A 32-bit build sitting under the name ReShade64.dll produces an install that never
        # loads, with no error anywhere, so it does not count as present.
        if comp_id == COMP_RESHADE:
            arch_ok = {
                "x86": cls._is_machine(comp_dir / "ReShade32.dll", 0x14C),
                "x64": cls._is_machine(comp_dir / "ReShade64.dll", 0x8664),
            }
            is_ready = all(arch_ok.values())
            if not is_ready:
                missing_files = [
                    label for label, ok in (("ReShade32.dll (x86)", arch_ok["x86"]),
                                            ("ReShade64.dll (x64)", arch_ok["x64"])) if not ok
                ]
        elif comp_id == COMP_LUMENITE:
            is_ready = (comp_dir / "Shaders" / "lumenite_Kernel.fx").exists()
        elif comp_id == COMP_VORT:
            # The pack is only ever staged for its motion vectors, and vort_Motion.fx
            # will not compile without the headers it includes - so both count.
            is_ready = (comp_dir / "Shaders" / "vort_Motion.fx").exists() and (
                comp_dir / "Shaders" / "Includes" / "vort_MotionVectors.fxh"
            ).exists()
        elif comp_id == COMP_FEEDER:
            # The .fx alone is not an install: the add-on binary is what appears in the
            # ReShade Add-ons tab and does the DLSS work.
            is_ready = (comp_dir / "DLSS5_Feed.fx").exists() and (
                (comp_dir / "dlss5-feed.addon64").exists() or (comp_dir / "dlss5-feed.addon32").exists()
            )
        elif comp_id == COMP_RESHADE_SHADERS:
            is_ready = (comp_dir / "ReShade.fxh").exists() and (comp_dir / "DrawText.fxh").exists()
        elif comp_id == COMP_DGVOODOO:
            is_ready = any(
                (comp_dir / rel).exists()
                for rel in ("D3D9_x86.dll", "D3D9_x64.dll", "MS/x86/D3D9.dll", "MS/x64/D3D9.dll")
            )
        elif comp_id == COMP_OPTISCALER:
            is_ready = any("dxgi.dll" in f.name.lower() or "optiscaler" in f.name.lower() for f in found_files)
        else:
            is_ready = len(missing_files) == 0 and len(found_files) > 0

        # Specialized validation for proprietary files
        if is_ready and comp_id == COMP_NVNGX_DLSSNR:
            primary_file = found_files[0]
            sha256_hash = cls._compute_sha256(primary_file)
            gpu_target = cls._detect_dlssnr_gpu_variant(primary_file)
        elif is_ready and comp_id == COMP_RENODX:
            primary_file = found_files[0]
            sha256_hash = cls._compute_sha256(primary_file)

        # Set status label and color
        if is_ready:
            status_label = "Ready"
            status_color = "#4CAF50"  # Green
            if comp_id == COMP_NVNGX_DLSSNR:
                details = f"Verified: {gpu_target} ({total_size / (1024*1024):.1f} MB)"
            elif comp_id == COMP_RENODX:
                details = f"Verified RenoDX Add-on ({total_size / 1024:.0f} KB)"
            else:
                details = f"Installed ({len(found_files)} files ready)"
        else:
            status_label = "Missing"
            status_color = "#FF5252"  # Red
            if meta.is_user_supplied:
                details = "User-supplied file required. Click 'Import' or place in components/user_supplied/"
            else:
                details = "Click 'Download' to automatically fetch latest release."

        return ComponentStatus(
            meta=meta,
            is_installed=is_ready,
            found_files=found_files,
            missing_files=missing_files,
            total_size_bytes=total_size,
            hash_sha256=sha256_hash,
            detected_gpu_target=gpu_target,
            status_label=status_label,
            status_color=status_color,
            details=details,
        )

    @classmethod
    def get_all_statuses(cls) -> Dict[str, ComponentStatus]:
        """Return status dictionary for all registered components."""
        return {comp_id: cls.check_component_status(comp_id) for comp_id in COMPONENTS_REGISTRY}

    @classmethod
    def are_core_components_ready(cls) -> Tuple[bool, List[str]]:
        """Check if minimum required components are ready for building."""
        statuses = cls.get_all_statuses()
        missing: List[str] = []

        essential = [
            COMP_RESHADE,
            COMP_RESHADE_SHADERS,
            COMP_FEEDER,
            COMP_LUMENITE,
            COMP_RENODX,
            COMP_NVNGX_DLSSNR,
            COMP_NVNGX_DLSS,
        ]
        for comp_id in essential:
            if not statuses[comp_id].is_installed:
                missing.append(COMPONENTS_REGISTRY[comp_id].name)

        return len(missing) == 0, missing

    @classmethod
    def import_user_file(cls, source_file: str | Path) -> Tuple[bool, str, Optional[str]]:
        """Import a user-supplied file (renodx addon or nvngx_dlssnr.dll) into cache."""
        source = Path(source_file).resolve()
        if not source.exists() or not source.is_file():
            return False, f"Source file does not exist: {source}", None

        file_name = source.name.lower()
        target_dir = USER_SUPPLIED_DIR
        target_dir.mkdir(parents=True, exist_ok=True)

        # Route on the file's own name. nvngx_dlss.dll is ~59 MB and nvngx_dlssnr.dll ~158 MB,
        # so a size threshold silently files one as the other.
        if "nvngx_dlssnr" in file_name:
            if not cls._is_machine(source, 0x8664):
                return False, "nvngx_dlssnr.dll must be the 64-bit NVIDIA runtime.", None
            target_path = target_dir / "nvngx_dlssnr.dll"
            shutil.copy2(source, target_path)
            gpu_variant = cls._detect_dlssnr_gpu_variant(target_path)
            return True, f"Successfully imported nvngx_dlssnr.dll ({gpu_variant})", COMP_NVNGX_DLSSNR

        elif file_name.startswith("nvngx_dlss."):
            if not cls._is_machine(source, 0x8664):
                return False, "nvngx_dlss.dll must be the 64-bit NVIDIA runtime.", None
            target_path = target_dir / "nvngx_dlss.dll"
            shutil.copy2(source, target_path)
            size_mb = target_path.stat().st_size / (1024 * 1024)
            return True, f"Successfully imported nvngx_dlss.dll ({size_mb:.1f} MB)", COMP_NVNGX_DLSS

        elif "alexs-toolkit" in file_name or "alexs_toolkit" in file_name:
            # This has to be tested before the generic .addon64 branch below, which would
            # otherwise file the toolkit as renodx-dlss5.addon64 and overwrite the neural
            # add-on with a different add-on entirely.
            if not cls._is_machine(source, 0x8664):
                return False, (
                    f"'{source.name}' is not a 64-bit binary. Alex's Toolkit runs alongside "
                    "renodx-dlss5, which is x64 only."
                ), None
            target_path = target_dir / "alexs-toolkit.addon64"
            shutil.copy2(source, target_path)
            return True, "Successfully imported alexs-toolkit.addon64", COMP_TOOLKIT

        elif "renodx" in file_name or file_name.endswith((".addon64", ".addon32")):
            # RenoDX DLSS 5 is 64-bit only; a 32-bit game reaches it through host64/,
            # so an .addon32 name here is always a mislabelled file.
            if not cls._is_machine(source, 0x8664):
                return False, (
                    f"'{source.name}' is not a 64-bit binary. The RenoDX DLSS 5 add-on only exists "
                    "as x64 - 32-bit games use it through the host64 helper, not an .addon32."
                ), None
            target_path = target_dir / "renodx-dlss5.addon64"
            shutil.copy2(source, target_path)
            return True, "Successfully imported renodx-dlss5.addon64", COMP_RENODX

        elif file_name.endswith(".fx") and "dlss5" in file_name:
            target_path = cls.get_component_dir(COMP_FEEDER) / "DLSS5_Feed.fx"
            shutil.copy2(source, target_path)
            return True, "Successfully imported DLSS5_Feed.fx shader", COMP_FEEDER

        elif file_name.endswith(".fx") and "lumenite" in file_name:
            target_path = cls.get_component_dir(COMP_LUMENITE) / "Lumenite_Kernel.fx"
            shutil.copy2(source, target_path)
            return True, "Successfully imported Lumenite_Kernel.fx shader", COMP_LUMENITE

        elif file_name.endswith(".fx") and "vort" in file_name:
            # Into Shaders/, which is where the installer and the readiness check both
            # look. A lone .fx is still not a working component - vort_Motion.fx needs
            # its Includes/ folder - so this stays "not ready" until the pack is
            # downloaded or the headers are copied in by hand.
            target_dir_vort = cls.get_component_dir(COMP_VORT) / "Shaders"
            target_dir_vort.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_dir_vort / "vort_Motion.fx")
            return True, "Successfully imported vort_Motion.fx shader", COMP_VORT

        else:
            # Fallback copy
            target_path = target_dir / source.name
            shutil.copy2(source, target_path)
            return True, f"Imported {source.name} to user_supplied folder", None

    @classmethod
    def auto_scan_and_import_existing(cls, extra_locations: Optional[List[Path]] = None) -> List[str]:
        """Scan Downloads/Desktop (plus any caller-supplied folders) for the user-supplied files."""
        imported: List[str] = []
        search_locations = [
            Path.home() / "Downloads",
            Path.home() / "Desktop",
        ]
        if extra_locations:
            search_locations.extend(Path(loc) for loc in extra_locations)

        target_dlssnr = USER_SUPPLIED_DIR / "nvngx_dlssnr.dll"
        target_dlss = USER_SUPPLIED_DIR / "nvngx_dlss.dll"
        target_renodx = USER_SUPPLIED_DIR / "renodx-dlss5.addon64"

        for loc in search_locations:
            if not loc.exists():
                continue
            try:
                # Fast direct check
                files_to_check = []
                if loc.is_file():
                    files_to_check.append(loc)
                else:
                    # Scan 1 level deep only
                    for item in loc.iterdir():
                        if item.is_file():
                            files_to_check.append(item)
                        elif item.is_dir() and item.name.lower() in ("gtaiv", "binaries", "plugins", "dlss5", "renodx", "mods"):
                            for sub_item in item.iterdir():
                                if sub_item.is_file():
                                    files_to_check.append(sub_item)

                for f in files_to_check:
                    fname = f.name.lower()
                    if fname == "nvngx_dlssnr.dll" and not target_dlssnr.exists():
                        cls.import_user_file(f)
                        imported.append(f"Auto-imported nvngx_dlssnr.dll from {f.parent.name}")
                    elif fname == "nvngx_dlss.dll" and not target_dlss.exists():
                        cls.import_user_file(f)
                        imported.append(f"Auto-imported nvngx_dlss.dll from {f.parent.name}")
                    elif (fname == "renodx-dlss5.addon64" or "renodx-dlss5" in fname) and not target_renodx.exists():
                        cls.import_user_file(f)
                        imported.append(f"Auto-imported renodx-dlss5.addon64 from {f.parent.name}")
            except Exception:
                pass

        return imported

    @classmethod
    def _detect_dlssnr_gpu_variant(cls, file_path: Path) -> str:
        """Detect whether nvngx_dlssnr.dll is RTX 50 Blackwell build or RTX 20/30/40 FP16 community patch."""
        size_mb = file_path.stat().st_size / (1024 * 1024)
        if size_mb > 150:
            return "NVIDIA Blackwell / Ada / Ampere v310.8 (Neural Rendering 158MB)"
        else:
            return f"Community Patched FP16 Build ({size_mb:.1f} MB)"

    @staticmethod
    def _is_machine(file_path: Path, expected_machine: int) -> bool:
        """True when file_path is a PE image built for expected_machine (0x14C x86, 0x8664 x64)."""
        try:
            with open(file_path, "rb") as f:
                head = f.read(0x400)
            if head[:2] != b"MZ":
                return False
            e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
            if head[e_lfanew:e_lfanew + 4] != b"PE" + bytes(2):
                return False
            return struct.unpack_from("<H", head, e_lfanew + 4)[0] == expected_machine
        except Exception:
            return False

    @classmethod
    def _compute_sha256(cls, file_path: Path) -> str:
        """Compute SHA256 hex digest of file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()[:16]
