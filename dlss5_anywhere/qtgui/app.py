"""
The application window.

The shell is a navigation rail and a stack. Every view is built once at startup and put
into a QStackedWidget, so switching tabs shows a widget that is already laid out - there
is no construction, no geometry pass and no repaint of anything but the newly exposed
area.

That was true of the Tk build too, by the end, but it had to be arranged by hand: views
were stacked in one grid cell and raised, they had to be un-gridded before a resize
because seven of them re-running geometry cost the best part of a second per step, and the
content area's painting had to be frozen through a Win32 call so the window did not show
half of the old layout and half of the new one. None of that is here. A QStackedWidget
lays out only its current page, and a Qt window composes its frame off-screen and presents
it in one piece, so a resize cannot show an intermediate state - there is no moment at
which one exists.
"""

from pathlib import Path
import sys
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from ..config import APP_AUTHOR, APP_ICON, APP_NAME, APP_VERSION
from . import theme
from .components import (
    EyebrowLabel,
    accent_rule,
    body_text,
    divider,
    hbox,
    restyle,
    vbox,
)
from .views.components_view import ComponentsView
from .views.credits_view import CreditsView
from .views.dashboard_view import DashboardView
from .views.guide_view import GuideView
from .views.library_view import LibraryView
from .views.logs_view import LogsView
from .views.profiles_view import ProfilesView

# Nav items, grouped: what you do, then what you set up, then what you read.
NAV_GROUPS: List[Tuple[str, List[Tuple[str, str]]]] = [
    ("Build", [("dashboard", "Builder"), ("library", "Game Library")]),
    ("Setup", [("components", "Components"), ("profiles", "Profiles")]),
    ("Reference", [("guide", "User Guide"), ("logs", "Logs"), ("credits", "Credits")]),
]


