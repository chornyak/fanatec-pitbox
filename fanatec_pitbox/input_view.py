"""INPUT TEST tab: live steering, pedals and shifter, modelled on the official app's 'Input feedback'."""

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .inputs import PEDALS, InputReader

YELLOW = QColor("#f2e600")
TEAL = QColor("#2bb8aa")
TEAL_DARK = QColor("#1d6f69")
TRACK = QColor("#24262b")
TEXT = QColor("#e6e6e6")
DIM = QColor("#8a8d93")
FLASH_S = 0.25
PEAK_HOLD_S = 1.5


def _font(size, bold=False, italic=False):
    f = QFont()
    f.setPointSizeF(size)
    f.setBold(bold)
    f.setItalic(italic)
    return f


class WheelGraphic(QWidget):
    """A steering wheel that rotates with the real one, plus angle readout and range strip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.position = None  # -1..1 of the rotation range
        self.range_deg = 1080
        self.auto = False
        self.setMinimumSize(360, 380)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_state(self, position, range_deg, auto):
        self.position, self.range_deg, self.auto = position, range_deg, auto
        self.update()

    @property
    def angle(self) -> float | None:
        return None if self.position is None else self.position * self.range_deg / 2

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        strip_h = 86
        size = min(w, h - strip_h) * 0.86
        cx, cy = w / 2, (h - strip_h) / 2
        angle = self.angle or 0.0

        p.save()
        p.translate(cx, cy)
        p.rotate(angle)
        r = size / 2
        rim = max(10.0, size * 0.075)
        # spokes and hub
        p.setPen(QPen(QColor("#3a3d42"), rim * 0.9, Qt.SolidLine, Qt.RoundCap))
        for a in (180, 0, 90):  # left, right, bottom
            rad = math.radians(a)
            p.drawLine(QPointF(0, 0), QPointF(math.cos(rad) * (r - rim / 2), math.sin(rad) * (r - rim / 2)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2c31"))
        p.drawEllipse(QPointF(0, 0), r * 0.24, r * 0.24)
        p.setBrush(QColor("#1b1c20"))
        p.drawEllipse(QPointF(0, 0), r * 0.16, r * 0.16)
        # rim
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#2f3237"), rim))
        p.drawEllipse(QPointF(0, 0), r - rim / 2, r - rim / 2)
        p.setPen(QPen(QColor("#45484e"), 2))
        p.drawEllipse(QPointF(0, 0), r - 1, r - 1)
        p.drawEllipse(QPointF(0, 0), r - rim, r - rim)
        # top-centre marker
        p.setPen(Qt.NoPen)
        p.setBrush(YELLOW)
        p.drawRoundedRect(QRectF(-rim * 0.35, -r, rim * 0.7, rim), 2, 2)
        p.restore()

        # angle readout
        p.setPen(TEXT if self.position is not None else DIM)
        p.setFont(_font(20, bold=True))
        text = "—" if self.position is None else f"{angle:+.0f}°"
        p.drawText(QRectF(0, h - strip_h, w, 34), Qt.AlignCenter, text)

        # range strip: where the wheel is within the full rotation range
        half = self.range_deg / 2
        x0, x1 = w * 0.12, w * 0.88
        y = h - 34
        p.setPen(QPen(TRACK, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))
        p.setPen(QPen(DIM, 1))
        p.drawLine(QPointF((x0 + x1) / 2, y - 7), QPointF((x0 + x1) / 2, y + 7))
        if self.position is not None:
            x = (x0 + x1) / 2 + max(-1, min(1, self.position)) * (x1 - x0) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(YELLOW)
            p.drawEllipse(QPointF(x, y), 7, 7)
        p.setPen(DIM)
        p.setFont(_font(8.5))
        label = f"AUTO · ±{half:.0f}°" if self.auto else f"SEN {self.range_deg:.0f}° · ±{half:.0f}°"
        p.drawText(QRectF(x0, y + 8, x1 - x0, 20), Qt.AlignCenter, label)
        p.drawText(QRectF(x0 - 60, y - 9, 54, 18), Qt.AlignRight | Qt.AlignVCenter, f"-{half:.0f}°")
        p.drawText(QRectF(x1 + 6, y - 9, 60, 18), Qt.AlignLeft | Qt.AlignVCenter, f"+{half:.0f}°")


class PedalBar(QWidget):
    """Vertical bar that fills with pedal travel; holds the recent peak; turns yellow at 100%."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.value = None
        self.peak = 0.0
        self.peak_at = 0.0
        self.note = ""
        self.setMinimumSize(58, 250)

    def set_value(self, value):
        now = time.monotonic()
        self.value = value
        if value is not None and (value >= self.peak or now - self.peak_at > PEAK_HOLD_S):
            self.peak, self.peak_at = value, now
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bar_w = 44
        top, bottom = 30, h - (24 if self.note else 6)
        bar = QRectF((w - bar_w) / 2, top, bar_w, bottom - top)
        p.setPen(Qt.NoPen)
        p.setBrush(TRACK)
        p.drawRect(bar)
        full = self.value is not None and self.value >= 0.995
        if self.value is not None and self.value > 0:
            fill = QRectF(bar.left(), bar.bottom() - bar.height() * self.value, bar_w, bar.height() * self.value)
            if full:
                p.setBrush(YELLOW)
            else:
                g = QLinearGradient(0, bar.bottom(), 0, bar.top())
                g.setColorAt(0, TEAL_DARK)
                g.setColorAt(1, TEAL)
                p.setBrush(g)
            p.drawRect(fill)
        if self.value is not None and self.peak > 0.01:
            y = bar.bottom() - bar.height() * self.peak
            p.setPen(QPen(YELLOW if self.peak >= 0.995 else TEXT, 2))
            p.drawLine(QPointF(bar.left() - 4, y), QPointF(bar.right() + 4, y))
        # percentage above
        p.setPen(YELLOW if full else (TEXT if self.value is not None else DIM))
        p.setFont(_font(10.5, bold=True))
        text = "—" if self.value is None else f"{self.value * 100:.0f}%"
        p.drawText(QRectF(0, 0, w, top - 6), Qt.AlignCenter, text)
        # name, rotated, inside the bar at the bottom
        p.save()
        p.translate(bar.center().x(), bar.bottom() - 8)
        p.rotate(-90)
        p.setPen(QColor("#111214") if (self.value or 0) > 0.35 else TEXT)
        p.setFont(_font(9.5, bold=True))
        p.drawText(QRectF(0, -10, bar.height() - 16, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   self.name if self.value is not None else f"{self.name} (not connected)")
        p.restore()
        if self.note:
            p.setPen(DIM)
            p.setFont(_font(8.5))
            p.drawText(QRectF(0, h - 20, w, 18), Qt.AlignCenter, self.note)


class ShiftIndicator(QWidget):
    """Downshift/upshift dots that flash on each shift, as in the official app."""

    def __init__(self, title_up="UPSHIFT", title_down="DOWNSHIFT", parent=None):
        super().__init__(parent)
        self.titles = {"down": title_down, "up": title_up}
        self.flash = {"up": 0.0, "down": 0.0}
        self.counts = {"up": 0, "down": 0}
        self.setMinimumSize(120, 150)
        self._timer = QTimer(self, interval=40, timeout=self._tick)

    def pulse(self, which: str):
        self.flash[which] = time.monotonic() + FLASH_S
        self.counts[which] += 1
        self._timer.start()
        self.update()

    def _tick(self):
        if all(t < time.monotonic() for t in self.flash.values()):
            self._timer.stop()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx = w / 2
        y_down, y_up = 42, h - 42
        p.setPen(QPen(QColor("#5a5d63"), 2))
        p.drawLine(QPointF(cx, y_down + 9), QPointF(cx, y_up - 9))
        now = time.monotonic()
        p.setFont(_font(9.5, bold=True))
        for which, y, ty in (("down", y_down, 4), ("up", y_up, h - 22)):
            lit = self.flash[which] > now
            p.setPen(QPen(YELLOW if lit else TEXT, 2))
            p.setBrush(YELLOW if lit else Qt.NoBrush)
            p.drawEllipse(QPointF(cx, y), 8, 8)
            p.setPen(YELLOW if lit else TEXT)
            p.drawText(QRectF(0, ty, w, 18), Qt.AlignCenter, self.titles[which])


class InputPage(QWidget):
    def __init__(self, reader: InputReader, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.range_deg, self.auto, self.brf = 1080, False, None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 18, 32, 18)
        outer.setSpacing(14)

        self.status = QLabel(objectName="dim", wordWrap=True)
        outer.addWidget(self.status)

        grid = QGridLayout()
        grid.setSpacing(14)
        outer.addLayout(grid, 1)

        wheel_panel, wl = self._panel("STEERING")
        self.wheel = WheelGraphic()
        wl.addWidget(self.wheel, 1)
        grid.addWidget(wheel_panel, 0, 0, 2, 1)

        pedal_panel, pl = self._panel("PEDALS")
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch(1)
        self.bars = {}
        for name in PEDALS:
            bar = PedalBar(name.capitalize())
            self.bars[name] = bar
            row.addWidget(bar)
        row.addStretch(1)
        pl.addLayout(row, 1)
        grid.addWidget(pedal_panel, 0, 1)

        shift_panel, sl = self._panel("SHIFTER")
        srow = QHBoxLayout()
        srow.setSpacing(24)
        self.shifter = ShiftIndicator()
        srow.addWidget(self.shifter, 1)
        gear_box = QVBoxLayout()
        gear_box.setSpacing(2)
        self.mode_lbl = QLabel(objectName="dim", alignment=Qt.AlignCenter)
        self.gear_lbl = QLabel("N", objectName="gear", alignment=Qt.AlignCenter)
        gear_box.addStretch(1)
        gear_box.addWidget(self.gear_lbl)
        gear_box.addWidget(self.mode_lbl)
        gear_box.addStretch(1)
        srow.addLayout(gear_box, 1)
        self.paddles = ShiftIndicator("RIGHT PADDLE", "LEFT PADDLE")
        srow.addWidget(self.paddles, 1)
        sl.addLayout(srow, 1)
        grid.addWidget(shift_panel, 1, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        reader.changed.connect(self.update_view)
        reader.shifted.connect(self._shifted)
        self._peak_timer = QTimer(self, interval=250, timeout=self._refresh_bars)  # lets peak markers expire
        self.update_view()

    def _panel(self, title):
        frame = QFrame(objectName="panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(24, 16, 24, 18)
        v.setSpacing(10)
        v.addWidget(QLabel(title, objectName="panelTitle"))
        return frame, v

    def set_tuning(self, sen: int | None, sen_max: int, brf: int | None):
        """SEN decides how far the drawn wheel turns; BRF is shown under the brake bar."""
        self.auto = sen is not None and sen >= sen_max
        self.range_deg = (sen_max - 10) if self.auto or sen is None else sen  # AUTO = full range
        self.brf = brf
        self.bars["brake"].note = f"BRF {brf}%" if brf is not None else ""
        self.update_view()

    def showEvent(self, event):
        self._peak_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._peak_timer.stop()
        super().hideEvent(event)

    def _refresh_bars(self):
        for name, bar in self.bars.items():
            bar.set_value(self.reader.pedal(name) if self.reader.is_open else None)

    def _shifted(self, which):
        target = self.shifter if which.startswith("seq") else self.paddles
        target.pulse("up" if which.endswith("up") else "down")
        self.update_view()

    def update_view(self):
        r = self.reader
        if r.is_open:
            self.status.setText("Move the wheel, press the pedals and shift to see the inputs live. "
                                "The marker on each bar holds the highest point briefly; the brake bar turns "
                                "yellow at 100%, which is handy for setting BRF.")
        else:
            self.status.setText(r.error or "No wheel base input device found.")
        self.wheel.set_state(r.steering() if r.is_open else None, self.range_deg, self.auto)
        self._refresh_bars()
        mode = {"sequential": "SEQUENTIAL", "h-pattern": "H-PATTERN"}.get(r.mode, "shift to detect mode")
        self.mode_lbl.setText(mode)
        self.gear_lbl.setText(r.gear() if r.mode == "h-pattern" else "–")
