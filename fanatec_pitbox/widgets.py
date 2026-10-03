"""Controls for a single tuning parameter, styled after the official Fanatec app."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
                               QSpinBox, QToolButton, QToolTip, QVBoxLayout, QWidget)

from .params import ACP_LABELS, Param


def tip_html(html: str) -> str:
    """Rich-text tooltip with a fixed width, so long descriptions wrap into a readable block."""
    return f"<table width='340'><tr><td>{html}</td></tr></table>"


class InfoIcon(QLabel):
    """The small 'i' next to a setting: shows its description on hover, and on click."""

    def __init__(self, html: str, parent=None):
        super().__init__("i", parent)
        self.setObjectName("infoIcon")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(14, 14)
        self.setCursor(Qt.WhatsThisCursor)
        self.setToolTip(tip_html(html))

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        # shown on release: Qt hides tooltips on any button press/release that happens after showing one
        QToolTip.showText(event.globalPosition().toPoint(), self.toolTip(), self)


class ValueBox(QSpinBox):
    """Numeric box that shows AUTO/OFF/units and accepts typed values or those words."""

    def __init__(self, param: Param, parent=None):
        super().__init__(parent)
        self.param = param
        self.setRange(param.min, param.max)
        self.setSingleStep(param.step)
        self.setButtonSymbols(QSpinBox.NoButtons)
        self.setAlignment(Qt.AlignCenter)
        self.setKeyboardTracking(False)
        self.setFixedSize(68, 32)

    def textFromValue(self, value: int) -> str:
        return self.param.fmt(value)

    def valueFromText(self, text: str) -> int:
        text = text.strip().upper()
        for v, label in self.param.specials.items():
            if text == label.upper():
                return v
        digits = "".join(c for c in text if c.isdigit() or c == "-")
        try:
            return self.param.snap(int(digits))
        except ValueError:
            return self.value()

    def validate(self, text, pos):
        stripped = text.strip().upper().rstrip("%°")
        if not stripped:
            return QValidator.Intermediate, text, pos
        if any(label.upper().startswith(stripped) for label in self.param.specials.values()):
            exact = stripped in (l.upper() for l in self.param.specials.values())
            return (QValidator.Acceptable if exact else QValidator.Intermediate), text, pos
        try:
            int(stripped)
        except ValueError:
            return (QValidator.Intermediate if stripped == "-" else QValidator.Invalid), text, pos
        return QValidator.Acceptable, text, pos


def repolish(widget: QWidget, **props):
    """Set dynamic properties and re-apply the stylesheet rules that depend on them."""
    changed = False
    for name, value in props.items():
        if widget.property(name) != value:
            widget.setProperty(name, value)
            changed = True
    if changed:
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class Segmented(QFrame):
    """Two or more mutually exclusive options in one bordered control (Standard | Advanced, Linear | Peak)."""

    clicked = Signal(int)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedHeight(32)  # same height as the buttons next to it
        h = QHBoxLayout(self)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for value, label in options:
            b = QPushButton(label, objectName="segment", checkable=True)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, value)
            self.buttons[value] = b
            h.addWidget(b)
        self.group.idClicked.connect(self.clicked.emit)

    def set_value(self, value):
        b = self.buttons.get(value)
        if b is not None and not b.isChecked():
            b.setChecked(True)

    def value(self):
        return self.group.checkedId()


class ParamControl(QWidget):
    """One setting: label, code chip and (i), an optional "was …" hint, then a slider with a value box, or a
    segmented control for choices."""

    edited = Signal(str, int)

    def __init__(self, param: Param, parent=None):
        super().__init__(parent)
        self.param = param
        self._value = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = self.title = QLabel(param.label)
        title.setObjectName("paramTitle")
        chip = QLabel(param.key, objectName="codeChip")
        tip = f"<b>{param.label}</b><br>{param.desc}"
        if param.key == "ACP":
            tip += "<br>" + "<br>".join(f"<b>{dict(param.choices)[k]}</b>: {v}" for k, v in ACP_LABELS.items())
        self.tip = tip
        info = InfoIcon(tip)
        title.setToolTip(tip_html(tip))
        self.was = QLabel(objectName="wasHint")
        self.was.hide()
        head.addWidget(title)
        head.addWidget(chip, 0, Qt.AlignVCenter)
        head.addWidget(info, 0, Qt.AlignVCenter)
        head.addStretch(1)
        head.addWidget(self.was)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body)
        self.slider = self.box = self.group = self.segmented = None
        if param.choices:
            self.segmented = Segmented(param.choices)
            if param.key == "ACP":
                for value, b in self.segmented.buttons.items():
                    b.setToolTip(ACP_LABELS[value])
            self.group = self.segmented.group
            body.addWidget(self.segmented)
            body.addStretch(1)
            self.segmented.clicked.connect(self._choice_clicked)
        else:
            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, (param.max - param.min) // param.step)
            self.slider.setPageStep(max(1, 10 // param.step))
            self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.box = ValueBox(param)
            body.addWidget(self.slider, 1)
            body.addWidget(self.box)
            self.slider.valueChanged.connect(self._slider_moved)
            self.box.valueChanged.connect(self._box_changed)

    # -- user edits -------------------------------------------------------------------------
    def _slider_moved(self, idx: int):
        value = self.param.min + idx * self.param.step
        self.box.blockSignals(True)
        self.box.setValue(value)
        self.box.blockSignals(False)
        self._emit(value)

    def _box_changed(self, value: int):
        value = self.param.snap(value)
        self.slider.blockSignals(True)
        self.slider.setValue((value - self.param.min) // self.param.step)
        self.slider.blockSignals(False)
        self._emit(value)

    def _choice_clicked(self, value: int):
        self._emit(value)

    def _emit(self, value: int):
        if value != self._value:
            self._value = value
            self.edited.emit(self.param.key, value)

    # -- device updates ---------------------------------------------------------------------
    def interacting(self) -> bool:
        return bool(self.slider and (self.slider.isSliderDown() or self.box.hasFocus()))

    def set_dirty(self, dirty: bool, was=None):
        """Highlight a value that differs from the wheel base and has not been written yet; `was` is the
        base's value, shown as "was …"."""
        for w in filter(None, (self.title, self.box, self.slider)):
            repolish(w, dirty=dirty)
        self.was.setVisible(dirty and was is not None)
        if dirty and was is not None:
            self.was.setText(f"was {self.param.fmt(was)}")
        note = "<br><br><i>Changed — not yet written to the wheel base.</i>" if dirty else ""
        self.title.setToolTip(tip_html(self.tip + note))

    def set_value(self, value: int | None):
        self._value = value
        if value is None:
            return
        if self.segmented:
            self.segmented.set_value(value)
            return
        for w, v in ((self.slider, round((value - self.param.min) / self.param.step)), (self.box, value)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)


