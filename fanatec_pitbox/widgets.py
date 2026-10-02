"""Controls for a single tuning parameter, styled after the official Fanatec app."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QRadioButton, QSizePolicy, QSlider,
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
        self.setFixedWidth(64)

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


class ParamControl(QWidget):
    """Label row plus either slider + value box, or radio buttons for choice parameters."""

    edited = Signal(str, int)

    def __init__(self, param: Param, parent=None):
        super().__init__(parent)
        self.param = param
        self._value = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = self.title = QLabel(f"[{param.key.upper()}] {param.label}")
        title.setObjectName("paramTitle")
        tip = f"<b>{param.label}</b><br>{param.desc}"
        if param.key == "ACP":
            tip += "<br>" + "<br>".join(f"<b>{dict(param.choices)[k]}</b>: {v}" for k, v in ACP_LABELS.items())
        self.tip = tip
        info = InfoIcon(tip)
        title.setToolTip(tip_html(tip))
        head.addWidget(title)
        head.addWidget(info)
        head.addStretch(1)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body)
        self.slider = self.box = self.group = None
        if param.choices:
            self.group = QButtonGroup(self)
            for value, label in param.choices:
                rb = QRadioButton(label)
                if param.key == "ACP":
                    rb.setToolTip(ACP_LABELS[value])
                self.group.addButton(rb, value)
                body.addWidget(rb)
                body.addSpacing(24)
            body.addStretch(1)
            self.group.idClicked.connect(self._choice_clicked)
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

    def set_dirty(self, dirty: bool):
        """Highlight a value that differs from the wheel base and has not been written yet."""
        for w in filter(None, (self.title, self.box)):
            if w.property("dirty") != dirty:
                w.setProperty("dirty", dirty)
                w.style().unpolish(w)
                w.style().polish(w)
        note = "<br><br><i>Changed — not yet written to the wheel base.</i>" if dirty else ""
        self.title.setToolTip(tip_html(self.tip + note))

    def set_value(self, value: int | None):
        self._value = value
        if value is None:
            return
        if self.group:
            btn = self.group.button(value)
            if btn:
                btn.setChecked(True)
            return
        for w, v in ((self.slider, round((value - self.param.min) / self.param.step)), (self.box, value)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)


class SlotCard(QFrame):
    """Sidebar entry for one of the wheel base's 5 setups: app-side name, 'SETUP n' subhead, summary."""

    clicked = Signal(int)
    rename = Signal(int)

    def __init__(self, slot: int, standard=False, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.standard = standard
        self.setObjectName("slotCard")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 8, 10)
        h.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(objectName="slotTitle")
        self.sub = QLabel(objectName="slotSub")
        self.summary = QLabel(objectName="slotSummary")
        for w in (self.title, self.sub, self.summary):
            text.addWidget(w)
        h.addLayout(text, 1)
        right = QVBoxLayout()
        right.setSpacing(4)
        self.badge = QLabel("ACTIVE", objectName="slotBadge")
        self.edit = QToolButton(objectName="slotEdit", text="✎")
        self.edit.setCursor(Qt.PointingHandCursor)
        self.edit.setToolTip("Name this setup (only shown in this app)")
        self.edit.clicked.connect(lambda: self.rename.emit(self.slot))
        right.addWidget(self.badge, 0, Qt.AlignRight)
        right.addStretch(1)
        right.addWidget(self.edit, 0, Qt.AlignRight)
        h.addLayout(right)
        if standard:
            self.edit.hide()
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip("Standard mode has a single custom setup.")
        else:
            self.setToolTip("Click to make this the active setup on the wheel base.\nDouble-click or ✎ to name it.")

    def update_card(self, alias: str | None, summary: str, active: bool, enabled: bool):
        if self.standard:
            self.title.setText("Custom setup")
            self.sub.setText("STANDARD MODE")
        else:
            self.title.setText(alias or f"SETUP {self.slot}")
            self.sub.setText(f"SETUP {self.slot}" if alias else "")
        self.sub.setVisible(bool(self.sub.text()))
        self.summary.setText(summary)
        self.summary.setVisible(bool(summary))
        self.badge.setVisible(active)
        self.setEnabled(enabled or active)
        muted = not (enabled or active)
        for w in (self.title, self.sub, self.summary):
            if w.property("muted") != muted:
                w.setProperty("muted", muted)
                w.style().unpolish(w)
                w.style().polish(w)
        if self.property("active") != active:
            for w in (self, self.title):
                w.setProperty("active", active)
                w.style().unpolish(w)
                w.style().polish(w)

    def mouseReleaseEvent(self, event):
        if self.standard:
            return
        if event.button() == Qt.LeftButton and self.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.slot)

    def mouseDoubleClickEvent(self, event):
        if not self.standard:
            self.rename.emit(self.slot)
