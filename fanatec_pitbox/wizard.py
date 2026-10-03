"""First-run wizard (check your system → your wheel base → starting point) and the system check list it
shares with the Settings page."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QRadioButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from .baseline_ui import BaselinePicker, ModelCombo
from .checks import FAIL, OK, SKIP, WARN, Check, summary
from .params import Model

_ICONS = {OK: ("✓", "#3cc46a"), WARN: ("!", "#f2a600"), FAIL: ("✕", "#e0584d"), SKIP: ("–", "#5a5d63")}


def status_icon(label: QLabel, checks: list[Check]):
    """Overall result as one mark: green ✓, yellow ! (suggestions) or red ! (problems); summary on hover."""
    fail = any(c.status == FAIL for c in checks)
    warn = any(c.status == WARN for c in checks)
    mark, color = ("!", _ICONS[FAIL][1]) if fail else ("!", _ICONS[WARN][1]) if warn else _ICONS[OK]
    label.setText(mark)
    label.setStyleSheet(f"color:{color}; border:2px solid {color}; border-radius:13px; font-weight:800;")
    label.setToolTip(summary(checks))
    label.setProperty("state", FAIL if fail else WARN if warn else OK)


class CheckList(QWidget):
    """One row per check: status mark, title, explanation and, if there is one, the fix command to copy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(12)
        self.grid.setColumnStretch(1, 1)
        self.checks: list[Check] = []

    def set_checks(self, checks: list[Check]):
        self.checks = checks
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for row, c in enumerate(checks):
            mark, color = _ICONS[c.status]
            icon = QLabel(mark, alignment=Qt.AlignCenter, objectName="checkMark")
            icon.setFixedSize(24, 24)
            icon.setStyleSheet(f"color:{color}; border:2px solid {color}; border-radius:12px;")
            self.grid.addWidget(icon, row, 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.setContentsMargins(0, 0, 0, 0)  # so the title line starts level with the status mark
            text.setSpacing(4)
            title = QLabel(c.title, objectName="checkTitle")
            title.setFixedHeight(24)  # same height as the mark: centred against each other
            title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if c.status == SKIP:
                title.setStyleSheet("color:#6a6c70;")
            detail = QLabel(c.detail, wordWrap=True, objectName="dim")
            text.addWidget(title)
            text.addWidget(detail)
            if c.fix:
                fix_row = QHBoxLayout()
                cmd = QLabel(c.fix, objectName="fixCommand", textInteractionFlags=Qt.TextSelectableByMouse)
                copy = QPushButton("Copy", objectName="small")
                copy.setCursor(Qt.PointingHandCursor)
                copy.clicked.connect(lambda _=False, t=c.fix, b=copy: self._copy(t, b))
                fix_row.addWidget(cmd)
                fix_row.addWidget(copy)
                fix_row.addStretch(1)
                text.addLayout(fix_row)
            holder = QWidget()
            holder.setLayout(text)
            self.grid.addWidget(holder, row, 1)
        for row in range(self.grid.rowCount()):
            self.grid.setRowStretch(row, 0)
        self.grid.setRowStretch(len(checks), 1)  # spare height goes below the list, not between rows

    @staticmethod
    def _copy(text: str, button: QPushButton):
        QGuiApplication.clipboard().setText(text)
        button.setText("Copied")


class SystemCheckDialog(QDialog):
    """The system check on its own, opened from Settings."""

    def __init__(self, run_checks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("System check")
        self.resize(720, 640)
        self.run_checks = run_checks
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(10)
        v.addWidget(QLabel("System check", objectName="sideTitle"))
        v.addWidget(QLabel("Everything Fanatec Pitbox needs from your system, and how to fix what's missing.",
                           wordWrap=True))
        self.summary = QLabel(objectName="checkSummary")
        v.addWidget(self.summary)
        self.check_list = CheckList()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="scrollHolder")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 10, 0)
        hl.addWidget(self.check_list)
        hl.addStretch(1)
        scroll.setWidget(holder)
        v.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        again = QPushButton("Check again")
        again.clicked.connect(self.recheck)
        close = QPushButton("CLOSE")
        close.setProperty("accent", True)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(again)
        buttons.addStretch(1)
        buttons.addWidget(close)
        v.addLayout(buttons)
        self.recheck()

    def recheck(self):
        checks = self.run_checks()
        self.check_list.set_checks(checks)
        set_summary(self.summary, checks)


