"""
Modal dialogs.

The anti-cheat dialog is the one piece of UI in this app whose job is to be *harder* to
get through than the rest. Everything else here is optimised for one click; this is
deliberately not, because the cost it guards against - a permanently banned account -
lands on the user and cannot be undone by uninstalling the mod afterwards.
"""

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QWidget

from ..config import ANTI_CHEAT_GUIDANCE, ANTI_CHEAT_HEADLINE
from . import theme
from .components import (
    CheckBox,
    Pill,
    body_text,
    danger_fill_button,
    hbox,
    heading,
    secondary_button,
    vbox,
)


# Wide enough for the guidance to read as prose rather than a column of fragments.
WIDTH = 640


class RiskConfirmDialog(QDialog):
    """Confirm an install into a game that ships anti-cheat, or is played online.

    A plain yes/no box gets dismissed by reflex - it looks like every other dialog the
    user has clicked through today. This one names the anti-cheat that was found, states
    the consequence at the top rather than in the small print, and keeps the accept button
    disabled until the acknowledgement is ticked. Cancel is focused, so Return and Escape
    both back out safely.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        exe_name: str,
        risk_level: str,
        anti_cheat_names: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
        action: str = "Install",
    ):
        super().__init__(parent)
        self.setWindowTitle("Anti-cheat detected")
        self.setModal(True)
        # Fixed width. Every paragraph here wraps, and none of them knows how tall it is
        # until it knows how wide it is; pinning the width is what makes that answerable.
        self.setFixedWidth(WIDTH)

        is_online = risk_level == "online_competitive"
        headline = (
            f"{exe_name} is a known online / anti-cheat-protected title"
            if is_online
            else f"{exe_name} ships with anti-cheat protection"
        )

        layout = vbox(self, margin=theme.SPACE_XL, spacing=theme.SPACE_LG)

        # ---- headline ----------------------------------------------------------------
        head = QWidget()
        head_row = hbox(head, spacing=theme.SPACE_SM)
        head_row.addWidget(
            Pill("BAN RISK" if is_online else "ANTI-CHEAT", "danger"), 0, Qt.AlignTop
        )
        title = heading(headline, "subtitle")
        title.setWordWrap(True)          # some executables have very long names
        head_row.addWidget(title, 1)
        layout.addWidget(head)

        warn = body_text(ANTI_CHEAT_HEADLINE)
        warn.setStyleSheet(f"color: {theme.COLOR_DANGER}; background: transparent;")
        layout.addWidget(warn)

        # ---- what was actually found -------------------------------------------------
        detail_lines = list(warnings or [])
        if anti_cheat_names:
            detail_lines.insert(0, "Detected: " + ", ".join(anti_cheat_names))
        if detail_lines:
            found = QFrame()
            found.setObjectName("Sunken")
            found_col = vbox(found, margin=theme.SPACE_MD, spacing=theme.SPACE_XS)
            for line in detail_lines[:5]:
                found_col.addWidget(body_text(f"• {line}", small=True))
            layout.addWidget(found)

        layout.addWidget(body_text(ANTI_CHEAT_GUIDANCE, small=True))

        # ---- the gate ----------------------------------------------------------------
        self._ack = CheckBox(
            "I understand this can get my account permanently banned, and I will not take "
            "this installation online.",
            danger=True,
        )
        self._ack.setWordWrap(True)
        self._ack.toggled.connect(self._on_ack_toggle)
        layout.addWidget(self._ack)

        # ---- actions -----------------------------------------------------------------
        actions = QWidget()
        act_row = hbox(actions, spacing=theme.SPACE_SM)
        act_row.addStretch(1)
        cancel = secondary_button("Cancel", self.reject, width=110)
        act_row.addWidget(cancel)
        self._go = danger_fill_button(f"{action} anyway", self.accept, width=190)
        self._go.setEnabled(False)
        act_row.addWidget(self._go)
        layout.addWidget(actions)

        cancel.setDefault(True)
        cancel.setFocus()

        # Exactly as tall as what it has to say, at the width it has.
        #
        # A layout reports one minimum height, computed at its own minimum width - here
        # 356px, where every paragraph wraps to half as many lines again. Sizing the dialog
        # from that leaves it 190px taller than its content at the width it is actually
        # given, and the slack spreads into the gaps between the paragraphs, which reads as
        # though something failed to load in them. Asking the layout how tall it is at the
        # width it will have is the question that has the right answer.
        self.setFixedHeight(self.layout().heightForWidth(WIDTH))

    def _on_ack_toggle(self, checked: bool) -> None:
        self._go.setEnabled(checked)

    @classmethod
    def ask(cls, parent, analysis, action: str = "Install") -> bool:
        """Show the dialog for an analysis and return whether the user accepted.

        Returns True immediately when there is nothing to warn about, so callers can use
        it unconditionally instead of re-deriving the risk themselves.
        """
        if getattr(analysis, "risk_level", "none") == "none":
            return True
        dialog = cls(
            parent,
            exe_name=analysis.exe_name,
            risk_level=analysis.risk_level,
            anti_cheat_names=getattr(analysis, "anti_cheat_names", []),
            warnings=getattr(analysis, "anti_cheat_warnings", []),
            action=action,
        )
        return dialog.exec() == QDialog.Accepted
