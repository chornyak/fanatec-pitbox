"""Dialogs (design 1a): the first-run wizard (check your system → your wheel base → starting point), System check,
Recommended baseline and the wheel base model picker. They share one frame: title, subtitle, body, and a footer
with the secondary action on the left and one yellow primary on the right."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from .baseline_ui import BaselinePicker
from .checks import FAIL, OK, SKIP, WARN, Check, summary
from .params import Model
from .widgets import repolish


def _dot(state: str, size: int = 8) -> QLabel:
    dot = QLabel(objectName="statusDot")
    dot.setFixedSize(size, size)
    dot.setStyleSheet(f"border-radius:{size // 2}px;")
    repolish(dot, state=state)
    return dot


def _scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea(widgetResizable=True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    holder = QWidget(objectName="scrollHolder")
    hl = QVBoxLayout(holder)
    hl.setContentsMargins(0, 0, 14, 0)
    hl.addWidget(widget)
    hl.addStretch(1)
    scroll.setWidget(holder)
    return scroll


def _overall(checks: list[Check]) -> str:
    return FAIL if any(c.status == FAIL for c in checks) else WARN if any(c.status == WARN for c in checks) else OK


class CheckSummary(QWidget):
    """Status dot and the one-line result ("Everything is set up", "1 problem to fix, 1 warning")."""

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.dot = _dot(OK)
        self.label = QLabel(objectName="checkSummary")
        h.addWidget(self.dot)
        h.addWidget(self.label)
        h.addStretch(1)

    def set_checks(self, checks: list[Check]):
        state = _overall(checks)
        self.label.setText(summary(checks))
        repolish(self.dot, state=state)
        repolish(self.label, state=state)

    def text(self) -> str:
        return self.label.text()


class CheckList(QFrame):
    """One row per check in a panel: status dot, title, explanation and, when there is one, the fix command."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.checks: list[Check] = []

    def set_checks(self, checks: list[Check]):
        self.checks = checks
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, c in enumerate(checks):
            row = QWidget(objectName="checkRow" if i < len(checks) - 1 else "checkRowLast")
            row.setAttribute(Qt.WA_StyledBackground, True)
            h = QHBoxLayout(row)
            h.setContentsMargins(18, 12, 18, 12)
            h.setSpacing(14)
            dot_box = QVBoxLayout()
            dot_box.setContentsMargins(0, 6, 0, 0)
            dot_box.addWidget(_dot(c.status if c.status != SKIP else "skip"))
            dot_box.addStretch(1)
            h.addLayout(dot_box)
            text = QVBoxLayout()
            text.setSpacing(3)
            title = QLabel(c.title, objectName="checkTitle")
            repolish(title, muted=c.status == SKIP)
            text.addWidget(title)
            text.addWidget(QLabel(c.detail, objectName="dim", wordWrap=True))
            if c.fix:
                fix_row = QHBoxLayout()
                fix_row.setContentsMargins(0, 4, 0, 0)
                fix_row.setSpacing(8)
                cmd = QLineEdit(c.fix, objectName="fixCommand", readOnly=True)
                cmd.setCursorPosition(0)
                copy = QPushButton("Copy", objectName="small")
                copy.setCursor(Qt.PointingHandCursor)
                copy.clicked.connect(lambda _=False, t=c.fix, b=copy: self._copy(t, b))
                fix_row.addWidget(cmd, 1)
                fix_row.addWidget(copy)
                text.addLayout(fix_row)
            h.addLayout(text, 1)
            self.rows.addWidget(row)

    @staticmethod
    def _copy(text: str, button: QPushButton):
        QGuiApplication.clipboard().setText(text)
        button.setText("Copied")


