"""Access to the wheel base through the hid-fanatecff sysfs interface."""

import errno
import os
import socket
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, Signal

from .params import PROFILE_KEYS

NETLINK_KOBJECT_UEVENT = 15  # not exported by the socket module

# Point at a fake tree for development/tests, e.g. FANATEC_PITBOX_SYSFS=/tmp/fake/ftec_tuning
SYSFS_ROOT = Path(os.environ.get("FANATEC_PITBOX_SYSFS", "/sys/class/ftec_tuning"))


# Values the driver has no (correct) sysfs file for, handled through the hidraw TuningLink instead.
# brF: the driver's `brF` file is tuning address 0x10, but the brake force the base applies (and the
# official app sets) is address 0x0f; see references/BRF_log.pcapng.
LINK_KEYS = {"brF": 0x0F}


class WheelBase:
    """One `ftec_tuning` class device, e.g. /sys/class/ftec_tuning/0003:0EB7:0020.0003."""

    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.product = int(path.name.split(":")[2].split(".")[0], 16)
        self.link: "TuningLink | None" = None

    def ready(self) -> bool:
        """False until the driver has received tuning data from the base (e.g. right after boot)."""
        return self._read_file("SLOT") not in (None, 0)

    @property
    def hid_dir(self) -> Path:
        return self.path / "device"

    def keys(self) -> list[str]:
        """Profile keys available for this base."""
        return [k for k in PROFILE_KEYS
                if (self.link is not None if k in LINK_KEYS else (self.path / k).exists())]

    def read(self, key: str) -> int | None:
        if key in LINK_KEYS:
            return self.link.value(LINK_KEYS[key], self._read_file("SLOT")) if self.link else None
        return self._read_file(key)

    def _read_file(self, key: str) -> int | None:
        try:
            return int((self.path / key).read_text().strip(), 0)
        except (OSError, ValueError):
            return None

    def read_all(self) -> dict[str, int]:
        values = {k: self.read(k) for k in self.keys()}
        values["SLOT"] = self.read("SLOT")
        values["advanced_mode"] = self.read("advanced_mode")
        return {k: v for k, v in values.items() if v is not None}

    def write(self, key: str, value: int) -> None:
        """Write a value; raises OSError (EINVAL for out-of-range, EACCES without `games`)."""
        if key in LINK_KEYS:
            if not self.link:
                raise OSError(errno.ENODEV, "no hidraw access to the wheel base")
            self.link.write(LINK_KEYS[key], int(value), self._read_file("SLOT"))
            return
        if key == "advanced_mode":
            # The driver toggles on any write whose value differs from the current mode.
            if self.read(key) == int(value):
                return
        with open(self.path / key, "w") as f:
            f.write(f"{int(value)}\n")

    def reset(self) -> None:
        with open(self.path / "RESET", "w") as f:
            f.write("1\n")

    def writable(self) -> bool:
        return os.access(self.path / "FF", os.W_OK)

    def info(self, attr: str) -> str | None:
        try:
            return (self.hid_dir / attr).read_text().strip()
        except OSError:
            return None


class TuningLink(QObject):
    """The base's tuning reports and commands over the driver's hidraw node.

    The driver forwards reports with id 0xff from hidraw straight to the base and passes every report
    from the base to hidraw while it is open. A tuning report is `ff 03 <slot> <values by address>`;
    a write is `ff 03 00 <slot> <values>` (one byte further), exactly what the official app sends.
    """

    report = Signal()

    def __init__(self, node: Path, parent=None):
        super().__init__(parent)
        self.node = node
        self.latest: bytes | None = None
        self.fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        self._notifier = QSocketNotifier(self.fd, QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._read)

    def _read(self):
        while True:
            try:
                data = os.read(self.fd, 64)
            except BlockingIOError:
                return
            except OSError:
                self.close()
                return
            if len(data) == 64 and data[0] == 0xFF and data[1] == 0x03:
                self.latest = data
                self.report.emit()

    @property
    def slot(self) -> int | None:
        return self.latest[2] & 0x0F if self.latest else None

    def value(self, addr: int, slot: int | None) -> int | None:
        """Value at a tuning address, if the last report is for `slot` (the active setup)."""
        if self.latest is None or (slot is not None and self.slot != slot):
            return None
        return self.latest[addr]

    def write(self, addr: int, value: int, slot: int | None):
        if self.latest is None or self.slot != slot:
            raise OSError(errno.EAGAIN, "no current tuning data from the base yet")
        out = bytearray([0xFF, 0x03, 0x00]) + self.latest[2:63]
        out[3] &= 0x0F
        out[addr + 1] = value
        os.write(self.fd, bytes(out))

    def select(self, slot: int):
        """Make `slot` active; the base answers with a tuning report (unless it already was active)."""
        os.write(self.fd, bytes([0xFF, 0x03, 0x01, slot]) + bytes(60))

    def close(self):
        if self._notifier:
            self._notifier.setEnabled(False)
            self._notifier = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def find_hidraw(base: WheelBase) -> Path | None:
    """The driver's hidraw node for this base (a sibling HID device on the same USB interface)."""
    hid = (base.hid_dir).resolve()
    tag = f"{base.name.split('.')[0]}."
    for entry in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        dev = (entry / "device").resolve()
        if dev.parent == hid.parent and dev.name.upper().startswith(tag.upper()):
            node = Path("/dev") / entry.name
            if node.exists():
                return node
    return None


def open_tuning_link(base: WheelBase, parent=None) -> TuningLink | None:
    node = find_hidraw(base)
    if not node:
        return None
    try:
        return TuningLink(node, parent)
    except OSError:
        return None


def find_wheel_bases() -> list[WheelBase]:
    if not SYSFS_ROOT.is_dir():
        return []
    return [WheelBase(p) for p in sorted(SYSFS_ROOT.iterdir()) if (p / "SLOT").exists()]


class UeventWatcher(QObject):
    """Emits `changed` for kernel uevents of the ftec_tuning class.

    The driver sends a `change` uevent whenever the base reports new tuning data (a value or slot
    changed, from this app or from the wheel's own menu), and `add`/`remove` on (un)plug.
    """

    changed = Signal(str)  # action

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        try:
            sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK,
                                 NETLINK_KOBJECT_UEVENT)
            sock.bind((0, 1))  # group 1: kernel events
        except OSError:
            return  # caller falls back to polling
        self._sock = sock
        self._notifier = QSocketNotifier(sock.fileno(), QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._drain)

    @property
    def active(self) -> bool:
        return self._sock is not None

    def _drain(self):
        while True:
            try:
                data = self._sock.recv(16384)
            except BlockingIOError:
                return
            except OSError:
                return
            fields = dict(f.split("=", 1) for f in data.decode(errors="replace").split("\0") if "=" in f)
            if fields.get("SUBSYSTEM") == "ftec_tuning":
                self.changed.emit(fields.get("ACTION", ""))
