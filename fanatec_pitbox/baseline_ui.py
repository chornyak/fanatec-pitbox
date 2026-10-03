"""Fanatec's recommended baseline: the first-run wizard and the Settings panel share BaselinePicker."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .baselines import SOURCE, SOURCE_TITLE, Baseline, baseline_for
from .params import Model


class BaselinePicker(QWidget):
    """Target setup plus a table of the setup's current values next to the recommended ones."""

    changed = Signal()
    values_needed = Signal(int)  # the target setup's current values are unknown; the app can read them

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
        if slot and not current:
            self.values_needed.emit(slot)
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
        if not current:
            summary = ""
        elif changes:
            summary = f"<span style='color:#f2e600'>{changes} value{'s' if changes != 1 else ''} will change.</span> "
        else:
            summary = "<span style='color:#3cc46a'>Already matches the recommended baseline.</span> "
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
