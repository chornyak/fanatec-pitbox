"""Building blocks of the Settings tab (design 1a): setting rows, the setups on the wheel base, and profile rows."""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget

from .widgets import repolish


def rule() -> QFrame:
    line = QFrame(objectName="rule")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


def section(title: str, panel: QWidget) -> QWidget:
    """Section label above its panel (label → panel 10px)."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(10)
    v.addWidget(QLabel(title, objectName="sectionLabel"))
    v.addWidget(panel)
    return box


def setting_row(title: str, helper: str, right: QWidget | None = None, title_extra: QWidget | None = None):
    """One row of a settings panel: title (+ e.g. a status dot) and helper on the left, controls on the right."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(20, 16, 20, 16)
    h.setSpacing(24)
    text = QVBoxLayout()
    text.setSpacing(3)
    head = QHBoxLayout()
    head.setSpacing(8)
    head.addWidget(QLabel(title, objectName="rowTitle"))
    if title_extra is not None:
        head.addWidget(title_extra, 0, Qt.AlignVCenter)
    head.addStretch(1)
    text.addLayout(head)
    text.addWidget(QLabel(helper, objectName="dim", wordWrap=True))
    h.addLayout(text, 1)
    if right is not None:
        h.addWidget(right, 0, Qt.AlignVCenter)
    return row


def meta(label: str, value: QLabel) -> QWidget:
    """Small faint label above a value (Firmware / 2305)."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)
    v.addWidget(QLabel(label, objectName="metaLabel"))
    v.addWidget(value)
    return box


def status_dot() -> QLabel:
    dot = QLabel(objectName="statusDot")
    dot.setFixedSize(7, 7)
    return dot


class BaseSetupRow(QWidget):
    """A setup on the wheel base in the Profiles panel: number box (yellow when active), name, summary."""

    def __init__(self, slot: int, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 7, 8, 7)
        h.setSpacing(10)
        self.number = QLabel(str(slot), objectName="setupNumber", alignment=Qt.AlignCenter)
        self.number.setFixedSize(22, 22)
        self.title = QLabel(objectName="setupName")
        self.summary = QLabel(objectName="metaValue")
        h.addWidget(self.number)
        h.addWidget(self.title, 1)
        h.addWidget(self.summary)

    def update_row(self, title: str, summary: str, active: bool):
        self.title.setText(title)
        self.summary.setText(summary)
        repolish(self.number, active=active)


def nice_date(saved: str) -> str:
    """'2026-10-02 21:07' → '2 Oct 2026, 21:07'."""
    try:
        t = time.strptime(saved, "%Y-%m-%d %H:%M")
    except ValueError:
        return saved
    return f"{t.tm_mday} {time.strftime('%b %Y, %H:%M', t)}"


class ProfileRow(QFrame):
    """A profile on this PC: name, "Loaded" pill, date and a ⋯ menu. Selected (and not loaded), it expands to
    show its setup names and a Load into wheel base button."""

    selected = Signal(str)
    load = Signal(str)
    action = Signal(str, str)  # name, "rename" | "duplicate" | "delete"

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.setObjectName("profileRow")
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 9, 8, 9)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.title = QLabel(name, objectName="profileName")
        self.pill = QLabel("Loaded", objectName="loadedPill")
        self.date = QLabel(objectName="metaValue")
        self.menu_btn = QToolButton(objectName="moreButton", text="⋯")
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.menu_btn.setFixedSize(26, 26)
        menu = QMenu(self.menu_btn)
        for verb, text in (("rename", "Rename…"), ("duplicate", "Duplicate…"), ("delete", "Delete…")):
            menu.addAction(text, lambda v=verb: self.action.emit(self.name, v))
        self.menu_btn.setMenu(menu)
        head.addWidget(self.title)
        head.addWidget(self.pill, 0, Qt.AlignVCenter)
        head.addStretch(1)
        head.addWidget(self.date)
        head.addWidget(self.menu_btn)
        v.addLayout(head)
        self.details = QLabel(objectName="metaValue", wordWrap=True)
        v.addWidget(self.details)
        load_row = QHBoxLayout()
        self.load_btn = QPushButton("← Load into wheel base")
        self.load_btn.setProperty("teal", True)
        self.load_btn.setCursor(Qt.PointingHandCursor)
        self.load_btn.clicked.connect(lambda: self.load.emit(self.name))
        load_row.addWidget(self.load_btn)
        load_row.addStretch(1)
        self.load_box = QWidget()
        self.load_box.setLayout(load_row)
        load_row.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.load_box)

    def update_row(self, saved: str, setup_names: str, loaded: bool, selected: bool, can_load: bool):
        self.date.setText(nice_date(saved))
        self.pill.setVisible(loaded)
        expanded = selected and not loaded
        self.details.setText(setup_names)
        self.details.setVisible(expanded)
        self.load_box.setVisible(expanded)
        self.load_btn.setEnabled(can_load)
        repolish(self, selected=expanded)
        repolish(self.title, selected=expanded)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.selected.emit(self.name)


class Disclosure(QWidget):
    """▸ Title (count) …… note — click to show or hide the content below it."""

    toggled = Signal(bool)

    def __init__(self, title: str, note: str, parent=None):
        super().__init__(parent)
        self.open = False
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self.arrow = QLabel("▸", objectName="metaValue")
        self.title = QLabel(title, objectName="disclosureTitle")
        self.count = QLabel(objectName="countPill")
        h.addWidget(self.arrow)
        h.addWidget(self.title)
        h.addWidget(self.count)
        h.addStretch(1)
        h.addWidget(QLabel(note, objectName="metaLabel"))

    def set_count(self, n: int):
        self.count.setText(str(n))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.open = not self.open
            self.arrow.setText("▾" if self.open else "▸")
            self.toggled.emit(self.open)
