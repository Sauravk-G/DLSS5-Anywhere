"""
Game Library scanner and browser.

This is the view the whole port was worth doing for. A machine with a large Steam library
puts several thousand rows on this screen, and under Tk every one of those rows was
twenty real operating-system windows whether or not it was scrolled into view - which is
why filtering the list took a second and opening the tab took three quarters of one, and
why a hand-written virtual list had to be built to hold it to a fixed pool of rows.

Qt does that natively. A QListView asks its model only for the rows that are on screen and
draws each one through a delegate, so the cost of the list is the cost of what is visible
and nothing else. The delegate below paints a row rather than building widgets for it,
which is what lets ten thousand games scroll at the same speed as ten - and it means the
list has no children at all, so there is nothing to move, restack or re-lay-out.

The list has two sources. The scanners find what the launchers know about; `CustomLibrary`
holds what the user added by hand, which is where emulators, portable builds and anything
else that arrived without a manifest come from. The two are kept apart in this view so
that adding or removing an entry costs a re-merge rather than another walk over every
drive in the machine.
"""

import math
import os
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QInputDialog,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from ...core.custom_library import CustomLibrary, suggest_name
from ...core.favourites import Favourites
from ...core.library_scanner import DiscoveredGame, GameLibraryScanner
from .. import theme
from ..components import (
    Card,
    SegmentedControl,
    body_text,
    heading,
    hbox,
    primary_button,
    secondary_button,
    vbox,
)
from ..workers import run_async

# Each store gets a colour, used for the row's left stripe and its chip. Keeping the three
# values together stops the stripe and the chip drifting apart, which is what happens when
# the same conditional is written out at each use.
STORE_STYLES = {
    "Steam": ("#1E3A5F", "#89CFF0", "#3B82C4"),
    "Epic Games": ("#2A1E5F", "#C084FC", "#8B5CF6"),
    "GOG Galaxy": ("#3B2A1A", "#FDBA74", "#D97706"),
    "Custom": ("#12301F", theme.ACCENT_GREEN, theme.ACCENT_GREEN_DARK),
}
STORE_STYLE_DEFAULT = ("#243044", "#94A3B8", "#64748B")

# A stored entry whose executable is no longer there gets the danger palette rather than
# its store's, because the row still exists but nothing on it will work.
MISSING_STYLE = (theme.COLOR_DANGER_SOFT, theme.COLOR_DANGER, theme.COLOR_DANGER)

ROW_HEIGHT = 62
ROW_GAP = 6

GAME_ROLE = Qt.UserRole + 1

# The label of the favourites segment. Named once: it is both the button text and the
# value the filter compares against.
FAVOURITES_FILTER = "★ Favourites"


def fav_key(exe_path) -> str:
    """The same identity the favourites store uses, so the two always agree."""
    return os.path.normcase(os.path.normpath(str(exe_path)))


