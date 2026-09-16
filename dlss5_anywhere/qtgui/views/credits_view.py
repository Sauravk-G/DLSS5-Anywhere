"""
Credits.

This tool installs other people's work. The Credits tab exists so that is visible from
inside the app and not only in a README nobody opens after the first install - and so the
anti-cheat position is stated somewhere permanent rather than only in the dialog that
appears once.
"""

import webbrowser
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from ...config import (
    ANTI_CHEAT_GUIDANCE,
    ANTI_CHEAT_HEADLINE,
    APP_AUTHOR,
    APP_NAME,
    APP_VERSION,
    CREDITS,
    CREDITS_NOTE,
)
from .. import theme
from ..components import (
    Banner,
    Card,
    EyebrowLabel,
    ScrollPage,
    body_text,
    divider,
    heading,
    hbox,
    vbox,
)


class CreditsView(QWidget):
    """Attribution for every project this tool depends on, plus the standing warning."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = vbox(self, margin=0)
        page = ScrollPage()
        layout.addWidget(page)

        # ---- masthead ---------------------------------------------------------------
        page.add(heading("Built on other people's work", "display"))
        page.add(body_text(CREDITS_NOTE))
        page.add_spacing(theme.SPACE_SM)

        # ---- the standing anti-cheat position ---------------------------------------
        page.add(Banner(
            tone="danger",
            title=ANTI_CHEAT_HEADLINE,
            body=ANTI_CHEAT_GUIDANCE.replace("\n\n", "  "),
        ))
        page.add_spacing(theme.SPACE_SM)

        # ---- the list ---------------------------------------------------------------
        page.add(EyebrowLabel("Projects this build depends on"))
        for project, authors, role, url in CREDITS:
            page.add(self._credit_card(project, authors, role, url))

        # ---- licence ----------------------------------------------------------------
        page.add_spacing(theme.SPACE_MD)
        page.add(divider())
        page.add(body_text(
            f"{APP_NAME} v{APP_VERSION} — built by {APP_AUTHOR}, released under the MIT "
            "License. Made for the game preservation and modding community.",
            muted=True, small=True,
        ))
        page.finish()

    def _credit_card(self, project: str, authors: str, role: str, url: str) -> Card:
        card = Card()
        card._outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_MD, theme.SPACE_LG, theme.SPACE_MD
        )

        title_row = QWidget()
        row = hbox(title_row, spacing=theme.SPACE_SM)

        name = QLabel(project)
        name.setFont(theme.font_body(bold=True))
        name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        row.addWidget(name)

        by = QLabel(f"· {authors}")
        by.setFont(theme.font_body())
        by.setStyleSheet(f"color: {theme.ACCENT_GREEN}; background: transparent;")
        row.addWidget(by)
        row.addStretch(1)

        if url:
            link = QLabel(url.replace("https://", "").replace("http://", ""))
            link.setFont(theme.font_small())
            link.setCursor(Qt.PointingHandCursor)
            link.setStyleSheet(f"color: {theme.ACCENT_CYAN}; background: transparent;")
            link.mousePressEvent = lambda _e, u=url: webbrowser.open(u)
            row.addWidget(link)

        card.add(title_row)
        card.add(body_text(role, small=True))
        return card
