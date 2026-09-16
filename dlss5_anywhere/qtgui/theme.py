"""
Design tokens and the application stylesheet.

The palette is carried over unchanged from the Tk build - it was designed there and there
was nothing wrong with it. What changes is how it reaches the screen. Under Tk every
colour had to be handed to every widget individually at construction time, because a Tk
widget has no notion of a style it belongs to; here the same values are compiled once
into a Qt stylesheet and applied to the whole application, so a card looks like a card
because it *is* a Card, not because seven arguments were passed correctly.

The ground is a cool, blue-leaning near-black rather than a neutral grey. The subject is a
graphics tool that spends its time reporting on GPUs, depth buffers and shader passes, and
a slightly blue ground lets the one saturated colour in the interface - the green that
marks anything DLSS is doing - sit on top without either fighting the other.

Two things drive the palette:

* **Separation comes from the surface ramp, not from borders.** The steps below are far
  enough apart to be seen, which lets borders drop back to a hairline that suggests an
  edge rather than drawing one.

* **One green.** Green means one thing - "this is on, ready, or the thing to press" - and
  the states that are *not* that get genuinely different hues: blue for information,
  amber for caution, red for danger.
"""

from PySide6.QtGui import QColor, QFont, QPalette

# --------------------------------------------------------------------------------------
# Surfaces. A ramp from the window ground up to a raised control.
# --------------------------------------------------------------------------------------
BG_MAIN = "#090B10"          # window ground
BG_SIDEBAR = "#0C0F15"       # navigation rail
BG_CARD = "#141922"          # panel surface
BG_CARD_HOVER = "#1A2130"    # panel surface, pointer over it
BG_RAISED = "#222A3A"        # a control sitting on a panel
BG_INPUT = "#0B0E14"         # sunken: entries, log views, code
BG_OVERLAY = "#05070A"       # behind a modal

# --------------------------------------------------------------------------------------
# Brand accent. Green because this tool exists to turn DLSS on.
# --------------------------------------------------------------------------------------
ACCENT_GREEN = "#46D07F"
ACCENT_GREEN_HOVER = "#5FDD93"
ACCENT_GREEN_DARK = "#2E9E60"
ACCENT_GREEN_SOFT = "#0F2A1D"   # tinted fill for a green-marked row

# Secondary accent, for informational emphasis that must not read as success.
ACCENT_CYAN = "#5AB0F0"
ACCENT_CYAN_HOVER = "#7CC4F6"
ACCENT_CYAN_SOFT = "#0E1F2C"

# --------------------------------------------------------------------------------------
# Semantic state.
# --------------------------------------------------------------------------------------
COLOR_SUCCESS = ACCENT_GREEN
COLOR_SUCCESS_SOFT = ACCENT_GREEN_SOFT
COLOR_WARNING = "#F2B441"
COLOR_WARNING_SOFT = "#2B2110"
COLOR_DANGER = "#F2585B"
COLOR_DANGER_SOFT = "#2C1316"
COLOR_INFO = "#5AB0F0"
COLOR_INFO_SOFT = "#0E1F2C"

COLOR_CONFIDENCE = {
    "high": COLOR_SUCCESS,
    "medium": COLOR_WARNING,
    "low": COLOR_DANGER,
}

# --------------------------------------------------------------------------------------
# Text and lines
# --------------------------------------------------------------------------------------
TEXT_PRIMARY = "#EDF1F8"
TEXT_SECONDARY = "#A4AEC1"
TEXT_MUTED = "#6D7889"
TEXT_ON_ACCENT = "#062012"   # dark text on the green fill; white on it fails contrast

BORDER_COLOR = "#1E2532"
BORDER_SUBTLE = "#161C26"
BORDER_STRONG = "#2C3547"
BORDER_FOCUS = ACCENT_GREEN

# --------------------------------------------------------------------------------------
# Tone table. One place that maps a semantic name to its (ink, ground) pair, used by the
# pills, banners, metric tiles and anything else that reports a state.
# --------------------------------------------------------------------------------------
TONES = {
    "neutral": (TEXT_SECONDARY, BG_RAISED),
    "accent": (ACCENT_GREEN, ACCENT_GREEN_SOFT),
    "info": (ACCENT_CYAN, ACCENT_CYAN_SOFT),
    "success": (COLOR_SUCCESS, COLOR_SUCCESS_SOFT),
    "warning": (COLOR_WARNING, COLOR_WARNING_SOFT),
    "danger": (COLOR_DANGER, COLOR_DANGER_SOFT),
}


def tone(name: str):
    """(ink, ground) for a tone name, falling back on neutral."""
    return TONES.get(name, TONES["neutral"])


