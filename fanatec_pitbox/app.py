"""Main window: the wheel base's 5 setups (sidebar), the tuning panel for the active setup, and
profiles (snapshots of all 5 setups) for backup and restore."""

import errno
import os
import time
from pathlib import Path
from importlib import resources

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import __version__
from . import profiles as store
from .device import UeventWatcher, WheelBase, find_wheel_bases, open_tuning_link
from .params import STANDARD_KEYS, find_model, models_for, params_for
from .input_view import InputPage
from .inputs import InputReader, find_event_device
from .baseline_ui import BaselinePicker, FirstRunDialog, ModelCombo
from .baselines import available as baselines_available
from .widgets import ParamControl, SlotCard

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
        self.setWindowIcon(QIcon.fromTheme("input-gaming"))
        self.resize(1360, 880)

        self.base: WheelBase | None = None
        self.params = {}
        self.controls: dict[str, ParamControl] = {}
        self.live: dict[str, int] = {}
        self.draft: dict[str, int] = {}  # edits not yet written to the base
        self.busy: str | None = None     # label of a running multi-step operation
        self._asked_base = False         # asked the base to report its settings (once per connection)
        self._first_run_pending = False
        self._slot_titles_key = None
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

        # full-width header: device name, loaded profile, tabs
        header = QWidget()
        hv = QVBoxLayout(header)
        hv.setContentsMargins(32, 22, 32, 0)
        hv.setSpacing(14)
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(0)
        self.series_lbl = QLabel(objectName="series")
        self.model_lbl = QLabel("NO WHEEL BASE", objectName="model")
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
        self.loaded_lbl = QLabel(objectName="activeProfile")
        self.loaded_lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.loaded_lbl.setTextFormat(Qt.RichText)
        top.addWidget(self.loaded_lbl, 0, Qt.AlignTop)
        hv.addLayout(top)
        tabs = QHBoxLayout()
        tabs.setSpacing(28)
        self.tab_group = QButtonGroup(self)
        for i, text in enumerate(("TUNING", "INPUT TEST", "SETTINGS")):
            t = QPushButton(text, objectName="tab", checkable=True)
            t.setCursor(Qt.PointingHandCursor)
            self.tab_group.addButton(t, i)
            tabs.addWidget(t)
        self.tab_group.button(0).setChecked(True)
        tabs.addStretch(1)
        hv.addLayout(tabs)
        hv.addWidget(_rule())
        self.banner = QLabel(objectName="banner", wordWrap=True)
        self.banner.setTextFormat(Qt.RichText)
        self.banner.hide()
        hv.addWidget(self.banner)
        v.addWidget(header)

        self.pages = QStackedWidget()
        self.reader = InputReader(parent=self)
        self.input_page = InputPage(self.reader)
        self.pages.addWidget(self._build_tuning_page())
        self.pages.addWidget(self.input_page)
        self.pages.addWidget(self._build_settings_page())
        self.tab_group.idClicked.connect(self._show_tab)
        v.addWidget(self.pages, 1)

        self.statusBar().setSizeGripEnabled(False)  # transient messages only

    def _show_tab(self, index: int):
        self.pages.setCurrentIndex(index)
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
        side.setFixedWidth(296)
        v = QVBoxLayout(side)
        v.setContentsMargins(32, 18, 16, 16)  # left edge lines up with the header
        v.setSpacing(10)
        self.side_title = QLabel(objectName="sideTitle")
        self.side_text = QLabel(objectName="dim", wordWrap=True)
        v.addWidget(self.side_title)
        v.addWidget(self.side_text)
        v.addSpacing(6)
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
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        content = QWidget()
        h.addWidget(content, 1)
        v = QVBoxLayout(content)
        v.setContentsMargins(28, 18, 32, 18)
        v.setSpacing(14)

        bar = QHBoxLayout()
        bar.setSpacing(18)
        bar.addWidget(QLabel("Tuning Mode:"))
        self.mode_toggle = QCheckBox("Advanced", objectName="modeToggle")
        self.mode_toggle.setCursor(Qt.PointingHandCursor)
        self.mode_toggle.setToolTip("Standard / Advanced tuning menu mode of the wheel base.\n"
                                    "Switching modes can overwrite setups, so all setups are backed up first.")
        self.mode_toggle.clicked.connect(self._change_mode)
        bar.addWidget(self.mode_toggle)
        bar.addSpacing(8)
        self.reset_btn = _button("RESET  ⟳", accent=True)
        self.reset_btn.setToolTip("Reset the wheel base's tuning setups to factory defaults.")
        self.reset_btn.clicked.connect(self._reset)
        bar.addWidget(self.reset_btn)
        bar.addStretch(1)
        self.draft_lbl = QLabel(objectName="draftNote")
        bar.addWidget(self.draft_lbl)
        self.revert_btn = _button("REVERT")
        self.revert_btn.setToolTip("Discard changes that have not been written to the wheel base.")
        self.revert_btn.clicked.connect(self._revert_draft)
        bar.addWidget(self.revert_btn)
        self.write_btn = _button("WRITE TO WHEEL BASE", accent=True)
        self.write_btn.setToolTip("Send the highlighted changes to the active setup on the wheel base.")
        self.write_btn.clicked.connect(self._write_draft)
        bar.addWidget(self.write_btn)
        v.addLayout(bar)

        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget(objectName="scrollHolder")
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 8, 0)
        hv.setSpacing(14)
        self.tuning_panel, self.tuning_grid = self._panel(None)
        self.editing_lbl = QLabel(objectName="panelTitle")
        self.tuning_panel.layout().insertWidget(0, self.editing_lbl)
        self.other_panel, self.other_grid = self._panel("RIM & PEDALS")
        hv.addWidget(self.tuning_panel)
        hv.addWidget(self.other_panel)
        hv.addStretch(1)
        scroll.setWidget(holder)
        v.addWidget(scroll, 1)
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
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget(objectName="scrollHolder")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(32, 18, 24, 18)
        outer.setSpacing(14)
        outer.addWidget(self._build_info_panel())  # foundational: what is connected, and how
        profiles = self._build_profiles_panel()
        profiles.setMinimumHeight(380)
        outer.addWidget(profiles)
        outer.addWidget(self._build_baseline_panel())
        outer.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _build_baseline_panel(self):
        frame = QFrame(objectName="panel")
        self.baseline_layout = QVBoxLayout(frame)
        self.baseline_layout.setContentsMargins(24, 16, 24, 18)
        self.baseline_layout.setSpacing(10)
        self.baseline_layout.addWidget(QLabel("RECOMMENDED BASELINE", objectName="panelTitle"))
        self.baseline_hint = QLabel(
            "Fanatec's recommended starting settings for your wheel base: a good baseline for a setup to fine-tune "
            "from. All setups are backed up before it is applied.",
            objectName="dim", wordWrap=True)
        self.baseline_layout.addWidget(self.baseline_hint)
        self.baseline_picker = None
        row = QHBoxLayout()
        row.addStretch(1)
        self.baseline_btn = _button("APPLY BASELINE", accent=True)
        self.baseline_btn.clicked.connect(self._apply_baseline_from_settings)
        row.addWidget(self.baseline_btn)
        self.baseline_layout.addLayout(row)
        self.baseline_panel = frame
        return frame

    def _rebuild_baseline_picker(self):
        if self.baseline_picker:
            self.baseline_picker.setParent(None)
            self.baseline_picker.deleteLater()
            self.baseline_picker = None
        self._slot_titles_key = None
        if self.base and baselines_available(models_for(self.base.product)):
            self.baseline_picker = BaselinePicker(self.params, self._known_values)
            self.baseline_layout.insertWidget(2, self.baseline_picker)
        self.baseline_panel.setVisible(self.baseline_picker is not None)

    def _build_info_panel(self):
        frame = QFrame(objectName="panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(24, 16, 24, 18)
        v.setSpacing(10)
        v.addWidget(QLabel("WHEEL BASE & SYSTEM", objectName="panelTitle"))
        self.info_grid = QGridLayout()
        self.info_grid.setHorizontalSpacing(28)
        self.info_grid.setVerticalSpacing(6)
        self.info_values: dict[str, QLabel] = {}
        fields = ["Wheel base", "Device", "Firmware", "Rim id", "Updates", "Driver", "Kernel", "App", "Config"]
        for i, name in enumerate(fields):
            col = (i // 5) * 2
            self.info_grid.addWidget(QLabel(name, objectName="dim"), i % 5, col)
            val = QLabel(textInteractionFlags=Qt.TextSelectableByMouse)
            self.info_values[name] = val
            self.info_grid.addWidget(val, i % 5, col + 1)
        self.info_grid.setColumnStretch(1, 1)
        self.info_grid.setColumnStretch(3, 1)
        self.model_choice = None  # ModelCombo when several models share the USB id
        v.addLayout(self.info_grid)
        return frame

    def _rebuild_model_choice(self):
        if self.model_choice:
            self.model_choice.setParent(None)
            self.model_choice.deleteLater()
            self.model_choice = None
        models = models_for(self.base.product) if self.base else ()
        label = self.info_values["Wheel base"]
        label.setVisible(len(models) <= 1)
        if len(models) > 1:
            self.model_choice = ModelCombo(models, self.model())
            self.model_choice.currentIndexChanged.connect(lambda _i: self._set_model(self.model_choice.model()))
            self.info_grid.addWidget(self.model_choice, 0, 1, Qt.AlignLeft)

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

    def _build_profiles_panel(self):
        page = QFrame(objectName="panel")
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 18, 24, 20)
        v.setSpacing(12)
        v.addWidget(QLabel("PROFILES & BACKUP", objectName="panelTitle"))
        v.addWidget(QLabel(
            "A profile is a snapshot of all 5 setups and their names, saved on this PC. Use it to back up "
            "your wheel base or to swap in a complete set. One profile is loaded at a time. Saving or loading "
            "briefly switches through all 5 setups on the base, then returns to the setup you were on.",
            objectName="dim", wordWrap=True))

        row = QHBoxLayout()
        row.setSpacing(20)
        v.addLayout(row, 1)
        self.profile_list = QListWidget()
        self.profile_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.profile_list.itemSelectionChanged.connect(self._profile_selected)
        self.profile_list.setMinimumWidth(320)
        row.addWidget(self.profile_list, 2)
        self.profile_detail = QLabel(objectName="profileDetail", wordWrap=True)
        self.profile_detail.setTextFormat(Qt.RichText)
        self.profile_detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        row.addWidget(self.profile_detail, 3)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.save_new_btn = _button("SAVE SETUPS AS NEW PROFILE…", accent=True)
        self.save_new_btn.clicked.connect(self._save_new_profile)
        self.overwrite_btn = _button("Save setups to selected")
        self.overwrite_btn.clicked.connect(self._overwrite_profile)
        self.load_btn = _button("LOAD INTO WHEEL BASE", accent=True)
        self.load_btn.clicked.connect(self._load_profile)
        self.rename_btn = _button("Rename")
        self.rename_btn.clicked.connect(self._rename_profile)
        self.del_btn = _button("Delete", danger=True)
        self.del_btn.clicked.connect(self._delete_profile)
        folder = _button("Open folder")
        folder.clicked.connect(self._open_folder)
        for b in (self.save_new_btn, self.overwrite_btn, self.load_btn, self.rename_btn, self.del_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(folder)
        v.addLayout(buttons)
        return page

    def _build_controls(self):
        """(Re)create parameter controls for the parameters this base exposes."""
        for c in self.controls.values():
            c.setParent(None)
            c.deleteLater()
        self.controls.clear()
        if not self.base:
            return
        available = set(self.base.keys())
        rows = {"left": 0, "right": 0, "other": 0}
        for key, p in self.params.items():
            if key not in available:
                continue
            ctl = ParamControl(p)
            ctl.edited.connect(self._user_edit)
            self.controls[key] = ctl
            if p.section == "other":
                i = rows["other"]
                self.other_grid.addWidget(ctl, i // 2, i % 2)
            else:
                self.tuning_grid.addWidget(ctl, rows[p.section], 0 if p.section == "left" else 1, Qt.AlignTop)
            rows[p.section] += 1
        self.other_panel.setVisible(rows["other"] > 0)

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
            ctl.set_dirty(key in self.draft)
        if self.baseline_picker and self.baseline_picker.isVisible():
            self.baseline_picker.refresh()
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
        self._rebuild_baseline_picker()
        self._rebuild_model_choice()
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
            info = {"Wheel base": m.label if m else "",
                    "Device": f"{b.name}  (USB {b.product:04X})",
                    "Firmware": b.info("fw_version") or "?",
                    "Rim id": b.info("wheel_id") or "?"}
        else:
            self.series_lbl.setText("")
            self.model_lbl.setText("NO WHEEL BASE")
            self.boost_tag.hide()
            info = {"Wheel base": "not connected", "Device": "—", "Firmware": "—", "Rim id": "—"}
        info |= {"Updates": "live (kernel events)" if self.watcher.active else "polling every 1.5 s",
                 "Driver": driver_version(), "Kernel": os.uname().release,
                 "App": f"fanatec-pitbox {__version__}", "Config": str(store.CONFIG_DIR)}
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
            self.side_text.setText(
                "Your wheel base stores 5 setups and runs the active one. Switch here, or from the wheel's "
                "tuning menu (also in-game). Names are only shown in this app.")
        for n, card in self.slot_cards.items():
            seen = self.state.seen.get(n)
            card.setVisible(not standard)
            card.update_card(self.state.aliases.get(n), self._summary(seen), active=(n == slot and adv),
                             enabled=ready and adv and idle)
        self.standard_card.setVisible(standard)
        if standard:
            self.standard_card.update_card(None, self._summary(self.live), active=True, enabled=True)

        # tuning page
        self.mode_toggle.setChecked(adv)
        self.mode_toggle.setText("Advanced" if adv else "Standard")
        self.mode_toggle.setEnabled(ready and idle)
        self.reset_btn.setEnabled(ready and idle)
        for key, ctl in self.controls.items():
            ctl.setVisible(not standard or key in STANDARD_KEYS)
            ctl.setEnabled(ready and not standard and (key in self.live or key in self.draft))
        self.other_panel.setVisible(any(not c.isHidden() for k, c in self.controls.items()
                                        if self.params[k].section == "other"))
        if standard:
            self.editing_lbl.setText("STANDARD SETUP  ·  READ-ONLY IN THIS APP — CHANGE THESE ON THE WHEEL")
        else:
            self.editing_lbl.setText(f"EDITING  {self._slot_title(slot)}" if ready and slot else "")
        n = len(self.draft)
        self.write_btn.setEnabled(bool(n) and adv and idle)
        self.revert_btn.setEnabled(bool(n) and idle)
        self.write_btn.setText(f"WRITE TO WHEEL BASE ({n})" if n else "WRITE TO WHEEL BASE")
        self.draft_lbl.setText(f"{n} unwritten change{'s' if n != 1 else ''}" if n else "")

        # recommended baselines (settings page)
        if self.baseline_picker:
            self.baseline_picker.set_model(self.model())
            titles = {n: self._slot_title(n) for n in store.SLOTS}
            key = (tuple(titles.items()), slot)
            if key != self._slot_titles_key:
                self._slot_titles_key = key
                self.baseline_picker.set_slots(titles, slot)
            self.baseline_btn.setEnabled(ready and adv and idle and self.baseline_picker.baseline() is not None)
            self.baseline_picker.current_box.setText(
                f"Make this my current setup (recommended): {self._slot_title(slot)}" if slot
                else "Make this my current setup (recommended)")
        if (self.baseline_picker and ready and adv and idle and not self.state.onboarded
                and not self._first_run_pending):
            self._first_run_pending = True
            QTimer.singleShot(300, self._show_first_run)

        # profiles page
        sel = self._selected() is not None
        self.save_new_btn.setEnabled(ready and adv and idle)
        self.overwrite_btn.setEnabled(sel and ready and adv and idle)
        self.load_btn.setEnabled(sel and ready and adv and idle)
        self.rename_btn.setEnabled(sel and idle)
        self.del_btn.setEnabled(sel and idle)

        prof = self._profile(self.state.loaded)
        if self.busy:
            self.loaded_lbl.setText(f"<span style='color:#f2e600'>{self.busy}…</span>")
        elif prof:
            changed = prof.differs(self.state.seen)
            mark = (f"<span style='color:#f2e600'>  •  CHANGED: SETUP {', '.join(map(str, changed))}</span>"
                    if changed else "")
            self.loaded_lbl.setText(f"LOADED PROFILE:&nbsp; {prof.name}{mark}")
        else:
            self.loaded_lbl.setText("LOADED PROFILE:&nbsp; <span style='color:#8a8d93'>none</span>")

    def _summary(self, values) -> str:
        if not values or "SEN" not in self.params:
            return ""
        return f"{self.params['SEN'].fmt(values.get('SEN'))}  ·  FF {values.get('FF', '?')}%"

    def _banner(self, html):
        self.banner.setVisible(bool(html))
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
        self.controls[key].set_dirty(key in self.draft)
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
        self.mode_toggle.setChecked(not advanced)  # keep showing the device's state until it confirms
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
        if self.state.onboarded or not self.base or not self.baseline_picker or self.busy or not self._advanced():
            self._first_run_pending = False
            return
        slot = self.live.get("SLOT")
        dlg = FirstRunDialog(models_for(self.base.product), self.model(), self.params, self._known_values,
                             {n: self._slot_title(n) for n in store.SLOTS}, slot, self)
        dlg.picker.current_box.setText(f"Make this my current setup (recommended): {self._slot_title(slot)}")

        def finished(result):
            self.state.onboarded = True
            self.state.save()
            if dlg.chosen_model():
                self._set_model(dlg.chosen_model())
                if self.model_choice:
                    self.model_choice.blockSignals(True)
                    self.model_choice.setCurrentIndex(self.model_choice.findText(dlg.chosen_model().label))
                    self.model_choice.blockSignals(False)
            if result == QDialog.Accepted and dlg.use_baseline:
                self._apply_baseline(dlg.picker.baseline(), dlg.picker.target_slot())
            dlg.deleteLater()

        dlg.finished.connect(finished)
        self.first_run_dialog = dlg
        dlg.open()

    def _apply_baseline_from_settings(self):
        p = self.baseline_picker
        if not p or not p.baseline():
            return
        slot = p.target_slot()
        ok = QMessageBox.question(
            self, "Apply baseline",
            f"Write Fanatec's recommended baseline for the {p.baseline().base} to {self._slot_title(slot)}?\n\n"
            "All setups are backed up as an auto-backup profile first.")
        if ok == QMessageBox.Yes:
            self._apply_baseline(p.baseline(), slot)

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
        current = select or (self._selected().name if self._selected() else self.state.loaded)
        self.profiles = store.load_all()
        self.profile_list.clear()
        for p in self.profiles:
            mark = "●  " if p.name == self.state.loaded else "    "
            item = QListWidgetItem(f"{mark}{p.name}\n      saved {p.saved}")
            item.setData(Qt.UserRole, p.name)
            self.profile_list.addItem(item)
            if p.name == current:
                item.setSelected(True)
                self.profile_list.setCurrentItem(item)
        self._profile_selected()

    def _profile(self, name):
        return next((p for p in self.profiles if p.name == name), None)

    def _selected(self):
        items = self.profile_list.selectedItems()
        return self._profile(items[0].data(Qt.UserRole)) if items else None

    def _profile_selected(self):
        p = self._selected()
        if not p:
            self.profile_detail.setText("<span style='color:#8a8d93'>Select a profile to see its setups.</span>")
        else:
            rows = []
            for n in store.SLOTS:
                values = p.slots.get(n)
                if values is None:
                    continue
                alias = p.aliases.get(n, "")
                parts = [self.params[k].fmt(values[k]) if k in self.params else str(values[k])
                         for k in ("SEN", "FF", "NDP") if k in values]
                rows.append(f"<tr><td style='padding:4px 14px 4px 0'><b>SETUP {n}</b></td>"
                            f"<td style='padding:4px 14px 4px 0'>{alias}</td>"
                            f"<td style='color:#8a8d93'>{'  ·  '.join(parts)}</td></tr>")
            self.profile_detail.setText(f"<p style='font-size:12pt'><b>{p.name}</b></p>"
                                        f"<p style='color:#8a8d93'>saved {p.saved}</p><table>{''.join(rows)}</table>")
        self._update_view()

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

    def _overwrite_profile(self):
        p = self._selected()
        if p and QMessageBox.question(self, "Save setups",
                                      f"Overwrite “{p.name}” with the 5 setups currently on the wheel base?"
                                      ) == QMessageBox.Yes:
            self._save_setups(p.name, p)

    def _load_profile(self):
        p = self._selected()
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

    def _rename_profile(self):
        p = self._selected()
        if not p:
            return
        name = self._ask_name("Rename profile", p.name)
        if not name or name == p.name:
            return
        was_loaded = self.state.loaded == p.name
        store.rename(p, name)
        if was_loaded:
            self.state.loaded = name
            self.state.save()
        self._reload_profiles(select=name)

    def _delete_profile(self):
        p = self._selected()
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


def load_stylesheet() -> str:
    return resources.files(__package__).joinpath("style.qss").read_text()
