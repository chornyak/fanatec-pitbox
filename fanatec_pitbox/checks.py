"""System check: everything the app needs from the system, with the command that fixes each problem.

Used by the first-run wizard and the Settings page. Checks only look; they never change anything.
"""

import fcntl
import grp
import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

from .device import WheelBase, find_hidraw, find_wheel_bases
from .inputs import find_event_device

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
_USB_DEVICES = Path("/sys/bus/usb/devices")


@dataclass
class Check:
    title: str
    status: str
    detail: str
    fix: str = ""  # shell command(s) that fix it, if there is one


def _package_install(arch: str, debian: str, fedora: str | None = None) -> str:
    if shutil.which("pacman"):
        return f"sudo pacman -S {arch}"
    if shutil.which("apt"):
        return f"sudo apt install {debian}"
    if shutil.which("dnf") and fedora:
        return f"sudo dnf install {fedora}"
    return f"install the package that provides it ({arch} on Arch, {debian} on Debian/Ubuntu)"


def _fanatec_usb_present() -> bool:
    for dev in _USB_DEVICES.glob("*"):
        try:
            if (dev / "idVendor").read_text().strip().lower() == "0eb7":
                return True
        except OSError:
            continue
    return False


def _in_group_but_not_active(name: str) -> bool:
    """User is listed in the group, but this login session doesn't have it yet."""
    try:
        g = grp.getgrnam(name)
    except KeyError:
        return False
    user = os.environ.get("USER") or os.getlogin()
    return user in g.gr_mem and g.gr_gid not in os.getgroups()


def _axis_flat(node: Path) -> int | None:
    try:
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        buf = bytearray(24)
        fcntl.ioctl(fd, 0x80184540, buf)  # EVIOCGABS(ABS_X)
        return struct.unpack("6i", buf)[4]
    except OSError:
        return None
    finally:
        os.close(fd)


def run_checks(base: WheelBase | None = None) -> list[Check]:
    checks = []

    # 1. driver
    loaded = Path("/sys/module/hid_fanatec").exists()
    checks.append(Check(
        "hid-fanatecff driver", OK if loaded else FAIL,
        "The driver is loaded." if loaded else
        "The hid-fanatecff driver isn't loaded. Install it (Arch/CachyOS: AUR package hid-fanatecff-dkms), "
        "then load it or reboot.",
        "" if loaded else "sudo modprobe hid_fanatec"))

    # 2. wheel base
    if base is None:
        bases = find_wheel_bases()
        base = bases[0] if bases else None
    if base:
        checks.append(Check("Wheel base", OK, f"Connected ({base.name})."))
    elif _fanatec_usb_present():
        checks.append(Check(
            "Wheel base", FAIL,
            "A Fanatec device is plugged in, but the driver isn't handling it. Set the wheel base to PC mode "
            "(on the CSL DD the LED is red in PC mode)." if loaded else
            "A Fanatec device is plugged in, but the driver isn't loaded (see above)."))
    else:
        checks.append(Check("Wheel base", FAIL, "No Fanatec wheel base found. Make sure it is powered on, "
                                                "connected by USB and in PC mode."))

    if not base:
        for title in ("Permission to change settings", "Brake force access", "Live input access",
                      "Steering and pedal deadzone"):
            checks.append(Check(title, SKIP, "Needs a connected wheel base."))
        return checks

    # 3. write access via the games group
    if base.writable():
        checks.append(Check("Permission to change settings", OK, "You can change the wheel base's settings."))
    elif _in_group_but_not_active("games"):
        checks.append(Check(
            "Permission to change settings", FAIL,
            "You're in the games group, but this login doesn't have it yet. Log out and back in; on KDE Plasma "
            "a reboot may be needed."))
    else:
        checks.append(Check(
            "Permission to change settings", FAIL,
            "The driver gives the games group write access to the tuning settings. Add yourself, then log out "
            "and back in (on KDE Plasma, reboot).", "sudo usermod -aG games $USER"))

    # 4. hidraw (brake force)
    node = find_hidraw(base)
    if node and os.access(node, os.R_OK | os.W_OK):
        checks.append(Check("Brake force access", OK, f"Brake force (BRF) can be read and changed ({node.name})."))
    else:
        checks.append(Check(
            "Brake force access", WARN,
            "Brake force (BRF) needs the driver's hidraw device to be accessible; the driver's udev rule allows "
            "it. Everything else works without it.",
            "sudo udevadm control --reload-rules && sudo udevadm trigger"))

    # 5. input device (Input Test tab)
    event = find_event_device(base.hid_dir)
    if event and os.access(event, os.R_OK):
        checks.append(Check("Live input access", OK, "The Input Test tab can read steering, pedals and shifter."))
    else:
        checks.append(Check(
            "Live input access", WARN,
            "The Input Test tab can't read the wheel base's input device. This is normally allowed for the "
            "logged-in user; logging out and back in usually fixes it."))

    # 6. deadzone removed by the driver's udev rule (needs evdev-joystick)
    flat = _axis_flat(event) if event else None
    tool = shutil.which("evdev-joystick")
    if flat == 0:
        checks.append(Check("Steering and pedal deadzone", OK, "No deadzone on the steering and pedals."))
    else:
        fix = _package_install("joyutils", "joystick")
        checks.append(Check(
            "Steering and pedal deadzone", WARN,
            ("The axes still have a deadzone. evdev-joystick is installed; unplug and replug the wheel base so "
             "the driver's udev rule can remove it." if tool else
             "The axes have a deadzone (about 6% on the steering) because evdev-joystick isn't installed. Games "
             "that read the wheel through SDL can feel a dead spot. Install it, then unplug and replug the wheel "
             "base."), "" if tool else fix))
    return checks


def summary(checks: list[Check]) -> str:
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    if fails:
        return f"{fails} problem{'s' if fails != 1 else ''} to fix" + (f", {warns} warning{'s' if warns != 1 else ''}"
                                                                         if warns else "")
    if warns:
        return f"Ready, with {warns} suggestion{'s' if warns != 1 else ''}"
    return "Everything is set up"
