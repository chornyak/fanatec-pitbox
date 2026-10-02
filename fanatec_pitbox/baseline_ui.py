"""Fanatec's recommended baseline: the first-run dialog and the Settings panel share BaselinePicker."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                               QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from .baselines import SOURCE, SOURCE_TITLE, Baseline, baseline_for
from .params import Model


class BaselinePicker(QWidget):
    """Target setup plus a table of the setup's current values next to the recommended ones."""

    changed = Signal()

    def __init__(self, params: dict, current_values, parent=None):
        """`current_values(slot)` returns the last known values of a setup, or None if unknown."""
        super().__init__(parent)
        self.params = params
        self.current_values = current_values
        self.model: Model | None = None
        self.active = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        self.current_box = QCheckBox("Make this my current setup (recommended)")
        self.current_box.setChecked(True)
        self.current_box.toggled.connect(self._refresh_target)
        self.slot_combo = QComboBox()
        self.slot_combo.currentIndexChanged.connect(lambda _i: self._preview())
        self.target_widget = QWidget()
        target = QHBoxLayout(self.target_widget)
        target.setContentsMargins(0, 0, 0, 0)
        target.addWidget(self.current_box)
        target.addSpacing(12)
        target.addWidget(self.slot_combo)
        target.addStretch(1)
        v.addWidget(self.target_widget)

        self.preview = QLabel(wordWrap=True, objectName="baselinePreview")
        self.preview.setTextFormat(Qt.RichText)
        self.preview.setOpenExternalLinks(True)
        v.addWidget(self.preview)
        self._refresh_target()

    def set_model(self, model: Model | None):
        if model != self.model:
            self.model = model
            self._preview()

    def set_slots(self, titles: dict[int, str], active: int | None):
        """Setups to offer when not writing to the current one."""
        current = self.slot_combo.currentData()
        self.slot_combo.blockSignals(True)
        self.slot_combo.clear()
        for n, title in titles.items():
            if n != active:
                self.slot_combo.addItem(title, n)
        self.slot_combo.setCurrentIndex(max(0, self.slot_combo.findData(current)))
        self.slot_combo.blockSignals(False)
        self.active = active
        self._refresh_target()

    def _refresh_target(self, *_):
        self.slot_combo.setVisible(not self.current_box.isChecked())
        self._preview()

    def refresh(self):
        """Re-read the current values (they change when the base reports)."""
        self._preview()

    def baseline(self) -> Baseline | None:
        return baseline_for(self.model.key) if self.model else None

    def target_slot(self) -> int | None:
        return self.active if self.current_box.isChecked() else self.slot_combo.currentData()

    def _preview(self):
        b, m = self.baseline(), self.model
        self.target_widget.setVisible(b is not None)
        if not b:
            self.preview.setText("<span style='color:#8a8d93'>Choose your wheel base above to see Fanatec's "
                                 "recommended values.</span>" if not m else
                                 "<span style='color:#8a8d93'>No recommended baseline is published for this "
                                 "wheel base yet.</span>")
            self.changed.emit()
            return
        slot = self.target_slot()
        current = (self.current_values(slot) if slot else None) or {}
        head = ("<tr><td></td><td style='padding:0 28px 6px 0;color:#8a8d93'>Current</td>"
                "<td style='padding:0 0 6px 0;color:#8a8d93'>Recommended</td></tr>")
        rows, changes = [], 0
        for key, p in self.params.items():
            if key not in b.values:
                continue
            new, old = b.values[key], current.get(key)
            differs = old is not None and old != new
            changes += differs
            style = "color:#f2e600;font-weight:700" if differs else ""
            rows.append(f"<tr><td style='padding:3px 28px 3px 0;color:#c9c9c9'>[{key.upper()}] {p.label}</td>"
                        f"<td style='padding:3px 28px 3px 0;color:#8a8d93'>{p.fmt(old) if old is not None else '—'}"
                        f"</td><td style='padding:3px 0;{style}'>{p.fmt(new)}</td></tr>")
        summary = (f"<span style='color:#f2e600'>{changes} value{'s' if changes != 1 else ''} will change.</span> "
                   if current else "")
        note = b.note.replace("{label}", m.label) if b.note and m.key not in b.named else ""
        self.preview.setText(
            f"<table>{head}{''.join(rows)}</table>"
            f"<p style='color:#8a8d93'>{summary}BLI and BRF are personal preference and stay as they are."
            f"{'<br>' + note if note else ''}<br>Source: <a style='color:#2bb8aa' href='{SOURCE}'>{SOURCE_TITLE}</a>"
            f"</p>")
        self.changed.emit()


class ModelCombo(QComboBox):
    """Pick the wheel base model among those sharing the detected USB id."""

    def __init__(self, models, current: Model | None, parent=None):
        super().__init__(parent)
        if current is None:
            self.addItem("Choose your wheel base…", None)
        for m in models:
            self.addItem(m.label, m)
        if current is not None:
            self.setCurrentIndex(self.findText(current.label))

    def model(self) -> Model | None:
        return self.currentData()


class FirstRunDialog(QDialog):
    """Shown once: confirms the wheel base model and offers Fanatec's recommended baseline."""

    def __init__(self, models, current: Model | None, params: dict, current_values, slot_titles: dict,
                 active: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Fanatec Pitbox")
        self.resize(720, 780)
        self._only_model = models[0] if len(models) == 1 else None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(12)
        v.addWidget(QLabel("Welcome to Fanatec Pitbox", objectName="sideTitle"))

        self.model_combo = None
        if len(models) > 1:
            v.addWidget(QLabel("Which wheel base do you have? Several models share the same USB id, so the app "
                               "can't tell them apart on its own.", wordWrap=True))
            self.model_combo = ModelCombo(models, current)
            row = QHBoxLayout()
            row.addWidget(self.model_combo)
            row.addStretch(1)
            v.addLayout(row)
        else:
            v.addWidget(QLabel(f"Detected wheel base: <b>{models[0].label}</b>"))

        self.choice_widget = QWidget()
        choice = QVBoxLayout(self.choice_widget)
        choice.setContentsMargins(0, 6, 0, 0)
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

        self.picker = BaselinePicker(params, current_values)
        self.picker.set_slots(slot_titles, active)
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
                                "any time under <b>Settings → Recommended baseline</b>.", wordWrap=True,
                                objectName="dim")
        self.keep_note.setTextFormat(Qt.RichText)
        v.addWidget(self.keep_note)
        self.spacer = QWidget()
        v.addWidget(self.spacer, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.continue_btn = QPushButton("CONTINUE")
        self.continue_btn.setProperty("accent", True)
        self.continue_btn.setDefault(True)
        self.continue_btn.clicked.connect(self.accept)
        buttons.addWidget(self.continue_btn)
        v.addLayout(buttons)

        if self.model_combo:
            self.model_combo.currentIndexChanged.connect(lambda _i: self._update())
        self._update()

    def chosen_model(self) -> Model | None:
        return self.model_combo.model() if self.model_combo else self._only_model

    @property
    def use_baseline(self) -> bool:
        return self.recommended_rb.isChecked() and self.picker.baseline() is not None

    def _update(self):
        model = self.chosen_model()
        self.picker.set_model(model)
        has_baseline = self.picker.baseline() is not None
        self.choice_widget.setVisible(has_baseline)
        show_table = model is None or (has_baseline and self.recommended_rb.isChecked())
        self.scroll.setVisible(show_table)
        self.spacer.setVisible(not show_table)
        self.keep_note.setVisible(has_baseline and self.keep_rb.isChecked())
        self.continue_btn.setEnabled(model is not None)
