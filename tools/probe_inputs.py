#!/usr/bin/env python3
"""Interactive probe: records which axes/buttons of the Fanatec wheel base move for each control.

Read-only (only reads /dev/input). Run in a terminal and follow the prompts:
    python tools/probe_inputs.py
"""
import fcntl
import glob
import os
import select
import struct
import sys
import time

EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit: timeval, type, code, value
EV_KEY, EV_ABS = 1, 3
ABS_NAMES = {0: "X", 1: "Y", 2: "Z", 3: "RX", 4: "RY", 5: "RZ", 6: "THROTTLE", 7: "RUDDER",
             16: "HAT0X", 17: "HAT0Y", 40: "MISC"}

STEPS = [
    ("baseline", "Hands and feet off everything for a couple of seconds"),
    ("steer", "Turn the wheel slowly to the LEFT stop, then to the RIGHT stop, then back to centre"),
    ("steer90", "Turn the wheel exactly a quarter turn (90°) to the RIGHT and hold it there"),
    ("clutch", "Press the CLUTCH pedal fully and release"),
    ("brake", "Press the BRAKE as hard as you would in a full stop, then release"),
    ("throttle", "Press the THROTTLE fully and release"),
    ("handbrake", "Pull the HANDBRAKE fully and release"),
    ("shift_seq", "SHIFTER in sequential mode: upshift 3 times, then downshift 3 times"),
    ("shift_h", "SHIFTER in H-pattern mode: go through each gear in order (1-2-3-4-5-6-7-R), pausing in each"),
    ("paddles", "Pull the RIGHT shift paddle 3 times, then the LEFT paddle 3 times"),
]


def find_device():
    for path in sorted(glob.glob("/dev/input/by-id/*Fanatec*event-joystick")):
        return os.path.realpath(path)
    sys.exit("No Fanatec event device found under /dev/input/by-id")


def abs_info(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, 0x80184540 + code, buf)  # EVIOCGABS(code)
    return struct.unpack("6i", buf)  # value, min, max, fuzz, flat, resolution


def record(fd):
    """Record until the user presses Enter."""
    axes, keys = {}, []
    sys.stdout.out.write("  recording… press Enter when you're done ")
    sys.stdout.out.flush()
    while True:
        ready = select.select([fd, sys.stdin], [], [])[0]
        if sys.stdin in ready:
            sys.stdin.readline()
            break
        data = os.read(fd, EVENT.size * 64)
        for i in range(0, len(data), EVENT.size):
            _, _, etype, code, value = EVENT.unpack_from(data, i)
            if etype == EV_ABS:
                lo, hi, last = axes.get(code, (value, value, value))
                axes[code] = (min(lo, value), max(hi, value), value)
            elif etype == EV_KEY:
                keys.append((code, value))
    return axes, keys


def describe(axes, keys):
    lines = []
    moved = {c: v for c, v in axes.items() if v[1] - v[0] > 0}
    for code, (lo, hi, last) in sorted(moved.items()):
        lines.append(f"  axis {ABS_NAMES.get(code, code):9} min={lo:6} max={hi:6} end={last:6}")
    presses = [c for c, v in keys if v == 1]
    if presses:
        lines.append(f"  buttons pressed (codes, in order): {presses}")
    return lines or ["  nothing moved"]


class Tee:
    """Mirror everything printed to a log file, so results don't have to be copied from the terminal."""

    def __init__(self, path):
        self.file = open(path, "w")
        self.out = sys.stdout

    def write(self, text):
        self.out.write(text)
        if "\r" not in text:  # skip the live countdown
            self.file.write(text)
            self.file.flush()

    def flush(self):
        self.out.flush()


def main():
    logs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe-logs")
    os.makedirs(logs, exist_ok=True)
    log = os.path.join(logs, time.strftime("probe-%Y%m%d-%H%M%S.txt"))
    sys.stdout = Tee(log)
    print(f"Log: {log}")
    dev = find_device()
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    print(f"Device: {dev}\nRanges:")
    for code, name in ABS_NAMES.items():
        val, lo, hi, fuzz, flat, _ = abs_info(fd, code)
        print(f"  {name:9} value={val:6} range={lo}..{hi} fuzz={fuzz} flat={flat}")
    try:
        sen = open(glob.glob("/sys/class/ftec_tuning/*/SEN")[0]).read().strip()
        print(f"SEN (rotation) currently: {sen}")
    except (IndexError, OSError):
        pass
    print("\nFor each step: press Enter, then do the action. Type s + Enter to skip a step you can't do.\n")
    results = []
    for key, text in STEPS:
        prompt = f"[{key}] {text}. Enter to start, s to skip: "
        sys.stdout.out.write(prompt)  # terminal only; the log gets prompt + answer on one line below
        sys.stdout.out.flush()
        answer = input().strip().lower()
        sys.stdout.file.write(prompt + (answer or "<enter>") + "\n")
        if answer == "s":
            print("  skipped")
            results.append((key, ["  skipped"]))
            continue
        while select.select([fd], [], [], 0)[0]:
            os.read(fd, 4096)  # drop events from before the step
        lines = describe(*record(fd))
        print("\n".join(lines))
        results.append((key, lines))
    print("\n===== SUMMARY =====")
    for key, lines in results:
        print(f"[{key}]")
        print("\n".join(lines))
    print(f"\nDone. Results saved to {log}")


if __name__ == "__main__":
    main()