class MainWindow(QMainWindow):
    """Navigation rail on the left, the active view on the right."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            f"{APP_NAME} v{APP_VERSION} - DLSS 5 Neural Rendering Automation Tool"
        )
        self.setMinimumSize(940, 640)

        self._nav_buttons: Dict[str, QPushButton] = {}
        self._nav_indicators: Dict[str, QFrame] = {}
        self._views: Dict[str, QWidget] = {}
        self._pending_logs: List[Tuple[str, str]] = []

        self._build_layout()
        self._size_to_screen()

        self.log(f"{APP_NAME} v{APP_VERSION} initialized successfully.", "SUCCESS")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        central = QWidget()
        central.setObjectName("Content")
        central.setAttribute(Qt.WA_StyledBackground, True)
        root = hbox(central, spacing=0)
        root.addWidget(self._sidebar())

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._build_views()
        self.show_view("dashboard")

    def _sidebar(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("Sidebar")
        rail.setFixedWidth(theme.SIDEBAR_WIDTH)
        col = vbox(rail, spacing=2)
        col.setContentsMargins(theme.SPACE_SM, theme.SPACE_XL, theme.SPACE_SM, theme.SPACE_LG)

        # ---- brand -------------------------------------------------------------------
        brand = QWidget()
        brand_row = hbox(brand, spacing=theme.SPACE_SM)
        brand_row.setContentsMargins(theme.SPACE_SM, 0, 0, 0)
        # The mark, where a plain green rule used to be - and back to that rule if the
        # artwork is missing, so a checkout without assets\ has a branded rail rather than
        # a gap where a logo should be.
        #
        # Read from the .ico rather than a PNG so Qt picks whichever of the eight stored
        # sizes fits: the solid cross at 30 logical pixels, the full constellation on a
        # 150% display where those 30 become 45 real ones. Scaling a single PNG to fit
        # would resample a mark that was drawn per size precisely to avoid being resampled.
        mark = QIcon(str(APP_ICON)).pixmap(30, 30) if APP_ICON.is_file() else QPixmap()
        if mark.isNull():
            brand_row.addWidget(accent_rule(theme.ACCENT_GREEN, 4, 30), 0, Qt.AlignVCenter)
        else:
            logo = QLabel()
            logo.setPixmap(mark)
            logo.setFixedSize(30, 30)
            logo.setStyleSheet("background: transparent;")
            brand_row.addWidget(logo, 0, Qt.AlignVCenter)

        name_col = QWidget()
        names = vbox(name_col, spacing=0)
        title = body_text("DLSS 5", bold=True)
        title.setFont(theme.font_title())
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        names.addWidget(title)
        names.addWidget(body_text("Anywhere", muted=True, small=True))
        brand_row.addWidget(name_col, 1)
        col.addWidget(brand)

        col.addSpacing(theme.SPACE_MD)
        col.addWidget(divider())

        # ---- nav ---------------------------------------------------------------------
        for group_name, items in NAV_GROUPS:
            label = EyebrowLabel(group_name)
            label.setContentsMargins(theme.SPACE_SM, theme.SPACE_MD, 0, theme.SPACE_XS)
            col.addWidget(label)
            for key, text in items:
                col.addWidget(self._nav_row(key, text))

        col.addStretch(1)

        # ---- footer: the standing reminder about what this tool does ------------------
        warning = body_text("Mods inject DLLs.\nNever use online.", small=True)
        warning.setStyleSheet(f"color: {theme.COLOR_WARNING}; background: transparent;")
        warning.setContentsMargins(theme.SPACE_SM, 0, 0, 0)
        col.addWidget(warning)

        version = body_text(f"v{APP_VERSION}  ·  by {APP_AUTHOR}", muted=True, small=True)
        version.setContentsMargins(theme.SPACE_SM, 0, 0, 0)
        col.addWidget(version)
        return rail

    def _nav_row(self, key: str, text: str) -> QWidget:
        """One rail entry: an accent bar that says "you are here", and the label.

        A 3px rule rather than an icon. Emoji render at whatever size and colour the font
        decides, which is how a desktop app ends up with six differently-weighted glyphs
        in a column.
        """
        line = QWidget()
        row = hbox(line, spacing=theme.SPACE_SM)

        indicator = QFrame()
        indicator.setObjectName("NavIndicator")
        indicator.setFixedWidth(3)
        indicator.setMinimumHeight(22)
        indicator.setProperty("on", "false")
        row.addWidget(indicator)

        button = QPushButton(text)
        button.setObjectName("NavButton")
        button.setFont(theme.font_body())
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(34)
        button.clicked.connect(lambda _checked=False, k=key: self.show_view(k))
        row.addWidget(button, 1)

        self._nav_buttons[key] = button
        self._nav_indicators[key] = indicator
        return line

    def _build_views(self) -> None:
        """Build every view up front.

        Under Tk this had to be spread over idle callbacks, because a view was several
        hundred operating-system windows and constructing one blocked the loop for most of
        a second. A Qt view is one window and a tree of lightweight objects, so all seven
        cost less than a single Tk one did.
        """
        dashboard = DashboardView(log_callback=self.log)
        library = LibraryView(log_callback=self.log)
        library.game_selected.connect(self._on_game_selected)

        self._views = {
            "dashboard": dashboard,
            "library": library,
            "components": ComponentsView(log_callback=self.log),
            "profiles": ProfilesView(log_callback=self.log),
            "guide": GuideView(log_callback=self.log),
            "logs": LogsView(),
            "credits": CreditsView(),
        }
        for view in self._views.values():
            self.stack.addWidget(view)

        # Anything logged before the log view existed is replayed into it now; the startup
        # messages are the ones worth having and they arrive first.
        logs = self._views["logs"]
        for message, level in self._pending_logs:
            logs.append_log(message, level)
        self._pending_logs.clear()

    def _size_to_screen(self) -> None:
        """Open at a comfortable share of this screen, centred.

        A proportion rather than a pixel count, because the same window has to be sensible
        on a 1080p laptop panel and a 4K desktop monitor. Qt reports the screen in logical
        pixels that already account for the display's scaling, so there is no DPI
        arithmetic to get wrong here.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 820)
            return
        available = screen.availableGeometry()
        width = max(int(available.width() * 0.78), self.minimumWidth())
        height = max(int(available.height() * 0.84), self.minimumHeight())
        self.resize(min(width, available.width()), min(height, available.height()))
        self.move(
            available.left() + (available.width() - self.width()) // 2,
            available.top() + max((available.height() - self.height()) // 2 - 12, 0),
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def show_view(self, key: str) -> None:
        view = self._views.get(key)
        if view is None:
            return
        self.stack.setCurrentWidget(view)
        for name, button in self._nav_buttons.items():
            active = name == key
            button.setChecked(active)
            button.setFont(theme.font_body(bold=active))
            indicator = self._nav_indicators[name]
            indicator.setProperty("on", "true" if active else "false")
            restyle(indicator)   # a property set after construction needs a re-polish
        if key == "components":
            view.refresh_statuses()

    def _on_game_selected(self, exe_path: Path) -> None:
        """The Library tab picked a game: take it to the Builder and analyse it."""
        self.show_view("dashboard")
        self._views["dashboard"].load_game_path(exe_path)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, message: str, level: str = "INFO") -> None:
        """Central logger. Anything logged before the log view exists is held for it."""
        view = self._views.get("logs")
        if view is None:
            self._pending_logs.append((message, level))
            return
        view.append_log(message, level)


def build_application(argv: Optional[List[str]] = None) -> QApplication:
    """Create the QApplication with the settings that have to precede any widget."""
    existing = QApplication.instance()
    if existing is not None:
        return existing

    # Fractional display scaling - 125% and 150% are the common Windows settings - is
    # passed through rather than rounded to the nearest whole number. Rounding is what
    # makes a window at 150% either a sixth too large or a third too small.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # The window icon, and the identity Windows files this window under.
    #
    # setWindowIcon covers the title bar and Alt-Tab. It does not cover the taskbar: the
    # shell groups a window under the application ID of the process that created it,
    # which for a script is the interpreter's, so the taskbar button keeps showing
    # Python's icon however the window itself is marked. Claiming an ID of our own is
    # what fixes that, and it has to happen before the first window exists.
    if APP_ICON.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON)))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "DLSS5.Anywhere.Builder"
            )
        except (AttributeError, OSError):
            # Not worth failing a launch over: the title bar and Alt-Tab are still right,
            # and only the taskbar falls back to the interpreter's icon.
            pass

    # Fusion, not the native Windows style. The stylesheet below assumes a style that
    # honours it everywhere; the native one draws several controls through the OS theme
    # and ignores what it is told about them.
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(theme.font_body())
    app.setStyleSheet(theme.stylesheet())
    return app


def launch_gui() -> None:
    """Start the GUI application."""
    app = build_application()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    launch_gui()
