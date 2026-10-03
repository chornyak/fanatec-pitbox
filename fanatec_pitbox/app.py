"""Main window: the wheel base's 5 setups (sidebar), the tuning panel for the active setup, and
profiles (snapshots of all 5 setups) for backup and restore."""

import errno
import os
import shlex
import shutil
import time
from pathlib import Path
from importlib import resources

from PySide6.QtCore import QDir, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QLineEdit, QMenu, QToolButton, QButtonGroup, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import __version__
from . import profiles as store
from .device import UeventWatcher, WheelBase, find_wheel_bases, open_tuning_link
from .params import GROUPS, STANDARD_KEYS, find_model, models_for, params_for
from .input_view import InputPage
from .inputs import InputReader, find_event_device
from .baseline_ui import ModelCombo
from .checks import run_checks, summary as checks_summary
from .settings_ui import BaseSetupRow, Disclosure, ProfileRow, meta, rule, section, setting_row, status_dot
from .wizard import BaselineDialog, FirstRunDialog, ModelDialog, SystemCheckDialog
from .baselines import available as baselines_available
from .widgets import ParamControl, Segmented, SlotCard, repolish

WRITE_GAP_MS = 30        # gap between individual value writes
SETTLE_MS = 300          # wait after a setup switch so the driver holds the new setup's values
POLL_MS = 1500           # fallback refresh / hot-plug detection
AUTO_BACKUP_PREFIX = "Auto-backup "
AUTO_BACKUPS_KEPT = 5


def _rule() -> QFrame:
    line = QFrame()
    line.setObjectName("rule")
    line.setFrameShape(QFrame.HLine)
    return line


def _button(text: str, accent=False, danger=False) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if accent:
        b.setProperty("accent", True)
    if danger:
        b.setProperty("danger", True)
    return b


