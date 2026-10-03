"""Command line: switch the active setup, e.g. from Steam launch options, then start the game.

    fanatec-pitbox --setup 3 %command%
    fanatec-pitbox --list

Never blocks a game launch: if switching fails, a warning is printed and the game starts anyway.
"""

import argparse
import os
import sys
import time

from . import profiles as store
from .device import find_hidraw, find_wheel_bases


def _warn(msg: str):
    print(f"fanatec-pitbox: {msg}", file=sys.stderr)


def _resolve(setup: str, state: store.State) -> int | None:
    """Setup number from "3", "SETUP 3" or a name given in the app (case-insensitive)."""
    text = setup.strip().lower().removeprefix("setup").strip()
    if text.isdigit() and 1 <= int(text) <= 5:
        return int(text)
    for n, alias in state.aliases.items():
        if alias.strip().lower() == setup.strip().lower():
            return n
    return None


def switch_setup(slot: int) -> bool:
    bases = find_wheel_bases()
    if not bases:
        _warn("no Fanatec wheel base found; not switching setups")
        return False
    base = bases[0]
    if base.ready():
        if base.read("advanced_mode") == 0:
            _warn("the wheel base is in Standard mode, which has only one setup; not switching")
            return False
        if base.read("SLOT") == slot:
            return True
        try:
            base.write("SLOT", slot)
        except OSError as e:
            _warn(f"could not switch to SETUP {slot}: {e.strerror}")
            return False
        for _ in range(50):
            if base.read("SLOT") == slot:
                return True
            time.sleep(0.02)
        _warn(f"the wheel base did not confirm SETUP {slot}")
        return False
    # After a reboot the driver has no data from the base yet; select the setup directly, as the app does.
    node = find_hidraw(base)
    if not node:
        _warn("the driver has no data from the wheel base yet and its hidraw device isn't available")
        return False
    if store.State().last_advanced is False:
        _warn("the wheel base was last in Standard mode; not switching")
        return False
    try:
        fd = os.open(node, os.O_RDWR)
        try:
            os.write(fd, bytes([0xFF, 0x03, 0x01, slot]) + bytes(60))
        finally:
            os.close(fd)
    except OSError as e:
        _warn(f"could not switch to SETUP {slot}: {e.strerror}")
        return False
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="fanatec-pitbox",
        description="Fanatec Pitbox. Without options the app opens. In Steam launch options: "
                    "fanatec-pitbox --setup 3 %command%")
    parser.add_argument("--setup", metavar="SETUP",
                        help="make this setup active (1-5, or a name you gave it in the app), then run COMMAND")
    parser.add_argument("--list", action="store_true", help="list the setups and which one is active")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="game command to run afterwards (%%command%%)")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    state = store.State()

    if args.list:
        bases = find_wheel_bases()
        active = bases[0].read("SLOT") if bases and bases[0].ready() else None
        for n in store.SLOTS:
            name = state.aliases.get(n, "")
            print(f"{'*' if n == active else ' '} {n}  {name}".rstrip())
        if not bases:
            _warn("no Fanatec wheel base found")

    if args.setup is not None:
        slot = _resolve(args.setup, state)
        if slot is None:
            _warn(f"unknown setup {args.setup!r}; use 1-5 or a setup name from the app")
        elif switch_setup(slot):
            state.last_slot = slot
            state.save()
            print(f"fanatec-pitbox: SETUP {slot}{' · ' + state.aliases[slot] if state.aliases.get(slot) else ''}"
                  " is active", file=sys.stderr)

    if command:
        try:
            os.execvp(command[0], command)  # replaces this process: the game runs as if started directly
        except OSError as e:
            _warn(f"could not start {command[0]}: {e.strerror}")
            return 127
    return 0