def set_summary(label: QLabel, checks: list[Check]):
    """One-line result, red when something needs fixing."""
    label.setText(summary(checks))
    label.setProperty("bad", any(c.status == FAIL for c in checks))
    label.style().unpolish(label)
    label.style().polish(label)


class BaselineDialog(QDialog):
    """The welcome wizard's 'Starting point' step on its own, opened from Settings."""

    def __init__(self, models, current: Model | None, params: dict, current_values, slot_titles: dict,
                 active: int | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recommended baseline")
        self.resize(720, 760)
        self._only_model = models[0] if len(models) == 1 else None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(10)
        v.addWidget(QLabel("Recommended baseline", objectName="sideTitle"))
        v.addWidget(QLabel("Fanatec's recommended starting settings for your wheel base: a good baseline for a "
                           "setup to fine-tune from. All setups are backed up before it is applied.", wordWrap=True))
        self.model_combo = None
        if len(models) > 1:
            row = QHBoxLayout()
            row.addWidget(QLabel("Your wheel base:"))
            self.model_combo = ModelCombo(models, current)
            self.model_combo.currentIndexChanged.connect(lambda _i: self._update())
            row.addWidget(self.model_combo)
            row.addStretch(1)
            v.addLayout(row)
        self.picker = BaselinePicker(params, current_values)
        self.picker.set_slots(slot_titles, active)
        self.picker.changed.connect(self._update)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="scrollHolder")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 10, 0)
        hl.addWidget(self.picker)
        hl.addStretch(1)
        scroll.setWidget(holder)
        v.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.apply_btn = QPushButton("APPLY")
        self.apply_btn.setProperty("accent", True)
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_btn)
        v.addLayout(buttons)
        self._update()

    def chosen_model(self) -> Model | None:
        return self.model_combo.model() if self.model_combo else self._only_model

    def _update(self):
        self.picker.blockSignals(True)
        self.picker.set_model(self.chosen_model())
        self.picker.blockSignals(False)
        self.apply_btn.setEnabled(self.picker.baseline() is not None and self.picker.target_slot() is not None)


