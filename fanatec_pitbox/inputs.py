"""Live inputs of the wheel base (steering, pedals, shifter) read from its evdev device.

The wheel base's firmware reports accessories plugged into it (pedals, shifter) as part of its own input
device, with a fixed layout. The mapping below was captured on a CSL DD with ClubSport Pedals V3 and a
ClubSport Shifter SQ connected to the base (tools/probe_inputs.py).
"""

import fcntl
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, Signal

EVENT = struct.Struct("llHHi")  # struct input_event (64-bit): timeval, type, code, value
EV_KEY, EV_ABS = 1, 3
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = range(6)
KEY_MAX = 0x2FF


def _eviocgabs(code: int) -> int:
    return 0x80184540 + code


def _eviocgkey(length: int) -> int:
    return 0x80000000 | (length << 16) | (ord("E") << 8) | 0x18


@dataclass(frozen=True)
class InputMap:
    steer: int | None = ABS_X
    clutch: int | None = ABS_Y
    brake: int | None = ABS_RZ
    throttle: int | None = ABS_Z
    handbrake: int | None = None
    pedals_inverted: bool = True  # axis max = released
    seq_up: int = 669
    seq_down: int = 670
    paddle_up: int = 292  # right paddle
    paddle_down: int = 293  # left paddle
    gears: dict = field(default_factory=lambda: {
        301: "1", 302: "2", 303: "3", 657: "4", 658: "5", 659: "6", 660: "7", 300: "R"})


DEFAULT_MAP = InputMap()
PEDALS = ("clutch", "brake", "throttle", "handbrake")


def find_event_device(hid_dir: Path) -> Path | None:
    """The /dev/input/eventN node belonging to a HID device (e.g. the wheel base)."""
    for event in sorted(hid_dir.glob("input/input*/event*")):
        node = Path("/dev/input") / event.name
        if node.exists():
            return node
    return None


class InputReader(QObject):
    """Reads evdev events and keeps the current state; emits signals for the view."""

    changed = Signal()            # axes or held buttons changed
    shifted = Signal(str)         # "seq_up", "seq_down", "paddle_up", "paddle_down"

    def __init__(self, mapping: InputMap = DEFAULT_MAP, parent=None):
        super().__init__(parent)
        self.map = mapping
        self.fd = None
        self.path = None
        self.error = None
        self._notifier = None
        self.axes: dict[int, int] = {}
        self.ranges: dict[int, tuple[int, int]] = {}
        self.held: set[int] = set()
        self.mode = None  # "sequential" | "h-pattern" once a shift input was seen

    # -- device ----------------------------------------------------------------------------
    def open(self, path: Path) -> bool:
        self.close()
        try:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            self.error = f"Cannot read {path}: {e.strerror}"
            return False
        self.path, self.error = path, None
        for code in {c for c in (self.map.steer, *(getattr(self.map, p) for p in PEDALS)) if c is not None}:
            try:
                buf = bytearray(24)
                fcntl.ioctl(self.fd, _eviocgabs(code), buf)
                value, lo, hi = struct.unpack("3i", buf[:12])
                self.axes[code], self.ranges[code] = value, (lo, hi)
            except OSError:
                pass
        try:
            keys = bytearray(KEY_MAX // 8 + 1)
            fcntl.ioctl(self.fd, _eviocgkey(len(keys)), keys)
            self.held = {i for i in range(len(keys) * 8) if keys[i // 8] >> (i % 8) & 1}
        except OSError:
            pass
        if self.held & set(self.map.gears):
            self.mode = "h-pattern"  # already in gear when opened
        self._notifier = QSocketNotifier(self.fd, QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._read)
        self.changed.emit()
        return True

    def close(self):
        if self._notifier:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.path = None

    @property
    def is_open(self) -> bool:
        return self.fd is not None

    def _read(self):
        try:
            data = os.read(self.fd, EVENT.size * 128)
        except BlockingIOError:
            return
        except OSError:  # device unplugged
            self.close()
            self.changed.emit()
            return
        self.feed_bytes(data)

    # -- state -------------------------------------------------------------------------------
    def feed_bytes(self, data: bytes):
        changed = False
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, etype, code, value = EVENT.unpack_from(data, i)
            changed |= self.feed(etype, code, value)
        if changed:
            self.changed.emit()

    def feed(self, etype: int, code: int, value: int) -> bool:
        m = self.map
        if etype == EV_ABS:
            self.axes[code] = value
            return True
        if etype != EV_KEY:
            return False
        if value:
            self.held.add(code)
        else:
            self.held.discard(code)
        if value == 1:
            for name in ("seq_up", "seq_down", "paddle_up", "paddle_down"):
                if code == getattr(m, name):
                    if name.startswith("seq"):
                        self.mode = "sequential"
                    self.shifted.emit(name)
            if code in m.gears:
                self.mode = "h-pattern"
        return code in m.gears or code in (m.seq_up, m.seq_down, m.paddle_up, m.paddle_down)

    def pedal(self, name: str) -> float | None:
        """Pedal travel 0..1, or None if not mapped/available."""
        code = getattr(self.map, name)
        if code is None or code not in self.axes:
            return None
        lo, hi = self.ranges.get(code, (0, 65535))
        frac = (self.axes[code] - lo) / max(1, hi - lo)
        return 1 - frac if self.map.pedals_inverted else frac

    def steering(self) -> float | None:
        """Steering position -1 (full left) .. +1 (full right) of the configured rotation."""
        code = self.map.steer
        if code is None or code not in self.axes:
            return None
        lo, hi = self.ranges.get(code, (0, 65535))
        return (self.axes[code] - lo) / max(1, hi - lo) * 2 - 1

    def gear(self) -> str:
        held = [g for code, g in self.map.gears.items() if code in self.held]
        return held[0] if held else "N"