class GameListModel(QAbstractListModel):
    """The filtered list of games. Holds data; knows nothing about how a row looks."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._games: List[DiscoveredGame] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._games)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._games):
            return None
        game = self._games[index.row()]
        if role == GAME_ROLE:
            return game
        if role == Qt.DisplayRole:
            return game.name
        if role == Qt.ToolTipRole:
            if game.missing:
                return f"{game.exe_path}\n\nThis file is no longer there."
            return str(game.exe_path)
        return None

    def set_games(self, games: List[DiscoveredGame]) -> None:
        self.beginResetModel()
        self._games = list(games)
        self.endResetModel()

    def game_at(self, index: QModelIndex) -> Optional[DiscoveredGame]:
        if index.isValid() and 0 <= index.row() < len(self._games):
            return self._games[index.row()]
        return None


class GameRowDelegate(QStyledItemDelegate):
    """Paints one game row, including its buttons.

    A delegate draws; it does not own widgets. That is the whole reason the list is fast,
    and it means the buttons have to be hit-tested by hand - `editorEvent` receives the
    mouse events for whichever row is under the pointer, and the rectangles are recomputed
    from the row's own geometry rather than stored, so they stay correct through any
    resize without anything being notified.

    A row the user added carries a third button, because an entry that persists has to be
    removable; one whose executable has gone carries the same button and nothing else that
    can be pressed.
    """

    folder_clicked = Signal(object)
    select_clicked = Signal(object)
    remove_clicked = Signal(object)
    star_clicked = Signal(object)

    BTN_SELECT_W = 152
    BTN_FOLDER_W = 70
    BTN_REMOVE_W = 78
    STAR_W = 30
    BTN_H = 28
    CHIP_W = 84
    CHIP_H = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1
        self._hover_button = None   # None | "folder" | "select" | "remove" | "star"
        self._pressed_button = None
        # Comparison keys, refreshed by the view. Consulted once per painted row, which is
        # why it is a set and not a lookup through the store.
        self.favourites: set = set()

    def is_favourite(self, game) -> bool:
        return fav_key(game.exe_path) in self.favourites

    # -- geometry ------------------------------------------------------------------
    @staticmethod
    def _is_custom(game: DiscoveredGame) -> bool:
        return game.source == "Custom"

    def _card_rect(self, option_rect: QRect) -> QRect:
        return QRect(
            option_rect.left(), option_rect.top(),
            option_rect.width(), ROW_HEIGHT,
        )

    def _star_rect(self, card: QRect) -> QRect:
        return QRect(
            card.left() + 14, card.center().y() - self.STAR_W // 2,
            self.STAR_W, self.STAR_W,
        )

    def _select_rect(self, card: QRect) -> QRect:
        return QRect(
            card.right() - self.BTN_SELECT_W - theme.SPACE_MD,
            card.center().y() - self.BTN_H // 2 + 1,
            self.BTN_SELECT_W, self.BTN_H,
        )

    def _folder_rect(self, card: QRect) -> QRect:
        select = self._select_rect(card)
        return QRect(
            select.left() - self.BTN_FOLDER_W - theme.SPACE_SM,
            select.top(), self.BTN_FOLDER_W, self.BTN_H,
        )

    def _remove_rect(self, card: QRect) -> QRect:
        folder = self._folder_rect(card)
        return QRect(
            folder.left() - self.BTN_REMOVE_W - theme.SPACE_SM,
            folder.top(), self.BTN_REMOVE_W, self.BTN_H,
        )

    def _text_left_limit(self, card: QRect, game: DiscoveredGame) -> int:
        """Where the title has to stop: the left edge of the leftmost button."""
        left = self._remove_rect(card) if self._is_custom(game) else self._folder_rect(card)
        return left.left()

    def sizeHint(self, option, index) -> QSize:
        return QSize(320, ROW_HEIGHT + ROW_GAP)

    # -- painting ------------------------------------------------------------------
    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        game = index.data(GAME_ROLE)
        if game is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        card = self._card_rect(option.rect)
        hovered = index.row() == self._hover_row
        selected = bool(option.state & QStyle.State_Selected)
        custom = self._is_custom(game)
        gone = bool(game.missing)

        ground = theme.BG_CARD_HOVER if (hovered or selected) else theme.BG_CARD
        border = theme.COLOR_DANGER if gone else (
            theme.BORDER_STRONG if selected else theme.BORDER_COLOR
        )
        painter.setBrush(theme.qcolor(ground))
        painter.setPen(QPen(theme.qcolor(border), 1))
        painter.drawRoundedRect(
            card.adjusted(0, 0, -1, -1), theme.RADIUS_LG, theme.RADIUS_LG
        )

        if gone:
            chip_bg, chip_fg, stripe_colour = MISSING_STYLE
            chip_text = "MISSING"
        else:
            chip_bg, chip_fg, stripe_colour = STORE_STYLES.get(
                game.source, STORE_STYLE_DEFAULT
            )
            chip_text = game.source.upper()

        # Store stripe
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(stripe_colour))
        painter.drawRoundedRect(
            QRect(card.left() + 6, card.top() + 10, 4, card.height() - 20), 2, 2
        )

        # The star. Drawn before the chip so the chip's own colours cannot be mistaken
        # for it, and always present - a row with nowhere to click is worse than a
        # hollow outline.
        self._paint_star(painter, self._star_rect(card), self.is_favourite(game),
                         hovered and self._hover_button == "star")

        # Store chip
        chip = QRect(
            card.left() + 14 + self.STAR_W + theme.SPACE_SM,
            card.center().y() - self.CHIP_H // 2,
            self.CHIP_W, self.CHIP_H,
        )
        painter.setBrush(theme.qcolor(chip_bg))
        painter.drawRoundedRect(chip, 5, 5)
        painter.setFont(theme.font_small(bold=True))
        painter.setPen(theme.qcolor(chip_fg))
        painter.drawText(chip, Qt.AlignCenter, chip_text)

        # Buttons. A row whose executable has gone keeps only the one that can still do
        # something about it.
        select_rect = self._select_rect(card)
        folder_rect = self._folder_rect(card)
        if gone:
            self._paint_button(painter, select_rect, "File not found",
                               ground=theme.BG_INPUT, ink=theme.TEXT_MUTED,
                               border=theme.BORDER_COLOR)
            self._paint_button(painter, folder_rect, "Folder",
                               ground=theme.BG_INPUT, ink=theme.BORDER_STRONG,
                               border=theme.BORDER_SUBTLE)
        else:
            self._paint_button(
                painter, select_rect, "Select for Modding",
                ground=theme.ACCENT_GREEN if (hovered and self._hover_button == "select")
                else theme.ACCENT_GREEN_DARK,
                ink=theme.TEXT_ON_ACCENT, bold=True,
            )
            self._paint_button(
                painter, folder_rect, "Folder",
                ground=theme.BG_RAISED if (hovered and self._hover_button == "folder")
                else theme.BG_INPUT,
                ink=theme.TEXT_SECONDARY, border=theme.BORDER_COLOR,
            )
        if custom:
            remove_hot = hovered and self._hover_button == "remove"
            self._paint_button(
                painter, self._remove_rect(card), "Remove",
                ground=theme.COLOR_DANGER_SOFT if remove_hot else theme.BG_INPUT,
                ink=theme.COLOR_DANGER,
                border=theme.COLOR_DANGER if remove_hot else theme.BORDER_COLOR,
            )

        # Title and path, elided to whatever is left between the chip and the buttons.
        text_left = chip.right() + theme.SPACE_MD
        text_width = max(self._text_left_limit(card, game) - theme.SPACE_MD - text_left, 40)

        painter.setFont(theme.font_body(bold=True))
        painter.setPen(theme.qcolor(theme.TEXT_MUTED if gone else theme.TEXT_PRIMARY))
        metrics = QFontMetrics(painter.font())
        title_rect = QRect(text_left, card.top() + 12, text_width, metrics.height() + 2)
        painter.drawText(
            title_rect, Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(game.name, Qt.ElideRight, text_width),
        )

        painter.setFont(theme.font_small())
        painter.setPen(theme.qcolor(theme.TEXT_MUTED))
        path_metrics = QFontMetrics(painter.font())
        path_rect = QRect(
            text_left, title_rect.bottom() + 2, text_width, path_metrics.height() + 2
        )
        painter.drawText(
            path_rect, Qt.AlignLeft | Qt.AlignVCenter,
            path_metrics.elidedText(str(game.exe_path), Qt.ElideMiddle, text_width),
        )

        painter.restore()

    @staticmethod
    def _paint_star(painter: QPainter, rect: QRect, filled: bool, hovered: bool) -> None:
        """A five-pointed star, filled when the game is starred.

        Drawn as a path rather than a glyph. The star characters differ between fonts and
        several render as colour emoji on Windows, which would put one saturated yellow
        picture on every row of a list built to be scanned.
        """
        centre = QPointF(rect.center().x() + 0.5, rect.center().y() + 0.5)
        outer, inner = rect.width() * 0.42, rect.width() * 0.17
        path = QPainterPath()
        for index in range(10):
            radius = outer if index % 2 == 0 else inner
            angle = math.radians(-90 + index * 36)
            point = QPointF(centre.x() + radius * math.cos(angle),
                            centre.y() + radius * math.sin(angle))
            path.lineTo(point) if index else path.moveTo(point)
        path.closeSubpath()

        if filled:
            painter.setBrush(theme.qcolor(theme.COLOR_WARNING))
            painter.setPen(QPen(theme.qcolor(theme.COLOR_WARNING), 1))
        else:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(theme.qcolor(
                theme.COLOR_WARNING if hovered else theme.BORDER_STRONG), 1.4))
        painter.drawPath(path)

    def _paint_button(self, painter: QPainter, rect: QRect, text: str, ground: str,
                      ink: str, border: Optional[str] = None, bold: bool = False) -> None:
        painter.setBrush(theme.qcolor(ground))
        painter.setPen(QPen(theme.qcolor(border), 1) if border else Qt.NoPen)
        painter.drawRoundedRect(rect, theme.RADIUS_SM, theme.RADIUS_SM)
        painter.setFont(theme.font_small(bold=bold))
        painter.setPen(theme.qcolor(ink))
        painter.drawText(rect, Qt.AlignCenter, text)

    # -- interaction ---------------------------------------------------------------
    def _button_at(self, pos, card: QRect, game: DiscoveredGame) -> Optional[str]:
        if self._star_rect(card).adjusted(-4, -4, 4, 4).contains(pos):
            return "star"
        if self._is_custom(game) and self._remove_rect(card).contains(pos):
            return "remove"
        if game.missing:
            return None                  # nothing else on this row does anything
        if self._select_rect(card).contains(pos):
            return "select"
        if self._folder_rect(card).contains(pos):
            return "folder"
        return None

    def editorEvent(self, event, model, option, index) -> bool:
        game = index.data(GAME_ROLE)
        if game is None:
            return False

        card = self._card_rect(option.rect)
        kind = event.type()

        if kind == event.Type.MouseMove:
            button = self._button_at(event.position().toPoint(), card, game)
            if button != self._hover_button or index.row() != self._hover_row:
                self._hover_button = button
                self._hover_row = index.row()
                if self.parent() is not None:
                    self.parent().viewport().update()
            return False

        if kind == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            self._pressed_button = self._button_at(event.position().toPoint(), card, game)
            return self._pressed_button is not None

        if kind == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
            button = self._button_at(event.position().toPoint(), card, game)
            pressed, self._pressed_button = self._pressed_button, None
            if button is not None and button == pressed:
                {
                    "select": self.select_clicked,
                    "folder": self.folder_clicked,
                    "remove": self.remove_clicked,
                    "star": self.star_clicked,
                }[button].emit(game)
                return True

        if kind == event.Type.MouseButtonDblClick and event.button() == Qt.LeftButton:
            if not game.missing:
                self.select_clicked.emit(game)
            return True

        return False

    def clear_hover(self) -> None:
        if self._hover_row != -1 or self._hover_button is not None:
            self._hover_row = -1
            self._hover_button = None
            if self.parent() is not None:
                self.parent().viewport().update()


class GameListView(QListView):
    """The list itself. Only exists to clear the delegate's hover when the pointer goes."""

    def leaveEvent(self, event) -> None:
        delegate = self.itemDelegate()
        if isinstance(delegate, GameRowDelegate):
            delegate.clear_hover()
        super().leaveEvent(event)