class SlotCard(QFrame):
    """Sidebar entry for one of the wheel base's 5 setups: number box, app-side name, summary."""

    clicked = Signal(int)
    rename = Signal(int)

    def __init__(self, slot: int, standard=False, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.standard = standard
        self.setObjectName("slotCard")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 10, 10)
        h.setSpacing(12)
        self.number = QLabel("S" if standard else str(slot), objectName="slotNumber", alignment=Qt.AlignCenter)
        self.number.setFixedSize(26, 26)
        h.addWidget(self.number, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = QLabel(objectName="slotTitle")
        self.summary = QLabel(objectName="slotSummary")
        text.addWidget(self.title)
        text.addWidget(self.summary)
        h.addLayout(text, 1)
        self.edit = QToolButton(objectName="slotEdit", text="✎")
        self.edit.setCursor(Qt.PointingHandCursor)
        self.edit.setToolTip("Name this setup (only shown in this app)")
        self.edit.clicked.connect(lambda: self.rename.emit(self.slot))
        h.addWidget(self.edit, 0, Qt.AlignVCenter)
        if standard:
            self.edit.hide()
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip("Standard mode has a single custom setup.")
        else:
            self.setToolTip("Click to make this the active setup on the wheel base.\nDouble-click or ✎ to name it.")

    def update_card(self, alias: str | None, summary: str, active: bool, enabled: bool):
        self.title.setText("Standard setup" if self.standard else (alias or f"Setup {self.slot}"))
        self.summary.setText(summary)
        self.summary.setVisible(bool(summary))
        self.setEnabled(enabled or active)
        muted = not (enabled or active)
        for w in (self.title, self.summary):
            repolish(w, muted=muted)
        for w in (self, self.number):
            repolish(w, active=active)

    def mouseReleaseEvent(self, event):
        if self.standard:
            return
        if event.button() == Qt.LeftButton and self.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.slot)

    def mouseDoubleClickEvent(self, event):
        if not self.standard:
            self.rename.emit(self.slot)
