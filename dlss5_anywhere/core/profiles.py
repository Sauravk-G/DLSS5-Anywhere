"""
Profile Manager for DLSS5-Anywhere.
Handles saving, loading, importing, and exporting game-specific configurations and optimization presets.
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from ..config import DEFAULT_PROFILE, PROFILES_DIR

# Built-in Game & Strategy Presets
#
# Every preset used to carry the same values under a different name, so picking one
# changed nothing you could see. What actually separates them is the look: which
# LumeniteFX effects run after the neural pass, how hard the neural pass itself is
# driven ([RenoDX.DLSS5] in ReShade.ini), and how many passes the cascade runs.
BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "Balanced (Recommended)": {
        **DEFAULT_PROFILE,
        "feed_mode": 2,
        "feed_work_resolution": 100,
        "feed_preset": 0,
        "feed_mv_scale_x": 1.0,
        "mv_provider": 3,
        "depth_reversed": False,
        "depth_upsidedown": False,
        "nr_style": 0,                 # Natural
        # The default look: both AO passes plus TRAA. No SSSR - its reflections are the
        # least stable part of the suite, and no bloom, so the AO is what you see.
        "lumenite_effects": ["rtao", "lsao", "traa"],
        "toolkit_enabled": True,
        "toolkit_two_pass": True,
        "toolkit_three_pass": False,
    },
    "High Quality / Cinematic": {
        **DEFAULT_PROFILE,
        "feed_mode": 2,
        "feed_preset": 0,
        "feed_mv_scale_x": 1.0,
        "mv_provider": 3,
        "depth_reversed": False,
        "depth_upsidedown": False,
        # Cinematic style plus the three-pass cascade is the heaviest look available:
        # deepest neural reconstruction, and the most temporal history to smear if the
        # camera cuts. Meant for slow, scenic play, not for anything twitchy.
        "nr_style": 1,                 # Cinematic
        "nr_local_structure": 1.0,
        "nr_skin_structure": 1.0,
        "lumenite_effects": ["rtao", "lsao", "traa", "bloom"],
        "toolkit_enabled": True,
        "toolkit_two_pass": False,
        "toolkit_three_pass": True,
    },
    "High Framerate Performance (120 FPS Target)": {
        **DEFAULT_PROFILE,
        "feed_mode": 2,
        "feed_work_resolution": 75,
        "feed_preset": 5,
        "feed_mv_scale_x": 1.0,
        "mv_provider": 3,
        # QuantAO is the cheap AO; no screen-space reflections, no extra neural passes.
        "lumenite_effects": ["quantao"],
        "toolkit_enabled": False,
        "toolkit_two_pass": False,
        "toolkit_three_pass": False,
    },
    "DirectX 9 Classic Game (DXVK)": {
        **DEFAULT_PROFILE,
        "feed_mode": 2,
        "feed_work_resolution": 100,
        "feed_preset": 0,
        "mv_provider": 3,
        # D3D9 -> Vulkan in the game's own process. No watermark, and the memory figure the
        # engine reads back is a line in dxvk.conf rather than an emulated card's VRAM.
        "d3d9_translation": "dxvk",
        "dxvk_max_available_memory_mb": 4096,
        "rage_write_commandline": True,
        "lumenite_effects": ["rtao", "bloom"],
        "toolkit_enabled": True,
        "toolkit_two_pass": True,
        "toolkit_three_pass": False,
    },
    "DirectX 9 Classic Game (dgVoodoo2 fallback)": {
        **DEFAULT_PROFILE,
        "feed_mode": 2,
        "feed_work_resolution": 100,
        "feed_preset": 0,
        "dgvoodoo_resolution_scaling": "unforced",
        "dgvoodoo_vram_mb": 1024,
        "dgvoodoo_antialiasing": "appdriven",
        "mv_provider": 3,
        # For D3D8, and for the early titles that will not start under DXVK. The watermark
        # stays on because it is the only proof the wrapper loaded - it clears itself now.
        "d3d9_translation": "dgvoodoo",
        "dgvoodoo_watermark": True,
        "dgvoodoo_watermark_seconds": 15,
        "rage_write_commandline": True,
        # Screen-space reflections need a depth buffer ReShade has found cleanly, and
        # behind a wrapper that is the least reliable part of the stack - so AO and bloom
        # only. SSSR is off everywhere by default; behind dgVoodoo2 it is the last thing
        # to try, and only once ReShade's Depth tab shows a correct preview.
        "lumenite_effects": ["rtao", "bloom"],
        "toolkit_enabled": True,
        "toolkit_two_pass": True,
        "toolkit_three_pass": False,
    },
    "DirectX 12 / Modern (Unreal Engine 5)": {
        **DEFAULT_PROFILE,
        "feed_mode": 2,
        "feed_work_resolution": 100,
        "feed_preset": 0,
        "feed_mv_scale_x": 1.0,
        "depth_reversed": True,  # Common in modern UE4/UE5 reversed-Z depth buffers
        "depth_upsidedown": False,
        "mv_provider": 3,
        "lumenite_effects": ["rtao", "lsao", "traa"],
        "toolkit_enabled": True,
        "toolkit_two_pass": True,
        "toolkit_three_pass": False,
    },
    "Legacy 32-bit Game Preset": {
        **DEFAULT_PROFILE,
        "feed_mode": 2,
        "feed_work_resolution": 100,
        "feed_preset": 0,
        "dgvoodoo_vram_mb": 1024,
        "mv_provider": 3,
        "lumenite_effects": ["rtao", "bloom"],
        "toolkit_enabled": True,
        "toolkit_two_pass": True,
        "toolkit_three_pass": False,
    },
}


class ProfileManager:
    """Manages game profile persistence and tuning presets."""

    @classmethod
    def initialize_defaults(cls):
        """Create standard preset profiles in user profile directory if missing."""
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        for preset_name, data in BUILTIN_PRESETS.items():
            sanitized = cls._sanitize_filename(preset_name)
            file_path = PROFILES_DIR / f"{sanitized}.json"
            # Rewritten rather than only created: these files are the exported form of the
            # built-ins, and a stale copy from an older build is how a preset ends up
            # missing every setting added since.
            payload = json.dumps(data, indent=2)
            if not file_path.exists() or file_path.read_text(encoding="utf-8") != payload:
                file_path.write_text(payload, encoding="utf-8")

    @classmethod
    def list_profiles(cls) -> List[str]:
        """Return list of all available profile names (both custom and presets)."""
        cls.initialize_defaults()
        profiles = list(BUILTIN_PRESETS.keys())
        builtin_stems = {cls._sanitize_filename(n) for n in BUILTIN_PRESETS}
        for f in PROFILES_DIR.glob("*.json"):
            # Each built-in is also materialised as a file under a sanitised name; listing
            # that file as well showed the same preset twice, once under a name whose
            # punctuation had been stripped, and only one of the two was current.
            if f.stem in builtin_stems:
                continue
            name = f.stem.replace("_", " ").title()
            if name not in profiles:
                profiles.append(name)
        return sorted(profiles)

    @classmethod
    def load_profile(cls, profile_name: str) -> Dict[str, Any]:
        """Load a profile by name, falling back to defaults if not found."""
        if profile_name in BUILTIN_PRESETS:
            return dict(BUILTIN_PRESETS[profile_name])

        sanitized = cls._sanitize_filename(profile_name)
        file_path = PROFILES_DIR / f"{sanitized}.json"
        
        if file_path.exists():
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                return {**DEFAULT_PROFILE, **data}
            except Exception:
                pass

        return dict(DEFAULT_PROFILE)

    @classmethod
    def save_profile(cls, profile_name: str, settings: Dict[str, Any]) -> Path:
        """Save game configuration to JSON profile."""
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        sanitized = cls._sanitize_filename(profile_name)
        file_path = PROFILES_DIR / f"{sanitized}.json"
        file_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return file_path

    @classmethod
    def delete_profile(cls, profile_name: str) -> bool:
        """Delete a custom profile file."""
        sanitized = cls._sanitize_filename(profile_name)
        file_path = PROFILES_DIR / f"{sanitized}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    @classmethod
    def export_profile(cls, profile_name: str, destination_file: str | Path) -> bool:
        """Export a profile to an external JSON file."""
        data = cls.load_profile(profile_name)
        dest = Path(destination_file)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True

    @classmethod
    def import_profile(cls, source_file: str | Path) -> Tuple[bool, str]:
        """Import an external profile JSON file."""
        src = Path(source_file)
        if not src.exists() or not src.is_file():
            return False, f"Source file does not exist: {src}"

        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            name = src.stem.replace("_", " ").title()
            cls.save_profile(name, data)
            return True, f"Successfully imported profile '{name}'"
        except Exception as e:
            return False, f"Invalid profile JSON format: {str(e)}"

    @classmethod
    def _sanitize_filename(cls, name: str) -> str:
        """Sanitize profile name for valid filesystem usage."""
        cleaned = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_").lower()
        return cleaned or "default_profile"
