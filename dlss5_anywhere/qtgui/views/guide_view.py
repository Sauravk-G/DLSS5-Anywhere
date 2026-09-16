"""
User guide: what the pipeline is, what hardware runs it, and what to do when it breaks.

The content is data, not layout. Every section below is a list of items that a shared
component renders, which is what lets a bullet wrap under its own text rather than under
its bullet, lets a term be a different weight from its explanation, and lets a keyboard
key be drawn as a key.
"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ...config import ANTI_CHEAT_GUIDANCE, ANTI_CHEAT_HEADLINE
from .. import theme
from ..components import (
    Banner,
    BulletList,
    Card,
    KeyCap,
    Pill,
    ScrollPage,
    SectionHeader,
    body_text,
    heading,
    hbox,
    vbox,
)

# --------------------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------------------
INTRO = (
    "DLSS 5 Neural Rendering is an experimental, community-enabled technology built on an "
    "unreleased NVIDIA runtime. Unlike a spatial or temporal upscaler it generates detail "
    "by inference rather than by resampling, so it needs the same inputs a game with DLSS "
    "would hand it: a depth buffer, and motion vectors. This tool assembles the pieces "
    "that produce those inputs for a game that never shipped any."
)

PIPELINE = [
    (
        "ReShade with add-on support  (dxgi.dll)",
        "Intercepts swapchain frames and the depth buffer, and hosts everything below. "
        "Must match the game's architecture: x86 ReShade for a 32-bit game, x64 for a "
        "64-bit one.",
    ),
    (
        "A motion vector provider  (top of the effect stack)",
        "The one part of this pipeline you choose. LumeniteFX Kernel 2.0 is the default - "
        "pyramidal optical flow and a confidence map at 1/8 resolution, needing no depth "
        "buffer. vort_MotionEffects is the alternative: full-resolution flow, finer "
        "vectors, more cost per frame.",
    ),
    (
        "DLSS5-Feeder  (dlss5-feed.addon64 + DLSS5_Feed.fx)",
        "The .fx declares whichever texture DLSS5_MV_PROVIDER names and samples it, which "
        "is why the provider's technique has to be enabled above the feed. The add-on runs "
        "the DLSS evaluate.",
    ),
    (
        "RenoDX DLSS 5 add-on  (renodx-dlss5.addon64, pinned to v4.55)",
        "Hooks that evaluate and runs neural rendering through nvngx_dlssnr.dll. 64-bit "
        "only.",
    ),
    (
        "For a 32-bit game: the host64\\ helper",
        "NGX has no 32-bit build, so dlss5-feed.addon32 hands the work to "
        "host64\\dlss5-feed-host64.exe. ReShade x64, the RenoDX add-on and both nvngx "
        "runtimes are installed there - never next to the game .exe.",
    ),
    (
        "For a DirectX 9 or 8 game: a translation layer first",
        "DXVK or dgVoodoo2 turns legacy Direct3D into something ReShade can host. ReShade "
        "is then installed as dxgi.dll, never as d3d9.dll - the wrapper owns that name.",
    ),
]

# (tier, support pill text, pill tone, what that means in practice)
HARDWARE = [
    ("RTX 50 series", "Full", "success",
     "Blackwell. Native hardware tensor acceleration, minimal latency."),
    ("RTX 40 series", "Full", "success",
     "Ada Lovelace. Excellent acceleration through 4th-gen tensor cores."),
    ("RTX 30 / 20 series", "Patched", "warning",
     "Ampere and Turing, through community FP16 CUDA kernel patches. Neural rendering is "
     "compute-heavy here - lower the work resolution to hold a frame rate."),
    ("AMD Radeon / Intel Arc", "Unsupported", "danger",
     "nvngx_dlssnr.dll requires NVIDIA CUDA tensor hardware. Nothing in this tool changes "
     "that."),
]

# (key, what it does)
HOTKEYS = [
    ("Home", "Opens the ReShade overlay."),
    ("F2", "Toggles every effect on and off, for an A/B comparison."),
]

IN_GAME = [
    ("On the Home tab, enable the motion vector technique first, then 'DLSS 5 Feed' "
     "below it.",
     "The feed samples what the provider writes, so a feed above it reads last frame's "
     "vectors, or nothing at all."),
    ("On the Add-ons tab, open 'DLSS 5 Feed' and turn neural rendering on.", None),
    ("Nothing in the Add-ons tab?",
     "The add-on next to the .exe is the wrong bitness. A 32-bit ReShade loads only "
     ".addon32 files, a 64-bit one only .addon64."),
    ("Effects list empty?",
     "ReShade.fxh / DrawText.fxh are missing from reshade-shaders\\Shaders, or "
     "EffectSearchPaths does not point there. ReShade.log names the failing include."),
    ("32-bit games: the first fed frame opens a '32-bit DLSS 5 Feeder' window.",
     "That helper process is where the DLSS 5 add-on's own panel lives."),
]

VERIFY = [
    ("feature ready … DLAA", None),
    ("frame N delivered", None),
    ("An MV probe that is not 0% non-zero while you are moving.",
     "Zero here means the provider's technique is not enabled, or is below the feed."),
]

TROUBLESHOOTING = [
    ("Sky reads as foreground, or halos around geometry",
     "The depth buffer is inverted. ReShade overlay → Edit global preprocessor "
     "definitions → toggle RESHADE_DEPTH_INPUT_IS_REVERSED between 0 and 1, then reload."),
    ("The image is upside down",
     "Toggle RESHADE_DEPTH_INPUT_IS_UPSIDEDOWN the same way."),
    ("The game crashes on startup",
     "Disable other overlays - MSI Afterburner, RivaTuner, Discord, Steam. If it persists, "
     "use Restore / Uninstall to return the folder to vanilla."),
    ("It installed but nothing looks different",
     "Check the Add-ons tab has neural rendering ticked and the Home tab has the effects "
     "ticked. With both off, a working install is a DLAA pass and very little else."),
]


class GuideView(QWidget):
    """In-depth instructions, architecture notes and troubleshooting."""

    def __init__(self, log_callback=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.log_callback = log_callback or (lambda msg, lvl: None)

        layout = vbox(self, margin=0)
        self.page = ScrollPage()
        layout.addWidget(self.page)

        self._masthead()
        self._pipeline_section()
        self._hardware_section()
        self._in_game_section()
        self._troubleshooting_section()

        self.page.add_spacing(theme.SPACE_MD)
        self.page.add(Banner(
            tone="danger",
            title=ANTI_CHEAT_HEADLINE,
            body=ANTI_CHEAT_GUIDANCE.replace("\n\n", "  "),
        ))
        self.page.finish()

    # ------------------------------------------------------------------
    def _section(self, title: str, subtitle: Optional[str] = None,
                 accent: str = theme.ACCENT_GREEN) -> Card:
        """A titled section: header outside the card, content inside it."""
        self.page.add_spacing(theme.SPACE_LG)
        self.page.add(SectionHeader(title, subtitle, accent=accent))
        card = Card()
        self.page.add(card)
        return card

    def _masthead(self) -> None:
        self.page.add(heading("How this works", "display"))
        self.page.add(body_text(INTRO))

    def _pipeline_section(self) -> None:
        card = self._section(
            "The pipeline",
            "Six things load in this order. Each one exists because the one after it "
            "cannot work without it.",
        )
        card.add(BulletList(PIPELINE))

    def _hardware_section(self) -> None:
        card = self._section(
            "Hardware",
            "Neural rendering runs on NVIDIA tensor hardware. What varies is how much of it.",
            accent=theme.ACCENT_CYAN,
        )
        for tier, support, tone, note in HARDWARE:
            line = QWidget()
            row = hbox(line, spacing=theme.SPACE_MD)

            name = body_text(tier, bold=True, wrap=False)
            name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
            name.setFixedWidth(178)
            name.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            row.addWidget(name, 0, Qt.AlignTop)

            row.addWidget(Pill(support, tone), 0, Qt.AlignTop)
            row.addWidget(body_text(note, small=True), 1)
            card.add(line)

    def _in_game_section(self) -> None:
        card = self._section("In the game")

        for key, what in HOTKEYS:
            line = QWidget()
            row = hbox(line, spacing=theme.SPACE_MD)
            row.addWidget(KeyCap(key), 0, Qt.AlignTop)
            row.addWidget(body_text(what), 1)
            card.add(line)

        card.add(BulletList(IN_GAME))

        heads_up = body_text(
            "Confirm it is really running - dlss5-feed.log, next to the game .exe:",
            small=True, bold=True,
        )
        heads_up.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        card.add(heads_up)
        card.add(BulletList(VERIFY, level=1))

    def _troubleshooting_section(self) -> None:
        card = self._section(
            "When it does not work",
            "Symptom first, because that is what you have.",
            accent=theme.COLOR_WARNING,
        )
        card.add(BulletList(TROUBLESHOOTING))