class FirstRunDialog(QDialog):
    """Shown on first start. The wheel base steps are only included when a working wheel base is found;
    without one the wizard is just the system check, and it comes back on the next start."""

    def __init__(self, run_checks, models, current: Model | None, params: dict, current_values,
                 slot_titles: dict, active: int | None, base_steps: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Fanatec Pitbox")
        self.resize(720, 780)
        self.run_checks = run_checks
        self.models = models
        self._only_model = models[0] if len(models) == 1 else None
        self.base_steps = base_steps
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(10)
        v.addWidget(QLabel("Welcome to Fanatec Pitbox", objectName="sideTitle"))
        self.step_lbl = QLabel(objectName="stepLabel")
        v.addWidget(self.step_lbl)
        line = QFrame(objectName="rule")
        line.setFrameShape(QFrame.HLine)
        v.addWidget(line)

        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.steps: list[tuple[str, QWidget]] = [("Check your system", self._check_page())]
        self.model_combo = None
        if base_steps and len(models) > 1:
            self.steps.append(("Your wheel base", self._model_page(models, current)))
        self.picker = BaselinePicker(params, current_values)
        self.picker.set_slots(slot_titles, active)
        if base_steps:
            self.steps.append(("Starting point", self._baseline_page()))
        for _title, page in self.steps:
            self.stack.addWidget(page)

        buttons = QHBoxLayout()
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        self.next_btn = QPushButton("NEXT")
        self.next_btn.setProperty("accent", True)
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        buttons.addWidget(self.back_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.next_btn)
        v.addLayout(buttons)
        if self.model_combo:
            self.model_combo.currentIndexChanged.connect(lambda _i: self._update())
        self._go(0)

    # -- pages -----------------------------------------------------------------------------------
    def _check_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(10)
        intro = QLabel("Fanatec Pitbox works on top of the hid-fanatecff driver. First, a quick check that "
                       "everything it needs is in place.", wordWrap=True)
        v.addWidget(intro)
        self.check_summary = QLabel(objectName="checkSummary")
        v.addWidget(self.check_summary)
        self.check_list = CheckList()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="scrollHolder")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 10, 0)
        hl.addWidget(self.check_list)
        hl.addStretch(1)
        scroll.setWidget(holder)
        v.addWidget(scroll, 1)
        self.no_base_note = QLabel(wordWrap=True, objectName="dim")
        v.addWidget(self.no_base_note)
        row = QHBoxLayout()
        again = QPushButton("Check again")
        again.clicked.connect(self.recheck)
        row.addWidget(again)
        row.addStretch(1)
        v.addLayout(row)
        self.recheck()
        return page

    def _model_page(self, models, current):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(12)
        v.addWidget(QLabel("Which wheel base do you have? Several models share the same USB id, so the app "
                           "can't tell them apart on its own. This is used for the header and for Fanatec's "
                           "recommended settings, and can be changed later in Settings.", wordWrap=True))
        self.model_combo = ModelCombo(models, current)
        row = QHBoxLayout()
        row.addWidget(self.model_combo)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return page

    def _baseline_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(10)
        self.choice_widget = QWidget()
        choice = QVBoxLayout(self.choice_widget)
        choice.setContentsMargins(0, 0, 0, 0)
        choice.setSpacing(8)
        choice.addWidget(QLabel("How would you like to start?"))
        self.choice = QButtonGroup(self)
        self.recommended_rb = QRadioButton("Start with Fanatec's recommended baseline (recommended)")
        self.keep_rb = QRadioButton("Keep my current settings")
        self.recommended_rb.setChecked(True)
        for i, rb in enumerate((self.recommended_rb, self.keep_rb)):
            self.choice.addButton(rb, i)
            choice.addWidget(rb)
        self.choice.idClicked.connect(lambda _i: self._update())
        v.addWidget(self.choice_widget)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="scrollHolder")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 10, 0)
        hl.addWidget(self.picker)
        hl.addStretch(1)
        self.scroll.setWidget(holder)
        v.addWidget(self.scroll, 1)
        self.keep_note = QLabel("Nothing on your wheel base is changed. You can apply the recommended baseline "
                                "any time with <b>Recommended baseline…</b> on the Settings page.", wordWrap=True,
                                objectName="dim")
        self.keep_note.setTextFormat(Qt.RichText)
        v.addWidget(self.keep_note)
        self.spacer = QWidget()
        v.addWidget(self.spacer, 1)
        return page

    # -- behaviour -------------------------------------------------------------------------------
    def recheck(self):
        checks = self.run_checks()
        self.check_list.set_checks(checks)
        set_summary(self.check_summary, checks)
        self.no_base_note.setText(
            "" if self.base_steps else
            "Once the wheel base is connected and working, this welcome comes back on the next start to set up "
            "your wheel base. Use Check again after fixing something.")

    def chosen_model(self) -> Model | None:
        return self.model_combo.model() if self.model_combo else self._only_model

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
        title = self.steps[index][0]
        self.step_lbl.setText(f"STEP {index + 1} OF {len(self.steps)}  ·  {title.upper()}")
        self._update()

    def _update(self):
        index = self.stack.currentIndex()
        last = index == len(self.steps) - 1
        title = self.steps[index][0]
        self.back_btn.setVisible(index > 0)
        model = self.chosen_model()
        if self.base_steps:
            self.picker.set_model(model)
            has_baseline = self.picker.baseline() is not None
            self.choice_widget.setVisible(has_baseline)
            show_table = has_baseline and self.recommended_rb.isChecked()
            self.scroll.setVisible(show_table or not has_baseline)
            self.spacer.setVisible(not (show_table or not has_baseline))
            self.keep_note.setVisible(has_baseline and self.keep_rb.isChecked())
        if title == "Your wheel base":
            self.next_btn.setEnabled(model is not None)
        else:
            self.next_btn.setEnabled(True)
        if not last:
            self.next_btn.setText("NEXT")
        elif self.use_baseline:
            self.next_btn.setText("APPLY AND FINISH")
        else:
            self.next_btn.setText("FINISH")
