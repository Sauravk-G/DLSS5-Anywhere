"""
Component repository and proprietary-file importer.

Two kinds of thing are listed here and the distinction is the whole organising idea: what
this tool can fetch for you, and what you have to supply because it is proprietary or
distributed by hand. The section headings carry that, so no row has to repeat it.
"""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QWidget,
)

from ...config import COMPONENTS_DIR, COMPONENTS_REGISTRY
from ...core.components import ComponentManager, ComponentStatus
from ...core.downloader import ComponentDownloader
from .. import theme
from ..components import (
    Card,
    Pill,
    ScrollPage,
    SectionHeader,
    body_text,
    heading,
    hbox,
    primary_button,
    quiet_button,
    secondary_button,
    vbox,
)
from ..workers import run_async

GUIDANCE = [
    "RenoDX DLSS 5 Add-on (renodx-dlss5.addon64): RenoDX Discord, #DLSS5 channel. Pin to "
    "v4.55 - builds past it construct part of the DLSS contract themselves and conflict "
    "with DLSS5-Feeder.",
    "NVIDIA Neural Rendering Runtime (nvngx_dlssnr.dll): ~158 MB, from the same channel.",
    "DLSS Super Resolution Runtime (nvngx_dlss.dll): copy from any DLSS game, or DLSS "
    "Swapper.",
    "All three are 64-bit only. For a 32-bit game they are installed into host64\\, not "
    "next to the game .exe - a 32-bit process cannot load them, and ReShade will not list "
    "an .addon64.",
    "RTX 20 / 30 Series: community FP16-patched nvngx_dlssnr builds exist for Turing and "
    "Ampere.",
    "Click 'Import user file…' above, or drop the files into components/user_supplied/.",
]