# --------------------------------------------------------------------------------------
# Type. Segoe UI is the Windows system face and the right default here - the app is a
# Windows tool and should look native. Cascadia Mono carries every column of data, hash,
# path and log line, which is most of what this app displays.
#
# Sizes are points, and Qt resolves them against the monitor's real DPI, so one number is
# correct on every display. Under Tk these had to be chosen alongside a separate widget
# scaling factor that did not always agree with them.
# --------------------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"
FONT_MONO_FAMILY = "Cascadia Mono"

_SIZE_DISPLAY = 19
_SIZE_TITLE = 15
_SIZE_SUBTITLE = 12
_SIZE_SECTION = 10
_SIZE_BODY = 9.5
_SIZE_SMALL = 8.5
_SIZE_LABEL = 8
_SIZE_CODE = 9


def font(size: float, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont(FONT_MONO_FAMILY if mono else FONT_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return f


def font_display() -> QFont:
    f = font(_SIZE_DISPLAY, bold=True)
    f.setWeight(QFont.Weight.Bold)
    return f


def font_title() -> QFont:
    return font(_SIZE_TITLE, bold=True)


def font_subtitle() -> QFont:
    return font(_SIZE_SUBTITLE, bold=True)


def font_section() -> QFont:
    return font(_SIZE_SECTION, bold=True)


def font_body(bold: bool = False) -> QFont:
    return font(_SIZE_BODY, bold=bold)


def font_small(bold: bool = False) -> QFont:
    return font(_SIZE_SMALL, bold=bold)


def font_label() -> QFont:
    """The small uppercase eyebrow that names a field without competing with its value."""
    f = font(_SIZE_LABEL, bold=True)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    return f


def font_code(small: bool = False) -> QFont:
    return font(_SIZE_CODE - (1 if small else 0), mono=True)



# --------------------------------------------------------------------------------------
# Spacing and shape. One scale, used everywhere, so panels line up across views without
# anyone measuring.
# --------------------------------------------------------------------------------------
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
SPACE_2XL = 32

RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14

BORDER_WIDTH = 1

# Control heights. Anything the pointer aims at is at least 32px tall.
HEIGHT_CONTROL = 32
HEIGHT_BUTTON = 34
HEIGHT_BUTTON_SM = 28

# Nav rail width. Wide enough for the longest label at the body size without truncation.
SIDEBAR_WIDTH = 216


def palette() -> QPalette:
    """The colour roles Qt itself paints with, where no stylesheet rule reaches.

    A stylesheet covers the widgets this app builds. It does not cover what the *style*
    draws on its own account - a combo box's arrow, a scroll area's viewport fill, the
    chrome of a message or input dialog - and those fall back on the palette, which
    defaults to a light theme. Without this the app is dark except for the few places it
    did not think to name, which is worse than being light everywhere.
    """
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG_MAIN))
    pal.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Base, QColor(BG_INPUT))
    pal.setColor(QPalette.AlternateBase, QColor(BG_CARD))
    pal.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_MUTED))
    pal.setColor(QPalette.Button, QColor(BG_RAISED))
    pal.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.BrightText, QColor(COLOR_DANGER))
    pal.setColor(QPalette.Highlight, QColor(ACCENT_GREEN_DARK))
    pal.setColor(QPalette.HighlightedText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ToolTipBase, QColor(BG_RAISED))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Link, QColor(ACCENT_CYAN))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(TEXT_MUTED))
    return pal


def qcolor(value: str, alpha: int = 255) -> QColor:
    c = QColor(value)
    c.setAlpha(alpha)
    return c