class RadioCard(QFrame):
    """A radio button in a card; the whole card is clickable and highlighted when selected."""

    def __init__(self, text: str, helper: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("radioCard")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(12)
        self.radio = QRadioButton()
        self.radio.toggled.connect(self._toggled)
        h.addWidget(self.radio, 0, Qt.AlignTop)
        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        self.label = QLabel(text, objectName="cardTitle")
        text_box.addWidget(self.label)
        if helper:
            text_box.addWidget(QLabel(helper, objectName="dim", wordWrap=True))
        h.addLayout(text_box, 1)

    def _toggled(self, on: bool):
        repolish(self, selected=on)
        repolish(self.label, selected=on)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.radio.setChecked(True)


class ModelCards(QWidget):
    """The models sharing the detected USB id, as radio cards."""

    changed = Signal()

    def __init__(self, models, current: Model | None, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.group = QButtonGroup(self)
        self.cards = {}
        for i, m in enumerate(models):
            card = RadioCard(m.label)
            self.group.addButton(card.radio, i)
            self.cards[m.key] = (card, m)
            v.addWidget(card)
            if current is not None and m.key == current.key:
                card.radio.setChecked(True)
        self.group.idToggled.connect(lambda _i, on: on and self.changed.emit())

    def model(self) -> Model | None:
        return next((m for card, m in self.cards.values() if card.radio.isChecked()), None)

    def select(self, label: str):
        for card, m in self.cards.values():
            if m.label == label:
                card.radio.setChecked(True)


class StepProgress(QWidget):
    """Three thin bars, yellow up to the current step, with the step names below."""

    def __init__(self, titles, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        bars, names = QHBoxLayout(), QHBoxLayout()
        bars.setSpacing(6)
        names.setSpacing(18)
        self.bars, self.names = [], []
        for i, title in enumerate(titles):
            bar = QFrame(objectName="stepBar")
            bar.setFixedHeight(3)
            bars.addWidget(bar, 1)
            name = QLabel(f"{i + 1} {title}", objectName="stepName")
            names.addWidget(name, 1)
            self.bars.append(bar)
            self.names.append(name)
        v.addLayout(bars)
        v.addLayout(names)

    def set_current(self, index: int):
        for i, (bar, name) in enumerate(zip(self.bars, self.names)):
            repolish(bar, done=i <= index)
            repolish(name, current=i == index)

    def current_title(self) -> str:
        return next((n.text() for n in self.names if n.property("current")), "")


class DialogFrame(QDialog):
    """Title + optional subtitle, a body, and the footer bar."""

    def __init__(self, title: str, subtitle: str = "", parent=None, width=720, height=760):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, height)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QVBoxLayout()
        head.setContentsMargins(28, 22, 28, 0)
        head.setSpacing(4)
        self.title_lbl = QLabel(title, objectName="dialogTitle")
        head.addWidget(self.title_lbl)
        self.subtitle_lbl = QLabel(subtitle, objectName="dialogSubtitle", wordWrap=True)
        self.subtitle_lbl.setVisible(bool(subtitle))
        head.addWidget(self.subtitle_lbl)
        v.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(28, 18, 28, 24)
        self.body.setSpacing(16)
        v.addLayout(self.body, 1)
        footer = QFrame(objectName="dialogFooter")
        self.footer = QHBoxLayout(footer)
        self.footer.setContentsMargins(28, 14, 28, 14)
        self.footer.setSpacing(10)
        v.addWidget(footer)

    def add_footer(self, secondary: QPushButton | None, primary: QPushButton):
        if secondary is not None:
            self.footer.addWidget(secondary)
        self.footer.addStretch(1)
        primary.setProperty("primary", True)
        primary.setDefault(True)
        self.footer.addWidget(primary)


class ModelDialog(DialogFrame):
    """Change the wheel base model (Settings → Wheel base → Change…)."""

    def __init__(self, models, current: Model | None, parent=None):
        super().__init__("Your wheel base", "Several models share the same USB id, so the app can't tell them "
                         "apart on its own. This sets the header and Fanatec's recommended settings.", parent,
                         height=520)
        self.cards = ModelCards(models, current)
        self.body.addWidget(self.cards)
        self.body.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.accept)
        self.add_footer(cancel, self.save_btn)
        self.cards.changed.connect(lambda: self.save_btn.setEnabled(self.cards.model() is not None))
        self.save_btn.setEnabled(self.cards.model() is not None)

    def chosen_model(self) -> Model | None:
        return self.cards.model()


class SystemCheckDialog(DialogFrame):
    """The system check on its own, opened from Settings."""

    def __init__(self, run_checks, parent=None):
        super().__init__("System check", "Everything Fanatec Pitbox needs from your system, and how to fix what's "
                         "missing.", parent)
        self.run_checks = run_checks
        self.summary = CheckSummary()
        self.check_list = CheckList()
        self.body.addWidget(self.summary)
        self.body.addWidget(_scroll(self.check_list), 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        again = QPushButton("Check again")
        again.clicked.connect(self.recheck)
        self.add_footer(close, again)
        self.recheck()

    def recheck(self):
        checks = self.run_checks()
        self.check_list.set_checks(checks)
        self.summary.set_checks(checks)


class BaselineDialog(DialogFrame):
    """The welcome wizard's 'Starting point' on its own (Tuning → Setup tools → Recommended baseline…)."""

    def __init__(self, models, current: Model | None, params: dict, current_values, slot_names: dict,
                 active: int | None, parent=None):
        super().__init__("Recommended baseline", "", parent)
        self._only_model = models[0] if len(models) == 1 else None
        self.model_cards = None
        if current is None and len(models) > 1:  # the model decides the values: ask for it first
            self.body.addWidget(QLabel("Which wheel base do you have?", objectName="bodyMuted"))
            self.model_cards = ModelCards(models, None)
            self.model_cards.changed.connect(self._update)
            self.body.addWidget(self.model_cards)
        self._current = current
        self.picker = BaselinePicker(params, current_values)
        self.picker.set_slots(slot_names, active)
        self.picker.changed.connect(self._update)
        self.body.addWidget(_scroll(self.picker), 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.accept)
        self.add_footer(cancel, self.apply_btn)
        self._update()

    def chosen_model(self) -> Model | None:
        if self.model_cards is not None:
            return self.model_cards.model()
        return self._current or self._only_model

    def _update(self):
        model = self.chosen_model()
        self.picker.blockSignals(True)
        self.picker.set_model(model)
        self.picker.blockSignals(False)
        self.subtitle_lbl.setText(
            f"Fanatec's recommended starting settings for {model.label}. All setups are backed up before it is "
            f"applied." if model else "Fanatec's recommended starting settings for your wheel base.")
        self.subtitle_lbl.show()
        self.apply_btn.setEnabled(self.picker.baseline() is not None and self.picker.target_slot() is not None)


class FirstRunDialog(DialogFrame):
    """Shown on first start. The wheel base steps are only included when a working wheel base is found;
    without one the wizard is just the system check, and it comes back on the next start."""

    INTRO = ("Fanatec Pitbox works on top of the hid-fanatecff driver. First, a quick check that everything it "
             "needs is in place.")

    def __init__(self, run_checks, models, current: Model | None, params: dict, current_values,
                 slot_names: dict, active: int | None, base_steps: bool, parent=None):
        super().__init__("Welcome to Fanatec Pitbox", self.INTRO, parent)
        self.run_checks = run_checks
        self.base_steps = base_steps
        self._only_model = models[0] if len(models) == 1 else None
        self.model_cards = None

        self.steps: list[tuple[str, QWidget]] = [("Check your system", self._check_page())]
        if base_steps and len(models) > 1:
            self.steps.append(("Your wheel base", self._model_page(models, current)))
        self.picker = BaselinePicker(params, current_values)
        self.picker.set_slots(slot_names, active)
        if base_steps:
            self.steps.append(("Starting point", self._baseline_page()))
        self.progress = StepProgress([t for t, _ in self.steps])
        self.progress.setVisible(len(self.steps) > 1)
        self.body.addWidget(self.progress)
        self.stack = QStackedWidget()
        for _title, page in self.steps:
            self.stack.addWidget(page)
        self.body.addWidget(self.stack, 1)

        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        self.again_btn = QPushButton("Check again")
        self.again_btn.clicked.connect(self.recheck)
        self.footer.addWidget(self.again_btn)
        self.next_btn = QPushButton("Next")
        self.next_btn.clicked.connect(self._next)
        self.add_footer(self.back_btn, self.next_btn)
        if self.model_cards:
            self.model_cards.changed.connect(self._update)
        self._go(0)

    # -- pages ---------------------------------------------------------------------------------
    def _check_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        self.check_summary = CheckSummary()
        self.check_list = CheckList()
        v.addWidget(self.check_summary)
        v.addWidget(_scroll(self.check_list), 1)
        self.no_base_note = QLabel("Once the wheel base is connected and working, this welcome comes back on the "
                                   "next start to set up your wheel base.", objectName="dim", wordWrap=True)
        self.no_base_note.setVisible(not self.base_steps)
        v.addWidget(self.no_base_note)
        self.recheck()
        return page

    def _model_page(self, models, current):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        v.addWidget(QLabel("Which wheel base do you have? Several models share the same USB id, so the app can't "
                           "tell them apart on its own. This sets the header and Fanatec's recommended settings, "
                           "and can be changed later in Settings.", objectName="bodyMuted", wordWrap=True))
        self.model_cards = ModelCards(models, current)
        v.addWidget(self.model_cards)
        v.addStretch(1)
        return page

    def _baseline_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        self.choice_widget = QWidget()
        choice = QHBoxLayout(self.choice_widget)
        choice.setContentsMargins(0, 0, 0, 0)
        choice.setSpacing(8)
        self.choice = QButtonGroup(self)
        self.recommended_card = RadioCard("Recommended baseline", "Fanatec's starting settings for your base.")
        self.keep_card = RadioCard("Keep my current settings", "Nothing on the wheel base changes.")
        self.recommended_rb, self.keep_rb = self.recommended_card.radio, self.keep_card.radio
        for i, card in enumerate((self.recommended_card, self.keep_card)):
            self.choice.addButton(card.radio, i)
            choice.addWidget(card, 1)
        self.recommended_rb.setChecked(True)
        self.choice.idClicked.connect(lambda _i: self._update())
        v.addWidget(self.choice_widget)
        self.scroll = _scroll(self.picker)
        v.addWidget(self.scroll, 1)
        self.keep_note = QLabel("Nothing on your wheel base is changed. You can apply the recommended baseline any "
                                "time from Tuning → Setup tools.", objectName="dim", wordWrap=True)
        v.addWidget(self.keep_note)
        self.spacer = QWidget()
        v.addWidget(self.spacer, 1)
        return page

    # -- behaviour -------------------------------------------------------------------------------
    def recheck(self):
        checks = self.run_checks()
        self.check_list.set_checks(checks)
        self.check_summary.set_checks(checks)

    def chosen_model(self) -> Model | None:
        return self.model_cards.model() if self.model_cards else self._only_model

    @property
    def use_baseline(self) -> bool:
        return self.base_steps and self.recommended_rb.isChecked() and self.picker.baseline() is not None

    def _next(self):
        if self.stack.currentIndex() >= len(self.steps) - 1:
            self.accept()
        else:
            self._go(self.stack.currentIndex() + 1)

    def _go(self, index: int):
        index = max(0, min(index, len(self.steps) - 1))
        self.stack.setCurrentIndex(index)
        self.progress.set_current(index)
        self.subtitle_lbl.setVisible(index == 0)
        self._update()

    def _update(self):
        index = self.stack.currentIndex()
        last = index == len(self.steps) - 1
        title = self.steps[index][0]
        self.back_btn.setVisible(index > 0)
        self.again_btn.setVisible(index == 0)
        model = self.chosen_model()
        if self.base_steps:
            self.picker.set_model(model)
            has_baseline = self.picker.baseline() is not None
            self.choice_widget.setVisible(has_baseline)
            show_table = has_baseline and self.recommended_rb.isChecked()
            self.scroll.setVisible(show_table or not has_baseline)
            self.spacer.setVisible(not (show_table or not has_baseline))
            self.keep_note.setVisible(has_baseline and self.keep_rb.isChecked())
        self.next_btn.setEnabled(title != "Your wheel base" or model is not None)
        if not last:
            self.next_btn.setText("Next")
        elif self.use_baseline:
            self.next_btn.setText("Apply and finish")
        else:
            self.next_btn.setText("Finish")
