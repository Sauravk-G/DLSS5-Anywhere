"""
Component Downloader & Package Manager for DLSS5-Anywhere.
Fetches ReShade with Add-on Support, DLSS5-Feeder, LumeniteFX, the ReShade base
shader includes, dgVoodoo2, and OptiScaler.

Every component here is fetched from its real upstream. Nothing is substituted with
a locally generated stand-in: a component that cannot be downloaded is reported as
missing, because a placeholder that silently compiles to nothing is worse than an
absent file.
"""

from io import BytesIO
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
from typing import Callable, Dict, List, Optional, Tuple
import zipfile

import requests

from ..config import (
    COMP_DGVOODOO,
    COMP_FEEDER,
    COMP_DXVK,
    COMP_LUMENITE,
    COMP_MFG_UNLOCK,
    COMP_OPTISCALER,
    MFG_ADDON_NAME,
    MFG_MIN_DRIVER,
    COMP_RESHADE,
    COMP_REMIX,
    COMP_IMMERSE,
    COMP_RESHADE_SHADERS,
    COMP_VORT,
    COMPONENTS_REGISTRY,
)
from .components import ComponentManager

GITHUB_API_HEADERS = {
    "User-Agent": "DLSS5-Anywhere/1.0.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/vnd.github.v3+json",
}

# DLSS5-Feeder publishes the add-on binaries, the host helper and the shader as
# individual release assets. These are the files the project's install guide names.
FEEDER_REPO = "jlrouzies-fr/DLSS5-Feeder"
FEEDER_ASSETS = [
    "dlss5-feed.addon64",
    "dlss5-feed.addon32",
    "dlss5-feed-host64.exe",
    "DLSS5_Feed.fx",
]

class _SevenZipUnavailable(RuntimeError):
    """Nothing on this system could unpack a .7z release."""


# OptiScaler moved off `cdozdil` to its own organisation; the old path 404s.
OPTISCALER_REPO = "optiscaler/OptiScaler"

# LumeniteFX's default branch is 'mainline', not 'main'.
LUMENITE_ZIP_URL = "https://github.com/umar-afzaal/LumeniteFX/archive/refs/heads/mainline.zip"

# vort_Shaders cuts no releases, so the branch archive is the only way to fetch it.
VORT_ZIP_URL = "https://github.com/vortigern11/vort_Shaders/archive/refs/heads/main.zip"
IMMERSE_ZIP_URL = "https://github.com/martymcmodding/iMMERSE/archive/refs/heads/main.zip"

# crosire's shader repository; the 'slim' branch carries the includes with far less bulk.
RESHADE_SHADERS_ZIP_URLS = [
    "https://github.com/crosire/reshade-shaders/archive/refs/heads/slim.zip",
    "https://github.com/crosire/reshade-shaders/archive/refs/heads/master.zip",
]

# ReShade with add-on support. DLSS5-Feeder requires 6.8+, newest first.
RESHADE_ADDON_MIRRORS = [
    "https://reshade.me/downloads/ReShade_Setup_6.8.0_Addon.exe",
    "https://reshade.me/downloads/ReShade_Setup_6.7.0_Addon.exe",
    "https://reshade.me/downloads/ReShade_Setup_6.6.0_Addon.exe",
]

MACHINE_X64 = 0x8664
MACHINE_X86 = 0x14C

# Throwaway targets for harvesting a runtime of each architecture. The ReShade setup
# picks the build from the executable it is pointed at, so we point it at a copy of a
# system binary in a temp folder rather than at a game.
ARCH_STUB_SOURCES = {
    MACHINE_X64: [r"C:\Windows\System32\notepad.exe", r"C:\Windows\System32\write.exe"],
    MACHINE_X86: [r"C:\Windows\SysWOW64\notepad.exe", r"C:\Windows\SysWOW64\write.exe"],
}



