"""
The Builder.

Four steps, numbered, in the order they have to happen: pick the game, read what it is,
choose how to reach it, write the files. The numbering is the page's spine - the chips
down the left edge fill in as each step is satisfied, so the state of the job is legible
without reading a word of it.
"""

import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QSlider,
    QWidget,
)

from ...config import (
    ADDON_DIR_NAME,
    DEFAULT_PROFILE,
    MV_PROVIDERS,
    MV_PROVIDER_CHOICES,
    NEURAL_CONSUMER_CHOICES,
    NEURAL_CONSUMER_LABELS,
    STRATEGY_DESCRIPTIONS,
    STRATEGY_DISPLAY_NAMES,
    STRATEGY_FEEDER_DX11_12,
    USER_SUPPLIED_DIR,
    MFG_ADDON_NAME,
    MFG_COUNT_RANGE,
    MFG_MIN_DRIVER,
    MFG_MULTIPLIER_CHOICES,
    MFG_MULTIPLIER_LABELS,
    dfc_same_device_target,
    mfg_settings,
    mv_provider_label,
    uses_dfc,
)
from ...core.components import ComponentManager
from ...core.detector import GameAnalysis, GameDetector
from ...core.installer import BuildResult, ModInstaller
from ...core.library_scanner import DiscoveredGame, GameLibraryScanner
from ...core.profiles import ProfileManager
from .. import theme
from ..components import (
    Banner,
    Card,
    CheckBox,
    DiagnosticTile,
    Pill,
    ScrollPage,
    StepHeader,
    ToggleSwitch,
    accent_rule,
    body_text,
    danger_button,
    hbox,
    heading,
    info_button,
    primary_button,
    secondary_button,
    vbox,
)
from ..dialogs import RiskConfirmDialog
from ..workers import run_async

DIAGNOSTIC_TILES = ("Architecture", "Primary 3D API", "Engine", "Native Upscalers")


# Which profile a fresh install starts on. Any name that is not in profiles/ falls back to
# the first one listed, so a renamed or deleted preset cannot leave the box empty.
FALLBACK_PROFILE = "Balanced (Recommended)"


