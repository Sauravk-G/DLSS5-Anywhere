"""
Profile manager and preset editor.

A profile is the set of choices that do not depend on which game is selected, so they are
edited once here and reused. The form is grouped by what each setting acts on: the data
going *into* DLSS on the left, the legacy translation path on the right, and what comes
out of the neural pass underneath both.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QInputDialog,
    QMessageBox,
    QSlider,
    QWidget,
)

from ...config import (
    DEFAULT_PROFILE,
    DFC_LAYER_RANGE,
    MV_PROVIDERS,
    MV_PROVIDER_CHOICES,
    NEURAL_CONSUMER_CHOICES,
    NEURAL_CONSUMER_LABELS,
    mv_provider_for,
    mv_provider_label,
)
from ...core.config_gen import IMMERSE_EFFECTS, LUMENITE_EFFECTS, VISIBLE_EFFECTS
from ...core.profiles import BUILTIN_PRESETS, ProfileManager
from .. import theme
from ..components import (
    Card,
    CheckBox,
    ScrollPage,
    ToggleSwitch,
    body_text,
    danger_button,
    hbox,
    heading,
    primary_button,
    secondary_button,
    vbox,
)

# The effects a profile can switch on, in the order they are offered, from both suites.
# Every key has to be offered here: saving rebuilds "lumenite_effects" from these boxes
# alone, so an effect with no box is one the editor deletes from the profile the first time
# it is saved - which is how the iMMERSE half stayed invisible for so long.
EFFECT_LABELS = [
    ("rtao", "Ray-traced AO"),
    ("quantao", "Quant AO (cheap)"),
    ("lsao", "LSAO"),
    ("traa", "Temporal AA"),
    ("sssr", "Screen-space reflections (glitchy)"),
    ("bloom", "Anamorphic bloom"),
    ("mxao", "MXAO (needs Launchpad)"),
    ("solaris", "SOLARIS bloom"),
    ("smaa", "SMAA"),
    ("sharpen", "Sharpen"),
    ("filmgrain", "Film grain"),
]
assert all(key in VISIBLE_EFFECTS for key, _ in EFFECT_LABELS)
assert set(VISIBLE_EFFECTS) == {key for key, _ in EFFECT_LABELS}
assert set(LUMENITE_EFFECTS) | set(IMMERSE_EFFECTS) == set(VISIBLE_EFFECTS)

# Which suite each box belongs to, and how many fit across. One unwrapped row was fine for
# six boxes and put the last five past the right edge of the window once iMMERSE was added
# - reachable by nothing, since the panel does not scroll sideways. The caption carries the
# suite name so the labels themselves can stay short.
EFFECT_COLUMNS = 3
EFFECT_GROUPS = [
    ("LumeniteFX", [pair for pair in EFFECT_LABELS if pair[0] in LUMENITE_EFFECTS]),
    ("iMMERSE", [pair for pair in EFFECT_LABELS if pair[0] in IMMERSE_EFFECTS]),
]
assert sum(len(pairs) for _, pairs in EFFECT_GROUPS) == len(EFFECT_LABELS)

# Alex's Toolkit runs the neural pass more than once over the same frame. Each pass buys
# depth and costs temporal history, which is smearing behind fast motion.
CASCADE_CHOICES = [
    "Single pass (no cascade)",
    "Two passes (recommended)",
    "Three passes (cinematic)",
]
CASCADE_SETTINGS = {
    CASCADE_CHOICES[0]: {"toolkit_enabled": False, "toolkit_two_pass": False, "toolkit_three_pass": False},
    CASCADE_CHOICES[1]: {"toolkit_enabled": True, "toolkit_two_pass": True, "toolkit_three_pass": False},
    CASCADE_CHOICES[2]: {"toolkit_enabled": True, "toolkit_two_pass": False, "toolkit_three_pass": True},
}

# How a D3D9/D3D8 game reaches an API the add-on supports. There is no third option: the
# add-on has no D3D9 code path, so one of these has to translate first.
TRANSLATION_CHOICES = {
    "DXVK - D3D9 to Vulkan (no watermark)": "dxvk",
    "dgVoodoo2 - D3D9 to D3D11": "dgvoodoo",
}

PRESET_CHOICES = [
    "0 - default", "5 - CNN E", "6 - CNN F", "10 - transformer J", "11 - transformer K",
]


def _cascade_label(profile: Dict[str, Any]) -> str:
    """Map a profile's three cascade flags onto the one the combo box shows."""
    if not profile.get("toolkit_enabled", True):
        return CASCADE_CHOICES[0]
    if profile.get("toolkit_three_pass", False):
        return CASCADE_CHOICES[2]
    if profile.get("toolkit_two_pass", True):
        return CASCADE_CHOICES[1]
    return CASCADE_CHOICES[0]


