"""Input reader and INPUT TEST tab, fed with synthetic evdev events (no hardware)."""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QSocketNotifier  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fanatec_pitbox.input_view import InputPage  # noqa: E402
from fanatec_pitbox.inputs import ABS_RZ, ABS_X, ABS_Y, EV_ABS, EV_KEY, EVENT, InputReader  # noqa: E402

app = QApplication.instance() or QApplication([])


def ev(etype, code, value):
    return EVENT.pack(0, 0, etype, code, value)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.r = InputReader()
        self.r.ranges = {ABS_X: (0, 65535), ABS_Y: (0, 65535), ABS_RZ: (0, 65535)}
        self.shifts = []
        self.r.shifted.connect(self.shifts.append)

    def test_steering_and_inverted_pedals(self):
        self.r.feed_bytes(ev(EV_ABS, ABS_X, 38051) + ev(EV_ABS, ABS_RZ, 0) + ev(EV_ABS, ABS_Y, 65535))
        # captured: a quarter turn right read 38051 at SEN 1080 -> about +87 degrees
        self.assertAlmostEqual(self.r.steering() * 1080 / 2, 87, delta=1)
        self.assertEqual(self.r.pedal("brake"), 1.0)
        self.assertEqual(self.r.pedal("clutch"), 0.0)
        self.assertIsNone(self.r.pedal("handbrake"))

    def test_sequential_and_paddles(self):
        self.r.feed_bytes(ev(EV_KEY, 669, 1) + ev(EV_KEY, 669, 0) + ev(EV_KEY, 670, 1) + ev(EV_KEY, 292, 1))
        self.assertEqual(self.shifts, ["seq_up", "seq_down", "paddle_up"])
        self.assertEqual(self.r.mode, "sequential")

    def test_h_pattern_gears(self):
        self.r.feed_bytes(ev(EV_KEY, 657, 1))
        self.assertEqual((self.r.mode, self.r.gear()), ("h-pattern", "4"))
        self.r.feed_bytes(ev(EV_KEY, 657, 0))
        self.assertEqual(self.r.gear(), "N")
        self.r.feed_bytes(ev(EV_KEY, 300, 1))
        self.assertEqual(self.r.gear(), "R")

    def test_reads_from_a_file_descriptor(self):
        rfd, wfd = os.pipe()
        os.set_blocking(rfd, False)
        self.r.fd = rfd
        self.r._notifier = QSocketNotifier(rfd, QSocketNotifier.Read, self.r)
        self.r._notifier.activated.connect(self.r._read)
        os.write(wfd, ev(EV_ABS, ABS_RZ, 32768))
        end = time.monotonic() + 1
        while self.r.pedal("brake") is None and time.monotonic() < end:
            app.processEvents()
        self.assertAlmostEqual(self.r.pedal("brake"), 0.5, delta=0.01)
        self.r.close()
        os.close(wfd)


class PageTests(unittest.TestCase):
    def test_page_reflects_reader(self):
        r = InputReader()
        r.ranges = {ABS_X: (0, 65535), ABS_RZ: (0, 65535)}
        r.fd = -1  # pretend open
        page = InputPage(r)
        page.set_tuning(1080, 2530, 90)
        r.feed_bytes(ev(EV_ABS, ABS_X, 0) + ev(EV_ABS, ABS_RZ, 0) + ev(EV_KEY, 301, 1))
        self.assertAlmostEqual(page.wheel.angle, -540, delta=1)
        self.assertEqual(page.bars["brake"].value, 1.0)
        self.assertEqual(page.bars["brake"].note, "BRF 90%")
        self.assertEqual(page.gear_lbl.text(), "1")
        self.assertEqual(page.mode_lbl.text(), "H-PATTERN")
        page.set_tuning(2530, 2530, 90)  # AUTO -> full 2520 range
        self.assertTrue(page.wheel.auto)
        self.assertAlmostEqual(page.wheel.angle, -1260, delta=1)
        r.fd = None


if __name__ == "__main__":
    unittest.main()
