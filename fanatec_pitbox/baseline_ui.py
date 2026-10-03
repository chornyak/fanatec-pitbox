"""Fanatec's recommended baseline: the "Apply to" choice and the comparison table, shared by the welcome wizard's
last step and the Recommended baseline dialog (design 1a)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .baselines import SOURCE, SOURCE_TITLE, Baseline, baseline_for
from .params import Model


class BaselinePicker(QWidget):
    """Apply to [setup ▾], then Setting · Current → Recommended, with the values that change highlighted."""

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
        v.setSpacing(16)

        self.target_widget = QWidget()
        target = QHBoxLayout(self.target_widget)
        target.setContentsMargins(0, 0, 0, 0)
        target.setSpacing(12)
        target.addWidget(QLabel("Apply to", objectName="toolbarLabel"))
        self.apply_to = QComboBox()
        self.apply_to.setFixedWidth(220)
        self.apply_to.currentIndexChanged.connect(lambda _i: self._preview())
        target.addWidget(self.apply_to)
        target.addStretch(1)
        v.addWidget(self.target_widget)

        self.table = QFrame(objectName="panel")
        self.grid = QGridLayout(self.table)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(0)
        v.addWidget(self.table)

        notes = QVBoxLayout()
        notes.setSpacing(4)
        self.note = QLabel(objectName="dim", wordWrap=True)
        self.note.setTextFormat(Qt.RichText)
        self.source = QLabel(objectName="footnote", wordWrap=True)
        self.source.setTextFormat(Qt.RichText)
        self.source.setOpenExternalLinks(True)
        notes.addWidget(self.note)
        notes.addWidget(self.source)
        v.addLayout(notes)
        self._preview()

    def set_model(self, model: Model | None):
        if model != self.model:
            self.model = model
            self._preview()

    def set_slots(self, names: dict[int, str], active: int | None):
        """Setups to apply to; the active one is marked and selected by default."""
        current = self.apply_to.currentData()
        self.apply_to.blockSignals(True)
        self.apply_to.clear()
        for n, name in names.items():
            self.apply_to.addItem(f"{name} (active)" if n == active else name, n)
        index = self.apply_to.findData(current if current is not None and self.active == active else active)
        self.apply_to.setCurrentIndex(max(0, index))
        self.apply_to.blockSignals(False)
        self.active = active
        self._preview()

    def refresh(self):
        """Re-read the current values (they change when the base reports)."""
        self._preview()

    def baseline(self) -> Baseline | None:
        return baseline_for(self.model.key) if self.model else None

    def target_slot(self) -> int | None:
        return self.apply_to.currentData()

    def table_rows(self) -> list[tuple[str, str, str, bool]]:
        """What the table shows: (key, current, recommended, changes)."""
        return list(getattr(self, "_rows", []))

    def _clear_table(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _cell(self, text: str, name: str, align=Qt.AlignLeft) -> QLabel:
        label = QLabel(text, objectName=name)
        label.setAlignment(align | Qt.AlignVCenter)
        return label

    def _preview(self):
        b, m = self.baseline(), self.model
        self.target_widget.setVisible(b is not None)
        self.table.setVisible(b is not None)
        self.source.setVisible(b is not None)
        if not b:
            self.note.setText("Choose your wheel base to see Fanatec's recommended values." if not m else
                              "No recommended baseline is published for this wheel base yet.")
            self.changed.emit()
            return
        slot = self.target_slot()
        current = (self.current_values(slot) if slot else None) or {}
        if slot and not current:
            self.values_needed.emit(slot)
        self._clear_table()
        head = QWidget(objectName="tableHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        hh = QHBoxLayout(head)
        hh.setContentsMargins(18, 10, 18, 10)
        hh.setSpacing(8)
        for text, width, align in (("SETTING", 0, Qt.AlignLeft), ("CURRENT", 84, Qt.AlignRight), ("", 20, Qt.AlignLeft),
                                   ("RECOMMENDED", 112, Qt.AlignLeft)):
            cell = self._cell(text, "tableHeadCell", align)
            if width:
                cell.setFixedWidth(width)
                hh.addWidget(cell)
            else:
                hh.addWidget(cell, 1)
        self.grid.addWidget(head, 0, 0)
        changes = 0
        self._rows = []
        keys = [k for k in self.params if k in b.values]
        for row, key in enumerate(keys, start=1):
            p = self.params[key]
            new, old = b.values[key], current.get(key)
            differs = old is not None and old != new
            changes += differs
            self._rows.append((key, p.fmt(old) if old is not None else "—", p.fmt(new), differs))
            line = QWidget(objectName="tableRow" if row < len(keys) else "tableRowLast")
            line.setAttribute(Qt.WA_StyledBackground, True)
            lh = QHBoxLayout(line)
            lh.setContentsMargins(18, 6, 18, 6)
            lh.setSpacing(8)
            setting = QHBoxLayout()
            setting.setSpacing(8)
            setting.addWidget(self._cell(p.label, "tableSettingChanged" if differs else "tableSetting"))
            setting.addWidget(QLabel(key, objectName="codeChip"), 0, Qt.AlignVCenter)
            setting.addStretch(1)
            lh.addLayout(setting, 1)
            was = self._cell(p.fmt(old) if old is not None else "—", "tableCurrent", Qt.AlignRight)
            was.setFixedWidth(84)
            arrow = self._cell("→" if differs else "", "tableArrow", Qt.AlignHCenter)
            arrow.setFixedWidth(20)
            rec = self._cell(p.fmt(new), "tableNewChanged" if differs else "tableNew")
            rec.setFixedWidth(112)
            lh.addWidget(was)
            lh.addWidget(arrow)
            lh.addWidget(rec)
            self.grid.addWidget(line, row, 0)
        if not current:
            summary = ""
        elif changes:
            summary = (f"<span style='color:#f2e600;font-weight:600'>{changes} value{'s' if changes != 1 else ''} "
                       f"will change.</span> ")
        else:
            summary = "<span style='color:#3cc46a;font-weight:600'>Already matches the recommended baseline.</span> "
        extra = b.note.replace("{label}", m.label) if b.note and m.key not in b.named else ""
        self.note.setText(f"{summary}BLI and BRF are personal preference and stay as they are."
                          f"{' ' + extra if extra else ''}")
        self.source.setText(f"Source: <a style='color:#2bb8aa' href='{SOURCE}'>{SOURCE_TITLE}</a>")
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