class OperationFailed(Exception):
    pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fanatec Pitbox")
        self.setWindowIcon(app_icon())
        self.resize(1360, 880)

        self.base: WheelBase | None = None
        self.params = {}
        self.controls: dict[str, ParamControl] = {}
        self.live: dict[str, int] = {}
        self.draft: dict[str, int] = {}  # edits not yet written to the base
        self.busy: str | None = None     # label of a running multi-step operation
        self._asked_base = False         # asked the base to report its settings (once per connection)
        self._first_run_pending = False
        self._started = time.monotonic()
        self._steam_setup_chosen = False
        self._op = None
        self.state = store.State()
        self.profiles: list[store.Profile] = []

        self._build_ui()

        self.op_timer = QTimer(self, singleShot=True, timeout=self._op_step)
        self.refresh_timer = QTimer(self, singleShot=True, interval=40, timeout=self.refresh)
        self.poll_timer = QTimer(self, interval=POLL_MS, timeout=self.refresh)
        self.poll_timer.start()
        self.watcher = UeventWatcher(self)
        self.watcher.changed.connect(lambda _action: self.refresh_timer.start())

        self._reload_profiles()
        self.refresh()

    # ===================================================================================== UI
    def _build_ui(self):
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # full-width header: base name and badge, three readouts, tabs (1a)
        header = QWidget(objectName="appHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        hv = QVBoxLayout(header)
        hv.setContentsMargins(32, 20, 32, 0)
        hv.setSpacing(16)
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(2)
        self.series_lbl = QLabel(objectName="overline")
        self.model_lbl = QLabel("No wheel base", objectName="baseTitle")
        self.boost_tag = QLabel("BOOST KIT", objectName="boostTag")
        self.boost_tag.hide()
        model_row = QHBoxLayout()
        model_row.setSpacing(12)
        model_row.addWidget(self.model_lbl)
        model_row.addWidget(self.boost_tag, 0, Qt.AlignVCenter)
        model_row.addStretch(1)
        names.addWidget(self.series_lbl)
        names.addLayout(model_row)
        top.addLayout(names)
        top.addStretch(1)
        readouts = QHBoxLayout()
        readouts.setSpacing(28)
        self.base_dot = QLabel(objectName="statusDot")
        self.base_dot.setFixedSize(7, 7)
        self.ro_base, self.ro_setup, self.ro_profile = (QLabel(objectName="readoutValue") for _ in range(3))
        for label, value, dot in (("WHEEL BASE", self.ro_base, self.base_dot), ("ACTIVE SETUP", self.ro_setup, None),
                                  ("PROFILE", self.ro_profile, None)):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(QLabel(label, objectName="readoutLabel"), 0, Qt.AlignRight)
            row = QHBoxLayout()
            row.setSpacing(7)
            row.addStretch(1)
            if dot is not None:
                row.addWidget(dot, 0, Qt.AlignVCenter)
            row.addWidget(value)
            col.addLayout(row)
            col.addStretch(1)  # label and value stay together at the top, level with the overline
            readouts.addLayout(col)
        top.addLayout(readouts)
        hv.addLayout(top)
        tabs = QHBoxLayout()
        tabs.setSpacing(28)
        self.tab_group = QButtonGroup(self)
        for i, text in enumerate(("Tuning", "Input test", "Settings")):
            tab = QPushButton(text, objectName="tab", checkable=True)
            tab.setCursor(Qt.PointingHandCursor)
            self.tab_group.addButton(tab, i)
            tabs.addWidget(tab)
        self.tab_group.button(0).setChecked(True)
        tabs.addStretch(1)
        hv.addLayout(tabs)
        v.addWidget(header)

        banner_box = QWidget()
        bl = QVBoxLayout(banner_box)
        bl.setContentsMargins(32, 14, 32, 0)
        self.banner = QLabel(objectName="banner", wordWrap=True)
        self.banner.setTextFormat(Qt.RichText)
        bl.addWidget(self.banner)
        self.banner_box = banner_box
        banner_box.hide()
        v.addWidget(banner_box)

        self.pages = QStackedWidget()
        self.reader = InputReader(parent=self)
        self.input_page = InputPage(self.reader)
        self.pages.addWidget(self._build_tuning_page())
        self.pages.addWidget(self.input_page)
        self.pages.addWidget(self._build_settings_page())
        self.tab_group.idClicked.connect(self._show_tab)
        v.addWidget(self.pages, 1)

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)  # transient messages only, and only shown while there is one
        sb.messageChanged.connect(lambda msg: sb.setVisible(bool(msg)))
        sb.hide()

    def _show_tab(self, index: int):
        self.pages.setCurrentIndex(index)
        if index == 2:
            self._recheck_system()
        self._sync_input_reader()

    def _sync_input_reader(self):
        """Read the wheel's input device only while the INPUT TEST tab is shown."""
        want = self.pages.currentWidget() is self.input_page and self.base is not None
        if want and not self.reader.is_open:
            node = find_event_device(self.base.hid_dir)
            if node:
                self.reader.open(node)
            else:
                self.reader.error = "No input device found for the wheel base."
            self.input_page.update_view()
        elif not want and self.reader.is_open:
            self.reader.close()

    def _build_sidebar(self):
        side = QWidget(objectName="sidebar")
        side.setFixedWidth(272)
        v = QVBoxLayout(side)
        v.setContentsMargins(32, 30, 16, 16)  # left edge lines up with the header
        v.setSpacing(6)
        self.side_title = QLabel(objectName="sectionLabel")
        self.side_text = QLabel(objectName="dim", wordWrap=True)
        v.addWidget(self.side_title)
        v.addWidget(self.side_text)
        v.addSpacing(14)
        self.slot_cards: dict[int, SlotCard] = {}
        for n in store.SLOTS:
            card = SlotCard(n)
            card.clicked.connect(self._select_slot)
            card.rename.connect(self._rename_slot)
            self.slot_cards[n] = card
            v.addWidget(card)
        self.standard_card = SlotCard(0, standard=True)
        v.addWidget(self.standard_card)
        v.addStretch(1)
        return side

    def _build_tuning_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        body.addWidget(self._build_sidebar())
        content = QWidget()
        body.addWidget(content, 1)
        v = QVBoxLayout(content)
        v.setContentsMargins(16, 26, 24, 0)
        v.setSpacing(18)

        # heading: which setup is being edited; tuning mode and setup tools on the right
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 8, 0)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(QLabel("EDITING", objectName="overline"))
        self.editing_lbl = QLabel(objectName="editingName")
        heading.addWidget(self.editing_lbl)
        head.addLayout(heading)
        head.addStretch(1)
        head.addWidget(QLabel("Tuning mode", objectName="toolbarLabel"))
        head.addSpacing(10)
        self.mode_seg = Segmented(((0, "Standard"), (1, "Advanced")))
        self.mode_seg.setToolTip("Standard / Advanced tuning menu mode of the wheel base.\n"
                                 "Switching modes can overwrite setups, so all setups are backed up first.")
        self.mode_seg.clicked.connect(lambda value: self._change_mode(bool(value)))
        head.addWidget(self.mode_seg)
        head.addSpacing(20)
        self.tools_btn = QToolButton(objectName="menuButton", text="Setup tools   ▾")
        self.tools_btn.setCursor(Qt.PointingHandCursor)
        self.tools_btn.setPopupMode(QToolButton.InstantPopup)
        self.tools_btn.setFixedHeight(32)  # level with the Standard | Advanced control
        tools = QMenu(self.tools_btn)
        self.baseline_action = tools.addAction("Recommended baseline…", self._open_baseline)
        self.reset_action = tools.addAction("Reset to factory defaults…", self._reset)
        self.tools_btn.setMenu(tools)
        head.addWidget(self.tools_btn)
        v.addLayout(head)

        # settings in panels grouped by meaning, two columns
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="scrollHolder")
        cols = QHBoxLayout(holder)
        cols.setContentsMargins(0, 0, 8, 24)
        cols.setSpacing(16)
        self.group_panels = {}
        for column in GROUPS:
            col = QVBoxLayout()
            col.setSpacing(16)
            for key, title in column:
                frame = QFrame(objectName="panel")
                lay = QVBoxLayout(frame)
                lay.setContentsMargins(20, 20, 20, 22)
                lay.setSpacing(16)
                lay.addWidget(QLabel(title, objectName="groupTitle"))
                self.group_panels[key] = (frame, lay)
                col.addWidget(frame)
            col.addStretch(1)
            cols.addLayout(col, 1)
        scroll.setWidget(holder)
        v.addWidget(scroll, 1)

        # unwritten changes: only shown while there are any
        self.dirty_bar = QFrame(objectName="dirtyBar")
        bar = QHBoxLayout(self.dirty_bar)
        bar.setContentsMargins(32, 12, 32, 12)
        bar.setSpacing(10)
        dot = QLabel(objectName="dirtyDot")
        dot.setFixedSize(7, 7)
        bar.addWidget(dot)
        self.draft_lbl = QLabel(objectName="draftNote")
        bar.addWidget(self.draft_lbl)
        bar.addStretch(1)
        self.revert_btn = _button("Revert")
        self.revert_btn.setToolTip("Discard changes that have not been written to the wheel base.")
        self.revert_btn.clicked.connect(self._revert_draft)
        bar.addWidget(self.revert_btn)
        self.write_btn = _button("Write to wheel base")
        self.write_btn.setProperty("primary", True)
        self.write_btn.setToolTip("Send the highlighted changes to the active setup on the wheel base.")
        self.write_btn.clicked.connect(self._write_draft)
        bar.addWidget(self.write_btn)
        self.dirty_bar.hide()
        outer.addWidget(self.dirty_bar)
        return page

    def _panel(self, title):
        frame = QFrame(objectName="panel")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(24, 16, 24, 20)
        lay.setSpacing(14)
        if title:
            lay.addWidget(QLabel(title, objectName="panelTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(56)
        grid.setVerticalSpacing(18)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        return frame, grid

    def _build_settings_page(self):
        """One page, four sections in a centred column: Wheel base, Profiles, Launch from Steam, About."""
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="scrollHolder")
        centre = QHBoxLayout(holder)
        centre.setContentsMargins(32, 32, 32, 48)
        column = QWidget()
        column.setMaximumWidth(880)
        centre.addStretch(1)
        centre.addWidget(column, 100)
        centre.addStretch(1)
        v = QVBoxLayout(column)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(32)
        v.addWidget(section("WHEEL BASE", self._build_base_panel()))
        v.addWidget(section("PROFILES", self._build_profiles_panel()))
        v.addWidget(section("LAUNCH FROM STEAM", self._build_steam_panel()))
        v.addWidget(section("ABOUT", self._build_about_panel()))
        v.addStretch(1)
        scroll.setWidget(holder)
        return scroll

    def _build_base_panel(self):
        frame = QFrame(objectName="panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        right = QWidget()
        rh = QHBoxLayout(right)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(16)
        self.model_value = QLabel(objectName="modelValue")
        self.model_change_btn = _button("Change…")
        self.model_change_btn.clicked.connect(self._change_model)
        rh.addWidget(self.model_value)
        rh.addWidget(self.model_change_btn)
        v.addWidget(setting_row("Model", "Several bases share one USB id. Your choice sets the header and the "
                                         "recommended baseline.", right))
        v.addWidget(rule())
        self.check_dot = status_dot()
        self.check_btn = _button("Check")
        self.check_btn.clicked.connect(self._open_system_check)
        v.addWidget(setting_row("System check", "Driver, wheel base, permissions and axis deadzone, with the "
                                                "command to fix anything missing.", self.check_btn, self.check_dot))
        v.addWidget(rule())
        footer = QWidget()
        fh = QHBoxLayout(footer)
        fh.setContentsMargins(20, 14, 20, 14)
        fh.setSpacing(16)
        self.info_values = {}
        for name in ("Firmware", "Rim id", "Device"):
            value = QLabel(objectName="metaMono", textInteractionFlags=Qt.TextSelectableByMouse)
            self.info_values[name] = value
            fh.addWidget(meta(name, value), 1)
        v.addWidget(footer)
        return frame

    def _recheck_system(self):
        """Status dot and Check button in Settings → Wheel base: Check is only offered when something is off,
        and becomes the primary action when something fails."""
        checks = run_checks(self.base)
        fail = any(c.status == "fail" for c in checks)
        warn = any(c.status == "warn" for c in checks)
        repolish(self.check_dot, state="fail" if fail else "warn" if warn else "ok")
        self.check_dot.setToolTip(checks_summary(checks))
        self.check_btn.setEnabled(fail or warn)
        repolish(self.check_btn, primary=fail)

    def _open_system_check(self):
        dlg = SystemCheckDialog(lambda: run_checks(self.base), self)
        dlg.finished.connect(lambda _r: (self._recheck_system(), dlg.deleteLater()))
        self.system_check_dialog = dlg
        dlg.open()

    def _change_model(self):
        if not self.base:
            return
        dlg = ModelDialog(models_for(self.base.product), self.model(), self)

        def finished(result):
            if result == QDialog.Accepted:
                self._set_model(dlg.chosen_model())
            dlg.deleteLater()

        dlg.finished.connect(finished)
        self.model_dialog = dlg
        dlg.open()

    def _build_profiles_panel(self):
        frame = QFrame(objectName="panel")
        split = QHBoxLayout(frame)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        # left: the setups on the wheel base
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 16, 0, 18)
        lv.setSpacing(0)
        head = QVBoxLayout()
        head.setContentsMargins(20, 0, 20, 12)
        head.setSpacing(4)
        head.addWidget(QLabel("ON THE WHEEL BASE", objectName="yellowLabel"))
        head.addWidget(QLabel("The current setups loaded on the wheel base.", objectName="bodyMuted", wordWrap=True))
        lv.addLayout(head)
        rows = QVBoxLayout()
        rows.setContentsMargins(12, 0, 12, 0)
        rows.setSpacing(0)
        self.base_setup_rows = {}
        for n in store.SLOTS:
            row = BaseSetupRow(n)
            self.base_setup_rows[n] = row
            rows.addWidget(row)
        lv.addLayout(rows)
        lv.addStretch(1)
        self.differs_box = QWidget()
        dv = QVBoxLayout(self.differs_box)
        dv.setContentsMargins(20, 12, 20, 0)
        dv.setSpacing(12)
        dv.addWidget(rule())
        dh = QHBoxLayout()
        dh.setSpacing(8)
        dot = QLabel(objectName="dirtyDot")
        dot.setFixedSize(6, 6)
        self.differs_lbl = QLabel(objectName="differsNote")
        dh.addWidget(dot)
        dh.addWidget(self.differs_lbl, 1)
        dv.addLayout(dh)
        lv.addWidget(self.differs_box)
        split.addWidget(left, 100)

        divider = QFrame(objectName="vrule")
        divider.setFixedWidth(1)
        split.addWidget(divider)

        # right: profiles on this PC
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 16, 0, 0)
        rv.setSpacing(0)
        head = QVBoxLayout()
        head.setContentsMargins(20, 0, 20, 12)
        head.setSpacing(4)
        head.addWidget(QLabel("ON THIS PC", objectName="yellowLabel"))
        helper_row = QHBoxLayout()
        helper_row.setSpacing(16)
        helper_row.addWidget(QLabel("Saved copies of all 5 setups. Loading one replaces the base's setups, after an "
                                    "automatic backup.", objectName="bodyMuted", wordWrap=True), 1)
        self.save_new_btn = QPushButton("+", objectName="addButton")
        self.save_new_btn.setFixedSize(30, 30)
        self.save_new_btn.setCursor(Qt.PointingHandCursor)
        self.save_new_btn.setToolTip("Save the 5 setups on the wheel base as a new profile")
        self.save_new_btn.clicked.connect(self._save_new_profile)
        helper_row.addWidget(self.save_new_btn, 0, Qt.AlignVCenter)
        head.addLayout(helper_row)
        rv.addLayout(head)
        self.profile_rows_box = QVBoxLayout()
        self.profile_rows_box.setContentsMargins(12, 0, 12, 0)
        self.profile_rows_box.setSpacing(2)
        rv.addLayout(self.profile_rows_box)
        self.no_profiles = QLabel("No profiles yet. Save the setups on the wheel base to keep a copy here.",
                                  objectName="dim", wordWrap=True)
        self.no_profiles.setContentsMargins(20, 4, 20, 4)
        rv.addWidget(self.no_profiles)
        rv.addStretch(1)
        self.backups_box = QWidget()
        backups = QVBoxLayout(self.backups_box)
        backups.setContentsMargins(20, 12, 20, 18)
        backups.setSpacing(12)
        backups.addWidget(rule())
        self.backups_header = Disclosure("Automatic backups", f"Last {AUTO_BACKUPS_KEPT} kept")
        self.backups_header.toggled.connect(lambda _on: self._reload_profiles())
        backups.addWidget(self.backups_header)
        rv.addWidget(self.backups_box)
        self.backup_rows_box = QVBoxLayout()
        self.backup_rows_box.setContentsMargins(12, 0, 12, 12)
        self.backup_rows_box.setSpacing(2)
        rv.addLayout(self.backup_rows_box)
        split.addWidget(right, 125)
        self.profile_rows = {}
        self.selected_profile = None
        return frame

    def _build_steam_panel(self):
        frame = QFrame(objectName="panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        text = QLabel("Switch the base to a setup when a game starts. In Steam open the game's "
                      "<span style='color:#e8e8e9'>Properties → General → Launch Options</span> and paste the "
                      "line. If the base isn't connected, the game still starts.",
                      objectName="bodyMuted", wordWrap=True)
        text.setTextFormat(Qt.RichText)
        text.setContentsMargins(20, 14, 20, 14)
        v.addWidget(text)
        v.addWidget(rule())
        launcher = Path(__file__).resolve().parent.parent / "fanatec-pitbox"
        self.steam_exe = str(launcher) if launcher.exists() else (shutil.which("fanatec-pitbox") or "fanatec-pitbox")
        row = QHBoxLayout()
        row.setContentsMargins(20, 14, 20, 14)
        row.setSpacing(12)
        self.steam_setup = QComboBox()
        self.steam_setup.setFixedWidth(200)
        for n in store.SLOTS:
            self.steam_setup.addItem(f"Setup {n}", n)
        self.steam_cmd = QLineEdit(objectName="codeField", readOnly=True)
        self.steam_copy = _button("Copy")
        self.steam_copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.steam_cmd.text()),
                                                 self.steam_copy.setText("Copied")))
        self.steam_setup.currentIndexChanged.connect(lambda _i: self._update_steam_line())
        row.addWidget(self.steam_setup)
        row.addWidget(self.steam_cmd, 1)
        row.addWidget(self.steam_copy)
        v.addLayout(row)
        self._update_steam_line()
        return frame

    def _update_steam_line(self):
        n = self.steam_setup.currentData() or 1
        self.steam_cmd.setText(f"{shlex.quote(self.steam_exe)} --setup {n} %command%")
        self.steam_cmd.setCursorPosition(0)
        self.steam_copy.setText("Copy")

    def _build_about_panel(self):
        frame = QFrame(objectName="panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(20, 14, 20, 14)
        v.setSpacing(12)
        grid = QHBoxLayout()
        grid.setSpacing(16)
        for name in ("App", "Driver", "Kernel", "Updates"):
            value = QLabel(objectName="metaText", textInteractionFlags=Qt.TextSelectableByMouse)
            self.info_values[name] = value
            grid.addWidget(meta(name, value), 1)
        v.addLayout(grid)
        v.addWidget(rule())
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(QLabel("Config & profiles", objectName="metaLabel"))
        self.info_values["Config"] = QLabel(objectName="metaMono", textInteractionFlags=Qt.TextSelectableByMouse)
        row.addWidget(self.info_values["Config"])
        row.addStretch(1)
        folder = _button("Open folder")
        folder.setObjectName("small")
        folder.clicked.connect(self._open_folder)
        row.addWidget(folder)
        v.addLayout(row)
        return frame

    def model(self):
        """The user's wheel base model (asked once when several models share the USB id)."""
        if not self.base:
            return None
        return find_model(self.base.product, self.state.models.get(f"{self.base.product:04x}"))

    def _set_model(self, model):
        if not self.base or model is None:
            return
        self.state.models[f"{self.base.product:04x}"] = model.key
        self.state.save()
        self._update_view()

    def _build_controls(self):
        """(Re)create parameter controls for the parameters this base exposes, in their group panels."""
        for c in self.controls.values():
            c.setParent(None)
            c.deleteLater()
        self.controls.clear()
        if not self.base:
            return
        available = set(self.base.keys())
        for key, p in self.params.items():
            if key not in available:
                continue
            ctl = ParamControl(p)
            ctl.edited.connect(self._user_edit)
            self.controls[key] = ctl
            self.group_panels[p.group][1].addWidget(ctl)

    # =============================================================================== device
    def refresh(self):
        bases = find_wheel_bases()
        base = bases[0] if bases else None
        if (base and base.path) != (self.base and self.base.path):
            self._set_base(base)
        if not self.base:
            self._update_view()
            return
        if not self.base.ready():
            self.live = {}
            self._ask_base_for_settings()
            self._update_view()
            return
        live = self.base.read_all()
        if not live:  # unplugged between discovery and read
            self._set_base(None)
            return
        self.live = live
        slot = live.get("SLOT")
        if self.base.link and self.base.link.latest is None:
            self._ask_base_for_settings()  # the link needs one full report from the base (for BRF)
        if slot and (self.state.last_slot, self.state.last_advanced) != (slot, self._advanced()):
            self.state.last_slot, self.state.last_advanced = slot, self._advanced()
            self.state.save()
        if self._advanced() and slot and self.state.seen.get(slot) != self._tuning_values():
            self.state.seen[slot] = self._tuning_values()
            self.state.save()
        for key in [k for k, v in self.draft.items() if live.get(k) == v]:
            del self.draft[key]  # the base already has this value (e.g. changed on the wheel)
        for key, ctl in self.controls.items():
            if not ctl.interacting():
                ctl.set_value(self.draft.get(key, live.get(key)))
            ctl.set_dirty(key in self.draft, live.get(key))
        dlg = getattr(self, "baseline_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.picker.refresh()
        if "SEN" in self.params:
            self.input_page.set_tuning(live.get("SEN"), self.params["SEN"].max, live.get("brF"))
        self._update_view()

    def _set_base(self, base):
        self.reader.close()
        if self.base and self.base.link:
            self.base.link.close()
        if base:
            base.link = open_tuning_link(base, self)
            if base.link:
                base.link.report.connect(lambda: self.refresh_timer.start())
        self._asked_base = False
        self.base = base
        self.draft.clear()
        self.live = {}
        self.params = params_for(base.product) if base else {}
        self._build_controls()
        if base:
            self.refresh()
        self._sync_input_reader()

    def _ask_base_for_settings(self):
        """Get one full tuning report from the base: after a reboot the driver has none, and the hidraw
        link needs one for BRF. The base only reports changes, so briefly select another setup and then
        return to the active one (values are untouched). Only done when the base is (or was last seen)
        in Advanced mode; switching setups in Standard mode has caused trouble before."""
        link = self.base.link if self.base else None
        if not link or self._asked_base or self.busy:
            return
        ready = self.base.ready()
        if (self._advanced() if ready else self.state.last_advanced) is False:
            return
        self._asked_base = True
        target = self.live.get("SLOT") if ready else (self.state.last_slot or 1)
        other = 2 if target == 1 else 1

        def op():
            for slot in (other, target):
                before = link.latest
                link.select(slot)
                for _ in range(75):
                    if link.latest is not before:
                        break
                    yield 20
            yield 200

        self._run("Reading settings from the wheel base", op())

    def _advanced(self) -> bool:
        return bool(self.base) and self.live.get("advanced_mode", 1) == 1

    def _tuning_values(self) -> dict[str, int]:
        return {k: v for k, v in self.live.items() if k not in ("SLOT", "advanced_mode")}

    def _slot_title(self, n: int) -> str:
        alias = self.state.aliases.get(n)
        return f"SETUP {n} · {alias}" if alias else f"SETUP {n}"

    def _update_view(self):
        """Sync every piece of UI that depends on device/app state."""
        b = self.base
        ready = bool(b) and bool(self.live)
        adv = self._advanced()
        idle = self.busy is None
        slot = self.live.get("SLOT")

        if b:
            m = self.model()
            if m:
                self.series_lbl.setText(m.series)
                self.model_lbl.setText(m.name)
            else:
                names = models_for(b.product)
                self.series_lbl.setText(" / ".join(dict.fromkeys(x.series for x in names)) +
                                        "  ·  choose your model in Settings")
                self.model_lbl.setText(names[0].name)
            self.boost_tag.setVisible(bool(m and m.boost))
            info = {"Device": b.name, "Firmware": b.info("fw_version") or "?", "Rim id": b.info("wheel_id") or "?"}
        else:
            self.series_lbl.setText("")
            self.model_lbl.setText("No wheel base")
            self.boost_tag.hide()
            info = {"Device": "—", "Firmware": "—", "Rim id": "—"}
        home, config = str(Path.home()), str(store.CONFIG_DIR)
        info |= {"Updates": "Live (kernel events)" if self.watcher.active else "Polling every 1.5 s",
                 "Driver": f"hid-fanatecff {driver_version()}", "Kernel": os.uname().release,
                 "App": f"fanatec-pitbox {__version__}",
                 "Config": "~" + config[len(home):] if config.startswith(home + "/") else config}
        for k, text in info.items():
            self.info_values[k].setText(text)

        if b and not b.ready():
            self._banner("Waiting for the wheel base to report its settings…"
                         if b.link and self.state.last_advanced else
                         "The wheel base hasn't reported its settings to the driver yet. Turn the wheel base "
                         "off and on again.")
        elif not b:
            self._banner("No Fanatec wheel base found. Make sure it is powered on, in <b>PC mode</b> "
                         "and that the hid-fanatecff driver is loaded (<code>lsmod | grep hid_fanatec</code>).")
        elif not b.writable():
            self._banner("Tuning values are read-only for this user. Add yourself to the <b>games</b> group "
                         "(<code>sudo usermod -aG games $USER</code>) and reboot.")
        else:
            self._banner(None)

        # sidebar: mirrors the base's own tuning menu (5 setups in Advanced, 1 in Standard)
        standard = ready and not adv
        if standard:
            self.side_title.setText("STANDARD SETUP")
            self.side_text.setText(
                "The wheel base is in Standard mode: one setup with the core settings only (SEN, FF, NDP, "
                "BRF), as on the wheel's own tuning menu. Switch to Advanced for the 5 setups and all settings.")
        else:
            self.side_title.setText("SETUPS")
            self.side_text.setText("Stored on the wheel base. Click to make one active; double-click to rename.")
        for n, card in self.slot_cards.items():
            seen = self.state.seen.get(n)
            card.setVisible(not standard)
            card.update_card(self.state.aliases.get(n), self._summary(seen), active=(n == slot and adv),
                             enabled=ready and adv and idle)
        self.standard_card.setVisible(standard)
        if standard:
            self.standard_card.update_card(None, self._summary(self.live), active=True, enabled=True)

        # tuning page
        self.mode_seg.set_value(1 if adv else 0)
        self.mode_seg.setEnabled(ready and idle)
        self.tools_btn.setEnabled(ready and idle)
        self.baseline_action.setEnabled(adv and b is not None and baselines_available(models_for(b.product)))
        for key, ctl in self.controls.items():
            ctl.setVisible(not standard or key in STANDARD_KEYS)
            ctl.setEnabled(ready and not standard and (key in self.live or key in self.draft))
        for key, (frame, _lay) in self.group_panels.items():
            frame.setVisible(any(not c.isHidden() for k, c in self.controls.items() if self.params[k].group == key))
        if standard:
            self.editing_lbl.setText("Standard setup")
            self.editing_lbl.setToolTip("Read-only in this app: change these on the wheel.")
        else:
            self.editing_lbl.setText((self.state.aliases.get(slot) or f"Setup {slot}") if ready and slot else "—")
            self.editing_lbl.setToolTip(f"SETUP {slot}" if slot else "")
        n = len(self.draft)
        self.dirty_bar.setVisible(bool(n))
        self.write_btn.setEnabled(bool(n) and adv and idle)
        self.revert_btn.setEnabled(bool(n) and idle)
        self.draft_lbl.setText(f"{n} unwritten change{'s' if n != 1 else ''}" if n else "")

        for i, n in enumerate(store.SLOTS):  # setup names in the Steam drop-down
            alias = self.state.aliases.get(n)
            self.steam_setup.setItemText(i, f"Setup {n} · {alias}" if alias else f"Setup {n}")
        if slot and not self._steam_setup_chosen:
            self._steam_setup_chosen = True  # start on the active setup, then leave the choice to the user
            self.steam_setup.setCurrentIndex(slot - 1)

        # first start: show the welcome wizard once the base has reported (or straight away without one;
        # its system check is most useful exactly when something is missing)
        if (not self.state.onboarded and not self._first_run_pending and idle
                and (ready or not b or time.monotonic() - self._started > 4)):
            self._first_run_pending = True
            QTimer.singleShot(300, self._show_first_run)

        # settings: wheel base model, setups on the base, profiles
        m = self.model() if b else None
        models = models_for(b.product) if b else ()
        self.model_value.setText(m.label if m else ("Not chosen" if len(models) > 1 else "—"))
        repolish(self.model_value, muted=m is None)
        self.model_change_btn.setVisible(len(models) > 1)
        for n, row in self.base_setup_rows.items():
            row.update_row(self.state.aliases.get(n) or f"Setup {n}", self._summary(self.state.seen.get(n)),
                           active=(n == slot and adv))
        prof = self._profile(self.state.loaded)
        changed = prof.differs(self.state.seen) if prof else []
        self.differs_box.setVisible(bool(changed))
        if changed:
            setups = "Setup " + changed[0].__str__() if len(changed) == 1 else "Setups " + ", ".join(map(str, changed))
            self.differs_lbl.setText(f"{setups} {'differs' if len(changed) == 1 else 'differ'} from “{prof.name}”")
        can_save = ready and adv and idle
        self.save_new_btn.setEnabled(can_save)
        for name, row in self.profile_rows.items():
            p = self._profile(name)
            if p:
                row.update_row(p.saved, " · ".join(p.aliases.get(n) or f"Setup {n}" for n in sorted(p.slots)),
                               loaded=name == self.state.loaded, selected=name == self.selected_profile,
                               can_load=can_save)

        # header readouts
        state, text = ("ok", "Connected") if ready else ("warn", "Waiting…") if b else ("fail", "Not found")
        if self.base_dot.property("state") != state:
            self.base_dot.setProperty("state", state)
            self.base_dot.style().unpolish(self.base_dot)
            self.base_dot.style().polish(self.base_dot)
        self.ro_base.setText(text)
        if ready and not adv:
            self.ro_setup.setText("Standard")
        elif slot:
            self.ro_setup.setText(self.state.aliases.get(slot) or f"Setup {slot}")
        else:
            self.ro_setup.setText("—")
        prof = self._profile(self.state.loaded)
        self.ro_profile.setText(prof.name if prof else "None")
        changed = prof.differs(self.state.seen) if prof else []
        self.ro_profile.setToolTip("" if not prof else
                                   f"Setup {', '.join(map(str, changed))} differs from “{prof.name}”" if changed else
                                   f"The wheel base matches “{prof.name}”")

    def _summary(self, values) -> str:
        if not values or "SEN" not in self.params:
            return ""
        return f"{self.params['SEN'].fmt(values.get('SEN'))} · FF {values.get('FF', '?')}%"

    def _banner(self, html):
        self.banner_box.setVisible(bool(html))
        if html:
            self.banner.setText(html)

    def _write(self, key: str, value: int) -> bool:
        if not self.base:
            return False
        try:
            self.base.write(key, value)
        except OSError as e:
            reason = {errno.EACCES: "permission denied (are you in the games group?)",
                      errno.EINVAL: "rejected by the driver (out of range, or no data from the base yet)",
                      errno.ENOENT: "wheel base disconnected"}.get(e.errno, e.strerror)
            self.statusBar().showMessage(f"Could not set {key} = {value}: {reason}", 8000)
            self.refresh_timer.start()
            return False
        self.live[key] = value
        self.refresh_timer.start(250)  # pick up the device's echo even without uevents
        return True

    # ============================================================ multi-step operations
    # Operations are generators that yield a delay in ms between steps, so the UI stays responsive.
    def _run(self, label: str, gen, on_done=None):
        self.busy = label
        self._op = (gen, on_done)
        self.statusBar().showMessage(f"{label}…")
        self._update_view()
        self.op_timer.start(0)

    def _op_step(self):
        gen, on_done = self._op
        try:
            delay = next(gen)
        except StopIteration as stop:
            self._finish_op(on_done, stop.value, None)
            return
        except (OperationFailed, OSError) as e:
            self._finish_op(on_done, None, str(e))
            return
        self.op_timer.start(delay)

    def _finish_op(self, on_done, result, error):
        self.busy = None
        self._op = None
        self.statusBar().clearMessage()  # the "… in progress" message; on_done may show its own
        self.refresh()
        if error:
            self.statusBar().showMessage(f"Stopped: {error}", 15000)
            QMessageBox.warning(self, "Operation stopped", error)
        elif on_done:
            on_done(result)

    def _g_select(self, slot: int):
        """Make `slot` the active setup and wait until the driver holds its values."""
        if self.base.read("SLOT") != slot:
            self.base.write("SLOT", slot)
            for _ in range(100):
                if self.base.read("SLOT") == slot:
                    break
                yield 20
            else:
                raise OperationFailed(f"The wheel base did not switch to SETUP {slot}.")
        yield SETTLE_MS
        if self.base.read("SLOT") != slot or self.base.read("advanced_mode") != 1:
            raise OperationFailed(f"SETUP {slot} is no longer active; stopped to avoid writing to the wrong setup.")

    def _g_write(self, values: dict[str, int]):
        """Write values into the active setup, read back, retry once. Returns {key: (wanted, actual)}."""
        keys = set(self.base.keys())
        todo = {k: v for k, v in values.items() if k in keys}
        for _attempt in range(2):
            for k, v in todo.items():
                if self.base.read(k) != v:
                    try:
                        self.base.write(k, v)
                    except OSError:
                        pass  # reported by the read-back below
                    yield WRITE_GAP_MS
            yield 400
            todo = {k: v for k, v in todo.items() if self.base.read(k) != v}
            if not todo:
                break
        return {k: (v, self.base.read(k)) for k, v in todo.items()}

    def _g_read_all_slots(self):
        """Read all 5 setups (switching through them) and return to the original one."""
        start = self.base.read("SLOT")
        slots = {}
        for n in store.SLOTS:
            yield from self._g_select(n)
            values = {k: v for k, v in self.base.read_all().items() if k not in ("SLOT", "advanced_mode")}
            slots[n] = values
            self.state.seen[n] = values
        yield from self._g_select(start)
        self.state.save()
        return slots

    def _g_auto_backup(self):
        slots = yield from self._g_read_all_slots()
        name = AUTO_BACKUP_PREFIX + time.strftime("%Y-%m-%d %H.%M.%S")
        store.save(store.Profile(name, slots, dict(self.state.aliases)))
        autos = sorted((p for p in store.load_all() if p.name.startswith(AUTO_BACKUP_PREFIX)),
                       key=lambda p: p.name)
        for old in autos[:-AUTO_BACKUPS_KEPT]:
            store.delete(old)
        return name

    # ======================================================================= setups & draft
    def _user_edit(self, key: str, value: int):
        """Edits only change the draft; nothing reaches the base until WRITE TO WHEEL BASE."""
        if self.live.get(key) == value:
            self.draft.pop(key, None)
        else:
            self.draft[key] = value
        self.controls[key].set_dirty(key in self.draft, self.live.get(key))
        self._update_view()

    def _revert_draft(self):
        self.draft.clear()
        self.refresh()

    def _write_draft(self):
        if not self.draft or not self.base or self.busy:
            return
        slot = self.live.get("SLOT")
        values = dict(self.draft)

        def op():
            if self.base.read("SLOT") != slot:
                raise OperationFailed("The active setup changed before writing; nothing was written.")
            return (yield from self._g_write(values))

        def done(failed):
            for k in values:
                if k not in failed:
                    self.draft.pop(k, None)
            self.refresh()
            if failed:
                self.statusBar().showMessage("The base did not accept: " + ", ".join(
                    f"{k}={want} (is {got})" for k, (want, got) in failed.items()), 12000)
            else:
                self.statusBar().showMessage(f"Wrote {len(values)} change(s) to {self._slot_title(slot)}.", 6000)

        self._run("Writing changes", op(), done)

    def _confirm_discard_draft(self, action: str) -> bool:
        if not self.draft:
            return True
        n = len(self.draft)
        ok = QMessageBox.question(
            self, "Unwritten changes",
            f"{action} will discard {n} change{'s' if n != 1 else ''} not yet written to the wheel base. Continue?")
        if ok != QMessageBox.Yes:
            return False
        self.draft.clear()
        return True

    def _select_slot(self, slot: int):
        if self.busy or slot == self.live.get("SLOT"):
            return
        if not self._confirm_discard_draft(f"Switching to SETUP {slot}"):
            return
        self._write("SLOT", slot)
        self.refresh()

    def _rename_slot(self, slot: int):
        name, ok = QInputDialog.getText(self, f"Name SETUP {slot}",
                                        "Name for this setup, e.g. the game (empty to clear):",
                                        text=self.state.aliases.get(slot, ""))
        if not ok:
            return
        name = name.strip()
        if name:
            self.state.aliases[slot] = name
        else:
            self.state.aliases.pop(slot, None)
        self.state.save()
        self._update_view()

    def _change_mode(self, advanced: bool):
        self.mode_seg.set_value(1 if self._advanced() else 0)  # keep showing the device's state until it confirms
        if not self.base or self.busy or not self._confirm_discard_draft("Changing the tuning mode"):
            return
        target = "Advanced" if advanced else "Standard"
        backup = ("All 5 setups will be saved as an auto-backup profile first." if self._advanced() else
                  "Setups cannot be backed up while in Standard mode.")
        ok = QMessageBox.warning(
            self, "Change tuning mode",
            f"Switch the wheel base to {target} mode?\n\n"
            "On the CSL DD, switching to Standard mode and back can overwrite a setup with the Standard "
            f"setup's values. Setups and profiles are unavailable in Standard mode.\n\n{backup}",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if ok != QMessageBox.Ok:
            return

        def op():
            name = (yield from self._g_auto_backup()) if self._advanced() else None
            self.base.write("advanced_mode", int(advanced))
            yield 500
            return name

        def done(name):
            self._reload_profiles()
            self.statusBar().showMessage(f"Switched to {target} mode."
                                         + (f" Setups backed up as “{name}”." if name else ""), 10000)

        self._run("Backing up setups", op(), done)

    def _reset(self):
        if not self.base or self.busy:
            return
        ok = QMessageBox.warning(
            self, "Reset tuning",
            "Reset the wheel base's tuning setups to factory defaults?\n\n"
            "Tip: save your setups as a profile first (Profiles & Backup) so you can load them back.",
            QMessageBox.Reset | QMessageBox.Cancel, QMessageBox.Cancel)
        if ok != QMessageBox.Reset:
            return
        self.draft.clear()
        try:
            self.base.reset()
        except OSError as e:
            self.statusBar().showMessage(f"Reset failed: {e.strerror}", 8000)
            return
        self.statusBar().showMessage("Tuning reset sent to the wheel base.", 5000)
        self.refresh_timer.start(400)

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "Busy", f"Please wait: {self.busy.lower()}.")
            event.ignore()
        elif self._confirm_discard_draft("Closing"):
            event.accept()
        else:
            event.ignore()

    # ==================================================================== recommended baselines
    def _show_first_run(self):
        if self.state.onboarded or self.busy:
            self._first_run_pending = False
            return
        base_steps = bool(self.base and self.live and self._advanced())
        models = models_for(self.base.product) if self.base else ()
        slot = self.live.get("SLOT")
        dlg = FirstRunDialog(lambda: run_checks(self.base), models, self.model(), self.params, self._known_values,
                             {n: self.state.aliases.get(n) or f"Setup {n}" for n in store.SLOTS}, slot, base_steps, self)
        dlg.picker.values_needed.connect(lambda _slot: self._read_unknown_setups())

        def finished(result):
            if dlg.base_steps:  # without a working wheel base the welcome comes back on the next start
                self.state.onboarded = True
                self.state.save()
            if dlg.base_steps and dlg.chosen_model():
                self._set_model(dlg.chosen_model())
            if result == QDialog.Accepted and dlg.use_baseline:
                self._apply_baseline(dlg.picker.baseline(), dlg.picker.target_slot())
            dlg.deleteLater()

        dlg.finished.connect(finished)
        self.first_run_dialog = dlg
        dlg.open()

    def _open_baseline(self):
        if not self.base or self.busy:
            return
        slot = self.live.get("SLOT")
        dlg = BaselineDialog(models_for(self.base.product), self.model(), self.params, self._known_values,
                             {n: self.state.aliases.get(n) or f"Setup {n}" for n in store.SLOTS}, slot, self)
        dlg.picker.values_needed.connect(lambda _slot: self._read_unknown_setups())

        def finished(result):
            if result == QDialog.Accepted:
                if dlg.chosen_model():
                    self._set_model(dlg.chosen_model())
                if dlg.picker.baseline():
                    self._apply_baseline(dlg.picker.baseline(), dlg.picker.target_slot())
            dlg.deleteLater()

        dlg.finished.connect(finished)
        self.baseline_dialog = dlg
        dlg.open()

    def _read_unknown_setups(self):
        """Read all 5 setups (as Save setups does) when a setup's values are still unknown, so the
        recommended-baseline table can show them. Skipped while busy, with unwritten changes or in Standard mode."""
        if (not self.base or self.busy or self.draft or not self._advanced() or not self.base.ready()
                or all(n in self.state.seen for n in store.SLOTS)):
            return

        def done(_slots):
            for dlg in (getattr(self, "baseline_dialog", None), getattr(self, "first_run_dialog", None)):
                picker = getattr(dlg, "picker", None) if dlg is not None else None
                if picker is not None:
                    try:
                        picker.refresh()
                    except RuntimeError:  # dialog already closed
                        pass

        QTimer.singleShot(0, lambda: self.busy is None and self._run("Reading setups", self._g_read_all_slots(), done))

    def _known_values(self, slot):
        """Last known tuning values of a setup: live for the active one, last seen for the others."""
        if slot == self.live.get("SLOT"):
            return self._tuning_values() or None
        return self.state.seen.get(slot)

    def _apply_baseline(self, baseline, slot):
        if not self.base or self.busy or not baseline or not slot:
            return
        if not self._confirm_discard_draft("Applying a baseline"):
            return

        def op():
            backup = yield from self._g_auto_backup()
            start = self.base.read("SLOT")
            yield from self._g_select(slot)
            failed = yield from self._g_write(baseline.values)
            if start != slot:
                yield from self._g_select(start)  # writing to another setup doesn't change the active one
            return backup, failed

        def done(result):
            backup, failed = result
            if not self.state.aliases.get(slot):
                self.state.aliases[slot] = "Recommended baseline"
                self.state.save()
            self._reload_profiles()
            self.refresh()
            if failed:
                QMessageBox.warning(self, "Apply baseline", "The wheel base did not accept: " + ", ".join(
                    f"{k}={w} (is {g})" for k, (w, g) in failed.items()))
            else:
                self.statusBar().showMessage(
                    f"Applied the recommended baseline to {self._slot_title(slot)}. "
                    f"Previous setups saved as “{backup}”.", 15000)

        self._run("Applying the recommended baseline", op(), done)

    # ============================================================================= profiles
    def _reload_profiles(self, select: str | None = None):
        """Rebuild the profile rows (saved profiles, then automatic backups when expanded)."""
        if select is not None:
            self.selected_profile = select
        self.profiles = store.load_all()
        if self._profile(self.selected_profile) is None:
            self.selected_profile = None
        for row in self.profile_rows.values():
            row.setParent(None)
            row.deleteLater()
        self.profile_rows = {}
        saved = sorted((p for p in self.profiles if not p.name.startswith(AUTO_BACKUP_PREFIX)),
                       key=lambda p: (p.name != self.state.loaded, p.name.lower()))  # loaded one first
        backups = sorted((p for p in self.profiles if p.name.startswith(AUTO_BACKUP_PREFIX)),
                         key=lambda p: p.name, reverse=True)
        for p in saved:
            self.profile_rows_box.addWidget(self._profile_row(p))
        self.no_profiles.setVisible(not saved)
        self.backups_header.set_count(len(backups))
        self.backups_box.setVisible(bool(backups))
        if self.backups_header.open:
            for p in backups:
                self.backup_rows_box.addWidget(self._profile_row(p))
        self._update_view()

    def _profile_row(self, p):
        row = ProfileRow(p.name)
        row.selected.connect(self._select_profile)
        row.load.connect(self._load_profile)
        row.action.connect(self._profile_action)
        self.profile_rows[p.name] = row
        return row

    def _select_profile(self, name):
        self.selected_profile = None if name == self.selected_profile else name
        self._update_view()

    def _profile_action(self, name, verb):
        {"rename": self._rename_profile, "duplicate": self._duplicate_profile,
         "delete": self._delete_profile}[verb](name)

    def _profile(self, name):
        return next((p for p in self.profiles if p.name == name), None)

    def _ask_name(self, title, default=""):
        name, ok = QInputDialog.getText(self, title, "Profile name:", text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if self._profile(name) and name != default:
            QMessageBox.warning(self, title, f"A profile named “{name}” already exists.")
            return None
        return name

    def _save_setups(self, name: str, existing: store.Profile | None):
        if not self._confirm_discard_draft("Saving setups"):
            return

        def done(slots):
            p = existing or store.Profile(name)
            p.slots, p.aliases, p.saved = slots, dict(self.state.aliases), ""
            store.save(p)
            self.state.loaded = p.name
            self.state.save()
            self._reload_profiles(select=p.name)
            self.statusBar().showMessage(f"Saved all 5 setups as “{p.name}”.", 6000)

        self._run("Reading setups", self._g_read_all_slots(), done)

    def _save_new_profile(self):
        name = self._ask_name("Save setups as new profile", time.strftime("Setups %Y-%m-%d"))
        if name:
            self._save_setups(name, None)

    def _load_profile(self, name):
        p = self._profile(name)
        if not p or not self.base or self.busy:
            return
        ok = QMessageBox.warning(
            self, "Load profile",
            f"Load “{p.name}” into the wheel base?\n\nThis overwrites setups "
            f"{', '.join(map(str, sorted(p.slots)))} on the base and their names in this app. "
            "The current setups are saved as an auto-backup profile first.",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if ok != QMessageBox.Ok or not self._confirm_discard_draft("Loading a profile"):
            return

        def op():
            backup = yield from self._g_auto_backup()
            start = self.base.read("SLOT")
            failed = {}
            for n in sorted(p.slots):
                yield from self._g_select(n)
                bad = yield from self._g_write(p.slots[n])
                if bad:
                    failed[n] = bad
                self.state.seen[n] = {k: v for k, v in self.base.read_all().items()
                                      if k not in ("SLOT", "advanced_mode")}
            yield from self._g_select(start)
            return backup, failed

        def done(result):
            backup, failed = result
            self.state.aliases = dict(p.aliases)
            self.state.loaded = p.name
            self.state.save()
            self._reload_profiles(select=p.name)
            if failed:
                detail = "; ".join(f"SETUP {n}: " + ", ".join(f"{k}={w} (is {g})" for k, (w, g) in bad.items())
                                   for n, bad in failed.items())
                QMessageBox.warning(self, "Load profile", f"Loaded “{p.name}”, but the base did not accept: {detail}")
            else:
                self.statusBar().showMessage(f"Loaded “{p.name}”. Previous setups saved as “{backup}”.", 10000)

        self._run(f"Loading “{p.name}”", op(), done)

    def _rename_profile(self, name):
        p = self._profile(name)
        if not p:
            return
        new = self._ask_name("Rename profile", p.name)
        if not new or new == p.name:
            return
        was_loaded = self.state.loaded == p.name
        store.rename(p, new)
        if was_loaded:
            self.state.loaded = new
            self.state.save()
        self._reload_profiles(select=new if self.selected_profile == name else None)

    def _duplicate_profile(self, name):
        p = self._profile(name)
        if not p:
            return
        new = self._ask_name("Duplicate profile", f"{p.name} copy")
        if new:
            store.save(store.Profile(new, {n: dict(v) for n, v in p.slots.items()}, dict(p.aliases)))
            self._reload_profiles(select=new)

    def _delete_profile(self, name):
        p = self._profile(name)
        if not p or QMessageBox.question(self, "Delete profile", f"Delete “{p.name}”?") != QMessageBox.Yes:
            return
        store.delete(p)
        if self.state.loaded == p.name:
            self.state.loaded = None
            self.state.save()
        self._reload_profiles()

    def _open_folder(self):
        store.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(store.PROFILE_DIR)))


def driver_version() -> str:
    """hid-fanatecff doesn't report a version; DKMS installs keep their source in /usr/src."""
    srcs = sorted(Path("/usr/src").glob("hid-fanatecff-*"))
    if srcs:
        return srcs[-1].name.removeprefix("hid-fanatecff-") + " (DKMS)"
    try:
        return "srcversion " + Path("/sys/module/hid_fanatec/srcversion").read_text().strip()
    except OSError:
        return "not loaded"


def app_icon() -> QIcon:
    """The wheel icon; a simplified, lighter version is used up to 32 px so it stays readable."""
    icons = resources.files(__package__).joinpath("icons")
    icon = QIcon(str(icons.joinpath("fanatec-pitbox.svg")))
    # Qt picks SVG files by mode only, not by size, so the small sizes are added as rendered pixmaps
    small = QSvgRenderer(str(icons.joinpath("fanatec-pitbox-small.svg")))
    for size in (16, 22, 24, 32):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        small.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def load_fonts():
    """Bundled Noto Sans / Noto Sans Mono (SIL OFL, see fonts/OFL.txt; built by tools/build_fonts.py), so every
    weight the design uses exists on any system; many systems' Noto fonts lack SemiBold."""
    fonts = resources.files(__package__).joinpath("fonts")
    for entry in sorted(fonts.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".ttf"):
            QFontDatabase.addApplicationFont(str(entry))


def load_stylesheet() -> str:
    QDir.addSearchPath("icons", str(resources.files(__package__).joinpath("icons")))  # url(icons:…) in the QSS
    return resources.files(__package__).joinpath("style.qss").read_text()