def _combo(values, width: Optional[int] = None) -> QComboBox:
    box = QComboBox()
    box.addItems(list(values))
    box.setFont(theme.font_body())
    box.setCursor(Qt.PointingHandCursor)
    if width:
        box.setFixedWidth(width)
    return box


def _slider(minimum: int, maximum: int, value: int) -> QSlider:
    slider = QSlider(Qt.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(value)
    slider.setCursor(Qt.PointingHandCursor)
    return slider


class ProfilesView(QWidget):
    """Game-specific tuning profiles and optimisation presets."""

    def __init__(self, log_callback=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.log_callback = log_callback or (lambda msg, lvl: None)

        self.current_profile_name = "Balanced (Recommended)"
        self._loaded_profile: Dict[str, Any] = {}

        self._build_ui()
        self._load_profile(self.current_profile_name)

    def _log(self, msg: str, level: str = "INFO") -> None:
        self.log_callback(msg, level)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = vbox(self, margin=0)
        self.page = ScrollPage()
        layout.addWidget(self.page)

        self._selector_card()
        self._editor_card()
        self.page.finish()

    def _selector_card(self) -> None:
        card = Card(
            "Game profiles and optimisation presets",
            "The built-in presets cannot be deleted. Save a copy under a new name to "
            "change one.",
        )
        self.page.add(card)

        line = QWidget()
        row = hbox(line, spacing=theme.SPACE_SM)
        row.addWidget(body_text("Profile:", small=True, wrap=False))

        self.profile_combo = _combo(ProfileManager.list_profiles(), width=300)
        self.profile_combo.setCurrentText(self.current_profile_name)
        self.profile_combo.currentTextChanged.connect(self._on_profile_selected)
        row.addWidget(self.profile_combo)

        row.addWidget(secondary_button("New custom profile", self._on_new_profile))
        row.addWidget(danger_button("Delete", self._on_delete_profile, width=92))
        row.addStretch(1)
        card.add(line)

    def _editor_card(self) -> None:
        card = Card("Profile parameters and neural settings")
        self.page.add(card)

        columns = QGridLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(theme.SPACE_MD)
        columns.setColumnStretch(0, 1)
        columns.setColumnStretch(1, 1)
        columns.addWidget(self._feeder_panel(), 0, 0)
        columns.addWidget(self._legacy_panel(), 0, 1)
        card.add(columns)

        card.add(self._look_panel())
        card.add(self._action_row())

    # ---- left: the data going into DLSS ---------------------------------------------
    def _feeder_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Sunken")
        col = vbox(panel, margin=theme.SPACE_MD, spacing=theme.SPACE_SM)

        title = heading("Feeder and neural pipeline", "section")
        title.setStyleSheet(f"color: {theme.ACCENT_GREEN}; background: transparent;")
        col.addWidget(title)

        self.host_window = ToggleSwitch("Show the 32-bit helper window")
        self.host_window.setChecked(True)
        col.addWidget(self.host_window)

        self.work_res_lbl = body_text("Work resolution: 100%", small=True, wrap=False)
        col.addWidget(self.work_res_lbl)
        self.work_res = _slider(50, 100, 100)
        self.work_res.valueChanged.connect(
            lambda v: self.work_res_lbl.setText(f"Work resolution: {v}%  (64-bit D3D11 only)")
        )
        col.addWidget(self.work_res)

        col.addWidget(body_text(
            "DLSS reconstructs from this much of the frame - below 100% is the setting that "
            "buys frames. The add-on only offers it on its D3D11 transport, so a DirectX 9 "
            "game set below 100% is built with dgVoodoo2 rather than DXVK.",
            muted=True, small=True,
        ))

        col.addWidget(body_text("Motion vector provider:", small=True, wrap=False))
        self.mv_provider = _combo(MV_PROVIDER_CHOICES)
        self.mv_provider.currentTextChanged.connect(self._on_mv_provider_changed)
        col.addWidget(self.mv_provider)

        self.mv_provider_note = body_text("", muted=True, small=True)
        col.addWidget(self.mv_provider_note)

        self.mv_scale_lbl = body_text("Motion vector scale: 1.00", small=True, wrap=False)
        col.addWidget(self.mv_scale_lbl)
        # QSlider is integral, so the scale is carried in hundredths and divided out at
        # both ends. 0.5 to 2.0 in steps of 0.01 is finer than the control can be aimed.
        self.mv_scale = _slider(50, 200, 100)
        self.mv_scale.valueChanged.connect(
            lambda v: self.mv_scale_lbl.setText(f"Motion vector scale: {v / 100:.2f}")
        )
        col.addWidget(self.mv_scale)

        preset_row = QWidget()
        prow = hbox(preset_row, spacing=theme.SPACE_SM)
        prow.addWidget(body_text("DLSS render preset:", small=True, wrap=False))
        self.preset_combo = _combo(PRESET_CHOICES, width=180)
        prow.addWidget(self.preset_combo)
        prow.addStretch(1)
        col.addWidget(preset_row)
        col.addStretch(1)
        return panel

    # ---- right: how a legacy game gets there ----------------------------------------
    def _legacy_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Sunken")
        col = vbox(panel, margin=theme.SPACE_MD, spacing=theme.SPACE_SM)

        title = heading("Depth buffer and legacy wrapper", "section")
        title.setStyleSheet(f"color: {theme.ACCENT_CYAN}; background: transparent;")
        col.addWidget(title)

        self.depth_reversed = CheckBox("Invert depth (reversed-Z buffers)")
        col.addWidget(self.depth_reversed)
        self.depth_upsidedown = CheckBox("Upside-down depth buffer")
        col.addWidget(self.depth_upsidedown)

        col.addWidget(body_text("Legacy D3D9 path:", small=True, wrap=False))
        self.translation = _combo(TRANSLATION_CHOICES)
        col.addWidget(self.translation)

        vram_row = QWidget()
        vrow = hbox(vram_row, spacing=theme.SPACE_SM)
        vrow.addWidget(body_text("Video memory to report:", small=True, wrap=False))
        self.vram = _combo(["1024 MB", "2048 MB", "4096 MB"], width=124)
        vrow.addWidget(self.vram)
        vrow.addStretch(1)
        col.addWidget(vram_row)

        # An old engine sizes its texture budget and its graphics presets from whatever the
        # device reports, and behind a translation layer that is not the card's own figure.
        col.addWidget(body_text(
            "dgVoodoo2 reports this as an emulated card's VRAM; DXVK reports it through "
            "d3d9.maxAvailableMemory. Old engines clamp their presets to it.",
            muted=True, small=True,
        ))

        self.watermark = CheckBox("dgVoodoo2 watermark (clears after 15s)")
        self.watermark.setChecked(True)
        col.addWidget(self.watermark)
        col.addStretch(1)
        return panel

    # ---- what the frame ends up looking like ----------------------------------------
    def _look_panel(self) -> QFrame:
        """The settings that decide whether an install is visible at all.

        The neural switch is written to [RenoDX.DLSS5] in ReShade.ini, the effects to
        DLSS5_Preset.ini and the cascade to alexs-toolkit.cfg. Everything above this point
        tunes the data going *into* DLSS; this decides what comes out.
        """
        panel = QFrame()
        panel.setObjectName("Sunken")
        col = vbox(panel, margin=theme.SPACE_MD, spacing=theme.SPACE_SM)

        title = heading("Neural rendering look", "section")
        title.setStyleSheet(f"color: {theme.ACCENT_GREEN}; background: transparent;")
        col.addWidget(title)
        col.addWidget(body_text(
            "With neural rendering off, the feeder still runs and the result is DLAA only - "
            "clean edges and almost nothing else.",
            small=True,
        ))

        self.nr_enabled = ToggleSwitch("Enable DLSS 5 neural rendering on first launch")
        self.nr_enabled.setChecked(True)
        col.addWidget(self.nr_enabled)

        consumer_row = QWidget()
        crow = hbox(consumer_row, spacing=theme.SPACE_SM)
        crow.addWidget(body_text("Neural pass runs on:", small=True, wrap=False))
        self.neural_consumer = _combo(list(NEURAL_CONSUMER_CHOICES), width=240)
        self.neural_consumer.currentTextChanged.connect(self._on_consumer_changed)
        crow.addWidget(self.neural_consumer)
        crow.addSpacing(theme.SPACE_LG)
        self.dfc_layers_lbl = body_text("Chicken passes:", small=True, wrap=False)
        crow.addWidget(self.dfc_layers_lbl)
        self.dfc_layers = _combo([str(n) for n in DFC_LAYER_RANGE], width=80)
        crow.addWidget(self.dfc_layers)
        crow.addStretch(1)
        col.addWidget(consumer_row)

        self.consumer_note = body_text("", muted=True, small=True)
        col.addWidget(self.consumer_note)

        style_row = QWidget()
        srow = hbox(style_row, spacing=theme.SPACE_SM)
        srow.addWidget(body_text("Style:", small=True, wrap=False))
        self.nr_style = _combo(["Natural", "Cinematic"], width=140)
        srow.addWidget(self.nr_style)
        srow.addSpacing(theme.SPACE_LG)
        self.cascade_lbl = body_text("Cascade:", small=True, wrap=False)
        srow.addWidget(self.cascade_lbl)
        self.cascade = _combo(CASCADE_CHOICES, width=240)
        self.cascade.setCurrentText(CASCADE_CHOICES[1])
        srow.addWidget(self.cascade)
        srow.addStretch(1)
        col.addWidget(style_row)
        self._on_consumer_changed()

        col.addWidget(body_text(
            "Effects applied on top of the neural output (all of these ship with the build):",
            small=True,
        ))

        self.effect_boxes = {}
        for caption, pairs in EFFECT_GROUPS:
            col.addWidget(body_text(f"{caption}:", small=True, wrap=False))
            block = QWidget()
            grid = QGridLayout(block)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(theme.SPACE_LG)
            grid.setVerticalSpacing(theme.SPACE_SM)
            for index, (key, label) in enumerate(pairs):
                box = CheckBox(label)
                box.setFont(theme.font_small())
                self.effect_boxes[key] = box
                grid.addWidget(box, index // EFFECT_COLUMNS, index % EFFECT_COLUMNS)
            # Trailing stretch column, so the boxes stay left-aligned rather than spreading
            # across the panel as it widens.
            grid.setColumnStretch(EFFECT_COLUMNS, 1)
            col.addWidget(block)
        return panel

    def _action_row(self) -> QWidget:
        line = QWidget()
        row = hbox(line, spacing=theme.SPACE_SM)
        row.addWidget(primary_button("Save profile changes", self._on_save_profile, width=200))
        row.addWidget(secondary_button("Export JSON…", self._on_export_profile))
        row.addWidget(secondary_button("Import JSON…", self._on_import_profile))
        row.addStretch(1)
        return line

    # ------------------------------------------------------------------
    # Loading and saving
    # ------------------------------------------------------------------
    def _on_profile_selected(self, choice: str) -> None:
        if not choice:
            return
        self.current_profile_name = choice
        self._load_profile(choice)

    def _load_profile(self, name: str) -> None:
        data = ProfileManager.load_profile(name)

        self.host_window.setChecked(bool(data.get("feed_host_window", True)))

        work_res = int(data.get("feed_work_resolution", 100))
        self.work_res.setValue(work_res)
        self.work_res_lbl.setText(f"Work resolution: {work_res}%  (64-bit D3D11 only)")

        self.mv_provider.setCurrentText(mv_provider_label(mv_provider_for(data).id))
        self._on_mv_provider_changed()

        mv_scale = float(data.get("feed_mv_scale_x", 1.0))
        self.mv_scale.setValue(int(round(mv_scale * 100)))
        self.mv_scale_lbl.setText(f"Motion vector scale: {mv_scale:.2f}")

        preset = int(data.get("feed_preset", 0))
        self.preset_combo.setCurrentText(
            next((v for v in PRESET_CHOICES if v.startswith(f"{preset} ")), PRESET_CHOICES[0])
        )

        self.depth_reversed.setChecked(bool(data.get("depth_reversed", False)))
        self.depth_upsidedown.setChecked(bool(data.get("depth_upsidedown", False)))

        self.vram.setCurrentText(f"{int(data.get('dgvoodoo_vram_mb', 1024))} MB")

        translation = str(data.get("d3d9_translation", "dxvk")).lower()
        self.translation.setCurrentText(next(
            (label for label, key in TRANSLATION_CHOICES.items() if key == translation),
            next(iter(TRANSLATION_CHOICES)),
        ))
        self.watermark.setChecked(bool(data.get("dgvoodoo_watermark", True)))

        self.nr_enabled.setChecked(bool(data.get("nr_enabled", True)))
        self.nr_style.setCurrentText(
            "Cinematic" if int(data.get("nr_style", 0)) == 1 else "Natural"
        )
        self.cascade.setCurrentText(_cascade_label(data))
        self.neural_consumer.setCurrentText(
            NEURAL_CONSUMER_LABELS.get(str(data.get("neural_consumer", "renodx")).lower(),
                              NEURAL_CONSUMER_LABELS["renodx"])
        )
        self.dfc_layers.setCurrentText(str(int(data.get("dfc_layers", 1))))
        self._on_consumer_changed()

        enabled = {str(k).lower() for k in (data.get("lumenite_effects") or [])}
        for key, box in self.effect_boxes.items():
            box.setChecked(key in enabled)

        # The editor covers part of a profile, so keep the rest of the loaded values and
        # write them back on save - otherwise saving here quietly deletes every setting the
        # form has no widget for.
        self._loaded_profile = dict(data)
        self._log(f"Loaded profile: {name}", "INFO")

    def _on_consumer_changed(self, _choice: str = "") -> None:
        """Grey out whichever half of the pair does not apply.

        The cascade is Alex's Toolkit, which sits on RenoDX; the layer count is Chicken's.
        Leaving both live invited a profile that asks for a three-pass cascade and Deep
        Fried Chicken at once, which the installer can only answer with a warning.
        """
        is_dfc = NEURAL_CONSUMER_CHOICES.get(self.neural_consumer.currentText()) == "dfc"
        for widget in (self.cascade, self.cascade_lbl):
            widget.setEnabled(not is_dfc)
        for widget in (self.dfc_layers, self.dfc_layers_lbl):
            widget.setEnabled(is_dfc)
        self.consumer_note.setText(
            "Deep Fried Chicken replaces RenoDX and runs its own multi-pass stack, so Alex's "
            "Toolkit is not deployed with it."
            if is_dfc else
            "Alex's Toolkit cascades on top of RenoDX. It is deployed when it has been "
            "imported on the Components tab."
        )

    def _selected_mv_provider(self) -> int:
        return MV_PROVIDER_CHOICES.get(
            self.mv_provider.currentText(), int(DEFAULT_PROFILE.get("mv_provider", 3))
        )

    def _on_mv_provider_changed(self, _choice: str = "") -> None:
        self.mv_provider_note.setText(MV_PROVIDERS[self._selected_mv_provider()].summary)

    def _on_save_profile(self) -> None:
        vram_mb = int(self.vram.currentText().replace("MB", "").strip())
        mv_scale = round(self.mv_scale.value() / 100, 3)
        settings = {
            **self._loaded_profile,
            "nr_enabled": self.nr_enabled.isChecked(),
            "nr_style": 1 if self.nr_style.currentText() == "Cinematic" else 0,
            "lumenite_effects": [
                key for key, box in self.effect_boxes.items() if box.isChecked()
            ],
            **CASCADE_SETTINGS[self.cascade.currentText()],
            "neural_consumer": NEURAL_CONSUMER_CHOICES[self.neural_consumer.currentText()],
            "dfc_layers": int(self.dfc_layers.currentText()),
            "feed_host_window": self.host_window.isChecked(),
            "feed_work_resolution": int(self.work_res.value()),
            "feed_mv_scale_x": mv_scale,
            "feed_mv_scale_y": mv_scale,
            "feed_preset": int(self.preset_combo.currentText().split(" ", 1)[0]),
            "feed_mode": 2,
            "feed_hdr": -1,
            "depth_reversed": self.depth_reversed.isChecked(),
            "depth_upsidedown": self.depth_upsidedown.isChecked(),
            "dgvoodoo_vram_mb": vram_mb,
            "d3d9_translation": TRANSLATION_CHOICES.get(
                self.translation.currentText(), "dxvk"
            ),
            "dgvoodoo_watermark": self.watermark.isChecked(),
            # The same number, expressed the way each translation layer wants it.
            "dxvk_max_available_memory_mb": vram_mb,
            "mv_provider": self._selected_mv_provider(),
        }

        ProfileManager.save_profile(self.current_profile_name, settings)
        self._log(f"Saved profile: {self.current_profile_name}", "SUCCESS")
        QMessageBox.information(
            self, "Saved", f"Profile '{self.current_profile_name}' saved successfully."
        )

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------
    def _refill_profiles(self, select: Optional[str] = None) -> None:
        """Repopulate the combo without the reload that each change would otherwise fire."""
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(ProfileManager.list_profiles())
        if select:
            self.profile_combo.setCurrentText(select)
        self.profile_combo.blockSignals(False)

    def _on_new_profile(self) -> None:
        name, ok = QInputDialog.getText(
            self, "New custom profile", "Name for the new profile:"
        )
        if not ok or not name.strip():
            return
        clean = name.strip()
        ProfileManager.save_profile(clean, dict(DEFAULT_PROFILE))
        self._refill_profiles(clean)
        self.current_profile_name = clean
        self._load_profile(clean)
        self._log(f"Created profile: {clean}", "SUCCESS")

    def _on_delete_profile(self) -> None:
        if self.current_profile_name in BUILTIN_PRESETS:
            QMessageBox.warning(
                self, "Protected preset", "Built-in presets cannot be deleted."
            )
            return
        confirm = QMessageBox.question(
            self, "Confirm delete",
            f"Delete the profile '{self.current_profile_name}'?",
        )
        if confirm != QMessageBox.Yes:
            return

        ProfileManager.delete_profile(self.current_profile_name)
        self._log(f"Deleted profile: {self.current_profile_name}", "INFO")
        remaining = ProfileManager.list_profiles()
        self._refill_profiles(remaining[0])
        self.current_profile_name = remaining[0]
        self._load_profile(remaining[0])

    def _on_export_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export profile JSON",
            f"{self.current_profile_name}.json", "JSON files (*.json)",
        )
        if path:
            ProfileManager.export_profile(self.current_profile_name, Path(path))
            QMessageBox.information(self, "Export complete", f"Profile exported to:\n{path}")

    def _on_import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import profile JSON", "", "JSON files (*.json)"
        )
        if not path:
            return
        ok, message = ProfileManager.import_profile(Path(path))
        if ok:
            self._refill_profiles(self.current_profile_name)
            QMessageBox.information(self, "Import complete", message)
        else:
            QMessageBox.critical(self, "Import error", message)
