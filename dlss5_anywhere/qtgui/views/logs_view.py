"""
The log console.

The levels are chips that carry their own counts, so the shape of a run is legible before
reading a line of it, and an empty console says so instead of being an unexplained black
rectangle.

The console itself is a QPlainTextEdit rather than anything cleverer. It keeps its text as
a document and scrolls by moving the viewport over it, so a long session costs a line of
storage per entry and nothing per line on screen - which is the same reason the Tk build
kept a text widget here while every other view had to be rebuilt.
"""

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from .. import theme
from ..components import (
    SectionHeader,
    body_text,
    hbox,
    secondary_button,
    vbox,
)

# Level -> (label, colour). The order is the order the chips appear in.
LEVELS: Dict[str, Tuple[str, str]] = {
    "ALL": ("All", theme.TEXT_SECONDARY),
    "INFO": ("Info", theme.TEXT_SECONDARY),
    "SUCCESS": ("Success", theme.COLOR_SUCCESS),
    "WARNING": ("Warning", theme.COLOR_WARNING),
    "ERROR": ("Error", theme.COLOR_DANGER),
}


class LogsView(QWidget):
    """Real-time execution log, filterable by level."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.history: List[Tuple[str, str, str]] = []   # (timestamp, level, message)
        self._filter = "ALL"
        self._chips: Dict[str, QPushButton] = {}
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = vbox(self, margin=theme.SPACE_XL, spacing=theme.SPACE_MD)

        header = QWidget()
        head_row = hbox(header, spacing=theme.SPACE_SM)
        head_row.addWidget(
            SectionHeader(
                "Execution log",
                "Everything this session has done, in the order it did it.",
            ),
            1,
        )
        for text, handler in (
            ("Copy all", self._copy_to_clipboard),
            ("Save…", self._save_log_file),
            ("Clear", self.clear_logs),
        ):
            head_row.addWidget(secondary_button(text, handler, width=92), 0, Qt.AlignTop)
        outer.addWidget(header)

        # ---- level chips ------------------------------------------------------------
        chips = QWidget()
        chip_row = hbox(chips, spacing=theme.SPACE_SM)
        for level, (label, colour) in LEVELS.items():
            chip = QPushButton(label)
            chip.setProperty("variant", "chip")
            chip.setFont(theme.font_small(bold=True))
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setChecked(level == "ALL")
            chip.clicked.connect(lambda _c=False, lv=level: self._set_filter(lv))
            chip.level_colour = colour
            chip_row.addWidget(chip)
            self._chips[level] = chip
        chip_row.addStretch(1)
        outer.addWidget(chips)

        # ---- console ----------------------------------------------------------------
        console = QFrame()
        console.setObjectName("Sunken")
        console_layout = vbox(console, margin=theme.SPACE_MD)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(theme.font_code())
        self.text.setFrameShape(QFrame.NoFrame)
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text.setMaximumBlockCount(20_000)   # a session cannot grow without bound
        self.text.verticalScrollBar().setSingleStep(24)
        console_layout.addWidget(self.text)

        self.empty_lbl = body_text(
            "Nothing logged yet.\nAnalysing a game or running a build will fill this in.",
            muted=True,
        )
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setParent(self.text)

        outer.addWidget(console, 1)

        self._refresh_chips()
        self._update_empty_state()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.empty_lbl.setGeometry(
            0, int(self.text.height() * 0.38), self.text.width(), 60
        )

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _set_filter(self, level: str) -> None:
        self._filter = level
        self._refresh_chips()
        self._rerender()

    def _counts(self) -> Dict[str, int]:
        counts = {level: 0 for level in LEVELS}
        counts["ALL"] = len(self.history)
        for _ts, lvl, _msg in self.history:
            if lvl in counts:
                counts[lvl] += 1
        return counts

    def _refresh_chips(self) -> None:
        """Show each level's count on its chip, and mark the active one."""
        counts = self._counts()
        for level, chip in self._chips.items():
            label = LEVELS[level][0]
            count = counts.get(level, 0)
            active = level == self._filter
            chip.setChecked(active)
            chip.setText(f"{label}  {count}" if count else label)
            chip.setStyleSheet(
                f"color: {chip.level_colour if active else theme.TEXT_MUTED};"
                f" border-color: {chip.level_colour if active else theme.BORDER_COLOR};"
            )

    def _update_empty_state(self) -> None:
        showing = any(self._filter in ("ALL", lvl) for _ts, lvl, _msg in self.history)
        self.empty_lbl.setVisible(not showing)

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------
    def append_log(self, message: str, level: str = "INFO") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        lvl = level.upper()
        self.history.append((ts, lvl, message))
        self._refresh_chips()
        if self._filter in ("ALL", lvl):
            self._write(ts, lvl, message)
        self._update_empty_state()

    def _write(self, ts: str, lvl: str, message: str) -> None:
        colour = LEVELS.get(lvl, ("", theme.TEXT_SECONDARY))[1]
        # Non-breaking spaces for the level column: HTML collapses a run of ordinary ones,
        # so the padding that lines the messages up disappears and SUCCESS - the longest
        # level - runs straight into its own message.
        level = escape(lvl).ljust(8).replace(" ", "&nbsp;")
        self.text.appendHtml(
            f'<span style="color:{theme.TEXT_MUTED}">{ts}</span>&nbsp;&nbsp;'
            f'<span style="color:{colour}">{level}</span>'
            f'<span style="color:{colour}">{escape(message)}</span>'
        )
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _rerender(self) -> None:
        self.text.clear()
        for ts, lvl, msg in self.history:
            if self._filter in ("ALL", lvl):
                self._write(ts, lvl, msg)
        self._update_empty_state()

    def clear_logs(self) -> None:
        self.history.clear()
        self.text.clear()
        self._refresh_chips()
        self._update_empty_state()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _plain_text(self) -> str:
        return "\n".join(f"{ts}  {lvl:<7}{msg}" for ts, lvl, msg in self.history)

    def _copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setText(self._plain_text())
        QMessageBox.information(self, "Copied", "Log contents copied to the clipboard.")

    def _save_log_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log file", "dlss5-anywhere.log",
            "Text log (*.txt);;All files (*.*)",
        )
        if path:
            Path(path).write_text(self._plain_text(), encoding="utf-8")
            QMessageBox.information(self, "Saved", f"Log saved to:\n{path}")
