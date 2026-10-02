"""Runs against a fake sysfs tree and a temporary config dir; never touches real hardware.

    QT_QPA_PLATFORM=offscreen python -m unittest discover tests
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp(prefix="pitbox-test-"))
os.environ["FANATEC_PITBOX_SYSFS"] = str(TMP / "sys")
os.environ["FANATEC_PITBOX_CONFIG"] = str(TMP / "config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

import fakesys  # noqa: E402
from fanatec_pitbox import app as app_mod  # noqa: E402
from fanatec_pitbox import profiles as store  # noqa: E402
from fanatec_pitbox.app import MainWindow  # noqa: E402
from fanatec_pitbox.params import params_for  # noqa: E402

app = QApplication.instance() or QApplication([])
app_mod.SETTLE_MS = 20  # the fake base switches instantly
FAKE = None


def pump(seconds=0.3, until=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if FAKE:
            FAKE.sync()
        app.processEvents()
        if until and until():
            return
        time.sleep(0.003)


class ParamTests(unittest.TestCase):
    def test_sen_auto_depends_on_base(self):
        self.assertEqual(params_for(0x0020)["SEN"].max, 2530)
        self.assertEqual(params_for(0x0020)["SEN"].fmt(2530), "AUTO")
        self.assertEqual(params_for(0x0E03)["SEN"].max, 1090)

    def test_snap_and_format(self):
        p = params_for(0x0020)
        self.assertEqual(p["FOR"].snap(105), 100)
        self.assertEqual(p["FOR"].snap(500), 120)
        self.assertEqual(p["BLI"].fmt(101), "OFF")
        self.assertEqual(p["FFS"].fmt(1), "Peak")


class ProfileStoreTests(unittest.TestCase):
    def test_roundtrip(self):
        p = store.Profile('Mine "v1"', {1: {"FF": 80, "bogus": 3}, 2: {"SEN": 1080}}, {2: "ACC"})
        store.save(p)
        loaded = next(x for x in store.load_all() if x.name == 'Mine "v1"')
        self.assertEqual(loaded.slots, {1: {"FF": 80}, 2: {"SEN": 1080}})
        self.assertEqual(loaded.aliases, {2: "ACC"})
        store.rename(loaded, "Mine")
        self.assertEqual([x.name for x in store.load_all() if x.name.startswith("Mine")], ["Mine"])
        store.delete(store.load_all()[0])

    def test_state_roundtrip(self):
        s = store.State()
        s.aliases[3] = "iRacing"
        s.seen[3] = {"FF": 70}
        s.loaded = "x"
        s.save()
        s2 = store.State()
        self.assertEqual((s2.aliases[3], s2.seen[3], s2.loaded), ("iRacing", {"FF": 70}, "x"))


class WindowTests(unittest.TestCase):
    def setUp(self):
        global FAKE
        shutil.rmtree(TMP / "sys", ignore_errors=True)
        shutil.rmtree(TMP / "config", ignore_errors=True)
        FAKE = fakesys.FakeBase(TMP / "sys")
        self.fake = FAKE
        self.links = []

        def open_link(base, parent=None):
            link = fakesys.FakeLink(FAKE)
            self.links.append(link)
            return link

        self._patch = mock.patch.object(app_mod, "open_tuning_link", open_link)
        self._patch.start()
        self.win = MainWindow()
        self.wait_idle()  # initial read of the base's full settings (setup switch away and back)

    def tearDown(self):
        self._patch.stop()
        self.win.busy = None  # never leave a modal "please wait" dialog behind
        self.win.draft.clear()
        self.win.close()
        self.win.deleteLater()
        pump(0.05)

    def wait_idle(self, timeout=10):
        pump(timeout, until=lambda: self.win.busy is None)
        self.assertIsNone(self.win.busy)

    # -- basics ---------------------------------------------------------------------------
    def test_startup_reads_full_settings_and_returns_to_active_setup(self):
        self.assertEqual(self.fake.read("SLOT"), 2)
        self.assertEqual(self.win.controls["brF"].box.value(), 90)
        self.assertTrue(self.win.controls["brF"].isEnabled())

    def test_detects_base_and_shows_active_setup(self):
        self.assertEqual(self.win.model_lbl.text(), "DD WHEEL BASE")
        self.assertNotIn("DRI", self.win.controls)  # CSL Elite only
        self.assertEqual(self.win.controls["SEN"].box.text(), "1080°")
        self.assertTrue(self.win.slot_cards[2].badge.isVisibleTo(self.win.slot_cards[2]))
        self.assertFalse(self.win.slot_cards[1].badge.isVisibleTo(self.win.slot_cards[1]))
        self.assertIn("SETUP 2", self.win.editing_lbl.text())

    def test_switching_setup_and_alias(self):
        self.win.slot_cards[4].clicked.emit(4)
        pump(0.3)
        self.assertEqual(self.fake.read("SLOT"), 4)
        self.assertEqual(self.win.controls["SEN"].box.text(), "AUTO")
        with mock.patch.object(QInputDialog, "getText", return_value=("Rally", True)):
            self.win.slot_cards[4].rename.emit(4)
        self.assertEqual(self.win.slot_cards[4].title.text(), "Rally")
        self.assertEqual(self.win.slot_cards[4].sub.text(), "SETUP 4")
        self.assertEqual(store.State().aliases, {4: "Rally"})
        self.assertIn("SETUP 4 · Rally", self.win.editing_lbl.text())

    # -- draft / write ----------------------------------------------------------------------
    def test_edits_are_not_written_until_write_clicked(self):
        ctl = self.win.controls["DPR"]
        ctl.slider.setValue(7)  # 70%
        pump(0.3)
        self.assertEqual(self.fake.read("DPR"), 100)
        self.assertTrue(ctl.box.property("dirty"))
        self.assertIn("(1)", self.win.write_btn.text())
        self.win.refresh()
        self.assertEqual(ctl.box.value(), 70)
        self.win.write_btn.click()
        self.wait_idle()
        self.assertEqual(self.fake.read("DPR"), 70)
        self.assertEqual(self.win.draft, {})
        self.assertFalse(ctl.box.property("dirty"))

    def test_revert_and_back_to_base_value(self):
        ctl = self.win.controls["FF"]
        ctl.slider.setValue(50)
        self.win.revert_btn.click()
        self.assertEqual(ctl.box.value(), 100)
        ctl.slider.setValue(30)
        ctl.slider.setValue(100)
        self.assertEqual(self.win.draft, {})

    def test_typed_special_word(self):
        box = self.win.controls["SEN"].box
        box.lineEdit().setText("auto")
        box.interpretText()
        self.assertEqual(self.win.draft, {"SEN": 2530})

    def test_switching_setup_asks_before_discarding_edits(self):
        self.win.controls["FF"].slider.setValue(60)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            self.win.slot_cards[3].clicked.emit(3)
        self.assertEqual(self.fake.read("SLOT"), 2)
        self.assertEqual(self.win.draft, {"FF": 60})
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.win.slot_cards[3].clicked.emit(3)
        pump(0.2)
        self.assertEqual(self.fake.read("SLOT"), 3)
        self.assertEqual(self.win.draft, {})
        self.assertEqual(self.fake.slots[2]["FF"], 100)

    # -- profiles (all 5 setups) ---------------------------------------------------------------
    def save_profile(self, name):
        with mock.patch.object(QInputDialog, "getText", return_value=(name, True)):
            self.win.save_new_btn.click()
        self.wait_idle()

    def test_save_profile_reads_all_setups_and_returns(self):
        self.fake.slots[5]["FF"] = 42
        self.win.state.aliases[2] = "ACC"
        self.save_profile("Mine")
        p = next(x for x in store.load_all() if x.name == "Mine")
        self.assertEqual(sorted(p.slots), [1, 2, 3, 4, 5])
        self.assertEqual(p.slots[2]["SEN"], 1080)
        self.assertEqual(p.slots[5]["FF"], 42)
        self.assertEqual(p.aliases, {2: "ACC"})
        self.assertEqual(self.fake.read("SLOT"), 2)  # back where we started
        self.assertEqual(store.State().loaded, "Mine")
        self.assertIn("Mine", self.win.loaded_lbl.text())
        self.assertNotIn("CHANGED", self.win.loaded_lbl.text())
        self.assertIn("1080°", self.win.slot_cards[2].summary.text())

    def test_load_profile_writes_every_setup_with_auto_backup(self):
        store.save(store.Profile("Other", {n: {"FF": 60 + n, "NDP": n} for n in range(1, 6)},
                                 {1: "ACC", 3: "iRacing"}))
        self.win._reload_profiles(select="Other")
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            self.win.load_btn.click()
        self.wait_idle(20)
        self.fake.sync()
        for n in range(1, 6):
            self.assertEqual((self.fake.slots[n]["FF"], self.fake.slots[n]["NDP"]), (60 + n, n))
        self.assertEqual(self.fake.read("SLOT"), 2)
        self.assertEqual(self.win.state.aliases, {1: "ACC", 3: "iRacing"})
        self.assertEqual(store.State().loaded, "Other")
        backups = [p for p in store.load_all() if p.name.startswith("Auto-backup")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].slots[2]["SEN"], 1080)  # the previous setups were kept
        # a change made later on the wheel marks the loaded profile as changed
        self.fake.slots[2]["FF"] = 99
        self.fake._put({"FF": 99})
        self.win.refresh()
        self.assertIn("CHANGED: SETUP 2", self.win.loaded_lbl.text())

    def test_load_stops_if_setup_switch_is_lost(self):
        store.save(store.Profile("P", {n: {"FF": 10} for n in range(1, 6)}))
        self.win._reload_profiles(select="P")
        base = self.win.base
        real_write = base.write

        def stubborn(key, value):  # the base refuses to switch to setup 4 while loading
            if key == "SLOT" and value == 4 and self.win.busy and "Loading" in self.win.busy:
                return
            real_write(key, value)

        with mock.patch.object(base, "write", side_effect=stubborn), \
                mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            self.win.load_btn.click()
            self.wait_idle(20)
        self.fake.sync()
        self.assertEqual(self.fake.slots[4]["FF"], 100)  # never written
        self.assertNotEqual(store.State().loaded, "P")

    def test_mode_switch_backs_up_all_setups(self):
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Cancel):
            self.win.mode_toggle.click()
        self.assertEqual(self.fake.read("advanced_mode"), 1)
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            self.win.mode_toggle.click()
            self.wait_idle()
        self.assertEqual(self.fake.read("advanced_mode"), 0)
        backups = [p for p in store.load_all() if p.name.startswith("Auto-backup")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(sorted(backups[0].slots), [1, 2, 3, 4, 5])
        self.win.refresh()
        self.assertEqual(self.win.mode_toggle.text(), "Standard")
        self.assertTrue(self.win.slot_cards[3].isHidden())
        self.assertFalse(self.win.save_new_btn.isEnabled())

    def test_standard_mode_mirrors_the_base(self):
        (self.fake.dev / "advanced_mode").write_text("0\n")
        self.win.refresh()
        self.assertEqual(self.win.side_title.text(), "STANDARD SETUP")
        self.assertFalse(self.win.standard_card.isHidden())
        self.assertTrue(all(c.isHidden() for c in self.win.slot_cards.values()))
        shown = {k for k, c in self.win.controls.items() if not c.isHidden()}
        self.assertEqual(shown, {"SEN", "FF", "NDP", "brF"})
        self.assertFalse(self.win.controls["FF"].isEnabled())
        self.assertFalse(self.win.write_btn.isEnabled())
        (self.fake.dev / "advanced_mode").write_text("1\n")
        self.win.refresh()
        self.assertTrue(self.win.standard_card.isHidden())
        self.assertFalse(self.win.controls["FFS"].isHidden())
        self.assertTrue(self.win.controls["FF"].isEnabled())

    def test_settings_shows_device_info(self):
        info = {k: v.text() for k, v in self.win.info_values.items()}
        self.assertEqual(info["Firmware"], "2305")
        self.assertEqual(info["Rim id"], "0x08")
        self.assertIn("0003:0EB7:0020.0003", info["Device"])
        self.assertTrue(info["Kernel"])

    def test_every_setting_has_a_description_shown_on_click(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from fanatec_pitbox.widgets import InfoIcon
        for key, ctl in self.win.controls.items():
            self.assertGreater(len(ctl.param.desc), 60, key)
        QTest.mouseClick(self.win.controls["NDP"].findChild(InfoIcon), Qt.LeftButton)
        pump(0.2)
        tips = [x for x in app.topLevelWidgets() if x.objectName() == "qtooltip_label" and x.isVisible()]
        self.assertTrue(tips and "Natural Damper" in tips[0].text())

    def test_brake_force_goes_through_the_link_not_the_driver_file(self):
        self.assertEqual(self.win.controls["brF"].box.value(), 90)
        self.win.controls["brF"].slider.setValue(3)  # 30%
        self.win.write_btn.click()
        self.wait_idle()
        link = self.links[-1]
        self.assertEqual(link.writes, [(2, 30)])
        self.assertEqual(link.brf[2], 30)
        self.assertEqual(self.fake.read("brF"), 90)  # driver's file (address 0x10) untouched
        self.assertEqual(self.win.controls["brF"].box.value(), 30)

    def test_after_reboot_asks_base_for_its_settings(self):
        global FAKE
        self.win.close()
        self.win.state.last_slot = 4
        self.win.state.save()
        for k in ("SLOT", "FF", "SEN"):
            (self.fake.dev / k).write_text("0\n")  # driver has no data yet
        self.fake.active = 4  # the base is really on setup 4
        self.fake.slots[4]["FF"] = 77
        self.win = MainWindow()  # before any events run: nothing usable is shown yet
        self.assertIn("Waiting for the wheel base", self.win.banner.text())
        self.assertFalse(self.win.controls["FF"].isEnabled())
        self.assertEqual(self.win.busy, "Reading settings from the wheel base")
        self.wait_idle()
        self.win.refresh()
        self.assertEqual(self.fake.read("SLOT"), 4)
        self.assertEqual(self.win.controls["FF"].box.value(), 77)
        self.assertTrue(self.win.controls["FF"].isEnabled())
        self.assertFalse(self.win.banner.isVisibleTo(self.win))

    def test_unplug_and_replug(self):
        global FAKE
        shutil.rmtree(self.fake.dev)
        self.win.refresh()
        self.assertIsNone(self.win.base)
        self.assertTrue(self.win.banner.isVisibleTo(self.win))
        FAKE = self.fake = fakesys.FakeBase(TMP / "sys")
        self.win.refresh()
        self.assertIsNotNone(self.win.base)


if __name__ == "__main__":
    unittest.main()
