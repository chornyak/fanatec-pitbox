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

from PySide6.QtCore import QEvent, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QLineEdit, QMessageBox  # noqa: E402

import fakesys  # noqa: E402
from fanatec_pitbox import app as app_mod  # noqa: E402
from fanatec_pitbox import profiles as store  # noqa: E402
from fanatec_pitbox.app import MainWindow  # noqa: E402
from fanatec_pitbox.checks import FAIL, OK, WARN, Check  # noqa: E402
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


class WindowBase(unittest.TestCase):
    onboarded = True  # skip the first-run baseline offer unless a test is about it

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
        self.checks = [Check("hid-fanatecff driver", OK, "The driver is loaded."),
                       Check("Steering and pedal deadzone", WARN, "The axes have a deadzone.", "sudo pacman -S joyutils")]
        self._patch_checks = mock.patch.object(app_mod, "run_checks", lambda base=None: self.checks)
        self._patch_checks.start()
        st = store.State()
        st.onboarded = self.onboarded
        st.save()
        self.win = MainWindow()
        self.wait_idle()  # initial read of the base's full settings (setup switch away and back)

    def tearDown(self):
        self._patch.stop()
        self._patch_checks.stop()
        # Shut down every window from this test (some tests replace self.win): stop their timers and really
        # delete them, so nothing keeps polling the next test's fake wheel base.
        for w in app.topLevelWidgets():
            if isinstance(w, MainWindow):
                w.busy = None  # never leave a modal "please wait" dialog behind
                w.draft.clear()
                for timer in w.findChildren(QTimer):
                    timer.stop()
                w.close()
                w.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump(0.05)

    def wait_idle(self, timeout=10):
        pump(timeout, until=lambda: self.win.busy is None)
        self.assertIsNone(self.win.busy)