def pe_machine(path: Path) -> Optional[int]:
    """Return the PE machine type of a binary, or None if it is not a PE file."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x400)
        if head[:2] != b"MZ":
            return None
        e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
        if head[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
            return None
        return struct.unpack_from("<H", head, e_lfanew + 4)[0]
    except Exception:
        return None


class ComponentDownloader:
    """Automated downloader for the public mod components, with progress callbacks."""

    @classmethod
    def download_component(
        cls,
        comp_id: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Tuple[bool, str]:
        """Download and unpack a specific component into its cache directory."""
        if comp_id not in COMPONENTS_REGISTRY:
            return False, f"Unknown component ID: {comp_id}"

        meta = COMPONENTS_REGISTRY[comp_id]
        if meta.is_user_supplied:
            return False, f"'{meta.name}' is a proprietary file and must be supplied by user."

        target_dir = ComponentManager.get_component_dir(comp_id)

        try:
            if progress_callback:
                progress_callback(0.05, f"Starting download for {meta.name}...")

            handlers = {
                COMP_FEEDER: cls._download_feeder,
                COMP_LUMENITE: cls._download_lumenite,
                COMP_VORT: cls._download_vort,
                COMP_IMMERSE: cls._download_immerse,
                COMP_RESHADE_SHADERS: cls._download_reshade_shaders,
                COMP_DGVOODOO: cls._download_dgvoodoo,
                COMP_DXVK: cls._download_dxvk,
                COMP_OPTISCALER: cls._download_optiscaler,
                COMP_MFG_UNLOCK: cls._download_mfg_unlock,
                COMP_RESHADE: cls._download_reshade_addon,
                COMP_REMIX: cls._download_remix,
            }
            handler = handlers.get(comp_id)
            if handler is None:
                return False, f"No download handler defined for {comp_id}"
            return handler(target_dir, progress_callback)

        except Exception as e:
            return False, f"Download failed for {meta.name}: {e}"

    # ------------------------------------------------------------------
    # DLSS5-Feeder
    # ------------------------------------------------------------------
    @classmethod
    def _download_feeder(
        cls,
        target_dir: Path,
        progress_callback: Optional[Callable[[float, str], None]],
        include_prerelease: bool = False,
    ) -> Tuple[bool, str]:
        """Fetch the DLSS5-Feeder add-on binaries, host helper and shader from GitHub releases."""
        if progress_callback:
            progress_callback(0.1, "Querying DLSS5-Feeder releases...")

        release = cls._pick_feeder_release(include_prerelease)
        if release is None:
            return False, (
                "Could not reach the DLSS5-Feeder releases API. Download the assets manually from "
                f"https://github.com/{FEEDER_REPO}/releases/latest into components/feeder/."
            )

        tag = release.get("tag_name", "unknown")
        assets = {a.get("name", ""): a.get("browser_download_url") for a in release.get("assets", [])}

        # Newer betas ship one zip holding the same files.
        zip_assets = [n for n in assets if n.lower().endswith(".zip") and "dlss5-feeder" in n.lower()]
        fetched: List[str] = []

        if zip_assets and not any(n in assets for n in FEEDER_ASSETS):
            if progress_callback:
                progress_callback(0.4, f"Downloading {zip_assets[0]}...")
            resp = requests.get(assets[zip_assets[0]], headers=GITHUB_API_HEADERS, timeout=60)
            if resp.status_code != 200:
                return False, f"Failed to download {zip_assets[0]} (HTTP {resp.status_code})"
            with zipfile.ZipFile(BytesIO(resp.content)) as z:
                for entry in z.namelist():
                    base = Path(entry).name
                    if base in FEEDER_ASSETS:
                        (target_dir / base).write_bytes(z.read(entry))
                        fetched.append(base)
        else:
            for idx, name in enumerate(FEEDER_ASSETS):
                url = assets.get(name)
                if not url:
                    continue
                if progress_callback:
                    progress_callback(0.2 + 0.7 * idx / len(FEEDER_ASSETS), f"Downloading {name}...")
                resp = requests.get(url, headers=GITHUB_API_HEADERS, timeout=60)
                if resp.status_code == 200:
                    (target_dir / name).write_bytes(resp.content)
                    fetched.append(name)

        if "DLSS5_Feed.fx" not in fetched:
            return False, (
                f"DLSS5-Feeder {tag} did not yield DLSS5_Feed.fx. Fetch the release assets manually "
                f"from https://github.com/{FEEDER_REPO}/releases/latest."
            )

        (target_dir / "VERSION.txt").write_text(tag, encoding="utf-8")

        missing = [n for n in FEEDER_ASSETS if n not in fetched]
        if progress_callback:
            progress_callback(1.0, f"DLSS5-Feeder {tag} installed.")
        msg = f"DLSS5-Feeder {tag} ready ({len(fetched)} files)."
        if missing:
            msg += f" Not published in this release: {', '.join(missing)}."
        return True, msg

    @classmethod
    def _pick_feeder_release(cls, include_prerelease: bool) -> Optional[Dict]:
        """Return the newest release dict, honouring the prerelease preference."""
        try:
            if include_prerelease:
                resp = requests.get(
                    f"https://api.github.com/repos/{FEEDER_REPO}/releases",
                    headers=GITHUB_API_HEADERS, timeout=15,
                )
                if resp.status_code == 200 and resp.json():
                    return resp.json()[0]
            resp = requests.get(
                f"https://api.github.com/repos/{FEEDER_REPO}/releases/latest",
                headers=GITHUB_API_HEADERS, timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # LumeniteFX (motion vector provider 3)
    # ------------------------------------------------------------------
    @classmethod
    def _download_lumenite(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Fetch the LumeniteFX shader pack: Shaders/ (with include/) and Textures/."""
        if progress_callback:
            progress_callback(0.3, "Downloading LumeniteFX (mainline)...")

        resp = requests.get(LUMENITE_ZIP_URL, headers=GITHUB_API_HEADERS, timeout=60)
        if resp.status_code != 200:
            return False, (
                f"Failed to download LumeniteFX (HTTP {resp.status_code}). Grab it manually from "
                "https://github.com/umar-afzaal/LumeniteFX (Code > Download ZIP)."
            )

        if progress_callback:
            progress_callback(0.7, "Extracting shaders, includes and textures...")

        shaders_out = target_dir / "Shaders"
        textures_out = target_dir / "Textures"
        shaders_out.mkdir(parents=True, exist_ok=True)
        textures_out.mkdir(parents=True, exist_ok=True)

        extracted = 0
        with zipfile.ZipFile(BytesIO(resp.content)) as z:
            for entry in z.namelist():
                if entry.endswith("/"):
                    continue
                parts = Path(entry).parts
                if "Shaders" in parts:
                    rel = Path(*parts[parts.index("Shaders") + 1:])
                    dest = shaders_out / rel
                elif "Textures" in parts:
                    rel = Path(*parts[parts.index("Textures") + 1:])
                    dest = textures_out / rel
                else:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(entry))
                extracted += 1

        if not (shaders_out / "lumenite_Kernel.fx").exists():
            return False, "LumeniteFX archive did not contain Shaders/lumenite_Kernel.fx."

        if progress_callback:
            progress_callback(1.0, "LumeniteFX Kernel ready.")
        return True, f"LumeniteFX installed ({extracted} files, provider 3 = Kernel 2.0)."

    # ------------------------------------------------------------------
    # vort_Shaders (motion vector provider 2)
    # ------------------------------------------------------------------
    @classmethod
    def _download_immerse(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Fetch iMMERSE and keep Launchpad plus the headers it includes.

        Only Launchpad is used here - it is the motion vector provider DLSS5_Feed.fx reads
        as DLSS5_MV_PROVIDER=1 - but the whole Shaders folder is cached, because a partial
        cache looks like a failed download to anyone who opens it, and because Launchpad's
        eight MartysMods/mmx_*.fxh headers are not a list worth maintaining by hand here.

        Downloaded from the author's repository rather than redistributed: iMMERSE is
        copyright (c) Pascal Gilcher, all rights reserved. The repository's own instruction
        is "Download ZIP", which is what this does.
        """
        if progress_callback:
            progress_callback(0.3, "Downloading iMMERSE (Launchpad)...")

        resp = requests.get(IMMERSE_ZIP_URL, headers=GITHUB_API_HEADERS, timeout=90)
        if resp.status_code != 200:
            return False, (
                f"Failed to download iMMERSE (HTTP {resp.status_code}). Grab it manually from "
                "https://github.com/martymcmodding/iMMERSE (Code > Download ZIP) and copy its "
                "Shaders folder into components/immerse/."
            )

        if progress_callback:
            progress_callback(0.7, "Extracting Launchpad and its MartysMods headers...")

        shaders_out = target_dir / "Shaders"
        textures_out = target_dir / "Textures"
        shaders_out.mkdir(parents=True, exist_ok=True)
        textures_out.mkdir(parents=True, exist_ok=True)

        extracted = 0
        with zipfile.ZipFile(BytesIO(resp.content)) as z:
            for entry in z.namelist():
                if entry.endswith("/"):
                    continue
                parts = Path(entry).parts
                # Textures matter as much as the shader: Launchpad samples
                # iMMERSE_bluenoise_opt.png, and without it ReShade refuses the effect.
                if "Shaders" in parts:
                    rel = Path(*parts[parts.index("Shaders") + 1:])
                    dest = shaders_out / rel
                elif "Textures" in parts:
                    rel = Path(*parts[parts.index("Textures") + 1:])
                    dest = textures_out / rel
                else:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(entry))
                extracted += 1

        if not (textures_out / "iMMERSE_bluenoise_opt.png").exists():
            return False, "iMMERSE archive did not contain Textures/iMMERSE_bluenoise_opt.png."
        if not (shaders_out / "MartysMods_LAUNCHPAD.fx").exists():
            return False, "iMMERSE archive did not contain Shaders/MartysMods_LAUNCHPAD.fx."
        if not (shaders_out / "MartysMods").is_dir():
            return False, (
                "iMMERSE archive had Launchpad but not its Shaders/MartysMods headers, so it "
                "would not compile."
            )

        if progress_callback:
            progress_callback(1.0, "iMMERSE Launchpad ready.")
        return True, f"iMMERSE installed ({extracted} files, provider 1 = Launchpad)."

    # ------------------------------------------------------------------
    def _download_vort(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Fetch Vortigern's shader pack: Shaders/ (with Includes/) and Textures/.

        The whole pack is cached even though only vort_Motion.fx and its headers are
        ever staged into a game - the archive is small, and a partial cache would look
        like a failed download to anyone who opened the folder.
        """
        if progress_callback:
            progress_callback(0.3, "Downloading vort_Shaders (main)...")

        resp = requests.get(VORT_ZIP_URL, headers=GITHUB_API_HEADERS, timeout=60)
        if resp.status_code != 200:
            return False, (
                f"Failed to download vort_Shaders (HTTP {resp.status_code}). Grab it manually "
                "from https://github.com/vortigern11/vort_Shaders (Code > Download ZIP)."
            )

        if progress_callback:
            progress_callback(0.7, "Extracting shaders, includes and textures...")

        shaders_out = target_dir / "Shaders"
        textures_out = target_dir / "Textures"
        shaders_out.mkdir(parents=True, exist_ok=True)
        textures_out.mkdir(parents=True, exist_ok=True)

        extracted = 0
        with zipfile.ZipFile(BytesIO(resp.content)) as z:
            for entry in z.namelist():
                if entry.endswith("/"):
                    continue
                parts = Path(entry).parts
                if "Shaders" in parts:
                    rel = Path(*parts[parts.index("Shaders") + 1:])
                    dest = shaders_out / rel
                elif "Textures" in parts:
                    rel = Path(*parts[parts.index("Textures") + 1:])
                    dest = textures_out / rel
                else:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(entry))
                extracted += 1

        if not (shaders_out / "vort_Motion.fx").exists():
            return False, "vort_Shaders archive did not contain Shaders/vort_Motion.fx."

        if progress_callback:
            progress_callback(1.0, "vort_MotionEffects ready.")
        return True, f"vort_Shaders installed ({extracted} files, provider 2 = vort_MotionEffects)."

    # ------------------------------------------------------------------
    # DXVK (D3D9 -> Vulkan)
    # ------------------------------------------------------------------
    @classmethod
    def _download_dxvk(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Fetch DXVK and keep only d3d9.dll for each architecture.

        The release is a .tar.gz laid out as dxvk-<version>/x32|x64/*.dll. Only d3d9.dll is
        kept: DXVK's dxgi.dll and d3d11.dll would take filenames ReShade itself uses on
        every other path in this tool, and installing them next to a D3D9 game achieves
        nothing anyway.
        """
        if progress_callback:
            progress_callback(0.15, "Looking up the latest DXVK release...")

        api = "https://api.github.com/repos/doitsujin/dxvk/releases/latest"
        resp = requests.get(api, headers=GITHUB_API_HEADERS, timeout=30)
        if resp.status_code != 200:
            return False, (
                f"Could not reach the DXVK release API (HTTP {resp.status_code}). Download the "
                "release from https://github.com/doitsujin/dxvk/releases and place x32/d3d9.dll "
                "and x64/d3d9.dll under components/dxvk/."
            )

        release = resp.json()
        tag = release.get("tag_name", "unknown")
        archive = next(
            (a for a in release.get("assets", []) if a.get("name", "").endswith(".tar.gz")),
            None,
        )
        if archive is None:
            return False, f"DXVK release {tag} carries no .tar.gz asset."

        if progress_callback:
            progress_callback(0.4, f"Downloading DXVK {tag}...")

        data = requests.get(archive["browser_download_url"], timeout=180)
        if data.status_code != 200:
            return False, f"Failed to download {archive['name']} (HTTP {data.status_code})."

        if progress_callback:
            progress_callback(0.75, "Extracting d3d9.dll (x32 and x64)...")

        extracted = []
        with tarfile.open(fileobj=BytesIO(data.content), mode="r:gz") as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if not member.isfile() or Path(member.name).name.lower() != "d3d9.dll":
                    continue
                arch = next((seg for seg in parts if seg in ("x32", "x64")), None)
                if arch is None:
                    continue
                dest = target_dir / arch / "d3d9.dll"
                dest.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    continue
                dest.write_bytes(source.read())
                extracted.append(f"{arch}/d3d9.dll")

        if not extracted:
            return False, f"DXVK {tag} archive contained no d3d9.dll."

        (target_dir / "VERSION.txt").write_text(tag, encoding="utf-8")
        if progress_callback:
            progress_callback(1.0, f"DXVK {tag} ready.")
        return True, f"DXVK {tag} installed ({', '.join(sorted(extracted))})."

    # ------------------------------------------------------------------
    # ReShade base includes (ReShade.fxh, DrawText.fxh)
    # ------------------------------------------------------------------
    @classmethod
    def _download_reshade_shaders(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Fetch the .fxh includes that DLSS5_Feed.fx and lumenite_Kernel.fx require."""
        content = None
        for url in RESHADE_SHADERS_ZIP_URLS:
            try:
                if progress_callback:
                    progress_callback(0.3, f"Downloading {url.rsplit('/', 1)[-1]}...")
                resp = requests.get(url, headers=GITHUB_API_HEADERS, timeout=60)
                if resp.status_code == 200:
                    content = resp.content
                    break
            except Exception:
                continue

        if content is None:
            return False, (
                "Could not download the ReShade shader repository. Install the standard shaders "
                "through the ReShade setup instead."
            )

        if progress_callback:
            progress_callback(0.7, "Extracting shader includes...")

        target_dir.mkdir(parents=True, exist_ok=True)
        found: List[str] = []
        with zipfile.ZipFile(BytesIO(content)) as z:
            for entry in z.namelist():
                base = Path(entry).name
                if base.endswith(".fxh") and "Shaders" in Path(entry).parts:
                    (target_dir / base).write_bytes(z.read(entry))
                    found.append(base)

        missing = [n for n in ("ReShade.fxh", "DrawText.fxh") if n not in found]
        if missing:
            return False, f"Shader repository fetched but missing: {', '.join(missing)}"

        if progress_callback:
            progress_callback(1.0, "ReShade shader includes ready.")
        return True, f"ReShade base includes ready ({len(found)} .fxh files)."

    # ------------------------------------------------------------------
    # dgVoodoo2 (D3D9/8 -> D3D11)
    # ------------------------------------------------------------------
    @classmethod
    def _download_dgvoodoo(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Download the dgVoodoo2 release zip and flatten the x86/x64 wrappers."""
        if progress_callback:
            progress_callback(0.2, "Querying dgVoodoo2 latest release...")

        download_url = None
        try:
            resp = requests.get(
                "https://api.github.com/repos/dege-diosg/dgVoodoo2/releases/latest",
                headers=GITHUB_API_HEADERS, timeout=8,
            )
            if resp.status_code == 200:
                for asset in resp.json().get("assets", []):
                    name = asset.get("name", "").lower()
                    if name.endswith(".zip") and "dgvoodoo" in name:
                        download_url = asset.get("browser_download_url")
                        break
        except Exception:
            pass

        if not download_url:
            download_url = "https://github.com/dege-diosg/dgVoodoo2/releases/download/v2.82.5/dgVoodoo2_82_5.zip"

        if progress_callback:
            progress_callback(0.5, "Downloading dgVoodoo2 archive...")

        resp = requests.get(download_url, headers=GITHUB_API_HEADERS, timeout=60)
        if resp.status_code != 200:
            return False, f"Failed to download dgVoodoo2 (HTTP {resp.status_code})"

        if progress_callback:
            progress_callback(0.8, "Extracting dgVoodoo2 files...")

        with zipfile.ZipFile(BytesIO(resp.content)) as z:
            z.extractall(target_dir)

        # Keep the architecture explicit; the plain D3D9.dll name is ambiguous and
        # shipping the wrong one is silent - the game simply never loads it.
        for arch in ("x86", "x64"):
            src = target_dir / "MS" / arch / "D3D9.dll"
            if src.exists():
                shutil.copy2(src, target_dir / f"D3D9_{arch}.dll")

        legacy = target_dir / "D3D9.dll"
        if legacy.exists():
            legacy.unlink()

        if progress_callback:
            progress_callback(1.0, "dgVoodoo2 wrapper installed.")
        return True, "dgVoodoo2 downloaded and unpacked (x86 + x64 wrappers)."

    # ------------------------------------------------------------------
    # RTX Remix
    # ------------------------------------------------------------------
    @classmethod
    def _download_remix(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Download the RTX Remix runtime and unpack it under components/remix/runtime/."""
        if progress_callback:
            progress_callback(0.1, "Querying RTX Remix releases...")

        url = None
        tag = "unknown"
        try:
            resp = requests.get(
                "https://api.github.com/repos/NVIDIAGameWorks/rtx-remix/releases/latest",
                headers=GITHUB_API_HEADERS, timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", tag)
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    # the plain release build, not the debug or symbol archives
                    if name.endswith("-release.zip"):
                        url = asset.get("browser_download_url")
                        break
        except Exception:
            pass

        if not url:
            return False, (
                "Could not find an RTX Remix release archive. Download the '-release.zip' from "
                "https://github.com/NVIDIAGameWorks/rtx-remix/releases and unpack it into "
                "components/remix/runtime/."
            )

        if progress_callback:
            progress_callback(0.3, f"Downloading RTX Remix {tag} (this one is large)...")

        archive = target_dir / Path(url).name
        resp = requests.get(url, headers=GITHUB_API_HEADERS, timeout=900, stream=True)
        if resp.status_code != 200:
            return False, f"Failed to download RTX Remix (HTTP {resp.status_code})"
        with open(archive, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)

        if progress_callback:
            progress_callback(0.8, "Unpacking the Remix runtime...")

        runtime = target_dir / "runtime"
        with zipfile.ZipFile(archive) as z:
            z.extractall(runtime)

        bridge = runtime / ".trex" / "NvRemixBridge.exe"
        client = runtime / "d3d9.dll"
        if not (bridge.exists() and client.exists()):
            return False, "Remix archive unpacked but d3d9.dll / .trex/NvRemixBridge.exe are missing."

        (target_dir / "VERSION.txt").write_text(tag, encoding="utf-8")
        if progress_callback:
            progress_callback(1.0, f"RTX Remix {tag} ready.")
        return True, f"RTX Remix {tag} ready (32-bit client + 64-bit bridge renderer)."

    # ------------------------------------------------------------------
    # OptiScaler
    # ------------------------------------------------------------------
    @classmethod
    def _download_mfg_unlock(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Fetch renodx-mfgunlock.addon64 from its latest GitHub release.

        The simplest component here: the release carries one asset and it is the add-on
        itself, so there is nothing to unpack and nothing to pick between. It is MIT
        licensed, which is why this downloads at all - the other add-ons that touch the
        neural path are user-supplied because their licences ask for it.
        """
        if progress_callback:
            progress_callback(0.15, "Looking up the latest MFG Ada Unlock release...")

        api = "https://api.github.com/repos/mavismmg/MFGAdaUnlock-RenoDx/releases/latest"
        manual = (
            "Download it from https://github.com/mavismmg/MFGAdaUnlock-RenoDx/releases and "
            f"place {MFG_ADDON_NAME} under components/mfg_unlock/."
        )
        resp = requests.get(api, headers=GITHUB_API_HEADERS, timeout=30)
        if resp.status_code != 200:
            return False, (
                f"Could not reach the MFG Ada Unlock release API (HTTP {resp.status_code}). "
                + manual
            )

        release = resp.json()
        tag = release.get("tag_name", "unknown")
        asset = next(
            (a for a in release.get("assets", [])
             if a.get("name", "").lower() == MFG_ADDON_NAME),
            None,
        )
        if asset is None:
            return False, f"MFG Ada Unlock release {tag} carries no {MFG_ADDON_NAME}. " + manual

        if progress_callback:
            progress_callback(0.5, f"Downloading MFG Ada Unlock {tag}...")

        binary = requests.get(
            asset["browser_download_url"], headers=GITHUB_API_HEADERS, timeout=90
        )
        if binary.status_code != 200:
            return False, (
                f"Download of {MFG_ADDON_NAME} failed (HTTP {binary.status_code}). " + manual
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / MFG_ADDON_NAME).write_bytes(binary.content)

        if progress_callback:
            progress_callback(1.0, f"MFG Ada Unlock {tag} ready.")
        return True, (
            f"MFG Ada Unlock {tag} ready ({len(binary.content):,} bytes). It needs an RTX 40 "
            f"card, driver {MFG_MIN_DRIVER}+, and a game that already ships DLSS Frame "
            "Generation."
        )

    @classmethod
    def _download_optiscaler(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Fetch the latest OptiScaler release and unpack it.

        Two things about this component have changed under it and both broke the download
        silently, so they are worth stating. The project moved from `cdozdil/OptiScaler`
        to the `optiscaler` organisation, and it now publishes a single .7z per release
        rather than a .zip. The old code asked a dead API, fell back to a hard-coded URL
        on the same dead org, and reported nothing more useful than HTTP 404.

        There is no hard-coded release URL any more. A pinned version rots the same way
        this one did, and failing with the real reason is more use than failing with a
        stale link.
        """
        if progress_callback:
            progress_callback(0.2, "Querying OptiScaler latest release...")

        try:
            resp = requests.get(
                f"https://api.github.com/repos/{OPTISCALER_REPO}/releases/latest",
                headers=GITHUB_API_HEADERS, timeout=15,
            )
        except requests.RequestException as exc:
            return False, f"Could not reach the OptiScaler releases API: {exc}"

        if resp.status_code != 200:
            return False, (
                f"OptiScaler releases API returned HTTP {resp.status_code}. Download the "
                f"latest release by hand from https://github.com/{OPTISCALER_REPO}/releases "
                "and unpack it into components/optiscaler/."
            )

        release = resp.json()
        tag = release.get("tag_name", "unknown")
        asset = cls._pick_optiscaler_asset(release.get("assets", []))
        if asset is None:
            return False, (
                f"OptiScaler {tag} publishes no archive this tool can read. Download it by "
                f"hand from https://github.com/{OPTISCALER_REPO}/releases and unpack it "
                "into components/optiscaler/."
            )

        name = asset.get("name", "")
        if progress_callback:
            progress_callback(0.4, f"Downloading {name}...")

        resp = requests.get(asset["browser_download_url"], headers=GITHUB_API_HEADERS, timeout=180)
        if resp.status_code != 200:
            return False, f"Failed to download {name} (HTTP {resp.status_code})"

        if progress_callback:
            progress_callback(0.8, "Extracting OptiScaler files...")

        with tempfile.TemporaryDirectory(prefix="dlss5_opti_") as tmp:
            staging = Path(tmp) / "unpacked"
            staging.mkdir()
            try:
                if name.lower().endswith(".7z"):
                    archive_path = Path(tmp) / name
                    archive_path.write_bytes(resp.content)
                    cls._extract_7z(archive_path, staging)
                else:
                    with zipfile.ZipFile(BytesIO(resp.content)) as z:
                        z.extractall(staging)
            except _SevenZipUnavailable as exc:
                detail = f" ({exc})" if str(exc) else ""
                return False, (
                    f"OptiScaler {tag} ships as .7z and nothing on this system can unpack "
                    f"it{detail}. Windows 10 and 11 include a tool that can, at "
                    r"C:\Windows\System32\tar.exe" + "; if that is missing, install 7-Zip, "
                    "or unpack the release into components/optiscaler/ by hand from "
                    f"https://github.com/{OPTISCALER_REPO}/releases."
                )
            except (zipfile.BadZipFile, OSError) as exc:
                return False, f"Could not unpack {name}: {exc}"

            published = cls._publish(staging, target_dir)

        published += cls._name_optiscaler_proxies(target_dir)

        if progress_callback:
            progress_callback(1.0, "OptiScaler installed.")
        return True, f"OptiScaler {tag} downloaded and unpacked ({published} files)."

    @staticmethod
    def _name_optiscaler_proxies(target_dir: Path) -> int:
        """Give OptiScaler.dll the names the installer looks for.

        Older releases shipped a pre-named dxgi.dll. Current ones ship one binary,
        OptiScaler.dll, and their own setup_windows.bat renames a copy of it to whichever
        proxy the game needs - dxgi.dll for the DXGI hook, nvngx.dll for the NVNGX
        interposer. Both roles are the same file under different names.

        Without this the download looks like it worked and the install quietly does not
        use OptiScaler at all: the plan asks for `optiscaler/dxgi.dll`, does not find it,
        and falls back to plain ReShade, so a game picked for the FSR bridge gets no
        bridge and no message saying so.
        """
        source = target_dir / "OptiScaler.dll"
        if not source.is_file():
            return 0
        created = 0
        for proxy in ("dxgi.dll", "nvngx.dll"):
            destination = target_dir / proxy
            if not destination.exists():
                shutil.copy2(source, destination)
                created += 1
        return created

    @staticmethod
    def _pick_optiscaler_asset(assets: list) -> Optional[dict]:
        """Choose the release archive to fetch.

        A .zip is preferred where one exists because the standard library can open it;
        .7z is what the project actually ships today. Anything else in a release - a
        checksum, an installer script - is not an archive of the component.
        """
        for suffix in (".zip", ".7z"):
            for asset in assets:
                name = str(asset.get("name", "")).lower()
                if name.endswith(suffix) and "optiscaler" in name:
                    return asset
        # Some releases name the archive after the version alone.
        for suffix in (".zip", ".7z"):
            for asset in assets:
                if str(asset.get("name", "")).lower().endswith(suffix):
                    return asset
        return None

    @staticmethod
    def _find_7z_tool() -> Optional[List[str]]:
        """A command line that can unpack a .7z, or None.

        Windows 10 (1803+) and Windows 11 ship bsdtar as C:\\Windows\\System32\\tar.exe, and
        libarchive reads 7-Zip archives - including ones using the BCJ2 filter, which is
        what OptiScaler compresses its DLLs with. That matters more than it sounds:
        py7zr, the obvious pure-Python choice, cannot decode BCJ2 and fails part-way
        through this exact archive, after writing the .ini files and before the .dll files
        that are the entire point of the component. Preferring the tool that is already
        installed keeps the dependency list at zero and actually works.

        System32 is addressed by full path rather than by name because `tar` on PATH is
        often GNU tar - Git for Windows ships one - and GNU tar cannot read 7-Zip at all.
        """
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidates = [
            [str(Path(system_root) / "System32" / "tar.exe"), "-xf"],
            [r"C:\Program Files\7-Zip\7z.exe", "x", "-y"],
            [r"C:\Program Files (x86)\7-Zip\7z.exe", "x", "-y"],
        ]
        for command in candidates:
            if Path(command[0]).is_file():
                return command
        found = shutil.which("7z") or shutil.which("bsdtar")
        if found:
            return [found, "x", "-y"] if "7z" in Path(found).name.lower() else [found, "-xf"]
        return None

    @classmethod
    def _extract_7z(cls, archive_path: Path, target_dir: Path) -> None:
        """Unpack a .7z into `target_dir`, or raise _SevenZipUnavailable."""
        tool = cls._find_7z_tool()
        if tool is None:
            raise _SevenZipUnavailable()

        target_dir.mkdir(parents=True, exist_ok=True)
        is_seven_zip = "7z" in Path(tool[0]).name.lower()
        command = (
            tool + [str(archive_path), f"-o{target_dir}"] if is_seven_zip
            else tool + [str(archive_path), "-C", str(target_dir)]
        )
        result = subprocess.run(
            command, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise _SevenZipUnavailable(
                (result.stderr or result.stdout or "").strip()[:200]
                or f"{Path(tool[0]).name} exited {result.returncode}"
            )

    @staticmethod
    def _publish(staged: Path, target_dir: Path) -> int:
        """Move a fully extracted staging directory into place. Returns files published.

        Downloads used to extract straight into the component directory, which meant a
        failure part-way through left whatever had been written so far sitting there - and
        `check_component_status` counts files, so a broken download reported the component
        as installed. Staging first means the component directory only ever sees a
        complete unpack.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        published = 0
        for source in staged.rglob("*"):
            if source.is_dir():
                continue
            destination = target_dir / source.relative_to(staged)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            published += 1
        return published

    # ------------------------------------------------------------------
    # ReShade with add-on support
    # ------------------------------------------------------------------
    @classmethod
    def _download_reshade_addon(
        cls, target_dir: Path, progress_callback: Optional[Callable[[float, str], None]]
    ) -> Tuple[bool, str]:
        """Download the ReShade add-on setup and extract both architecture builds.

        Both are needed: x86 for 32-bit games, x64 for 64-bit games and for the
        host64 helper folder a 32-bit game needs.
        """
        if progress_callback:
            progress_callback(0.15, "Connecting to reshade.me...")

        setup_exe = target_dir / "ReShade_Setup_Addon.exe"
        downloaded = False

        for mirror in RESHADE_ADDON_MIRRORS:
            try:
                if progress_callback:
                    progress_callback(0.3, f"Downloading {mirror.rsplit('/', 1)[-1]}...")
                resp = requests.get(mirror, headers=GITHUB_API_HEADERS, timeout=120, stream=True)
                if resp.status_code == 200:
                    with open(setup_exe, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            f.write(chunk)
                    downloaded = True
                    break
            except Exception:
                continue

        if not downloaded and not setup_exe.exists():
            return False, (
                "Could not download ReShade. Get the add-on build from https://reshade.me and place "
                "ReShade32.dll and ReShade64.dll in components/reshade/."
            )

        # The runtimes are compressed inside the installer, so ask the installer for them:
        # it runs headless and writes the build matching whatever executable it is given.
        if progress_callback:
            progress_callback(0.6, "Harvesting ReShade runtimes (headless install)...")
        found = cls._harvest_reshade_runtimes(setup_exe, target_dir, progress_callback)

        if not found:
            found = cls._extract_reshade_binaries(setup_exe, target_dir)

        if not found:
            return False, (
                "ReShade setup downloaded but no runtime could be obtained from it. Run "
                "components/reshade/ReShade_Setup_Addon.exe manually against one 32-bit and one "
                "64-bit game, then copy the resulting dxgi.dll files here as ReShade32.dll and "
                "ReShade64.dll."
            )

        missing = [a for a in ("x86", "x64") if a not in found]
        if progress_callback:
            progress_callback(1.0, "ReShade with add-on support ready.")
        msg = f"ReShade add-on runtime ready ({', '.join(sorted(found))})."
        if missing:
            msg += (
                f" The {', '.join(missing)} build could not be extracted - games of that "
                "architecture cannot be installed until it is present."
            )
        return True, msg

    @classmethod
    def _harvest_reshade_runtimes(
        cls,
        setup_exe: Path,
        target_dir: Path,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Path]:
        """Run the ReShade installer headless against a stub of each architecture.

        `ReShade_Setup.exe <exe> --api dxgi --headless` installs the runtime next to the
        executable it is pointed at, choosing the build from that executable's machine type.
        Pointing it at a copy of a system binary in a temp folder yields both runtimes
        without touching any game.
        """
        out: Dict[str, Path] = {}
        arch_names = {MACHINE_X86: ("x86", "ReShade32.dll"), MACHINE_X64: ("x64", "ReShade64.dll")}

        for machine, (arch, dest_name) in arch_names.items():
            stub_source = next(
                (Path(c) for c in ARCH_STUB_SOURCES[machine]
                 if Path(c).exists() and pe_machine(Path(c)) == machine),
                None,
            )
            if stub_source is None:
                continue

            try:
                with tempfile.TemporaryDirectory() as tmp:
                    work = Path(tmp)
                    stub = work / f"reshade_probe_{arch}.exe"
                    shutil.copy2(stub_source, stub)

                    if progress_callback:
                        progress_callback(0.7, f"Installing ReShade {arch} runtime...")

                    result = subprocess.run(
                        [str(setup_exe), str(stub), "--api", "dxgi", "--headless"],
                        capture_output=True, text=True, timeout=300,
                    )
                    produced = work / "dxgi.dll"
                    if result.returncode == 0 and produced.exists() and pe_machine(produced) == machine:
                        dest = target_dir / dest_name
                        shutil.copy2(produced, dest)
                        out[arch] = dest
            except Exception:
                continue

        return out

    @classmethod
    def _extract_reshade_binaries(cls, setup_exe: Path, target_dir: Path) -> Dict[str, Path]:
        """Extract ReShade32.dll / ReShade64.dll from the installer, verifying each PE's machine type.

        The setup executable embeds both runtimes; whichever is recovered is written to the
        name matching its *actual* architecture. Naming a 32-bit build ReShade64.dll produces
        an install that never loads and reports no error anywhere.
        """
        out: Dict[str, Path] = {}
        try:
            data = setup_exe.read_bytes()
        except Exception:
            return out

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            candidates: List[Path] = []

            # The installer is a plain PE with the runtimes appended; scan for embedded images.
            pos = data.find(b"MZ", 2)
            index = 0
            while pos != -1:
                size = cls._pe_image_size(data, pos)
                if size and size > 1_000_000:
                    candidate = tmp_dir / f"embedded_{index}.bin"
                    candidate.write_bytes(data[pos:pos + size])
                    candidates.append(candidate)
                    index += 1
                    pos = data.find(b"MZ", pos + size)
                else:
                    pos = data.find(b"MZ", pos + 2)

            for candidate in candidates:
                machine = pe_machine(candidate)
                if machine == MACHINE_X64 and "x64" not in out:
                    dest = target_dir / "ReShade64.dll"
                    shutil.copy2(candidate, dest)
                    out["x64"] = dest
                elif machine == MACHINE_X86 and "x86" not in out:
                    dest = target_dir / "ReShade32.dll"
                    shutil.copy2(candidate, dest)
                    out["x86"] = dest

        # Anything already in the folder counts, as long as it really is that architecture.
        arch_files = (("x86", "ReShade32.dll", MACHINE_X86), ("x64", "ReShade64.dll", MACHINE_X64))
        for arch, name, expected in arch_files:
            existing = target_dir / name
            if arch not in out and existing.exists() and pe_machine(existing) == expected:
                out[arch] = existing

        # A mislabelled leftover is worse than nothing - drop it rather than ship it.
        for arch, name, expected in arch_files:
            existing = target_dir / name
            if arch not in out and existing.exists() and pe_machine(existing) not in (expected, None):
                existing.unlink()

        return out

    @staticmethod
    def _pe_image_size(data: bytes, offset: int) -> Optional[int]:
        """Best-effort size of the PE image starting at offset, from its section table."""
        try:
            if len(data) < offset + 0x40:
                return None
            e_lfanew = struct.unpack_from("<I", data, offset + 0x3C)[0]
            pe = offset + e_lfanew
            if pe + 24 > len(data) or data[pe:pe + 4] != b"PE\x00\x00":
                return None
            num_sections = struct.unpack_from("<H", data, pe + 6)[0]
            opt_size = struct.unpack_from("<H", data, pe + 20)[0]
            sect = pe + 24 + opt_size
            end = 0
            for i in range(num_sections):
                base = sect + i * 40
                if base + 40 > len(data):
                    return None
                raw_size, raw_ptr = struct.unpack_from("<II", data, base + 16)
                end = max(end, raw_ptr + raw_size)
            return end or None
        except Exception:
            return None

    # ------------------------------------------------------------------
    @classmethod
    def download_all_public_components(
        cls, progress_callback: Optional[Callable[[str, float, str], None]] = None
    ) -> Dict[str, Tuple[bool, str]]:
        """Download every public component that is not already present."""
        results: Dict[str, Tuple[bool, str]] = {}
        public_ids = [
            COMP_RESHADE,
            COMP_RESHADE_SHADERS,
            COMP_FEEDER,
            COMP_LUMENITE,
            COMP_VORT,
            COMP_DGVOODOO,
            COMP_OPTISCALER,
        ]
        total = len(public_ids)

        for idx, comp_id in enumerate(public_ids):
            meta = COMPONENTS_REGISTRY[comp_id]
            status = ComponentManager.check_component_status(comp_id)
            if status.is_installed:
                results[comp_id] = (True, f"{meta.name} is already up to date.")
                continue

            def item_progress(frac: float, msg: str, _idx=idx, _meta=meta):
                if progress_callback:
                    progress_callback(_meta.name, (_idx + frac) / total, msg)

            results[comp_id] = cls.download_component(comp_id, item_progress)

        return results
