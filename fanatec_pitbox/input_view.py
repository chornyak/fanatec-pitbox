"""Input test tab: live steering, pedals and shifter (design 1a)."""

import time
from importlib import resources

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .inputs import PEDALS, InputReader

YELLOW = QColor("#f2e600")
TEAL = QColor("#2bb8aa")
GROOVE = QColor("#2e3035")
PANEL = QColor("#17181b")
TEXT = QColor("#e8e8e9")
SECONDARY = QColor("#c9cacd")
BODY_MUTED = QColor("#a9acb1")
HELPER = QColor("#8a8d93")
FAINT = QColor("#6c6f75")
CONTROL = QColor("#3a3d42")
LINE = QColor("#45484e")
FLASH_S = 0.25
PEAK_HOLD_S = 1.5
MINUS = "−"


def _font(px: float, weight=QFont.Normal, spacing: float = 0.0):
    f = QFont("Noto Sans")
    f.setPixelSize(round(px)) if px == int(px) else f.setPointSizeF(px * 0.75)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def _signed(deg: float) -> str:
    return f"{MINUS if deg < -0.5 else '+' if deg > 0.5 else ''}{abs(deg):.0f}°"


class WheelGraphic(QWidget):
    """The wheel silhouette turning with the real one, the angle, and where it is within the rotation range."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.position = None  # -1..1 of the rotation range
        self.range_deg = 1080
        self.auto = False
        self.svg = QSvgRenderer(str(resources.files(__package__).joinpath("icons/wheel.svg")))
        self.setMinimumSize(320, 420)
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
        below = 40 + 12 + 16 + 12 + 16  # angle, gap, strip, gap, caption
        size = max(120, min(380, w, h - below - 28))
        top = (h - below - size - 8) / 2 + 20
        cx, cy = w / 2, top + size / 2

        p.save()
        p.translate(cx, cy)
        p.rotate(self.angle or 0.0)
        p.setOpacity(1.0 if self.position is not None else 0.45)
        self.svg.render(p, QRectF(-size / 2, -size / 2, size, size))
        p.restore()

        y = top + size + 8
        p.setPen(TEXT if self.position is not None else FAINT)
        p.setFont(_font(30, QFont.DemiBold, 0.3))
        p.drawText(QRectF(0, y, w, 40), Qt.AlignHCenter | Qt.AlignTop,
                   "—" if self.position is None else _signed(self.angle))
        y += 40 + 12 + 8

        # range strip: groove, teal from the centre to the position, centre tick, white handle
        half = self.range_deg / 2
        strip_w = min(560, w - 40)
        x0 = (w - strip_w) / 2
        label_w, gap = 44, 12
        g0, g1 = x0 + label_w + gap, x0 + strip_w - label_w - gap
        mid = (g0 + g1) / 2
        p.setFont(_font(11.5))
        p.setPen(FAINT)
        p.drawText(QRectF(x0, y - 8, label_w, 16), Qt.AlignRight | Qt.AlignVCenter, f"{MINUS}{half:.0f}°")
        p.drawText(QRectF(g1 + gap, y - 8, label_w, 16), Qt.AlignLeft | Qt.AlignVCenter, f"+{half:.0f}°")
        p.setPen(Qt.NoPen)
        p.setBrush(GROOVE)
        p.drawRoundedRect(QRectF(g0, y - 2, g1 - g0, 4), 2, 2)
        if self.position is not None:
            x = mid + max(-1.0, min(1.0, self.position)) * (g1 - g0) / 2
            p.setBrush(TEAL)
            p.drawRect(QRectF(min(mid, x), y - 2, abs(x - mid), 4))
        p.setBrush(FAINT)
        p.drawRect(QRectF(mid - 0.5, y - 6, 1, 12))
        if self.position is not None:
            p.setBrush(PANEL)
            p.drawEllipse(QPointF(x, y), 10, 10)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(x, y), 7, 7)
        y += 8 + 12
        p.setPen(HELPER)
        p.setFont(_font(12))
        text = f"Sensitivity AUTO · ±{half:.0f}°" if self.auto else f"Sensitivity {self.range_deg:.0f}° · ±{half:.0f}°"
        p.drawText(QRectF(0, y, w, 18), Qt.AlignHCenter | Qt.AlignTop, text)


class PedalBar(QWidget):
    """Value above, a 44×220 bar that fills with pedal travel (white mark holds the recent peak; yellow at 100%),
    name and note below. A pedal that isn't connected is a dashed outline."""

    BAR_W, BAR_H = 44, 220

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.value = None
        self.peak = 0.0
        self.peak_at = 0.0
        self.note = ""
        self.setFixedSize(88, 22 + 10 + self.BAR_H + 10 + 18 + 4 + 16)  # wide enough for 'Not connected'

    def set_value(self, value):
        now = time.monotonic()
        self.value = value
        if value is not None and (value >= self.peak or now - self.peak_at > PEAK_HOLD_S):
            self.peak, self.peak_at = value, now
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        missing = self.value is None
        full = not missing and self.value >= 0.995
        p.setFont(_font(15, QFont.DemiBold))
        p.setPen(FAINT if missing else (YELLOW if full else TEXT))
        p.drawText(QRectF(0, 0, w, 22), Qt.AlignCenter, "—" if missing else f"{self.value * 100:.0f}%")

        bar = QRectF((w - self.BAR_W) / 2, 32, self.BAR_W, self.BAR_H)
        if missing:
            p.setPen(QPen(CONTROL, 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        else:
            clip = QPainterPath()
            clip.addRoundedRect(bar, 5, 5)
            p.setClipPath(clip)
            p.fillRect(bar, GROOVE)
            if self.value > 0:
                fill_h = bar.height() * self.value
                p.fillRect(QRectF(bar.left(), bar.bottom() - fill_h, bar.width(), fill_h), YELLOW if full else TEAL)
            if self.peak > 0.01:
                y = bar.bottom() - bar.height() * min(1.0, self.peak + 0.02)
                p.fillRect(QRectF(bar.left(), y, bar.width(), 2), QColor("#ffffff"))
            p.setClipping(False)

        y = bar.bottom() + 10
        p.setFont(_font(12.5, QFont.Medium))
        p.setPen(FAINT if missing else TEXT)
        p.drawText(QRectF(0, y, w, 18), Qt.AlignHCenter | Qt.AlignTop, self.name)
        note = "Not connected" if missing else self.note
        if note:
            p.setFont(_font(11))
            p.setPen(HELPER)
            p.drawText(QRectF(0, y + 22, w, 16), Qt.AlignHCenter | Qt.AlignTop, note)


class ShiftIndicator(QWidget):
    """Two circles joined by a line, labelled at the ends; a circle fills yellow briefly on each shift."""

    def __init__(self, title_top="Downshift", title_bottom="Upshift", parent=None):
        super().__init__(parent)
        self.titles = {"down": title_top, "up": title_bottom}
        self.flash = {"up": 0.0, "down": 0.0}
        self.counts = {"up": 0, "down": 0}
        self.setFixedSize(96, 18 + 10 + 16 + 10 + 88 + 10 + 16 + 10 + 18)
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
        w, cx = self.width(), self.width() / 2
        now = time.monotonic()
        p.setFont(_font(12.5, QFont.Medium))
        p.setPen(BODY_MUTED)
        p.drawText(QRectF(0, 0, w, 18), Qt.AlignCenter, self.titles["down"])
        y_down = 18 + 10 + 8
        y_up = y_down + 8 + 10 + 88 + 10 + 8
        p.fillRect(QRectF(cx - 1, y_down + 8 + 10, 2, 88), LINE)
        for which, y in (("down", y_down), ("up", y_up)):
            lit = self.flash[which] > now
            p.setPen(QPen(YELLOW if lit else SECONDARY, 2))
            p.setBrush(YELLOW if lit else Qt.NoBrush)
            p.drawEllipse(QPointF(cx, y), 7, 7)
        p.setPen(BODY_MUTED)
        p.drawText(QRectF(0, y_up + 8 + 10, w, 18), Qt.AlignCenter, self.titles["up"])


class HPattern(QWidget):
    """The H-pattern gate (R 1 3 5 7 over 2 4 6, joined by a crossbar); the selected gear lights up yellow."""

    TOP = ("R", "1", "3", "5", "7")
    BOTTOM = ("2", "4", "6")
    PITCH = 36

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gear = None
        self.setFixedSize(162 + 24, 132 + 14 + 16)

    def set_gear(self, gear: str | None):
        if gear != self.gear:
            self.gear = gear
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ox = 12  # room for the outer labels
        col = {g: ox + 9 + i * self.PITCH for i, g in enumerate(self.TOP)}
        col.update({g: ox + 9 + (i + 1) * self.PITCH for i, g in enumerate(self.BOTTOM)})
        top_c, bottom_c, bar_y = 24 + 9, 90 + 9, 66
        # lines stop short of the circles: 5px gap below/above each ring
        for g in self.TOP:
            outer = g in ("R", "7")
            p.fillRect(QRectF(col[g] - 1, 47, 2, 20 if outer else 38), LINE)
        for g in self.BOTTOM:
            p.fillRect(QRectF(col[g] - 1, bar_y, 2, 90 - 5 - bar_y), LINE)
        p.fillRect(QRectF(col["R"] - 1, bar_y - 1, col["7"] - col["R"] + 2, 2), LINE)
        p.setFont(_font(12.5, QFont.DemiBold))
        for g, cy, label_y in ([(g, top_c, 0) for g in self.TOP] + [(g, bottom_c, 116) for g in self.BOTTOM]):
            lit = g == self.gear
            p.setPen(QPen(YELLOW if lit else SECONDARY, 2))
            p.setBrush(YELLOW if lit else Qt.NoBrush)
            p.drawEllipse(QPointF(col[g], cy), 8, 8)
            p.setPen(YELLOW if lit else SECONDARY)
            p.drawText(QRectF(col[g] - 12, label_y, 24, 16), Qt.AlignCenter, g)
        p.setFont(_font(11, QFont.Bold, 1.2))
        p.setPen(HELPER)
        p.drawText(QRectF(0, 132 + 14, self.width(), 16), Qt.AlignCenter, "H-PATTERN")


def _panel(title: str):
    frame = QFrame(objectName="panel")
    v = QVBoxLayout(frame)
    v.setContentsMargins(20, 16, 20, 20)
    v.setSpacing(12)
    v.addWidget(QLabel(title, objectName="groupTitle"))
    return frame, v


class InputPage(QWidget):
    def __init__(self, reader: InputReader, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.range_deg, self.auto, self.brf = 1080, False, None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 20, 32, 32)
        outer.setSpacing(16)

        self.status = QLabel(objectName="dim", wordWrap=True)
        outer.addWidget(self.status)

        grid = QHBoxLayout()
        grid.setSpacing(16)
        outer.addLayout(grid, 1)

        wheel_panel, wl = _panel("STEERING")
        wl.setContentsMargins(20, 16, 20, 24)
        self.wheel = WheelGraphic()
        wl.addWidget(self.wheel, 1)
        grid.addWidget(wheel_panel, 3)

        right = QVBoxLayout()
        right.setSpacing(16)
        grid.addLayout(right, 2)

        pedal_panel, pl = _panel("PEDALS")
        pl.setSpacing(16)
        row = QHBoxLayout()
        row.setSpacing(4)  # 88px columns: the 44px bars end up ~48px apart, as in the design
        row.addStretch(1)
        self.bars = {}
        for name in PEDALS:
            bar = PedalBar(name.capitalize())
            self.bars[name] = bar
            row.addWidget(bar)
        row.addStretch(1)
        pl.addLayout(row)
        right.addWidget(pedal_panel)

        shift_panel, sl = _panel("SHIFTER & PADDLES")
        sl.setSpacing(16)
        srow = QHBoxLayout()
        srow.setContentsMargins(8, 0, 8, 0)
        srow.setSpacing(16)
        self.shifter = ShiftIndicator("Downshift", "Upshift")
        self.hpattern = HPattern()
        self.paddles = ShiftIndicator("Left paddle", "Right paddle")
        srow.addWidget(self.shifter, 0, Qt.AlignVCenter)
        srow.addStretch(1)
        srow.addWidget(self.hpattern, 0, Qt.AlignVCenter)
        srow.addStretch(1)
        srow.addWidget(self.paddles, 0, Qt.AlignVCenter)
        sl.addLayout(srow, 1)
        right.addWidget(shift_panel, 1)

        reader.changed.connect(self.update_view)
        reader.shifted.connect(self._shifted)
        self._peak_timer = QTimer(self, interval=250, timeout=self._refresh_bars)  # lets peak markers expire
        self.update_view()

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
            self.status.setText("Move the wheel, press the pedals and shift to see inputs live. The white marker "
                                "holds each pedal's peak briefly; the brake bar turns yellow at 100%, which helps "
                                "when setting Brake Force.")
        else:
            self.status.setText(r.error or "No wheel base input device found.")
        self.wheel.set_state(r.steering() if r.is_open else None, self.range_deg, self.auto)
        self._refresh_bars()
        self.hpattern.set_gear(r.gear() if r.is_open and r.mode == "h-pattern" else None)