class DashboardView(QWidget):
    """Select a game, read what it is, choose a strategy, install."""

    def __init__(self, log_callback=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.log_callback = log_callback or (lambda msg, lvl: None)

        self.current_analysis: Optional[GameAnalysis] = None
        self.discovered_games: List[DiscoveredGame] = []
        self._diag_columns = 0

        # The profile a build is made from. Everything the Dashboard has no widget for -
        # the effect list, the neural rendering sliders, the cascade, the D3D9 path - comes
        # from here, and until this existed it came from DEFAULT_PROFILE every time.
        self._profile_name: str = ""
        self._selected_profile: Dict[str, Any] = dict(DEFAULT_PROFILE)

        self._build_ui()
        self._load_quick_games()

    def _log(self, msg: str, level: str = "INFO") -> None:
        self.log_callback(msg, level)

    # ==================================================================
    # Layout
    # ==================================================================
    def _build_ui(self) -> None:
        layout = vbox(self, margin=0)
        self.page = ScrollPage()
        layout.addWidget(self.page)

        self._masthead()
        self._select_card()
        self._diagnostics_card()
        self._strategy_card()
        self._deploy_card()
        self.page.finish()

    def _masthead(self) -> None:
        card = Card()
        head = QWidget()
        row = hbox(head, spacing=theme.SPACE_MD)
        # An accent rule instead of an emoji: it reads at any font size, matches the nav
        # rail's active marker, and does not change shape between Windows versions.
        row.addWidget(accent_rule(theme.ACCENT_GREEN, 4, 34), 0, Qt.AlignTop)

        text = QWidget()
        col = vbox(text, spacing=1)
        col.addWidget(heading("Neural Rendering Anywhere", "title"))
        col.addWidget(body_text(
            "Installs DLSS 5 neural rendering into games that never shipped with it - "
            "DirectX 9, 11, 12, Vulkan, OpenGL and emulators."
        ))
        row.addWidget(text, 1)
        card.add(head)
        self.page.add(card)

    # ---- 1. game selection -----------------------------------------------------------
    def _select_card(self) -> None:
        card = Card()
        self.page.add(card)

        self.step_select = StepHeader(
            1, "Select the game",
            "The .exe the game actually launches - not a launcher or a shortcut.",
        )
        card.add(self.step_select)

        entry_row = QWidget()
        row = hbox(entry_row, spacing=theme.SPACE_SM)
        self.exe_entry = QLineEdit()
        self.exe_entry.setPlaceholderText(
            "Paste a game executable path, or click Browse (e.g. C:\\Games\\Game.exe)"
        )
        self.exe_entry.setFont(theme.font_body())
        self.exe_entry.setMinimumHeight(theme.HEIGHT_BUTTON)
        self.exe_entry.returnPressed.connect(self._on_manual_analyze)
        row.addWidget(self.exe_entry, 1)
        row.addWidget(secondary_button("Browse .exe", self._on_browse_exe, width=124))
        row.addWidget(primary_button("Analyze", self._on_manual_analyze, width=104))
        card.add(entry_row)

        quick_row = QWidget()
        qrow = hbox(quick_row, spacing=theme.SPACE_SM)
        qrow.addWidget(body_text("Quick pick from the installed library:",
                                 muted=True, small=True, wrap=False))
        self.quick_combo = QComboBox()
        self.quick_combo.setFont(theme.font_body())
        self.quick_combo.addItem("Scanning installed games…")
        self.quick_combo.setCursor(Qt.PointingHandCursor)
        self.quick_combo.activated.connect(self._on_quick_pick)
        qrow.addWidget(self.quick_combo, 1)
        card.add(quick_row)

    # ---- 2. diagnostics --------------------------------------------------------------
    def _diagnostics_card(self) -> None:
        card = Card()
        self.page.add(card)

        self.step_diagnose = StepHeader(
            2, "What it found",
            "Architecture, rendering API and engine, read out of the binary itself.",
        )
        card.add(self.step_diagnose)

        # The tiles reflow rather than assuming a window width: four across is right on a
        # wide window and clipped on a narrow one, and the app has to be usable at both.
        self.diag_host = QWidget()
        self.diag_grid = QGridLayout(self.diag_host)
        self.diag_grid.setContentsMargins(0, 0, 0, 0)
        self.diag_grid.setSpacing(theme.SPACE_SM)

        self.tiles = {
            name: DiagnosticTile(name, "Select game") for name in DIAGNOSTIC_TILES
        }
        card.add(self.diag_host)

        # How sure the API call is, sitting on the tile it qualifies. The API decides where
        # ReShade attaches, so "DirectX 11, probably" and "DirectX 11" are different answers
        # and the difference has to be visible before an install, not after.
        self.api_confidence = Pill("", "neutral")
        self.api_confidence.hide()
        self.tiles["Primary 3D API"].add(self.api_confidence)

        # What the detector actually read. Shown rather than hidden behind a tooltip,
        # because the whole point is that a wrong API can be argued with.
        self.api_evidence = body_text("", muted=True, small=True)
        self.api_evidence.hide()
        card.add(self.api_evidence)

        # The banner is the standing on-screen state; the modal that blocks the install is
        # separate and fires at the click.
        self.ac_banner = Banner("danger", "", "")
        self.ac_banner.hide()
        card.add(self.ac_banner)

        # What else is already loading into this game. ReShade loads every add-on sitting
        # next to the .exe, so a folder that already holds a dozen of them produces an
        # install nobody chose - and the symptoms (a flickering overlay, a frame graded
        # three times, DLSS reporting ready while another add-on holds the hooks) look
        # like this tool being broken. It has to be said before the install, not in a
        # log afterwards.
        self.addon_banner = Banner("warning", "", "")
        self.addon_banner.hide()
        card.add(self.addon_banner)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow_diagnostics()

    def _reflow_diagnostics(self) -> None:
        """Lay the diagnostic tiles out in as many columns as the width allows."""
        width = self.diag_host.width()
        if width <= 1:
            return
        columns = 4 if width >= 860 else (2 if width >= 460 else 1)
        if columns == self._diag_columns:
            return
        self._diag_columns = columns

        for index, name in enumerate(DIAGNOSTIC_TILES):
            self.diag_grid.addWidget(
                self.tiles[name], index // columns, index % columns
            )
        for column in range(4):
            self.diag_grid.setColumnStretch(column, 1 if column < columns else 0)

    # ---- 3. strategy and tuning ------------------------------------------------------
    def _strategy_card(self) -> None:
        card = Card()
        self.page.add(card)

        self.step_strategy = StepHeader(
            3, "Strategy and tuning",
            "Chosen from the diagnostics. Override it only if you know why.",
        )
        card.add(self.step_strategy)

        strat_row = QWidget()
        row = hbox(strat_row, spacing=theme.SPACE_SM)
        row.addWidget(body_text("Active strategy:", small=True, bold=True, wrap=False))
        self.strategy_combo = QComboBox()
        self.strategy_combo.setFont(theme.font_body())
        self.strategy_combo.addItems(list(STRATEGY_DISPLAY_NAMES.values()))
        self.strategy_combo.setCurrentText(STRATEGY_DISPLAY_NAMES[STRATEGY_FEEDER_DX11_12])
        self.strategy_combo.setCursor(Qt.PointingHandCursor)
        self.strategy_combo.setMinimumWidth(340)
        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        row.addWidget(self.strategy_combo)
        self.auto_badge = Pill("Auto-recommended", "accent")
        row.addWidget(self.auto_badge)
        row.addStretch(1)
        card.add(strat_row)

        self.strategy_desc = body_text(STRATEGY_DESCRIPTIONS[STRATEGY_FEEDER_DX11_12])
        card.add(self.strategy_desc)

        card.add(self._tuning_panel())

    def _tuning_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Sunken")
        col = vbox(panel, margin=theme.SPACE_MD, spacing=theme.SPACE_SM)

        # Which saved profile the build starts from. The controls below override the five
        # settings they cover, and selecting a profile loads its values into them - so what
        # is on screen is what gets built, rather than the profile being a separate thing
        # the builder never opened.
        zeroth = QWidget()
        row0 = hbox(zeroth, spacing=theme.SPACE_SM)
        row0.addWidget(body_text("Profile:", small=True, wrap=False))
        self.profile_combo = QComboBox()
        self.profile_combo.setFont(theme.font_body())
        self.profile_combo.setCursor(Qt.PointingHandCursor)
        self.profile_combo.setMinimumWidth(280)
        row0.addWidget(self.profile_combo)
        row0.addStretch(1)
        col.addWidget(zeroth)

        self.profile_note = body_text("", muted=True, small=True)
        col.addWidget(self.profile_note)

        first = QWidget()
        row1 = hbox(first, spacing=theme.SPACE_LG)
        self.host_window = ToggleSwitch("Show 32-bit helper window")
        self.host_window.setChecked(True)
        row1.addWidget(self.host_window)
        self.work_res_lbl = body_text(
            "Work resolution: 100% (64-bit D3D11 only)", small=True, wrap=False
        )
        row1.addWidget(self.work_res_lbl)
        self.work_res = QSlider(Qt.Horizontal)
        self.work_res.setRange(50, 100)
        self.work_res.setValue(100)
        self.work_res.setFixedWidth(180)
        self.work_res.setCursor(Qt.PointingHandCursor)
        self.work_res.valueChanged.connect(
            lambda v: self.work_res_lbl.setText(f"Work resolution: {v}% (64-bit D3D11 only)")
        )
        row1.addWidget(self.work_res)
        row1.addStretch(1)
        col.addWidget(first)

        second = QWidget()
        row2 = hbox(second, spacing=theme.SPACE_LG)
        self.depth_reversed = CheckBox("Invert depth buffer (reversed-Z)")
        row2.addWidget(self.depth_reversed)
        self.mv_scale_lbl = body_text("Motion vector scale: 1.00", small=True, wrap=False)
        row2.addWidget(self.mv_scale_lbl)
        # QSlider is integral, so the scale is carried in hundredths and divided out at
        # both ends.
        self.mv_scale = QSlider(Qt.Horizontal)
        self.mv_scale.setRange(50, 200)
        self.mv_scale.setValue(100)
        self.mv_scale.setFixedWidth(180)
        self.mv_scale.setCursor(Qt.PointingHandCursor)
        self.mv_scale.valueChanged.connect(
            lambda v: self.mv_scale_lbl.setText(f"Motion vector scale: {v / 100:.2f}")
        )
        row2.addWidget(self.mv_scale)
        row2.addStretch(1)
        col.addWidget(second)

        # Which effect estimates the vectors DLSS reconstructs from. It is a build-time
        # choice, not an in-game one: the provider decides which texture DLSS5_Feed.fx
        # declares, so changing it means regenerating the preset.
        third = QWidget()
        row3 = hbox(third, spacing=theme.SPACE_SM)
        row3.addWidget(body_text("Motion vectors:", small=True, wrap=False))
        self.mv_provider = QComboBox()
        self.mv_provider.setFont(theme.font_body())
        self.mv_provider.addItems(list(MV_PROVIDER_CHOICES))
        self.mv_provider.setCurrentText(
            mv_provider_label(int(DEFAULT_PROFILE.get("mv_provider", 3)))
        )
        self.mv_provider.setCursor(Qt.PointingHandCursor)
        self.mv_provider.setMinimumWidth(250)
        self.mv_provider.currentTextChanged.connect(self._on_mv_provider_changed)
        row3.addWidget(self.mv_provider)
        row3.addStretch(1)
        col.addWidget(third)

        self.mv_provider_note = body_text("", muted=True, small=True)
        col.addWidget(self.mv_provider_note)
        self._on_mv_provider_changed()

        # Which add-on runs the neural pass. It is here and not only on the Profiles tab
        # because it changes what gets written into the game folder - RenoDX with Alex's
        # Toolkit beside it, or Deep Fried Chicken instead of both - and that is a decision
        # people make per game, at the moment they install.
        fourth = QWidget()
        row4 = hbox(fourth, spacing=theme.SPACE_SM)
        row4.addWidget(body_text("Neural pass:", small=True, wrap=False))
        self.neural_consumer = QComboBox()
        self.neural_consumer.setFont(theme.font_body())
        self.neural_consumer.addItems(list(NEURAL_CONSUMER_CHOICES))
        self.neural_consumer.setCursor(Qt.PointingHandCursor)
        self.neural_consumer.setMinimumWidth(250)
        self.neural_consumer.currentTextChanged.connect(self._on_consumer_changed)
        row4.addWidget(self.neural_consumer)
        row4.addStretch(1)
        col.addWidget(fourth)

        self.consumer_note = body_text("", muted=True, small=True)
        col.addWidget(self.consumer_note)
        self._on_consumer_changed()

        # Multi frame generation, which is unlike every other switch on this page: it
        # multiplies frame generation a game already ships rather than adding anything,
        # so it does nothing at all on the no-DLSS games this tool is usually pointed at.
        # It sits here rather than in the profile editor because whether it applies is a
        # property of the game in the box above, not of the profile.
        fifth = QWidget()
        row5 = hbox(fifth, spacing=theme.SPACE_SM)
        self.mfg_unlock = ToggleSwitch("Multi frame generation (RTX 40)")
        self.mfg_unlock.setChecked(False)
        self.mfg_unlock.toggled.connect(self._on_mfg_changed)
        row5.addWidget(self.mfg_unlock)

        # Two separate things, and conflating them is the mistake worth designing out.
        # The multiplier is what runs; the ceiling is only what the runtime is told is
        # available, which matters when the game picks for itself.
        row5.addWidget(body_text("Multiplier:", small=True, wrap=False))
        self.mfg_multiplier = QComboBox()
        self.mfg_multiplier.setFont(theme.font_body())
        self.mfg_multiplier.addItems(list(MFG_MULTIPLIER_CHOICES))
        self.mfg_multiplier.setCursor(Qt.PointingHandCursor)
        self.mfg_multiplier.setMinimumWidth(210)
        self.mfg_multiplier.currentTextChanged.connect(self._on_mfg_changed)
        row5.addWidget(self.mfg_multiplier)

        row5.addWidget(body_text("Ceiling:", small=True, wrap=False))
        self.mfg_ceiling = QComboBox()
        self.mfg_ceiling.setFont(theme.font_body())
        self.mfg_ceiling.addItems([f"{n}x" for n in MFG_COUNT_RANGE])
        self.mfg_ceiling.setCurrentText("4x")
        self.mfg_ceiling.setCursor(Qt.PointingHandCursor)
        self.mfg_ceiling.currentTextChanged.connect(self._on_mfg_changed)
        row5.addWidget(self.mfg_ceiling)

        row5.addStretch(1)
        col.addWidget(fifth)

        self.mfg_note = body_text("", muted=True, small=True)
        col.addWidget(self.mfg_note)
        self._on_mfg_changed()

        # Filled last: applying a profile writes into every control above.
        self.reload_profiles()
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        return panel

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        """Re-read the profile list whenever this page comes back to the front.

        A profile saved on the Profiles tab has to appear here without a restart, and
        watching for a signal from that view would only cover profiles this application
        wrote - not one dropped into profiles/ or imported from a JSON file.
        """
        super().showEvent(event)
        self.reload_profiles(keep=self.profile_combo.currentText())

    def reload_profiles(self, keep: str = "") -> None:
        names = ProfileManager.list_profiles()
        wanted = keep or self._profile_name or FALLBACK_PROFILE
        if wanted not in names:
            wanted = names[0] if names else ""

        blocked = self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(names)
        if wanted:
            self.profile_combo.setCurrentText(wanted)
        self.profile_combo.blockSignals(blocked)

        if wanted and wanted != self._profile_name:
            self._on_profile_changed(wanted)

    # Shown on the Builder tab the moment Chicken is picked for a Direct3D 12 target,
    # because that install gives no other sign. See DFC_BLOCKED_TRANSPORT_API in config.
    DFC_D3D12_WARNING = (
        "NOT SUPPORTED ON THIS GAME - Deep Fried Chicken cannot run on Direct3D 12, where "
        "the feeder has to use the game's own device. It loads, arms and delivers frames, "
        "and its neural path is off the whole time (deep-fried-chicken.log: \"FP16 codec "
        "allocation failed: game-output device identity failed\"). Use RenoDX here, or "
        "build for the game's DirectX 11 mode if it has one."
    )

    def _active_neural_consumer(self) -> str:
        return NEURAL_CONSUMER_CHOICES.get(self.neural_consumer.currentText(), "renodx")

    def _mfg_profile_bits(self, reconcile: bool = True) -> Dict[str, Any]:
        """The three MFG values as a profile fragment, read straight from the controls.

        Reconciled through mfg_settings on the way out, so a forced 6x under a ceiling of
        4 is stored as 6 and 6 rather than as a pair that only agrees once something
        downstream fixes it. A saved profile is a file someone can open and read.
        """
        raw = {
            "mfg_unlock": self.mfg_unlock.isChecked(),
            "mfg_force_multiplier": MFG_MULTIPLIER_CHOICES.get(
                self.mfg_multiplier.currentText(), 0),
            "mfg_max_count": int(self.mfg_ceiling.currentText().rstrip("x") or 4),
        }
        if not reconcile:
            return raw
        force, count = mfg_settings(raw)
        return {**raw, "mfg_force_multiplier": force, "mfg_max_count": count}

    def _on_mfg_changed(self, _checked: bool = False) -> None:
        """Say what the unlock needs, and whether this game can use it at all.

        The one failure it produces is silence: on a game with no DLSS Frame Generation
        the add-on loads, registers, and has nothing to multiply. That is worth saying
        here, where the switch is, rather than only in the build warnings.
        """
        on = self.mfg_unlock.isChecked()
        self.mfg_multiplier.setEnabled(on)
        self.mfg_ceiling.setEnabled(on)
        if not on:
            self.mfg_note.setText(
                "Off. Turn this on only for a game that already has DLSS Frame Generation "
                "in its own settings - it multiplies frame generation, it cannot add it."
            )
            return

        force, _ = mfg_settings(self._mfg_profile_bits())
        if force:
            note = (
                f"Forcing {force}x whatever the game's own menu says. Change it in game from "
                f"the overlay: Home -> Add-ons -> MFG Unlock -> Force frame multiplier.  "
            )
        else:
            note = (
                "Following the game's own multiplier - right for a menu that offers 2x/3x/4x, "
                "and does nothing for one that only offers Frame Generation on/off. Pick a "
                "multiplier here if the game has no selector of its own.  "
            )
        note += (
            f"Needs an RTX 40 card and NVIDIA driver {MFG_MIN_DRIVER}+, neither of which "
            "this tool can read. Never use it in a game with anti-cheat, and expect a "
            "driver update to break it."
        )
        report = self.current_analysis
        if report is not None and not getattr(report, "has_frame_generation", False):
            note = (
                "NOTHING TO MULTIPLY - no nvngx_dlssg.dll or Streamline sl.dlss_g in this "
                "game folder, so this game has no DLSS Frame Generation for the unlock to "
                "work on. It will load and do nothing.  "
            ) + note
        elif not (USER_SUPPLIED_DIR.parent / "mfg_unlock" / MFG_ADDON_NAME).exists():
            note = (
                f"NOT DOWNLOADED - '{MFG_ADDON_NAME}' is not in components/mfg_unlock/. "
                "Fetch it on the Components tab.  "
            ) + note
        self.mfg_note.setText(note)

    def _chicken_cannot_run_here(self) -> bool:
        """Whether the analysed game would put Chicken on the transport it cannot use.

        False with nothing analysed yet: the consumer is picked before a game as often as
        after one, and a warning about a game that has not been chosen is noise.
        """
        report = self.current_analysis
        if report is None:
            return False
        return dfc_same_device_target(
            {"neural_consumer": self._active_neural_consumer()},
            emulator_api=getattr(report, "emulator_api", "") or "",
            primary_api=report.primary_api,
            is_64bit=report.is_64bit,
            translated=report.primary_api.startswith(("DirectX 9", "DirectX 8")),
        )

    def _on_consumer_changed(self, _choice: str = "") -> None:
        """Say what each choice actually installs, and whether it is available.

        Both are optional imports on the Components tab, and a choice that silently falls
        back is worse than one that says it cannot be honoured.
        """
        if self._active_neural_consumer() == "dfc":
            available = (USER_SUPPLIED_DIR / "deep-fried-chicken.addon64").exists()
            note = (
                "Deep Fried Chicken replaces RenoDX and runs its own multi-pass stack; "
                "Alex's Toolkit is not deployed alongside it. Passes come from the "
                "profile's Chicken setting."
            )
            if not available:
                note = ("NOT IMPORTED - 'deep-fried-chicken.addon64' is not in "
                        "components/user_supplied/, so the build would fall back to "
                        "RenoDX. Import it on the Components tab.  ") + note
            # The plan warns about this too, but only once a plan exists. Choosing the
            # consumer is where the decision is actually made, and this is the one target
            # where the resulting install looks completely healthy and does nothing.
            if self._chicken_cannot_run_here():
                note = self.DFC_D3D12_WARNING + "  " + note
        else:
            available = (USER_SUPPLIED_DIR / "alexs-toolkit.addon64").exists()
            note = (
                "RenoDX runs the neural pass; Alex's Toolkit cascades over it for the "
                "two- and three-pass looks."
            )
            if not available:
                note = ("Alex's Toolkit is not imported, so this build runs a single "
                        "neural pass - everything works, it just looks flatter.  ") + note
        self.consumer_note.setText(note)

    def _on_profile_changed(self, name: str) -> None:
        if not name:
            return
        self._profile_name = name
        self._selected_profile = {**DEFAULT_PROFILE, **ProfileManager.load_profile(name)}
        self._apply_profile_to_controls(self._selected_profile)
        self._describe_profile(name, self._selected_profile)

    def _apply_profile_to_controls(self, profile: Dict[str, Any]) -> None:
        """Push the five settings the Dashboard also owns into its widgets.

        Both directions matter. Without this the controls keep whatever they were showing
        and silently override the profile on the next build; with it, picking a profile
        moves the sliders where the user can see what it chose.
        """
        self.host_window.setChecked(bool(profile.get("feed_host_window", True)))
        self.work_res.setValue(int(profile.get("feed_work_resolution", 100)))
        self.mv_scale.setValue(int(round(float(profile.get("feed_mv_scale_x", 1.0)) * 100)))
        self.depth_reversed.setChecked(bool(profile.get("depth_reversed", False)))
        self.mfg_unlock.setChecked(bool(profile.get("mfg_unlock", False)))
        force, ceiling = mfg_settings(profile)
        self.mfg_multiplier.setCurrentText(
            MFG_MULTIPLIER_LABELS.get(force, next(iter(MFG_MULTIPLIER_CHOICES)))
        )
        self.mfg_ceiling.setCurrentText(f"{ceiling}x")
        self.mv_provider.setCurrentText(
            mv_provider_label(int(profile.get("mv_provider", DEFAULT_PROFILE["mv_provider"])))
        )
        self.neural_consumer.setCurrentText(
            NEURAL_CONSUMER_LABELS.get(
                str(profile.get("neural_consumer", "renodx")).lower(),
                NEURAL_CONSUMER_LABELS["renodx"],
            )
        )

    def _describe_profile(self, name: str, profile: Dict[str, Any]) -> None:
        effects = [str(key) for key in (profile.get("lumenite_effects") or [])]
        cascade = (
            "3-pass cascade" if profile.get("toolkit_three_pass")
            else "2-pass cascade" if profile.get("toolkit_two_pass")
            else "single pass"
        )
        consumer = "Deep Fried Chicken" if uses_dfc(profile) else "RenoDX"
        self.profile_note.setText(
            f"{name}: {consumer}, {cascade}, "
            f"effects: {', '.join(effects) if effects else 'none'}. "
            "Edit these on the Profiles tab."
        )

    # ---- 4. deploy -------------------------------------------------------------------
    def _deploy_card(self) -> None:
        card = Card()
        self.page.add(card)

        self.step_deploy = StepHeader(
            4, "Deploy",
            "Installing writes into the game folder and backs up what it replaces.",
        )
        card.add(self.step_deploy)

        buttons = QWidget()
        row = hbox(buttons, spacing=theme.SPACE_SM)
        self.install_btn = primary_button("1-Click install to game", self._on_install)
        self.prepare_btn = secondary_button("Prepare build only", self._on_prepare)
        self.restore_btn = danger_button("Restore / uninstall", self._on_restore)
        self.launch_btn = info_button("Launch game", self._on_launch)
        for btn in (self.install_btn, self.prepare_btn, self.restore_btn, self.launch_btn):
            btn.setMinimumHeight(40)
            row.addWidget(btn, 1)
        card.add(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        card.add(self.progress)

        self.status_lbl = body_text("Ready. Select a game to begin.", muted=True, small=True)
        card.add(self.status_lbl)

    # ==================================================================
    # Selecting a game
    # ==================================================================
    def _on_browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select game executable", "",
            "Game executable (*.exe);;All files (*.*)",
        )
        if path:
            self.exe_entry.setText(path)
            self._analyze(Path(path))

    def _on_manual_analyze(self) -> None:
        text = self.exe_entry.text().strip().strip('"')
        if not text:
            QMessageBox.warning(
                self, "No path", "Enter or browse for a game .exe path first."
            )
            return
        path = Path(text)
        if not path.is_file():
            QMessageBox.critical(self, "File not found", f"No executable at:\n{path}")
            return
        self._analyze(path)

    def _load_quick_games(self) -> None:
        run_async(
            self,
            lambda _report: GameLibraryScanner.scan_all(),
            on_done=self._on_quick_games_ready,
            on_error=lambda msg: self._log(f"Library quick scan error: {msg}", "WARNING"),
        )

    def _on_quick_games_ready(self, games: List[DiscoveredGame]) -> None:
        self.discovered_games = games
        self.quick_combo.clear()
        if not games:
            self.quick_combo.addItem("No library games found")
            return
        self.quick_combo.addItem("Select a game from the library…")
        self.quick_combo.addItems([f"[{g.source}] {g.name}" for g in games])

    def _on_quick_pick(self, index: int) -> None:
        # Index 0 is the placeholder, so the games start one further along.
        if index <= 0 or index > len(self.discovered_games):
            return
        game = self.discovered_games[index - 1]
        self.exe_entry.setText(str(game.exe_path))
        self._analyze(game.exe_path)

    def load_game_path(self, exe_path: Path) -> None:
        """Load a path chosen elsewhere - the Library tab calls this."""
        self.exe_entry.setText(str(exe_path))
        self._analyze(exe_path)

    # ==================================================================
    # Analysis
    # ==================================================================
    def _analyze(self, exe_path: Path) -> None:
        self._set_status(f"Analyzing {exe_path.name}…", theme.ACCENT_CYAN)
        self.progress.setValue(30)
        self._log(f"Analyzing game: {exe_path}", "INFO")

        run_async(
            self,
            lambda _report, p=exe_path: GameDetector.analyze(p),
            on_done=self._apply_analysis,
            on_error=self._on_analysis_failed,
        )

    def _on_analysis_failed(self, message: str) -> None:
        self.progress.setValue(0)
        self._log(f"Analysis failed: {message}", "ERROR")
        self._set_status(f"Analysis failed: {message}", theme.COLOR_DANGER)

    def _apply_analysis(self, report: GameAnalysis) -> None:
        self.current_analysis = report
        for step in (self.step_select, self.step_diagnose, self.step_strategy):
            step.set_done(True)

        # The consumer note is written when the dropdown changes, and part of what it says
        # now depends on the game - so analysing a game after the choice was made has to
        # rewrite it.
        self._on_consumer_changed()
        self._on_mfg_changed()

        self.tiles["Architecture"].set(
            f"{report.architecture.upper()} ({'64-bit' if report.is_64bit else '32-bit'})",
            theme.COLOR_SUCCESS if report.is_64bit else theme.COLOR_WARNING,
        )

        self.tiles["Primary 3D API"].set(report.primary_api, theme.ACCENT_CYAN)
        confidence = getattr(report, "api_confidence", "low")
        self.api_confidence.set(
            f"{confidence} confidence",
            {"high": "success", "medium": "warning", "low": "danger"}.get(
                confidence, "neutral"
            ),
        )
        self.api_confidence.show()

        self.api_evidence.setText(self._evidence_text(report))
        self.api_evidence.setVisible(bool(self.api_evidence.text()))

        self.tiles["Engine"].set(report.detected_engine[:24], theme.TEXT_PRIMARY)

        if report.has_native_dlss:
            upscaler, colour = "Native DLSS / SL", theme.COLOR_SUCCESS
        elif report.has_native_fsr or report.has_native_xess:
            upscaler, colour = "FSR / XeSS", theme.ACCENT_CYAN
        elif report.has_existing_dlss5_mod:
            upscaler, colour = "DLSS 5 mod ready", theme.ACCENT_GREEN
        else:
            upscaler, colour = "None (feeder mode)", theme.TEXT_SECONDARY
        self.tiles["Native Upscalers"].set(upscaler, colour)

        self._apply_anti_cheat(report)
        self._apply_addon_warning(report)

        strategy = STRATEGY_DISPLAY_NAMES.get(
            report.recommended_strategy, report.recommended_strategy
        )
        self.strategy_combo.setCurrentText(strategy)
        self._on_strategy_changed(strategy)

        self.progress.setValue(100)
        self._set_status(
            f"Diagnostics complete for {report.exe_name}. Recommended: {strategy}",
            theme.TEXT_PRIMARY,
        )
        self._log(
            f"Analyzed {report.exe_name}: Primary API={report.primary_api}, "
            f"Arch={report.architecture}, Strategy={report.recommended_strategy}",
            "INFO",
        )

    @staticmethod
    def _evidence_text(report: GameAnalysis) -> str:
        parts = []
        reasons = (getattr(report, "api_evidence", {}) or {}).get(report.primary_api, [])[:2]
        if reasons:
            parts.append("Read from: " + "; ".join(reasons))
        if getattr(report, "detected_wrappers", None):
            parts.append(
                "Wrapper already present: " + ", ".join(report.detected_wrappers)
                + " - not counted as the game's own renderer."
            )
        others = [a for a in getattr(report, "detected_apis", []) if a != report.primary_api]
        if others:
            parts.append("Also linked: " + ", ".join(others))
        return "\n".join(parts)

    def _apply_anti_cheat(self, report: GameAnalysis) -> None:
        """Name the anti-cheat that was found.

        A warning that says what it found gets read; a generic one gets clicked past.
        """
        if not report.anti_cheat_detected:
            self.ac_banner.hide()
            return
        if getattr(report, "risk_level", "") == "online_competitive":
            title = f"Ban risk: {report.exe_name} is a known online / anti-cheat title"
        elif getattr(report, "anti_cheat_names", None):
            title = "Anti-cheat detected: " + ", ".join(report.anti_cheat_names)
        else:
            title = "Anti-cheat protection detected"
        self.ac_banner.set(title, " ".join(report.anti_cheat_warnings), "danger")
        self.ac_banner.show()

    def _apply_addon_warning(self, report: GameAnalysis) -> None:
        """Name the add-ons already in the folder, and the ones known to clash."""
        foreign = list(getattr(report, "foreign_addons", []) or [])
        if not foreign:
            self.addon_banner.hide()
            return

        clashing = [n for n in foreign if GameDetector.describe_addon_conflict(n)]
        title = f"{len(foreign)} other ReShade add-ons in this folder will not be loaded"
        body = (", ".join(foreign[:8])
                + (f" and {len(foreign) - 8} more" if len(foreign) > 8 else "")
                + ".  Installing gives this folder an add-on-capable ReShade, which would "
                  "otherwise start every one of them. Ours go in "
                + ADDON_DIR_NAME + chr(92) + " and ReShade is pointed at that folder alone, "
                  "so these are never opened. Nothing is moved, renamed or deleted.")
        if clashing:
            body += (f"  {len(clashing)} would have conflicted directly: "
                     + ", ".join(clashing[:5])
                     + (" and others" if len(clashing) > 5 else "") + ".")
        tone = "warning"

        self.addon_banner.set(title, body, tone)
        self.addon_banner.show()

    # ==================================================================
    # Strategy and tuning
    # ==================================================================
    def _on_strategy_changed(self, choice: str) -> None:
        key = self._strategy_key(choice)
        self.strategy_desc.setText(STRATEGY_DESCRIPTIONS.get(key, ""))
        recommended = (
            self.current_analysis is not None
            and key == self.current_analysis.recommended_strategy
        )
        if self.current_analysis is None or recommended:
            self.auto_badge.set("Auto-recommended", "accent")
        else:
            self.auto_badge.set("Custom override", "warning")

    @staticmethod
    def _strategy_key(choice: str) -> str:
        for key, display in STRATEGY_DISPLAY_NAMES.items():
            if display == choice:
                return key
        return STRATEGY_FEEDER_DX11_12

    def _active_mv_provider(self) -> int:
        return MV_PROVIDER_CHOICES.get(
            self.mv_provider.currentText(), int(DEFAULT_PROFILE.get("mv_provider", 3))
        )

    def _on_mv_provider_changed(self, _choice: str = "") -> None:
        """Explain the selected provider, and say so when it is not downloaded yet."""
        provider = MV_PROVIDERS[self._active_mv_provider()]
        note = provider.summary
        if not ComponentManager.check_component_status(provider.component).is_installed:
            note = (
                f"NOT INSTALLED - fetch it on the Components tab, or the build stages no "
                f"{provider.effect_file} and DLSS runs on zero motion vectors.  " + note
            )
        self.mv_provider_note.setText(note)

    def _profile(self) -> Dict[str, Any]:
        """The selected profile, with the five settings this page also owns applied over it.

        This used to return the dictionary below and nothing else, which meant a build took
        five values from these controls and DEFAULT_PROFILE for the rest - the effect list,
        the neural rendering settings, the cascade, the D3D9 path - no matter which profile
        had been saved. Selecting a profile loads its values into these controls, so the
        override is only ever the user moving something afterwards.

        Every key here is read by something: the feed_* keys land in dlss5-feed.cfg, and
        depth_reversed drives both the ReShade preprocessor definition and the feed's
        depth_inverted.
        """
        mv_scale = round(self.mv_scale.value() / 100, 3)
        return {
            **self._selected_profile,
            "feed_host_window": self.host_window.isChecked(),
            "feed_work_resolution": int(self.work_res.value()),
            "feed_mv_scale_x": mv_scale,
            "feed_mv_scale_y": mv_scale,
            "depth_reversed": self.depth_reversed.isChecked(),
            "mv_provider": self._active_mv_provider(),
            "neural_consumer": self._active_neural_consumer(),
            **self._mfg_profile_bits(),
        }

    # ==================================================================
    # Deploying
    # ==================================================================
    def _set_status(self, text: str, colour: str = theme.TEXT_MUTED) -> None:
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {colour}; background: transparent;")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self.install_btn, self.prepare_btn, self.restore_btn, self.launch_btn):
            btn.setEnabled(enabled)

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress.setValue(int(max(0.0, min(fraction, 1.0)) * 100))
        if message:
            self._set_status(message, theme.TEXT_SECONDARY)

    def _require_analysis(self) -> bool:
        if self.current_analysis is None:
            QMessageBox.warning(
                self, "Select a game",
                "Select and analyze a game executable first.",
            )
            return False
        return True

    def _on_install(self) -> None:
        if not self._require_analysis():
            return
        # An install writes DLLs into the game folder, so this is the last point where the
        # ban risk can still be declined. The dialog returns True on its own when there is
        # nothing to warn about.
        if not RiskConfirmDialog.ask(self, self.current_analysis, "Install"):
            self._log(
                f"Install cancelled at the anti-cheat warning for "
                f"{self.current_analysis.exe_name}.", "WARNING",
            )
            return

        self._set_buttons_enabled(False)
        self._set_status("Injecting DLSS 5 neural rendering…", theme.ACCENT_GREEN)
        analysis, strategy, profile = (
            self.current_analysis,
            self._strategy_key(self.strategy_combo.currentText()),
            self._profile(),
        )

        run_async(
            self,
            lambda report: ModInstaller.install_to_game(
                analysis=analysis, strategy_override=strategy,
                profile=profile, progress_callback=report,
            ),
            on_done=self._on_install_finished,
            on_error=self._on_deploy_failed,
            on_progress=self._on_progress,
        )

    def _on_install_finished(self, result: BuildResult) -> None:
        self._set_buttons_enabled(True)
        if not result.success:
            QMessageBox.critical(self, "Installation failed", result.message)
            self._set_status("Installation failed.", theme.COLOR_DANGER)
            return
        self._log(f"Installed DLSS 5 to {self.current_analysis.exe_name}", "SUCCESS")
        QMessageBox.information(
            self, "Installation successful",
            f"DLSS 5 neural rendering has been injected into:\n{result.target_directory}\n\n"
            "• Press [Home] in-game for the RenoDX DLSS 5 add-on.\n"
            "• Press [F2] to toggle the neural rendering pipeline.\n"
            "• Original files were backed up to .dlss5_backup/",
        )
        self._set_status("DLSS 5 mod active. Ready to launch.", theme.COLOR_SUCCESS)

    def _on_prepare(self) -> None:
        if not self._require_analysis():
            return
        # Staging is only a folder, but it is a folder people copy into the game by hand
        # afterwards - so the warning belongs here too, not just on the direct install.
        if not RiskConfirmDialog.ask(self, self.current_analysis, "Stage build"):
            self._log(
                f"Build staging cancelled at the anti-cheat warning for "
                f"{self.current_analysis.exe_name}.", "WARNING",
            )
            return

        self._set_buttons_enabled(False)
        self._set_status("Staging standalone build folder…", theme.ACCENT_CYAN)
        analysis, strategy, profile = (
            self.current_analysis,
            self._strategy_key(self.strategy_combo.currentText()),
            self._profile(),
        )

        run_async(
            self,
            lambda report: ModInstaller.prepare_build_folder(
                analysis=analysis, strategy_override=strategy,
                profile=profile, progress_callback=report,
            ),
            on_done=self._on_prepare_finished,
            on_error=self._on_deploy_failed,
            on_progress=self._on_progress,
        )

    def _on_prepare_finished(self, result: BuildResult) -> None:
        self._set_buttons_enabled(True)
        if not result.success:
            QMessageBox.critical(self, "Staging failed", result.message)
            return
        self._log(f"Staged build at: {result.target_directory}", "SUCCESS")
        os.startfile(result.target_directory)
        QMessageBox.information(
            self, "Build staging complete",
            f"Standalone build staged successfully.\n\nFolder opened at:\n"
            f"{result.target_directory}\n\n"
            "Inspect the files and run apply_dlss5.bat whenever you are ready.",
        )
        self._set_status("Standalone build staged successfully.", theme.COLOR_SUCCESS)

    def _on_restore(self) -> None:
        if not self._require_analysis():
            return
        confirm = QMessageBox.question(
            self, "Confirm restore",
            f"Remove all DLSS 5 mod files and restore "
            f"{self.current_analysis.exe_name} to its vanilla state?",
        )
        if confirm != QMessageBox.Yes:
            return

        self._set_buttons_enabled(False)
        self._set_status("Restoring vanilla game state…", theme.COLOR_WARNING)
        game_dir = self.current_analysis.game_dir

        run_async(
            self,
            lambda report: ModInstaller.restore_and_uninstall(game_dir, report),
            on_done=self._on_restore_finished,
            on_error=self._on_deploy_failed,
            on_progress=self._on_progress,
        )

    def _on_restore_finished(self, result) -> None:
        success, message, _removed = result
        self._set_buttons_enabled(True)
        if not success:
            QMessageBox.critical(self, "Restore failed", message)
            return
        self._log(message, "SUCCESS")
        QMessageBox.information(self, "Restore complete", message)
        self._set_status("Game restored to its vanilla state.", theme.COLOR_SUCCESS)
        if self.current_analysis:
            self._analyze(self.current_analysis.exe_path)

    def _on_deploy_failed(self, message: str) -> None:
        self._set_buttons_enabled(True)
        self._log(f"Operation failed: {message}", "ERROR")
        QMessageBox.critical(self, "Error", message)

    def _on_launch(self) -> None:
        if not self._require_analysis():
            return
        exe = self.current_analysis.exe_path
        self._log(f"Launching game: {exe}", "INFO")
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
        except OSError as exc:
            QMessageBox.critical(self, "Launch error", f"Failed to launch the game: {exc}")
            return
        self._set_status(f"Game launched: {exe.name}", theme.COLOR_SUCCESS)