class ComponentsView(QWidget):
    """Public component downloads, and the user-supplied proprietary files."""

    def __init__(self, log_callback=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.log_callback = log_callback or (lambda msg, lvl: None)

        # comp_id -> (badge, detail label): the only two widgets on a card that a status
        # refresh can change, kept so a refresh does not rebuild the whole list.
        self._cards: Dict[str, Tuple[Pill, QWidget]] = {}

        self._build_ui()
        self.refresh_statuses()

    def _log(self, msg: str, level: str = "INFO") -> None:
        self.log_callback(msg, level)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = vbox(self, margin=0)
        self.page = ScrollPage()
        layout.addWidget(self.page)

        # ---- header ------------------------------------------------------------------
        top = Card(
            "Component repository",
            "Public open-source tools this tool fetches, and the proprietary runtime files "
            "you supply.",
        )
        self.page.add(top)

        actions = QWidget()
        row = hbox(actions, spacing=theme.SPACE_SM)
        self.dl_all_btn = primary_button("Download all public components", self._on_download_all)
        row.addWidget(self.dl_all_btn)
        row.addWidget(secondary_button("Import user file…", self._on_import_file))
        row.addWidget(secondary_button("Auto-scan system", self._on_auto_scan))
        row.addStretch(1)
        row.addWidget(quiet_button("Open folder", lambda: os.startfile(COMPONENTS_DIR)))
        top.add(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        top.add(self.progress)

        # ---- the cards go here -------------------------------------------------------
        self.cards_host = QWidget()
        self.cards_layout = vbox(self.cards_host, spacing=theme.SPACE_SM)
        self.page.add(self.cards_host)

        # ---- guidance ----------------------------------------------------------------
        self.page.add_spacing(theme.SPACE_MD)
        guide = Card("Obtaining RenoDX DLSS 5 and nvngx_dlssnr.dll", accent=theme.ACCENT_CYAN)
        from ..components import BulletList
        guide.add(BulletList(GUIDANCE))
        self.page.add(guide)
        self.page.finish()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def refresh_statuses(self) -> None:
        """Re-check component statuses and update the cards.

        Only two things on a card can change, so the cards are built once and those two
        are updated in place; the disk work that decides them happens on a worker. This
        used to destroy every card and build them all again, on the GUI thread, every time
        the tab was opened.
        """
        run_async(
            self,
            lambda _report: ComponentManager.get_all_statuses(),
            on_done=self._apply_statuses,
            on_error=lambda msg: self._log(f"Could not read component status: {msg}", "WARNING"),
        )

    def _apply_statuses(self, statuses: Dict[str, ComponentStatus]) -> None:
        if set(statuses) != set(self._cards):
            self._render_groups(statuses)
            return
        for comp_id, status in statuses.items():
            badge, detail = self._cards[comp_id]
            badge.set(*self._badge_for(status))
            detail.setText(status.details)

    @staticmethod
    def _badge_for(status: ComponentStatus):
        return ("READY", "success") if status.is_installed else ("MISSING", "danger")

    def _render_groups(self, statuses: Dict[str, ComponentStatus]) -> None:
        """Draw the cards under two headings: fetched for you, and supplied by you."""
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

        groups = (
            ("Fetched automatically",
             "Open source. Download gets the current release.",
             False),
            ("You supply these",
             "Proprietary or community-distributed, so this tool cannot fetch them. Import "
             "them here or drop them into components/user_supplied/.",
             True),
        )
        for title, subtitle, user_supplied in groups:
            members = [
                (cid, st) for cid, st in statuses.items()
                if st.meta.is_user_supplied is user_supplied
            ]
            if not members:
                continue
            header = SectionHeader(title, subtitle)
            header.setContentsMargins(0, theme.SPACE_MD, 0, 0)
            self.cards_layout.addWidget(header)
            for comp_id, status in members:
                self.cards_layout.addWidget(self._component_card(comp_id, status))

    def _component_card(self, comp_id: str, status: ComponentStatus) -> Card:
        meta = status.meta
        card = Card()
        card._outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_MD, theme.SPACE_LG, theme.SPACE_MD
        )

        line = QWidget()
        row = hbox(line, spacing=theme.SPACE_MD)

        badge = Pill(*self._badge_for(status))
        badge.setFixedWidth(76)
        row.addWidget(badge, 0, Qt.AlignTop)

        info = QWidget()
        col = vbox(info, spacing=2)
        # No "[Public Open Source]" / "[User-Supplied]" tag on the card: the section
        # heading above it already says which of the two this is, and repeating it on
        # every row is a word the eye has to skip past on the way to the name.
        col.addWidget(heading(meta.name, "section"))
        col.addWidget(body_text(meta.description, small=True))
        detail = body_text(status.details, muted=True, small=True)
        col.addWidget(detail)
        row.addWidget(info, 1)

        if meta.is_user_supplied:
            action = quiet_button("Import file", self._on_import_file, width=118)
        else:
            action = quiet_button(
                "Download / update",
                lambda _checked=False, cid=comp_id: self._download_one(cid),
                width=142,
            )
        row.addWidget(action, 0, Qt.AlignTop)

        card.add(line)
        self._cards[comp_id] = (badge, detail)
        return card

    # ------------------------------------------------------------------
    # Downloading
    # ------------------------------------------------------------------
    def _set_progress(self, fraction: float, _msg: str = "") -> None:
        self.progress.setValue(int(max(0.0, min(fraction, 1.0)) * 100))

    def _download_one(self, comp_id: str) -> None:
        meta = COMPONENTS_REGISTRY[comp_id]
        self._log(f"Downloading {meta.name}...", "INFO")
        self._set_progress(0.05)

        run_async(
            self,
            lambda report, cid=comp_id: ComponentDownloader.download_component(
                cid, lambda f, m: report(f, m)
            ),
            on_done=lambda result, name=meta.name: self._on_one_done(result, name),
            on_error=lambda msg, name=meta.name: self._on_one_done((False, msg), name),
            on_progress=self._set_progress,
        )

    def _on_one_done(self, result, name: str) -> None:
        success, message = result
        self._set_progress(1.0 if success else 0.0)
        if success:
            self._log(f"{name} ready: {message}", "SUCCESS")
            self.refresh_statuses()
        else:
            self._log(f"Failed to download {name}: {message}", "ERROR")
            QMessageBox.critical(self, "Download error", message)

    def _on_download_all(self) -> None:
        self.dl_all_btn.setEnabled(False)
        self._log("Downloading all public components...", "INFO")
        self._set_progress(0.02)

        run_async(
            self,
            lambda report: ComponentDownloader.download_all_public_components(
                lambda name, fraction, message: report(fraction, f"{name}: {message}")
            ),
            on_done=self._on_download_all_done,
            on_error=self._on_download_all_failed,
            on_progress=self._set_progress,
        )

    def _on_download_all_done(self, _results) -> None:
        self.dl_all_btn.setEnabled(True)
        self._set_progress(1.0)
        self.refresh_statuses()
        self._log("Public components download completed.", "SUCCESS")
        QMessageBox.information(
            self, "Download complete",
            "All available public components have been downloaded and unpacked.",
        )

    def _on_download_all_failed(self, message: str) -> None:
        self.dl_all_btn.setEnabled(True)
        self._set_progress(0.0)
        self._log(f"Component download failed: {message}", "ERROR")
        QMessageBox.critical(self, "Download error", message)

    # ------------------------------------------------------------------
    # User files
    # ------------------------------------------------------------------
    def _on_import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select a proprietary mod file (renodx-dlss5.addon64 or nvngx_dlssnr.dll)",
            "",
            "Mod files (*.addon64 *.addon32 *.dll *.fx);;All files (*.*)",
        )
        if not path:
            return
        ok, message, _comp_id = ComponentManager.import_user_file(Path(path))
        if ok:
            self._log(f"User imported file: {message}", "SUCCESS")
            QMessageBox.information(self, "Import successful", message)
            self.refresh_statuses()
        else:
            QMessageBox.critical(self, "Import error", message)

    def _on_auto_scan(self) -> None:
        self._log("Scanning system for existing DLSS 5 files...", "INFO")
        run_async(
            self,
            lambda _report: ComponentManager.auto_scan_and_import_existing(),
            on_done=self._on_auto_scan_done,
            on_error=lambda msg: QMessageBox.critical(self, "Auto-scan error", msg),
        )

    def _on_auto_scan_done(self, imported) -> None:
        self.refresh_statuses()
        if imported:
            self._log(f"Auto-imported {len(imported)} files", "SUCCESS")
            QMessageBox.information(
                self, "Auto-import complete",
                "Found and imported:\n" + "\n".join(imported),
            )
        else:
            QMessageBox.information(
                self, "Auto-scan result",
                "No new proprietary files found in the standard locations.",
            )