# --------------------------------------------------------------------------------------
# The stylesheet
#
# Applied once to the QApplication. Everything that can be expressed here is expressed
# here rather than at each call site, which is the single biggest difference between this
# and the widget-by-widget colouring the Tk build needed: a rule written once covers every
# widget of that kind, including the ones added later.
#
# Selector notes:
#   * `#name` matches setObjectName, `[variant="x"]` matches a dynamic property. Changing
#     a property at runtime needs a style re-polish - see components.restyle().
#   * A bare QWidget does not paint a stylesheet background unless it has the
#     WA_StyledBackground attribute; the containers below are QFrames, which do.
# --------------------------------------------------------------------------------------
def stylesheet() -> str:
    return f"""
/* ---------- ground ------------------------------------------------------------ */
QWidget {{
    color: {TEXT_PRIMARY};
    background-color: transparent;
}}
QMainWindow, QDialog {{
    background-color: {BG_MAIN};
}}
#Content {{
    background-color: {BG_MAIN};
}}
/* A scroll area's viewport and its content widget are Qt's, not ours, and fill with the
   palette unless told otherwise. Named rather than matched by shape, so the rule cannot
   reach a card or a button further down the tree. */
QAbstractScrollArea > QWidget#qt_scrollarea_viewport {{
    background: transparent;
}}
#ScrollBody {{
    background: transparent;
}}
QToolTip {{
    background-color: {BG_RAISED};
    color: {TEXT_PRIMARY};
    border: {BORDER_WIDTH}px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    padding: 4px 8px;
}}

/* ---------- navigation rail --------------------------------------------------- */
#Sidebar {{
    background-color: {BG_SIDEBAR};
    border-right: {BORDER_WIDTH}px solid {BORDER_COLOR};
}}
#NavButton {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    color: {TEXT_SECONDARY};
    padding: 7px 10px;
    text-align: left;
}}
#NavButton:hover {{
    background-color: {BG_CARD_HOVER};
    color: {TEXT_PRIMARY};
}}
#NavButton:checked {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
}}
#NavIndicator {{
    background-color: transparent;
    border-radius: 1px;
}}
#NavIndicator[on="true"] {{
    background-color: {ACCENT_GREEN};
}}

/* ---------- surfaces ---------------------------------------------------------- */
#Card {{
    background-color: {BG_CARD};
    border: {BORDER_WIDTH}px solid {BORDER_COLOR};
    border-radius: {RADIUS_LG}px;
}}
#CardFlat {{
    background-color: {BG_CARD};
    border: none;
    border-radius: {RADIUS_LG}px;
}}
#Sunken {{
    background-color: {BG_INPUT};
    border: {BORDER_WIDTH}px solid {BORDER_SUBTLE};
    border-radius: {RADIUS_MD}px;
}}
#Divider {{
    background-color: {BORDER_SUBTLE};
    border: none;
}}
#AccentRule {{
    background-color: {ACCENT_GREEN};
    border-radius: 2px;
}}

/* ---------- text -------------------------------------------------------------- */
#Muted   {{ color: {TEXT_MUTED}; }}
#Body    {{ color: {TEXT_SECONDARY}; }}
#Heading {{ color: {TEXT_PRIMARY}; }}
#Eyebrow {{ color: {TEXT_MUTED}; }}

/* ---------- buttons ----------------------------------------------------------- */
QPushButton {{
    background-color: {BG_RAISED};
    color: {TEXT_PRIMARY};
    border: none;
    border-radius: {RADIUS_MD}px;
    padding: 7px 16px;
    min-height: {HEIGHT_BUTTON - 14}px;
}}
QPushButton:hover  {{ background-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background-color: {BG_CARD_HOVER}; }}
QPushButton:disabled {{ background-color: {BG_CARD}; color: {TEXT_MUTED}; }}

QPushButton[variant="primary"] {{
    background-color: {ACCENT_GREEN};
    color: {TEXT_ON_ACCENT};
}}
QPushButton[variant="primary"]:hover  {{ background-color: {ACCENT_GREEN_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background-color: {ACCENT_GREEN_DARK}; }}
QPushButton[variant="primary"]:disabled {{
    background-color: {ACCENT_GREEN_SOFT};
    color: {TEXT_MUTED};
}}

QPushButton[variant="secondary"] {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
    border: {BORDER_WIDTH}px solid {BORDER_STRONG};
}}
QPushButton[variant="secondary"]:hover {{
    background-color: {BG_CARD_HOVER};
    border-color: {TEXT_MUTED};
}}
QPushButton[variant="secondary"]:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER_COLOR};
}}

QPushButton[variant="danger"] {{
    background-color: transparent;
    color: {COLOR_DANGER};
    border: {BORDER_WIDTH}px solid {COLOR_DANGER};
}}
QPushButton[variant="danger"]:hover {{ background-color: {COLOR_DANGER_SOFT}; }}
QPushButton[variant="danger"]:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER_COLOR};
}}

QPushButton[variant="dangerFill"] {{
    background-color: {COLOR_DANGER};
    color: #FFFFFF;
}}
QPushButton[variant="dangerFill"]:hover {{ background-color: #F06A6D; }}
QPushButton[variant="dangerFill"]:disabled {{
    background-color: {COLOR_DANGER_SOFT};
    color: {TEXT_MUTED};
}}

QPushButton[variant="info"] {{
    background-color: transparent;
    color: {ACCENT_CYAN};
    border: {BORDER_WIDTH}px solid {BORDER_STRONG};
}}
QPushButton[variant="info"]:hover {{
    background-color: {ACCENT_CYAN_SOFT};
    border-color: {ACCENT_CYAN};
}}

QPushButton[variant="quiet"] {{
    background-color: {BG_INPUT};
    color: {TEXT_SECONDARY};
    border: {BORDER_WIDTH}px solid {BORDER_COLOR};
    padding: 5px 12px;
}}
QPushButton[variant="quiet"]:hover {{
    background-color: {BG_CARD_HOVER};
    color: {TEXT_PRIMARY};
}}

/* Filter chips in the log console. */
QPushButton[variant="chip"] {{
    background-color: transparent;
    color: {TEXT_MUTED};
    border: {BORDER_WIDTH}px solid {BORDER_COLOR};
    border-radius: 14px;
    padding: 4px 14px;
    min-height: 20px;
}}
QPushButton[variant="chip"]:hover {{ background-color: {BG_CARD_HOVER}; }}
QPushButton[variant="chip"]:checked {{ background-color: {BG_CARD}; }}

/* ---------- text entry -------------------------------------------------------- */
QLineEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: {BORDER_WIDTH}px solid {BORDER_COLOR};
    border-radius: {RADIUS_MD}px;
    padding: 6px 10px;
    selection-background-color: {ACCENT_GREEN_DARK};
    selection-color: {TEXT_PRIMARY};
}}
QLineEdit:focus {{ border-color: {BORDER_FOCUS}; }}
QLineEdit:disabled {{ color: {TEXT_MUTED}; }}

QPlainTextEdit, QTextEdit {{
    background-color: transparent;
    color: {TEXT_SECONDARY};
    border: none;
    selection-background-color: {ACCENT_GREEN_DARK};
    selection-color: {TEXT_PRIMARY};
}}

/* ---------- combo box --------------------------------------------------------- */
QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: {BORDER_WIDTH}px solid {BORDER_COLOR};
    border-radius: {RADIUS_MD}px;
    padding: 5px 10px;
    min-height: {HEIGHT_CONTROL - 14}px;
}}
QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
QComboBox:focus {{ border-color: {BORDER_FOCUS}; }}
QComboBox:disabled {{ color: {TEXT_MUTED}; }}
/* The drop-down button and its arrow are left to the style. Styling either one means
   supplying an arrow image, and any bitmap shipped for it is the wrong size at every
   scaling factor but the one it was drawn for; Fusion draws a vector triangle in the
   palette's text colour, which is correct at all of them. */
QComboBox QAbstractItemView {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: {BORDER_WIDTH}px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    outline: none;
    selection-background-color: {ACCENT_GREEN_SOFT};
    selection-color: {ACCENT_GREEN};
}}
QComboBox QAbstractItemView::item {{
    padding: 5px 8px;
    border-radius: {RADIUS_SM - 2}px;
    min-height: 20px;
}}

/* ---------- check box and switch ---------------------------------------------- */
QCheckBox {{
    color: {TEXT_SECONDARY};
    spacing: 9px;
    background: transparent;
}}
QCheckBox:disabled {{ color: {TEXT_MUTED}; }}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: {BORDER_WIDTH}px solid {BORDER_STRONG};
    background-color: {BG_INPUT};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT_GREEN}; }}
QCheckBox::indicator:checked {{
    background-color: {ACCENT_GREEN};
    border-color: {ACCENT_GREEN};
    image: url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-16.png);
}}
QCheckBox[variant="danger"]::indicator:checked {{
    background-color: {COLOR_DANGER};
    border-color: {COLOR_DANGER};
}}

/* ---------- slider ------------------------------------------------------------ */
QSlider::groove:horizontal {{
    height: 4px;
    background: {BG_RAISED};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT_GREEN_DARK};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT_GREEN};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_GREEN_HOVER}; }}
/* Subcontrol first, then its state. Written the other way round -
   `QSlider:disabled::handle` - Qt reads the rule as one on the slider itself and
   paints the whole widget this colour, in every state. */
QSlider::handle:horizontal:disabled {{ background: {TEXT_MUTED}; }}
QSlider {{ background: transparent; }}

/* ---------- progress bar ------------------------------------------------------ */
QProgressBar {{
    background-color: {BG_INPUT};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {ACCENT_GREEN};
    border-radius: 4px;
}}

/* ---------- scroll bars -------------------------------------------------------- */
QScrollArea {{ border: none; background: transparent; }}
QAbstractScrollArea {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0; background: none; border: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 0 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0; background: none; border: none;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

/* ---------- game list ---------------------------------------------------------- */
#GameList {{
    background: transparent;
    border: none;
    outline: none;
}}

/* ---------- segmented store filter --------------------------------------------- */
#Segmented {{
    background-color: {BG_INPUT};
    border: {BORDER_WIDTH}px solid {BORDER_COLOR};
    border-radius: {RADIUS_MD}px;
}}
#SegmentedButton {{
    background-color: transparent;
    color: {TEXT_SECONDARY};
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 5px 12px;
    min-height: 18px;
}}
#SegmentedButton:hover {{ color: {TEXT_PRIMARY}; }}
#SegmentedButton:checked {{
    background-color: {ACCENT_GREEN};
    color: {TEXT_ON_ACCENT};
}}

/* ---------- message box -------------------------------------------------------- */
QMessageBox {{ background-color: {BG_CARD}; }}
QMessageBox QLabel {{ color: {TEXT_PRIMARY}; }}
"""
