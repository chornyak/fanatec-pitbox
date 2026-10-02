"""Fake /sys/class/ftec_tuning tree mirroring a CSL DD, for running without hardware.

FakeBase emulates the 5 setups: call sync() regularly (tests do it while pumping events) and a change
of the SLOT file swaps the value files to that setup's stored values, like the real base does.
"""
from pathlib import Path

from PySide6.QtCore import QObject, Signal

KEYS = "SEN FF FFS NDP NFR NIN INT FEI FOR SPR DPR SHO BLI ACP brF FUL".split()
DEFAULT = dict(SEN=2530, FF=100, FFS=1, NDP=50, NFR=0, NIN=0, INT=11, FEI=100, FOR=100, SPR=100, DPR=100,
               SHO=100, BLI=101, ACP=1, brF=50, FUL=0)
CSL_DD_SLOT2 = dict(DEFAULT, SEN=1080, NDP=15, INT=6, brF=90)


class FakeBase:
    def __init__(self, root: Path, name="0003:0EB7:0020.0003", active=2):
        self.dev = root / name
        (self.dev / "device").mkdir(parents=True, exist_ok=True)
        self.slots = {n: dict(DEFAULT) for n in range(1, 6)}
        self.slots[2] = dict(CSL_DD_SLOT2)
        self.active = active
        self._put(self.slots[active])
        (self.dev / "SLOT").write_text(f"{active}\n")
        (self.dev / "advanced_mode").write_text("1\n")
        (self.dev / "RESET").write_text("")
        (self.dev / "device" / "fw_version").write_text("2305\n")
        (self.dev / "device" / "wheel_id").write_text("0x08\n")

    def _put(self, values):
        for k, v in values.items():
            (self.dev / k).write_text(f"{v}\n")

    def read(self, key):
        return int((self.dev / key).read_text())

    def sync(self):
        if not self.dev.exists():
            return
        slot = self.read("SLOT")
        if slot == 0:
            return  # driver has no data from the base yet (after a reboot)
        self.slots[self.active] = {k: self.read(k) for k in KEYS}
        if slot != self.active:
            self.active = slot
            self._put(self.slots[slot])


class FakeLink(QObject):
    """Stands in for the hidraw TuningLink: the real brake force (address 0x0f) per setup."""

    report = Signal()

    def __init__(self, base: "FakeBase"):
        super().__init__()
        self.base = base
        self.brf = {n: 50 for n in range(1, 6)}
        self.brf[2] = 90
        self.writes = []
        self.seen_report = False  # like the real link: nothing known until the base sends a report

    @property
    def latest(self):
        if not self.seen_report or not self.base.dev.exists() or self.base.read("SLOT") == 0:
            return None
        data = bytearray(64)
        data[0], data[1], data[2] = 0xFF, 0x03, self.base.active
        data[0x0F] = self.brf[self.base.active]
        return bytes(data)

    @property
    def slot(self):
        return self.base.active

    def value(self, addr, slot):
        latest = self.latest
        if latest is None or (slot is not None and slot != self.base.active):
            return None
        return latest[addr]

    def write(self, addr, value, slot):
        assert addr == 0x0F and slot == self.base.active
        self.brf[slot] = value
        self.writes.append((slot, value))
        self.seen_report = True
        self.report.emit()

    def select(self, slot):
        # like the real base: switching makes it report the new setup, which also fills the driver's data
        self.base.active = slot
        self.base._put(self.base.slots[slot])
        (self.base.dev / "SLOT").write_text(f"{slot}\n")
        self.seen_report = True
        self.report.emit()

    def close(self):
        pass
