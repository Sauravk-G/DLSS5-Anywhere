"""
Composed UI components.

Everything here exists so that a panel, a status chip or a metric looks the same in every
view without each view re-deriving it from raw colours. A view that needs a card asks for
a Card; it does not pick a fill, a border and a radius and hope they match the card three
tabs over.

Most of what this module used to do under Tk has gone. Colour and shape now live in the
stylesheet, so these classes are about *structure* - what a card is made of, what a bullet
row contains - and not about repeating twelve keyword arguments. The wrapping machinery
has gone entirely: a Qt label given `setWordWrap(True)` wraps to whatever width its layout
hands it, recomputed on every resize, which is the thing the Tk build needed a
width-observer and a scaling correction to approximate.

Two widgets are painted by hand rather than styled: the check box and the switch. Qt's
stylesheet can shape a check indicator but cannot put a tick inside it without an image
file, and drawing the tick is both fewer moving parts and sharper at every DPI.
"""

from typing import Iterable, Optional, Sequence, Tuple, Union

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def restyle(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property has changed.

    Qt resolves `[variant="x"]` selectors when a widget is polished and does not watch the
    property afterwards, so a property set after construction needs this to take effect.
    """
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _label(text: str, fnt, colour: str, wrap: bool = False, name: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(fnt)
    lbl.setStyleSheet(f"color: {colour}; background: transparent;")
    if name:
        lbl.setObjectName(name)
    if wrap:
        lbl.setWordWrap(True)
        lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


def heading(text: str, level: str = "subtitle") -> QLabel:
    fonts = {
        "display": theme.font_display,
        "title": theme.font_title,
        "subtitle": theme.font_subtitle,
        "section": theme.font_section,
    }
    return _label(text, fonts[level](), theme.TEXT_PRIMARY)


def body_text(text: str, muted: bool = False, small: bool = False, bold: bool = False,
              wrap: bool = True) -> QLabel:
    """A paragraph, or - with `wrap=False` - a caption sitting beside a control.

    The distinction matters more than it looks. A wrapping label reports a minimum width
    of one word, so a layout is free to squeeze it to nothing and stack "Video memory to
    report:" over four lines to give a combo box room it did not need. A label that names
    the control next to it should never wrap; a paragraph always should.
    """
    fnt = theme.font_small(bold) if small else theme.font_body(bold)
    return _label(text, fnt, theme.TEXT_MUTED if muted else theme.TEXT_SECONDARY, wrap=wrap)


class EyebrowLabel(QLabel):
    """A small uppercase label that names a field without competing with its value."""

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text.upper(), parent)
        self.setFont(theme.font_label())
        self.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent;")


def divider(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    if vertical:
        line.setFixedWidth(1)
    else:
        line.setFixedHeight(1)
    return line


def accent_rule(colour: str = theme.ACCENT_GREEN, width: int = 3, height: int = 18) -> QFrame:
    """The short coloured bar that marks a heading or names a card's kind.

    Cheaper than an icon and it reads at any font size, which an emoji does not - the
    glyph a system font picks for one changes shape between Windows versions.
    """
    rule = QFrame()
    rule.setObjectName("AccentRule")
    rule.setFixedSize(width, height)
    rule.setStyleSheet(f"background-color: {colour}; border-radius: {width // 2}px;")
    return rule


def vbox(widget: QWidget, margin: int = 0, spacing: int = theme.SPACE_SM) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return layout


def hbox(widget: QWidget, margin: int = 0, spacing: int = theme.SPACE_SM) -> QHBoxLayout:
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return layout


# --------------------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------------------
def _button(text: str, variant: str, on_click=None, width: Optional[int] = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("variant", variant)
    btn.setFont(theme.font_body(bold=variant in ("primary", "dangerFill")))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(theme.HEIGHT_BUTTON)
    if width:
        btn.setFixedWidth(width)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def primary_button(text: str, on_click=None, width: Optional[int] = None) -> QPushButton:
    """The one action a screen exists for."""
    return _button(text, "primary", on_click, width)


def secondary_button(text: str, on_click=None, width: Optional[int] = None) -> QPushButton:
    """Everything else - outlined, so it never competes with the primary action."""
    return _button(text, "secondary", on_click, width)


def danger_button(text: str, on_click=None, width: Optional[int] = None) -> QPushButton:
    """Destructive, or accepting a risk the app has just spelled out."""
    return _button(text, "danger", on_click, width)


def danger_fill_button(text: str, on_click=None, width: Optional[int] = None) -> QPushButton:
    return _button(text, "dangerFill", on_click, width)


def info_button(text: str, on_click=None, width: Optional[int] = None) -> QPushButton:
    return _button(text, "info", on_click, width)


def quiet_button(text: str, on_click=None, width: Optional[int] = None) -> QPushButton:
    """A small, low-contrast action that sits inside a row of content."""
    btn = _button(text, "quiet", on_click, width)
    btn.setFont(theme.font_small())
    btn.setMinimumHeight(theme.HEIGHT_BUTTON_SM)
    return btn


# --------------------------------------------------------------------------------------
# Controls drawn by hand
# --------------------------------------------------------------------------------------
class CheckBox(QCheckBox):
    """A check box with a real tick, and text that wraps if it is given a width.

    Qt's stylesheet can shape the indicator but can only put a mark inside it by pointing
    at an image file, which would mean shipping a bitmap that is wrong at every scaling
    factor but one. The tick here is a two-segment path scaled to the box, so it is sharp
    at any DPI, and drawing it costs less than the stylesheet rule it replaces.
    """

    BOX = 16
    GAP = 10

    def __init__(self, text: str = "", parent: Optional[QWidget] = None, danger: bool = False):
        super().__init__(text, parent)
        self.setFont(theme.font_body())
        self.setCursor(Qt.PointingHandCursor)
        self._wrap = False
        self._danger = danger
        self.setAttribute(Qt.WA_Hover, True)

    def setWordWrap(self, wrap: bool) -> None:
        self._wrap = wrap
        # A layout only asks heightForWidth of a widget whose size policy says to. Without
        # this it uses sizeHint instead, which for wrapped text is a height for some width
        # the widget is not going to get - so the row is reserved several lines too tall
        # and the extra shows up as a gap under it.
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(wrap)
        self.setSizePolicy(policy)
        self.updateGeometry()

    def _text_rect(self, width: int, height: float) -> QRectF:
        left = self.BOX + self.GAP
        return QRectF(left, 0, max(width - left, 10), height)

    def hasHeightForWidth(self) -> bool:
        return self._wrap

    def heightForWidth(self, width: int) -> int:
        if not self._wrap:
            return super().sizeHint().height()
        rect = self.fontMetrics().boundingRect(
            self._text_rect(width, 10_000).toRect(), Qt.TextWordWrap, self.text()
        )
        return max(rect.height() + 4, self.BOX + 4)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        if self._wrap:
            return QSize(240, self.heightForWidth(240))
        width = self.BOX + self.GAP + metrics.horizontalAdvance(self.text()) + 4
        return QSize(width, max(metrics.height() + 6, self.BOX + 6))

    def minimumSizeHint(self) -> QSize:
        if self._wrap:
            return QSize(self.BOX + self.GAP + 60, self.BOX + 4)
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        enabled = self.isEnabled()
        checked = self.isChecked()
        fill = theme.COLOR_DANGER if self._danger else theme.ACCENT_GREEN

        # The box sits on the first text line, not on the middle of a wrapped block.
        top = (self.fontMetrics().height() - self.BOX) / 2 if self._wrap else \
            (self.height() - self.BOX) / 2
        box = QRectF(0.5, max(top, 0) + 0.5, self.BOX - 1, self.BOX - 1)

        if checked and enabled:
            painter.setBrush(theme.qcolor(fill))
            painter.setPen(QPen(theme.qcolor(fill), 1))
        else:
            painter.setBrush(theme.qcolor(theme.BG_INPUT))
            border = theme.BORDER_STRONG
            if enabled and self.underMouse():
                border = fill
            painter.setPen(QPen(theme.qcolor(border), 1))
        painter.drawRoundedRect(box, 4, 4)

        if checked:
            tick = QPainterPath()
            tick.moveTo(QPointF(box.left() + box.width() * 0.26, box.top() + box.height() * 0.52))
            tick.lineTo(QPointF(box.left() + box.width() * 0.44, box.top() + box.height() * 0.70))
            tick.lineTo(QPointF(box.left() + box.width() * 0.76, box.top() + box.height() * 0.30))
            ink = theme.TEXT_ON_ACCENT if not self._danger else "#FFFFFF"
            pen = QPen(theme.qcolor(ink if enabled else theme.TEXT_MUTED), 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(tick)

        painter.setPen(theme.qcolor(theme.TEXT_SECONDARY if enabled else theme.TEXT_MUTED))
        flags = Qt.AlignLeft | (Qt.AlignTop | Qt.TextWordWrap if self._wrap else Qt.AlignVCenter)
        painter.drawText(self._text_rect(self.width(), self.height()), flags, self.text())


class ToggleSwitch(QAbstractButton):
    """An on/off switch, for a setting that takes effect rather than one that is ticked.

    A check box says "include this"; a switch says "this is running". The app has both
    kinds and used to draw them the same way, so a control that turns the neural pipeline
    on looked identical to one that adds an effect to a list.
    """

    WIDTH = 38
    HEIGHT = 20

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setFont(theme.font_body())
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def sizeHint(self) -> QSize:
        width = self.WIDTH + (theme.SPACE_MD + self.fontMetrics().horizontalAdvance(self.text())
                              if self.text() else 0)
        return QSize(width + 4, max(self.HEIGHT + 6, self.fontMetrics().height() + 6))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        enabled = self.isEnabled()
        on = self.isChecked()
        top = (self.height() - self.HEIGHT) / 2
        track = QRectF(0.5, top + 0.5, self.WIDTH - 1, self.HEIGHT - 1)

        if on and enabled:
            painter.setBrush(theme.qcolor(theme.ACCENT_GREEN_DARK))
            painter.setPen(QPen(theme.qcolor(theme.ACCENT_GREEN), 1))
        else:
            painter.setBrush(theme.qcolor(theme.BG_INPUT))
            painter.setPen(QPen(theme.qcolor(theme.BORDER_STRONG), 1))
        painter.drawRoundedRect(track, self.HEIGHT / 2, self.HEIGHT / 2)

        knob_d = self.HEIGHT - 6
        knob_x = track.right() - knob_d - 2.5 if on else track.left() + 2.5
        knob = QRectF(knob_x, track.top() + 3, knob_d, knob_d)
        if on and enabled:
            painter.setBrush(theme.qcolor(theme.ACCENT_GREEN))
        else:
            painter.setBrush(theme.qcolor(theme.TEXT_MUTED if enabled else theme.BORDER_STRONG))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(knob)

        if self.text():
            painter.setPen(theme.qcolor(theme.TEXT_SECONDARY if enabled else theme.TEXT_MUTED))
            painter.drawText(
                QRectF(self.WIDTH + theme.SPACE_MD, 0, self.width() - self.WIDTH, self.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                self.text(),
            )


# --------------------------------------------------------------------------------------
# Status display
# --------------------------------------------------------------------------------------
class Pill(QLabel):
    """A status chip: one short word, in its own colour, on a tint of that colour.

    The text always states the status as well. Colour makes it findable; it is never the
    only thing carrying the meaning.
    """

    def __init__(self, text: str = "", tone: str = "neutral", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setFont(theme.font_small(bold=True))
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.tone = tone
        self._apply(tone)

    def _apply(self, tone: str) -> None:
        ink, ground = theme.tone(tone)
        self.setStyleSheet(
            f"color: {ink}; background-color: {ground};"
            f" border-radius: {theme.RADIUS_SM}px; padding: 3px 9px;"
        )

    def set(self, text: str, tone: Optional[str] = None) -> None:
        self.setText(text)
        if tone and tone != self.tone:
            self.tone = tone
            self._apply(tone)


class DiagnosticTile(QFrame):
    """An eyebrow label over the one value it reports, for the analysis read-out."""

    def __init__(self, label: str, value: str = "—", colour: str = theme.TEXT_MUTED,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Sunken")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._layout = vbox(self, margin=theme.SPACE_MD, spacing=2)
        self._layout.addWidget(EyebrowLabel(label))

        self.value_lbl = QLabel(value)
        self.value_lbl.setFont(theme.font_body(bold=True))
        self.value_lbl.setWordWrap(True)
        self.value_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._layout.addWidget(self.value_lbl)

    def set(self, value: str, colour: str = theme.TEXT_PRIMARY) -> None:
        self.value_lbl.setText(value)
        self.value_lbl.setStyleSheet(f"color: {colour}; background: transparent;")

    def add(self, widget: QWidget) -> None:
        self._layout.addWidget(widget, 0, Qt.AlignLeft)


class Banner(QFrame):
    """A full-width message with a tone, for anything the user has to read."""

    def __init__(self, tone: str = "warning", title: str = "", body: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        outer = hbox(self, margin=0, spacing=0)
        self.stripe = QFrame()
        self.stripe.setFixedWidth(3)
        outer.addWidget(self.stripe)

        text_col = QWidget()
        col = vbox(text_col, margin=theme.SPACE_MD, spacing=3)
        outer.addWidget(text_col, 1)

        self.title_lbl = QLabel(title)
        self.title_lbl.setFont(theme.font_body(bold=True))
        self.title_lbl.setWordWrap(True)
        col.addWidget(self.title_lbl)

        self.body_lbl = QLabel(body)
        self.body_lbl.setFont(theme.font_small())
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; background: transparent;")
        col.addWidget(self.body_lbl)
        self.body_lbl.setVisible(bool(body))

        self.set(title, body, tone)

    def set(self, title: str, body: str = "", tone: Optional[str] = None) -> None:
        if tone:
            ink, ground = theme.tone(tone)
            self.setStyleSheet(
                f"QFrame {{ background-color: {ground};"
                f" border: {theme.BORDER_WIDTH}px solid {ink};"
                f" border-radius: {theme.RADIUS_MD}px; }}"
            )
            self.stripe.setStyleSheet(f"background-color: {ink}; border: none;")
            self.title_lbl.setStyleSheet(f"color: {ink}; background: transparent; border: none;")
            self.body_lbl.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; background: transparent; border: none;"
            )
        self.title_lbl.setText(title)
        self.body_lbl.setText(body)
        self.body_lbl.setVisible(bool(body))


class KeyCap(QLabel):
    """A keyboard key, drawn as one."""

    def __init__(self, key: str, parent: Optional[QWidget] = None):
        super().__init__(key, parent)
        self.setFont(theme.font_code(small=True))
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background-color: {theme.BG_RAISED};"
            f" border: {theme.BORDER_WIDTH}px solid {theme.BORDER_STRONG};"
            f" border-bottom-width: 2px;"
            f" border-radius: {theme.RADIUS_SM}px; padding: 3px 10px;"
        )


# --------------------------------------------------------------------------------------
# Headers and panels
# --------------------------------------------------------------------------------------
class SectionHeader(QWidget):
    """A heading with a coloured rule, for a section that is not part of a sequence."""

    def __init__(self, title: str, subtitle: Optional[str] = None,
                 accent: str = theme.ACCENT_GREEN, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = vbox(self, spacing=3)

        top = QWidget()
        top_row = hbox(top, spacing=theme.SPACE_MD)
        top_row.addWidget(accent_rule(accent, 3, 18), 0, Qt.AlignVCenter)
        top_row.addWidget(heading(title, "subtitle"), 0, Qt.AlignVCenter)
        top_row.addStretch(1)
        layout.addWidget(top)

        if subtitle:
            sub = body_text(subtitle, muted=True, small=True)
            sub.setContentsMargins(15, 0, 0, 0)
            layout.addWidget(sub)


class StepHeader(QWidget):
    """A numbered step: the number in a chip, the title beside it.

    The Builder is a four-step sequence and used to say so by starting each heading with
    "1. ", "2. " and so on. That is a number the reader has to pick out of a sentence.
    Putting it in a chip of its own gives the page a spine you can follow down the left
    edge without reading a word, which is the whole job of numbering the steps.
    """

    def __init__(self, number: int, title: str, subtitle: Optional[str] = None,
                 done: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._done = None

        layout = hbox(self, spacing=theme.SPACE_MD)

        self.chip = QLabel(str(number))
        self.chip.setFont(theme.font_small(bold=True))
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setFixedSize(24, 24)
        layout.addWidget(self.chip, 0, Qt.AlignTop)

        text_col = QWidget()
        col = vbox(text_col, spacing=1)
        col.addWidget(heading(title, "subtitle"))
        if subtitle:
            col.addWidget(body_text(subtitle, muted=True, small=True))
        layout.addWidget(text_col, 1)

        self.accessory = QWidget()
        self.accessory_layout = hbox(self.accessory, spacing=theme.SPACE_SM)
        layout.addWidget(self.accessory, 0, Qt.AlignTop)

        self.set_done(done)

    def set_done(self, done: bool) -> None:
        """Fill the chip once the step has been satisfied."""
        if done == self._done:
            return
        self._done = done
        ground = theme.ACCENT_GREEN if done else theme.BG_RAISED
        ink = theme.TEXT_ON_ACCENT if done else theme.TEXT_SECONDARY
        self.chip.setStyleSheet(
            f"color: {ink}; background-color: {ground}; border-radius: {theme.RADIUS_SM}px;"
        )


class Card(QFrame):
    """A panel, optionally with a title row and a right-hand accessory slot.

    `body` is the layout to put content into; `header_layout` is the row the title sits
    on, for anything that belongs beside it.
    """

    def __init__(self, title: Optional[str] = None, subtitle: Optional[str] = None,
                 accent: Optional[str] = None, flat: bool = False,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        self._outer = vbox(self, margin=theme.SPACE_LG, spacing=theme.SPACE_SM)
        self.header_layout: Optional[QHBoxLayout] = None

        if title:
            header = QWidget()
            self.header_layout = hbox(header, spacing=theme.SPACE_SM)

            text_col = QWidget()
            col = vbox(text_col, spacing=2)
            title_row = QWidget()
            trow = hbox(title_row, spacing=theme.SPACE_SM)
            if accent:
                trow.addWidget(accent_rule(accent, 3, 15), 0, Qt.AlignVCenter)
            trow.addWidget(heading(title, "section"), 0, Qt.AlignVCenter)
            trow.addStretch(1)
            col.addWidget(title_row)
            if subtitle:
                col.addWidget(body_text(subtitle, muted=True, small=True))

            self.header_layout.addWidget(text_col, 1)
            self._outer.addWidget(header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.SPACE_SM)
        self._outer.addLayout(self.body)

    def add(self, item: Union[QWidget, QLayout], stretch: int = 0) -> None:
        if isinstance(item, QLayout):
            self.body.addLayout(item, stretch)
        else:
            self.body.addWidget(item, stretch)

    def add_header_widget(self, widget: QWidget) -> None:
        if self.header_layout is not None:
            self.header_layout.addWidget(widget, 0, Qt.AlignTop)


BulletItem = Union[str, Tuple[str, Optional[str]]]


class BulletList(QWidget):
    """A list where each item is its own row, wrapped and hanging-indented.

    Every list in this app used to be one label holding a string full of newlines and
    bullet prefixes. That has three problems and the guide had all three: a continuation
    line wraps back under the bullet instead of under the text, so the shape of the list
    disappears as soon as an item runs long; the indentation of a sub-item is spaces,
    which stop lining up the moment the font changes; and no part of it can be styled, so
    a term and its explanation are the same grey.

    Items are `(text, detail)` pairs, or plain strings. `level` indents a nested run.
    """

    def __init__(self, items: Sequence[BulletItem], level: int = 0,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = vbox(self, spacing=theme.SPACE_SM)
        layout.setContentsMargins(level * theme.SPACE_LG, 0, 0, 0)

        for item in items:
            text, detail = (item, None) if isinstance(item, str) else item

            line = QWidget()
            row_layout = hbox(line, spacing=theme.SPACE_SM)

            marker = QLabel("•" if level == 0 else "–")
            marker.setFont(theme.font_body(bold=True))
            marker.setFixedWidth(10)
            marker.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            marker.setStyleSheet(
                f"color: {theme.ACCENT_GREEN if level == 0 else theme.TEXT_MUTED};"
                " background: transparent;"
            )
            row_layout.addWidget(marker, 0, Qt.AlignTop)

            text_col = QWidget()
            col = vbox(text_col, spacing=2)
            lead = QLabel(text)
            lead.setFont(theme.font_body(bold=bool(detail)))
            lead.setWordWrap(True)
            lead.setStyleSheet(
                f"color: {theme.TEXT_PRIMARY if detail else theme.TEXT_SECONDARY};"
                " background: transparent;"
            )
            lead.setTextInteractionFlags(Qt.TextSelectableByMouse)
            col.addWidget(lead)

            if detail:
                explain = body_text(detail, small=True)
                col.addWidget(explain)

            row_layout.addWidget(text_col, 1)
            layout.addWidget(line)


class SegmentedControl(QWidget):
    """One choice out of a short, fixed set, shown as a joined row of segments.

    For a filter with five options and no room for five separate controls. The whole set
    is visible at once, which a drop-down cannot manage, and the pointer never has to
    travel to find the option next to the current one.
    """

    changed = Signal(str)

    def __init__(self, options: Iterable[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = hbox(self, margin=3, spacing=2)

        self._buttons = {}
        for index, option in enumerate(options):
            btn = QPushButton(option)
            btn.setObjectName("SegmentedButton")
            btn.setFont(theme.font_small(bold=True))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setChecked(index == 0)
            btn.clicked.connect(lambda _checked=False, value=option: self.set_value(value))
            layout.addWidget(btn)
            self._buttons[option] = btn

        self._value = next(iter(self._buttons), "")

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        if value not in self._buttons:
            return
        self._value = value
        for option, btn in self._buttons.items():
            btn.setChecked(option == value)
        self.changed.emit(value)


# --------------------------------------------------------------------------------------
# Scrolling
# --------------------------------------------------------------------------------------
class ScrollPage(QScrollArea):
    """A scrollable page. `body` is the layout to fill.

    Worth noting what is *not* here. Under Tk this was 274 lines: a hand-written scroll
    area that moved a content frame with `place`, because the stock scrollable frame drew
    through a canvas that smeared its children across the window on a fast wheel. Qt
    scrolls a double-buffered viewport, so the whole problem - and the whole file -
    disappears. What is left is choosing a wheel step that matches the rest of the
    desktop.
    """

    def __init__(self, margins: int = theme.SPACE_XL, spacing: int = theme.SPACE_MD,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(28)

        # Both of these are transparent by name rather than by a stylesheet set on them:
        # a stylesheet set on a container applies to everything inside it, and a
        # "background: transparent" there repaints every button on the page.
        self._content = QWidget()
        self._content.setObjectName("ScrollBody")
        self.body = vbox(self._content, margin=margins, spacing=spacing)
        self.setWidget(self._content)
        self.viewport().setAutoFillBackground(False)
        self._content.setAutoFillBackground(False)

    @property
    def content(self) -> QWidget:
        return self._content

    def add(self, item: Union[QWidget, QLayout], stretch: int = 0) -> None:
        if isinstance(item, QLayout):
            self.body.addLayout(item, stretch)
        else:
            self.body.addWidget(item, stretch)

    def add_spacing(self, amount: int) -> None:
        self.body.addSpacing(amount)

    def finish(self) -> None:
        """Push the content to the top; call once, after everything has been added."""
        self.body.addStretch(1)