class WindowTests(WindowBase):
    # -- basics ---------------------------------------------------------------------------
    def test_startup_reads_full_settings_and_returns_to_active_setup(self):
        self.assertEqual(self.fake.read("SLOT"), 2)
        self.assertEqual(self.win.controls["brF"].box.value(), 90)
        self.assertTrue(self.win.controls["brF"].isEnabled())

    def test_detects_base_and_shows_active_setup(self):
        self.assertEqual(self.win.model_lbl.text(), "DD Wheel Base")
        self.assertEqual((self.win.ro_base.text(), self.win.ro_setup.text()), ("Connected", "Setup 2"))
        self.assertNotIn("DRI", self.win.controls)  # CSL Elite only
        self.assertEqual(self.win.controls["SEN"].box.text(), "1080°")
        self.assertTrue(self.win.slot_cards[2].property("active"))  # yellow number box
        self.assertFalse(self.win.slot_cards[1].property("active"))
        self.assertEqual(self.win.editing_lbl.text(), "Setup 2")

    def test_switching_setup_and_alias(self):
        self.win.slot_cards[4].clicked.emit(4)
        pump(0.3)
        self.assertEqual(self.fake.read("SLOT"), 4)
        self.assertEqual(self.win.controls["SEN"].box.text(), "AUTO")
        with mock.patch.object(QInputDialog, "getText", return_value=("Rally", True)):
            self.win.slot_cards[4].rename.emit(4)
        self.assertEqual(self.win.slot_cards[4].title.text(), "Rally")
        self.assertEqual(self.win.slot_cards[4].number.text(), "4")
        self.assertEqual(store.State().aliases, {4: "Rally"})
        self.assertEqual(self.win.editing_lbl.text(), "Rally")

    # -- draft / write ----------------------------------------------------------------------
    def test_edits_are_not_written_until_write_clicked(self):
        ctl = self.win.controls["DPR"]
        ctl.slider.setValue(7)  # 70%
        pump(0.3)
        self.assertEqual(self.fake.read("DPR"), 100)
        self.assertTrue(ctl.box.property("dirty"))
        self.assertEqual(self.win.draft_lbl.text(), "1 unwritten change")
        self.assertFalse(self.win.dirty_bar.isHidden())
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
        self.assertEqual(self.win.ro_profile.text(), "Mine")
        self.assertIn("matches", self.win.ro_profile.toolTip())
        self.assertIn("1080°", self.win.slot_cards[2].summary.text())

    def test_load_profile_writes_every_setup_with_auto_backup(self):
        store.save(store.Profile("Other", {n: {"FF": 60 + n, "NDP": n} for n in range(1, 6)},
                                 {1: "ACC", 3: "iRacing"}))
        self.win._reload_profiles(select="Other")
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            self.win.profile_rows["Other"].load_btn.click()
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
        self.assertIn("Setup 2 differs", self.win.ro_profile.toolTip())

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
            self.win.profile_rows["P"].load_btn.click()
            self.wait_idle(20)
        self.fake.sync()
        self.assertEqual(self.fake.slots[4]["FF"], 100)  # never written
        self.assertNotEqual(store.State().loaded, "P")

    def test_mode_switch_backs_up_all_setups(self):
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Cancel):
            self.win.mode_seg.buttons[0].click()
        self.assertEqual(self.fake.read("advanced_mode"), 1)
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            self.win.mode_seg.buttons[0].click()
            self.wait_idle()
        self.assertEqual(self.fake.read("advanced_mode"), 0)
        backups = [p for p in store.load_all() if p.name.startswith("Auto-backup")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(sorted(backups[0].slots), [1, 2, 3, 4, 5])
        self.win.refresh()
        self.assertEqual(self.win.mode_seg.value(), 0)
        self.assertEqual(self.win.editing_lbl.text(), "Standard setup")
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


class BaselineTests(WindowBase):
    onboarded = False

    def wizard(self):
        pump(2, until=lambda: getattr(self.win, "first_run_dialog", None) is not None
             and self.win.first_run_dialog.isVisible())
        dlg = self.win.first_run_dialog
        self.assertTrue(dlg.isVisible())
        return dlg

    def first_run(self, model="CSL DD with Boost Kit (8 Nm)"):
        """Open the wizard and go to the model step; with a model, choose it and go on to the baseline step."""
        dlg = self.wizard()
        self.assertEqual(len(dlg.steps), 3)
        self.assertEqual(dlg.progress.current_title(), "1 Check your system")
        dlg.next_btn.click()
        self.assertEqual(dlg.progress.current_title(), "2 Your wheel base")
        if model:
            dlg.model_cards.select(model)
            dlg.next_btn.click()
            self.assertEqual(dlg.progress.current_title(), "3 Starting point")
        return dlg

    def test_model_must_be_chosen_because_the_usb_id_is_shared(self):
        dlg = self.first_run(model=None)
        labels = [m.label for _card, m in dlg.model_cards.cards.values()]
        self.assertIsNone(dlg.model_cards.model())  # nothing chosen yet
        self.assertIn("GT DD Pro (5 Nm)", labels)
        self.assertIn("ClubSport DD (12 Nm)", labels)
        self.assertFalse(dlg.next_btn.isEnabled())
        dlg.model_cards.select("ClubSport DD (12 Nm)")
        self.assertTrue(dlg.next_btn.isEnabled())
        dlg.next_btn.click()
        self.assertIn("same values are used for the ClubSport DD (12 Nm)", dlg.picker.note.text())
        self.assertEqual(dlg.next_btn.text(), "Apply and finish")
        dlg.keep_rb.click()
        self.assertEqual(dlg.next_btn.text(), "Finish")
        dlg.next_btn.click()
        pump(0.2)
        self.assertEqual(self.win.model().key, "clubsport-dd")  # remembered when keeping current settings too
        self.assertEqual(self.win.series_lbl.text(), "CLUBSPORT")
        self.assertTrue(self.win.boost_tag.isHidden())

    def test_table_shows_current_and_recommended_with_changes_highlighted(self):
        self.fake.slots[2]["INT"] = 11
        self.fake._put({"INT": 11})
        self.win.refresh()
        dlg = self.first_run()
        rows = {key: (cur, new, changed) for key, cur, new, changed in dlg.picker.table_rows()}
        self.assertEqual(rows["INT"], ("11", "6", True))  # highlighted: current → recommended
        self.assertEqual(rows["NDP"], ("15%", "15%", False))
        self.assertEqual(dlg.picker.apply_to.currentText(), "Setup 2 (active)")
        note = dlg.picker.note.text()
        self.assertIn("1 value will change", note)
        self.assertNotIn("same values are used", note)  # the post names the CSL DD
        dlg.keep_rb.click()
        self.assertTrue(dlg.scroll.isHidden())
        self.assertFalse(dlg.keep_note.isHidden())

    def test_first_run_writes_baseline_to_current_setup(self):
        self.fake.slots[2]["INT"] = 11
        self.fake._put({"INT": 11})
        self.win.refresh()
        dlg = self.first_run()
        self.assertTrue(dlg.recommended_rb.isChecked())
        self.assertEqual(dlg.picker.target_slot(), 2)  # the active setup by default
        dlg.accept()
        self.wait_idle(20)
        self.fake.sync()
        s2 = self.fake.slots[2]
        self.assertEqual((s2["SEN"], s2["FF"], s2["NDP"], s2["INT"], s2["DPR"]), (1080, 100, 15, 6, 100))
        self.assertEqual(self.links[-1].brf[2], 90)  # BRF is personal preference: untouched
        self.assertEqual(self.win.state.aliases[2], "Recommended baseline")
        self.assertTrue(store.State().onboarded)
        self.assertEqual(store.State().models, {"0020": "csl-dd-boost"})
        self.assertFalse(self.win.boost_tag.isHidden())
        self.assertTrue(any(p.name.startswith("Auto-backup") for p in store.load_all()))

    def test_first_run_to_another_setup_keeps_the_active_one(self):
        dlg = self.first_run()
        dlg.picker.apply_to.setCurrentIndex(dlg.picker.apply_to.findData(4))
        self.assertEqual(dlg.picker.target_slot(), 4)
        before2 = dict(self.fake.slots[2])
        dlg.accept()
        self.wait_idle(20)
        self.fake.sync()
        self.assertEqual((self.fake.slots[4]["SEN"], self.fake.slots[4]["NDP"], self.fake.slots[4]["INT"]),
                         (1080, 15, 6))
        self.assertEqual(self.fake.slots[2], before2)
        self.assertEqual(self.fake.read("SLOT"), 2)
        self.assertEqual(self.win.state.aliases, {4: "Recommended baseline"})

    def test_keep_current_settings_changes_nothing_and_is_not_asked_again(self):
        dlg = self.first_run()
        before = {n: dict(v) for n, v in self.fake.slots.items()}
        dlg.keep_rb.click()
        dlg.accept()
        pump(0.5)
        self.assertEqual(self.fake.slots, before)
        self.assertTrue(store.State().onboarded)
        self.assertIsNone(self.win.busy)

    def test_closing_the_dialog_without_a_model(self):
        self.first_run(model=None).reject()
        pump(0.3)
        self.assertTrue(store.State().onboarded)
        self.assertIn("choose your model in Settings", self.win.series_lbl.text())

    def open_baseline(self):
        self.win.baseline_action.trigger()
        pump(0.2)
        dlg = self.win.baseline_dialog
        self.assertTrue(dlg.isVisible())
        return dlg

    def test_baseline_window_asks_for_the_model_when_unknown(self):
        self.first_run(model=None).reject()
        pump(0.2)
        self.assertTrue(self.win.baseline_action.isEnabled())
        dlg = self.open_baseline()
        self.assertFalse(dlg.apply_btn.isEnabled())  # no model chosen yet
        dlg.model_cards.select("CSL DD (5 Nm)")
        self.assertTrue(dlg.apply_btn.isEnabled())
        self.fake.slots[2]["NDP"] = 40
        self.fake._put({"NDP": 40})
        self.win.refresh()
        dlg.picker.refresh()
        self.assertIn("1 value will change", dlg.picker.note.text())
        dlg.accept()
        self.wait_idle(20)
        self.fake.sync()
        self.assertEqual(self.fake.slots[2]["NDP"], 15)
        self.assertEqual(self.win.model().key, "csl-dd")  # remembered

    def test_baseline_window_cancel_changes_nothing(self):
        self.first_run().accept()  # finish the wizard (applies to SETUP 2)
        self.wait_idle(20)
        before = {n: dict(v) for n, v in self.fake.slots.items()}
        dlg = self.open_baseline()
        self.assertIsNone(dlg.model_cards)  # model known: named in the subtitle instead
        self.assertIn("CSL DD with Boost Kit (8 Nm)", dlg.subtitle_lbl.text())
        self.assertIn("Already matches the recommended baseline", dlg.picker.note.text())
        dlg.reject()
        pump(0.3)
        self.fake.sync()
        self.assertEqual(self.fake.slots, before)

    def test_unknown_setups_are_read_for_the_table(self):
        dlg = self.first_run()
        dlg.keep_rb.click()
        dlg.accept()
        pump(0.3)
        self.assertNotIn(5, self.win.state.seen)  # never read so far
        picker = self.open_baseline().picker
        picker.apply_to.setCurrentIndex(picker.apply_to.findData(5))
        self.wait_idle(10)
        pump(0.2)
        self.assertEqual(sorted(self.win.state.seen), [1, 2, 3, 4, 5])
        self.assertEqual(self.fake.read("SLOT"), 2)  # back on the active setup
        self.assertIn("will change", picker.note.text())
        self.assertIn(("NDP", "50%", "15%", True), picker.table_rows())

    def test_unknown_setups_are_not_read_with_unwritten_changes(self):
        self.first_run().reject()
        pump(0.3)
        self.win.controls["FF"].slider.setValue(60)
        picker = self.open_baseline().picker
        picker.apply_to.setCurrentIndex(picker.apply_to.findData(5))
        pump(0.5)
        self.assertIsNone(self.win.busy)
        self.assertNotIn(5, self.win.state.seen)
        self.assertEqual(self.win.draft, {"FF": 60})

    def test_single_model_ids_need_no_choice(self):
        from fanatec_pitbox.params import models_for
        from fanatec_pitbox.wizard import FirstRunDialog
        dlg = FirstRunDialog(lambda: self.checks, models_for(0x0006), None, params_for(0x0006), lambda slot: None,
                             {1: "SETUP 1"}, 1, True)
        self.assertEqual([t for t, _ in dlg.steps], ["Check your system", "Starting point"])
        self.assertEqual(dlg.chosen_model().key, "podium-dd1")
        dlg.next_btn.click()
        self.assertTrue(dlg.use_baseline)
        dlg.deleteLater()

    def test_check_step_lists_checks_with_fix_commands(self):
        dlg = self.wizard()
        self.assertIn("1 suggestion", dlg.check_summary.text())
        cmds = [w.text() for w in dlg.check_list.findChildren(QLineEdit) if w.objectName() == "fixCommand"]
        self.assertEqual(cmds, ["sudo pacman -S joyutils"])
        self.checks = [Check("hid-fanatecff driver", OK, "The driver is loaded.")]
        dlg.recheck()
        self.assertEqual(dlg.check_summary.text(), "Everything is set up")
        dlg.reject()

    def test_without_a_wheel_base_only_the_check_is_shown_and_it_comes_back(self):
        global FAKE
        self.win.close()
        shutil.rmtree(self.fake.dev)
        FAKE = None
        self.checks = [Check("Wheel base", FAIL, "No Fanatec wheel base found.")]
        self.win = MainWindow()
        dlg = self.wizard()
        self.assertEqual(len(dlg.steps), 1)
        self.assertEqual(dlg.next_btn.text(), "Finish")
        self.assertIn("1 problem to fix", dlg.check_summary.text())
        dlg.next_btn.click()
        pump(0.2)
        self.assertFalse(store.State().onboarded)  # shown again on the next start


class SettingsPanelTests(WindowBase):
    def test_system_check_dot_button_and_popup(self):
        self.win.tab_group.button(2).click()
        dot, btn = self.win.check_dot, self.win.check_btn
        self.assertEqual(dot.property("state"), "warn")  # a suggestion: Check offered, not primary
        self.assertTrue(btn.isEnabled())
        self.assertFalse(btn.property("primary"))
        self.checks = [Check("hid-fanatecff driver", FAIL, "Not loaded.", "sudo modprobe hid_fanatec")]
        btn.click()
        dlg = self.win.system_check_dialog
        self.assertTrue(dlg.isVisible())
        self.assertIn("1 problem to fix", dlg.summary.text())
        cmds = [w.text() for w in dlg.check_list.findChildren(QLineEdit) if w.objectName() == "fixCommand"]
        self.assertEqual(cmds, ["sudo modprobe hid_fanatec"])
        dlg.accept()
        pump(0.1)
        self.assertEqual(dot.property("state"), "fail")  # refreshed on close; Check becomes the primary
        self.assertTrue(btn.property("primary"))
        self.checks = [Check("hid-fanatecff driver", OK, "The driver is loaded.")]
        self.win._recheck_system()
        self.assertEqual(dot.property("state"), "ok")
        self.assertFalse(btn.isEnabled())  # all green: nothing to check

    def test_steam_launch_line_for_the_chosen_setup(self):
        self.win.state.aliases[3] = "iRacing"
        self.win._update_view()
        combo, cmd = self.win.steam_setup, self.win.steam_cmd
        self.assertEqual(combo.currentData(), 2)  # starts on the active setup
        self.assertTrue(cmd.text().endswith("fanatec-pitbox --setup 2 %command%"), cmd.text())
        combo.setCurrentIndex(combo.findData(3))
        self.assertEqual(combo.currentText(), "Setup 3 · iRacing")
        self.assertTrue(cmd.text().endswith("fanatec-pitbox --setup 3 %command%"), cmd.text())
        self.win.steam_copy.click()
        self.assertEqual(QApplication.clipboard().text(), cmd.text())
        self.win._update_view()
        self.assertEqual(combo.currentData(), 3)  # the user's choice is kept

    def test_model_change_dialog(self):
        self.assertEqual(self.win.model_value.text(), "Not chosen")
        self.win.model_change_btn.click()
        dlg = self.win.model_dialog
        self.assertFalse(dlg.save_btn.isEnabled())
        dlg.cards.cards["gt-dd-pro-boost"][0].radio.setChecked(True)
        dlg.accept()
        pump(0.1)
        self.assertEqual(self.win.model_value.text(), "GT DD Pro with Boost Kit (8 Nm)")
        self.assertEqual(self.win.model_lbl.text(), "DD Pro Wheel Base")

    def test_profiles_panel(self):
        with mock.patch.object(QInputDialog, "getText", return_value=("Mine", True)):
            self.win.save_new_btn.click()
        self.wait_idle()
        store.save(store.Profile("League", {n: {"FF": 90} for n in range(1, 6)}, {4: "LMU"}, saved="2026-09-18 19:42"))
        self.win._reload_profiles()
        rows = self.win.profile_rows
        self.assertEqual(list(rows), ["Mine", "League"])  # loaded profile first
        self.assertFalse(rows["Mine"].pill.isHidden())
        self.assertTrue(rows["League"].pill.isHidden())
        self.assertEqual(rows["League"].date.text(), "18 Sep 2026, 19:42")
        self.assertTrue(rows["League"].load_box.isHidden())  # collapsed until selected
        rows["League"].selected.emit("League")
        self.assertFalse(rows["League"].load_box.isHidden())
        self.assertEqual(rows["League"].details.text(), "Setup 1 · Setup 2 · Setup 3 · LMU · Setup 5")
        rows["Mine"].selected.emit("Mine")
        self.assertTrue(rows["Mine"].load_box.isHidden())  # the loaded profile has nothing to load
        self.assertEqual(self.win.save_new_btn.text(), "+")  # save as new: the + next to the PC side's helper
        self.assertTrue(self.win.differs_box.isHidden())
        self.fake.slots[2]["FF"] = 55
        self.fake._put({"FF": 55})
        self.win.refresh()
        self.assertFalse(self.win.differs_box.isHidden())
        self.assertEqual(self.win.differs_lbl.text(), "Setup 2 differs from “Mine”")
        # ⋯ menu: duplicate, rename, delete
        with mock.patch.object(QInputDialog, "getText", return_value=("League 2", True)):
            rows["League"].action.emit("League", "duplicate")
        self.assertIn("League 2", self.win.profile_rows)
        with mock.patch.object(QInputDialog, "getText", return_value=("Mine renamed", True)):
            self.win.profile_rows["Mine"].action.emit("Mine", "rename")
        self.assertEqual(store.State().loaded, "Mine renamed")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.win.profile_rows["League 2"].action.emit("League 2", "delete")
        self.assertNotIn("League 2", self.win.profile_rows)

    def test_automatic_backups_are_folded_away(self):
        for i in range(2):
            store.save(store.Profile(f"Auto-backup 2026-10-0{i + 1} 10.00.00", {1: {"FF": 80}}))
        self.win._reload_profiles()
        self.assertEqual(self.win.backups_header.count.text(), "2")
        self.assertFalse(any(n.startswith("Auto-backup") for n in self.win.profile_rows))
        self.win.backups_header.mouseReleaseEvent(type("E", (), {"button": lambda self: Qt.LeftButton})())
        self.assertEqual(sum(n.startswith("Auto-backup") for n in self.win.profile_rows), 2)


if __name__ == "__main__":
    unittest.main()