class LibraryView(QWidget):
    """Every discovered Steam, Epic and GOG game, plus everything the user added."""

    game_selected = Signal(object)   # Path

    def __init__(self, log_callback=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.log_callback = log_callback or (lambda msg, lvl: None)

        # Kept apart so that adding or removing a manual entry re-merges two lists instead
        # of walking every drive in the machine again.
        self._scanned: List[DiscoveredGame] = []
        self._custom: List[DiscoveredGame] = []
        self.all_games: List[DiscoveredGame] = []
        self._favourites: set = set()

        self._build_ui()
        self._reload_custom()
        self._reload_favourites()
        self.rescan_libraries()

    def _log(self, msg: str, level: str = "INFO") -> None:
        self.log_callback(msg, level)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = vbox(self, margin=theme.SPACE_XL, spacing=theme.SPACE_MD)

        # ---- controls ---------------------------------------------------------------
        top = Card()
        outer.addWidget(top)

        top.add(heading("Installed game library", "subtitle"))

        controls = QWidget()
        row = hbox(controls, spacing=theme.SPACE_SM)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by title or executable name…")
        self.search.setFont(theme.font_body())
        self.search.setMinimumWidth(220)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_games)
        row.addWidget(self.search, 1)

        # Favourites first, because it is the one people reach for repeatedly. It is a
        # filter rather than a separate screen: the rows, their buttons and the search box
        # all behave identically, which a second view would have to duplicate and then
        # keep in step forever.
        self.store_filter = SegmentedControl(
            [FAVOURITES_FILTER, "All Stores", "Steam", "Epic Games", "GOG Galaxy", "Custom"]
        )
        self.store_filter.set_value("All Stores")
        self.store_filter.changed.connect(lambda _v: self._filter_games())
        row.addWidget(self.store_filter)

        row.addStretch(1)

        # The primary action on this screen. A scan cannot find an emulator, a portable
        # build or anything else that arrived without a launcher manifest, and those are
        # a large part of what this tool is for - so pointing at one by hand is a
        # first-class way to use the library, not a fallback hidden behind the folder
        # scan it kept being confused with.
        self.add_btn = primary_button("Add .exe…", self._on_add_exe)
        row.addWidget(self.add_btn)

        self.custom_btn = secondary_button("Scan folder…", self._on_scan_custom_folder)
        row.addWidget(self.custom_btn)

        self.rescan_btn = secondary_button("Rescan", self.rescan_libraries)
        row.addWidget(self.rescan_btn)

        top.add(controls)

        # ---- count ------------------------------------------------------------------
        self.count_lbl = body_text("Scanning…", muted=True, small=True)
        outer.addWidget(self.count_lbl)

        # ---- list -------------------------------------------------------------------
        self.model = GameListModel(self)
        self.list = GameListView()
        self.list.setObjectName("GameList")
        self.list.setModel(self.model)
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)          # fixed row height: no per-row measuring
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.verticalScrollBar().setSingleStep(28)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setSpacing(0)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)

        self.delegate = GameRowDelegate(self.list)
        self.delegate.folder_clicked.connect(self._open_game_folder)
        self.delegate.select_clicked.connect(self._select_game_for_modding)
        self.delegate.remove_clicked.connect(self._remove_custom_game)
        self.delegate.star_clicked.connect(self._toggle_favourite)
        self.list.setItemDelegate(self.delegate)

        outer.addWidget(self.list, 1)

        # Sits over the list rather than replacing it, so an empty result does not have to
        # tear the view down and build it again when the next keystroke matches something.
        self.empty_lbl = body_text("", muted=True)
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setParent(self.list)
        self.empty_lbl.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.empty_lbl.setGeometry(0, 60, self.list.width(), 48)

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    def rescan_libraries(self) -> None:
        self.rescan_btn.setEnabled(False)
        self.count_lbl.setText("Scanning drives and store manifests for games…")
        self._log("Scanning system for games...", "INFO")

        # The manual entries are already in hand and must not be re-read from disk on a
        # worker thread while the GUI thread may be editing them.
        run_async(
            self,
            lambda _report: GameLibraryScanner.scan_all(include_custom=False),
            on_done=self._on_scan_completed,
            on_error=self._on_scan_failed,
        )

    def _on_scan_completed(self, games: List[DiscoveredGame]) -> None:
        self.rescan_btn.setEnabled(True)
        self._scanned = games
        self._log(f"Discovered {len(games)} installed games", "INFO")
        self._merge()

    def _on_scan_failed(self, message: str) -> None:
        self.rescan_btn.setEnabled(True)
        self.count_lbl.setText("Scan failed.")
        self._log(f"Scan error: {message}", "ERROR")
        self._merge()

    def _reload_custom(self) -> None:
        self._custom = CustomLibrary.load()

    def _reload_favourites(self) -> None:
        self._favourites = Favourites.load()
        self.delegate.favourites = self._favourites

    def _toggle_favourite(self, game: DiscoveredGame) -> None:
        starred = Favourites.toggle(game.exe_path)
        self._reload_favourites()
        # Re-filter rather than repaint: on the Favourites tab an un-starred row has to
        # leave the list, and everywhere else the order changes because starred games
        # sort to the top.
        self._merge()
        self._log(f"{'Starred' if starred else 'Un-starred'}: {game.name}", "INFO")

    def _merge(self) -> None:
        """One list out of the two sources.

        A scanned entry wins over a manual one for the same executable: if a game the user
        added by hand has since become detectable, the launcher knows its real name and
        which store it came from, and that is the better answer.
        """
        merged: List[DiscoveredGame] = list(self._scanned)
        seen = {str(g.exe_path).lower() for g in merged}
        for game in self._custom:
            if str(game.exe_path).lower() not in seen:
                merged.append(game)
        # Starred first, then alphabetical within each group. A library of several
        # hundred titles is a scroll bar, and this is what stops the two or three being
        # worked on this week living somewhere in the middle of it.
        merged.sort(key=lambda g: (fav_key(g.exe_path) not in self._favourites,
                                   g.name.lower()))
        self.all_games = merged
        self._filter_games()

    # ------------------------------------------------------------------
    # Adding
    # ------------------------------------------------------------------
    def _on_add_exe(self) -> None:
        """Point at one or more executables directly.

        Several at once is allowed and skips the naming step - an emulator collection is
        added in one pass, and being asked to name each of eight in turn is worse than
        fixing the two guesses that came out wrong.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add game executables to the library", "",
            "Game executable (*.exe);;All files (*.*)",
        )
        if not paths:
            return

        if len(paths) == 1:
            exe = Path(paths[0])
            name, ok = QInputDialog.getText(
                self, "Add to library",
                f"Name for {exe.name}:", QLineEdit.Normal, suggest_name(exe),
            )
            if not ok:
                return
            added, message = CustomLibrary.add(exe, name)
            if not added:
                QMessageBox.information(self, "Add to library", message)
                return
            self._log(f"Added to library: {name.strip() or exe.stem} ({exe})", "SUCCESS")
        else:
            added_names = []
            skipped = []
            for raw in paths:
                exe = Path(raw)
                ok, message = CustomLibrary.add(exe)
                (added_names if ok else skipped).append(exe.name)
            if not added_names:
                QMessageBox.information(
                    self, "Add to library",
                    "Nothing new to add - all of those are already in the library.",
                )
                return
            self._log(
                f"Added {len(added_names)} executables to the library"
                + (f"; skipped {len(skipped)} already present" if skipped else ""),
                "SUCCESS",
            )

        self._after_custom_change()

    def _on_scan_custom_folder(self) -> None:
        """Scan a folder of game directories, and keep what it finds.

        The results are stored, not just shown. Under the previous build they lived in
        memory and were gone at the next launch, which made the whole feature a way to
        waste the user's time.
        """
        folder = QFileDialog.getExistingDirectory(self, "Select a folder to scan for games")
        if not folder:
            return

        self.custom_btn.setEnabled(False)
        self.count_lbl.setText(f"Scanning {folder}…")
        run_async(
            self,
            lambda _report, root=folder: GameLibraryScanner.scan_custom_directory(root),
            on_done=lambda found, root=folder: self._on_folder_scanned(found, root),
            on_error=self._on_folder_scan_failed,
        )

    def _on_folder_scanned(self, found: List[DiscoveredGame], folder: str) -> None:
        self.custom_btn.setEnabled(True)
        added = CustomLibrary.add_many(found)
        self._after_custom_change()
        self._log(
            f"Folder scan of {folder}: {len(found)} found, {added} added to the library",
            "INFO",
        )
        if not found:
            QMessageBox.information(
                self, "Custom scan",
                f"No games found in:\n{folder}\n\n"
                "The scan looks one folder deep for a likely game executable. For an "
                "emulator or a portable build, use 'Add .exe…' and point at the "
                "executable itself.",
            )
        else:
            QMessageBox.information(
                self, "Custom scan",
                f"Found {len(found)} game(s) in:\n{folder}\n\n"
                f"{added} added to the library"
                + (f"; {len(found) - added} were already there." if added < len(found)
                   else "."),
            )

    def _on_folder_scan_failed(self, message: str) -> None:
        self.custom_btn.setEnabled(True)
        self._log(f"Folder scan failed: {message}", "ERROR")
        QMessageBox.critical(self, "Scan error", message)
        self._merge()

    def _after_custom_change(self) -> None:
        """Re-read the stored list and re-merge. No rescan: nothing on disk moved."""
        self._reload_custom()
        self._merge()

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _filter_games(self) -> None:
        query = self.search.text().strip().lower()
        store = self.store_filter.value()

        def matches_store(g: DiscoveredGame) -> bool:
            if store == "All Stores":
                return True
            if store == FAVOURITES_FILTER:
                return fav_key(g.exe_path) in self._favourites
            return g.source == store

        filtered = [
            g for g in self.all_games
            if matches_store(g)
            and (not query or query in g.name.lower() or query in g.exe_path.name.lower())
        ]

        self.model.set_games(filtered)
        self.list.scrollToTop()

        summary = f"Showing {len(filtered)} of {len(self.all_games)} games"
        if self._favourites:
            summary += f"  ·  {len(self._favourites)} starred"
        if self._custom:
            gone = sum(1 for g in self._custom if g.missing)
            summary += f"  ·  {len(self._custom)} added by you"
            if gone:
                summary += f", {gone} no longer on disk"
        self.count_lbl.setText(summary)

        if filtered:
            self.empty_lbl.hide()
        else:
            if store == FAVOURITES_FILTER and not self._favourites:
                message = ("Nothing starred yet.\n"
                           "Click the star on any row to keep it at the top of the list.")
            elif not self.all_games:
                message = ("No games found.\n"
                           "Use “Add .exe…” for an emulator or anything the scan missed.")
            else:
                message = "No games match this search."
            self.empty_lbl.setText(message)
            self.empty_lbl.show()

    # ------------------------------------------------------------------
    # Row actions
    # ------------------------------------------------------------------
    def _open_game_folder(self, game: DiscoveredGame) -> None:
        try:
            os.startfile(game.install_dir)
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not open directory:\n{exc}")

    def _select_game_for_modding(self, game: DiscoveredGame) -> None:
        if game.missing:
            return
        self._log(f"Selected game for modding: {game.name} ({game.exe_path})", "INFO")
        self.game_selected.emit(game.exe_path)

    def _remove_custom_game(self, game: DiscoveredGame) -> None:
        confirm = QMessageBox.question(
            self, "Remove from library",
            f"Remove “{game.name}” from your library?\n\n"
            "This only forgets the entry. Nothing on disk is touched.",
        )
        if confirm != QMessageBox.Yes:
            return
        if CustomLibrary.remove(game.exe_path):
            Favourites.remove(game.exe_path)   # no star for a row that no longer exists
            self._log(f"Removed from library: {game.name}", "INFO")
            self._reload_favourites()
            self._after_custom_change()

    def _rename_custom_game(self, game: DiscoveredGame) -> None:
        name, ok = QInputDialog.getText(
            self, "Rename", "Name:", QLineEdit.Normal, game.name
        )
        if ok and name.strip() and CustomLibrary.rename(game.exe_path, name):
            self._log(f"Renamed library entry to: {name.strip()}", "INFO")
            self._after_custom_change()

    def _on_context_menu(self, pos) -> None:
        """Rename and remove, for the entries the user owns."""
        game = self.model.game_at(self.list.indexAt(pos))
        if game is None or game.source != "Custom":
            return

        menu = QMenu(self)
        rename = QAction("Rename…", menu)
        rename.triggered.connect(lambda: self._rename_custom_game(game))
        menu.addAction(rename)

        remove = QAction("Remove from library", menu)
        remove.triggered.connect(lambda: self._remove_custom_game(game))
        menu.addAction(remove)

        menu.exec(self.list.viewport().mapToGlobal(pos))
