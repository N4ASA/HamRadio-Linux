#!/usr/bin/env python3
"""
FlexSpots for Linux v1.2
------------------------
Connects to multiple DX Clusters via Telnet, parses incoming spots,
and pushes them to a FlexRadio SmartSDR radio via the TCP Spots API.

Requires: PyQt6
    sudo apt install python3-pyqt6
"""

import sys
import socket
import threading
import re
import time
import json
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton, QLineEdit,
    QSpinBox, QGroupBox, QStatusBar, QHeaderView,
    QCheckBox, QSplitter, QTextEdit, QFormLayout, QDialog,
    QDialogButtonBox, QTabWidget, QListWidget, QListWidgetItem,
    QInputDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QBrush

APP_NAME    = "FlexSpots for Linux"
APP_VERSION = "1.2.0"
FLEX_PORT   = 4992
SETTINGS_FILE = Path.home() / ".config" / "flexspots" / "settings.json"

BAND_RANGES = {
    "160m": (1.800, 2.000),
    "80m":  (3.500, 4.000),
    "60m":  (5.330, 5.405),
    "40m":  (7.000, 7.300),
    "30m":  (10.100, 10.150),
    "20m":  (14.000, 14.350),
    "17m":  (18.068, 18.168),
    "15m":  (21.000, 21.450),
    "12m":  (24.890, 24.990),
    "10m":  (28.000, 29.700),
    "6m":   (50.000, 54.000),
    "2m":   (144.000, 148.000),
    "70cm": (420.000, 450.000),
}

MODE_GROUPS = {
    "SSB":     ("USB", "LSB", "SSB"),
    "CW":      ("CW",),
    "FT8/FT4": ("FT8", "FT4", "DIGU", "DIGI"),
    "RTTY":    ("RTTY",),
    "PSK":     ("PSK",),
    "Other":   ("FM", "AM", "JS8"),
}

DEFAULT_CLUSTERS = [
    {"host": "dxc.w3lpl.net",    "port": 7373, "name": "W3LPL"},
    {"host": "dxc.k3lr.com",     "port": 7373, "name": "K3LR"},
    {"host": "gb7mbc.sp5bot.net","port": 7373, "name": "GB7MBC"},
    {"host": "cluster.dl9gtb.de","port": 7373, "name": "DL9GTB"},
    {"host": "dxc.dx.to",        "port": 7373, "name": "DX.TO"},
]

class Settings:
    DEFAULTS = {
        "flex_ip":               "",
        "callsign":              "",
        "spot_lifetime":         1800,
        "max_spots":             200,
        "auto_connect_flex":     False,
        "auto_connect_clusters": False,
        "mode_filter":           ["SSB"],
        "band_filter":           [],
        "clusters":              DEFAULT_CLUSTERS,
        "active_clusters":       ["W3LPL"],
        "spot_click_tune":       False,
    }

    def __init__(self):
        self._data = dict(self.DEFAULTS)
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self):
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE) as f:
                    self._data.update(json.load(f))
        except Exception:
            pass

    def save(self):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            print(f"Failed to save settings: {e}")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()


class SpotParser:
    DX_PATTERN = re.compile(
        r"DX de\s+(\S+):\s+(\d+\.?\d*)\s+(\S+)\s+(.*?)\s+(\d{4}Z)",
        re.IGNORECASE
    )

    @staticmethod
    def parse(line: str):
        m = SpotParser.DX_PATTERN.search(line)
        if not m:
            return None
        freq_khz = float(m.group(2))
        freq_mhz = freq_khz / 1000.0
        comment  = m.group(4).strip()
        mode     = SpotParser._guess_mode(comment, freq_mhz)
        return {
            "spotter":   m.group(1).rstrip(":"),
            "freq_mhz":  freq_mhz,
            "callsign":  m.group(3),
            "comment":   comment,
            "time":      m.group(5),
            "mode":      mode,
            "band":      SpotParser._freq_to_band(freq_mhz),
            "timestamp": int(time.time()),
        }

    @staticmethod
    def _guess_mode(comment: str, freq_mhz: float) -> str:
        cu = comment.upper()
        for mode in ("FT8","FT4","CW","SSB","RTTY","PSK","JS8","DIGI","FM","AM"):
            if mode in cu:
                return mode
        if 14.070 <= freq_mhz <= 14.112: return "FT8"
        if 7.074  <= freq_mhz <= 7.078:  return "FT8"
        if 14.000 <= freq_mhz <= 14.070: return "CW"
        if 7.000  <= freq_mhz <= 7.040:  return "CW"
        return "USB"

    @staticmethod
    def _freq_to_band(freq_mhz: float) -> str:
        for band, (lo, hi) in BAND_RANGES.items():
            if lo <= freq_mhz <= hi:
                return band
        return "OOB"

    @staticmethod
    def mode_group(mode: str) -> str:
        for group, modes in MODE_GROUPS.items():
            if mode in modes:
                return group
        return "Other"


class FlexThread(QThread):
    connected    = pyqtSignal()
    disconnected = pyqtSignal(str)
    log_message  = pyqtSignal(str)

    def __init__(self, host, port=FLEX_PORT):
        super().__init__()
        self.host = host; self.port = port
        self._sock = None; self._seq = 1
        self._running = False; self._lock = threading.Lock()

    def run(self):
        self._running = True
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=10)
            self._sock.settimeout(2.0)
            self.log_message.emit(f"[FLEX] Connected to {self.host}:{self.port}")
            self.connected.emit()
            self._send_cmd("client program FlexSpotsLinux")
            self._send_cmd("sub spot all")
            self._reader_loop()
        except Exception as e:
            self.disconnected.emit(str(e))
        finally:
            if self._sock:
                try: self._sock.close()
                except Exception: pass
            self._running = False

    def _reader_loop(self):
        buf = ""
        while self._running:
            try:
                data = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not data: break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line: self.log_message.emit(f"[FLEX] {line}")
            except socket.timeout: continue
            except Exception as e:
                self.disconnected.emit(str(e)); return
        self.disconnected.emit("Connection closed")

    def _send_cmd(self, command):
        seq = self._seq; self._seq += 1
        msg = f"C{seq}|{command}\n"
        self.log_message.emit(f"[FLEX->] {msg.strip()}")
        with self._lock:
            try: self._sock.sendall(msg.encode("utf-8"))
            except Exception as e: self.log_message.emit(f"[FLEX] Send error: {e}")

    def send_spot(self, spot, color="#FFFFFF", lifetime=1800, tune_action=False):
        if not self._running or not self._sock: return
        freq = f"{spot['freq_mhz']:.6f}"
        mode = spot.get("mode","USB")
        ssdr_mode = {
            "CW":"CW","USB":"USB","LSB":"LSB","SSB":"USB",
            "FT8":"DIGU","FT4":"DIGU","RTTY":"RTTY",
            "PSK":"DIGU","JS8":"DIGU","DIGI":"DIGU","FM":"FM","AM":"AM",
        }.get(mode, "USB")
        cmd = (f"spot add rx_freq={freq} callsign={spot['callsign']} "
               f"mode={ssdr_mode} color={color} "
               f"source=FlexSpotsLinux spotter_callsign={spot.get('spotter','')} "
               f"lifetime_seconds={lifetime} trigger_action={'tune' if tune_action else 'none'}")
        comment = spot.get("comment","")[:64]
        if comment: cmd += f" comment={comment!r}"
        self._send_cmd(cmd)

    def clear_spots(self):
        if self._running and self._sock: self._send_cmd("spot clear")

    def stop(self):
        self._running = False
        if self._sock:
            try: self._sock.close()
            except Exception: pass


class ClusterThread(QThread):
    connected     = pyqtSignal(str)
    disconnected  = pyqtSignal(str, str)
    spot_received = pyqtSignal(dict)
    raw_line      = pyqtSignal(str)

    def __init__(self, name, host, port, callsign):
        super().__init__()
        self.name = name; self.host = host
        self.port = port; self.callsign = callsign
        self._sock = None; self._running = False

    def run(self):
        self._running = True
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=15)
            self._sock.settimeout(60.0)
            self.raw_line.emit(f"[{self.name}] Connected to {self.host}:{self.port}")
            self._login()
            self.connected.emit(self.name)
            self._reader_loop()
        except Exception as e:
            self.disconnected.emit(self.name, str(e))
        finally:
            if self._sock:
                try: self._sock.close()
                except Exception: pass
            self._running = False

    def _login(self):
        buf = ""; deadline = time.time() + 20
        while time.time() < deadline:
            try:
                data = self._sock.recv(1024).decode("utf-8", errors="replace")
                buf += data; self.raw_line.emit(data.strip())
                if any(p in buf.lower() for p in ("call","login","enter","please")):
                    time.sleep(0.5)
                    self._sock.sendall((self.callsign + "\r\n").encode())
                    self.raw_line.emit(f"[{self.name}->] {self.callsign}")
                    time.sleep(1); break
            except socket.timeout: break

    def _reader_loop(self):
        buf = ""
        while self._running:
            try:
                data = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not data: break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        self.raw_line.emit(f"[{self.name}] {line}")
                        spot = SpotParser.parse(line)
                        if spot:
                            spot["source"] = self.name
                            self.spot_received.emit(spot)
            except socket.timeout:
                try: self._sock.sendall(b"\r\n")
                except Exception: break
                continue
            except Exception as e:
                self.disconnected.emit(self.name, str(e)); return
        self.disconnected.emit(self.name, "Connection closed")

    def stop(self):
        self._running = False
        if self._sock:
            try: self._sock.close()
            except Exception: pass


class ClusterManagerDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Manage DX Clusters")
        self.setMinimumSize(500, 400)
        self._build_ui(); self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        lbl = QLabel("Check clusters to connect to. Use Add to add your own.")
        lbl.setWordWrap(True); layout.addWidget(lbl)
        self.list_widget = QListWidget(); layout.addWidget(self.list_widget)
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Cluster"); btn_add.clicked.connect(self._add_cluster)
        btn_remove = QPushButton("Remove Selected"); btn_remove.clicked.connect(self._remove_cluster)
        btn_row.addWidget(btn_add); btn_row.addWidget(btn_remove); btn_row.addStretch()
        layout.addLayout(btn_row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save_and_accept); bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _load(self):
        self.list_widget.clear()
        clusters = self.settings.get("clusters", DEFAULT_CLUSTERS)
        active   = self.settings.get("active_clusters", [])
        for c in clusters:
            item = QListWidgetItem(f"{c['name']}  -  {c['host']}:{c['port']}")
            item.setData(Qt.ItemDataRole.UserRole, c)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c["name"] in active else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)

    def _add_cluster(self):
        name, ok = QInputDialog.getText(self, "Add Cluster", "Cluster name:")
        if not ok or not name.strip(): return
        host, ok = QInputDialog.getText(self, "Add Cluster", "Hostname:")
        if not ok or not host.strip(): return
        port, ok = QInputDialog.getInt(self, "Add Cluster", "Port:", 7373, 1, 65535)
        if not ok: return
        clusters = self.settings.get("clusters", list(DEFAULT_CLUSTERS))
        clusters.append({"name": name.strip(), "host": host.strip(), "port": port})
        self.settings.set("clusters", clusters); self._load()

    def _remove_cluster(self):
        row = self.list_widget.currentRow()
        if row < 0: return
        c = self.list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        clusters = [x for x in self.settings.get("clusters", []) if x["name"] != c["name"]]
        self.settings.set("clusters", clusters); self._load()

    def _save_and_accept(self):
        active = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                active.append(item.data(Qt.ItemDataRole.UserRole)["name"])
        self.settings.set("active_clusters", active); self.accept()


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("FlexSpots Settings")
        self.setMinimumWidth(420)
        self._build_ui(); self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        radio_tab = QWidget()
        rf = QFormLayout(radio_tab)
        self.flex_ip = QLineEdit(); self.flex_ip.setPlaceholderText("192.168.1.x")
        rf.addRow("FlexRadio IP:", self.flex_ip)
        self.callsign = QLineEdit(); self.callsign.setPlaceholderText("Your callsign")
        rf.addRow("Callsign:", self.callsign)
        self.spot_lifetime = QSpinBox()
        self.spot_lifetime.setRange(60, 7200); self.spot_lifetime.setSuffix(" sec")
        rf.addRow("Spot lifetime:", self.spot_lifetime)
        self.max_spots = QSpinBox(); self.max_spots.setRange(10, 2000)
        rf.addRow("Max spots:", self.max_spots)
        self.auto_flex = QCheckBox("Auto-connect FlexRadio on startup")
        rf.addRow("", self.auto_flex)
        self.auto_clusters = QCheckBox("Auto-connect clusters on startup")
        self.spot_click_tune = QCheckBox("Enable spot click tuning (tune slice on spot click)")
        rf.addRow("", self.spot_click_tune)
        rf.addRow("", self.auto_clusters)
        tabs.addTab(radio_tab, "Radio / Callsign")
        layout.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save_and_accept); bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _load(self):
        self.flex_ip.setText(self.settings.get("flex_ip", ""))
        self.callsign.setText(self.settings.get("callsign", ""))
        self.spot_lifetime.setValue(self.settings.get("spot_lifetime", 1800))
        self.max_spots.setValue(self.settings.get("max_spots", 200))
        self.auto_flex.setChecked(self.settings.get("auto_connect_flex", False))
        self.auto_clusters.setChecked(self.settings.get("auto_connect_clusters", False))
        self.spot_click_tune.setChecked(self.settings.get("spot_click_tune", False))

    def _save_and_accept(self):
        self.settings.set("flex_ip",               self.flex_ip.text().strip())
        self.settings.set("callsign",              self.callsign.text().strip().upper())
        self.settings.set("spot_lifetime",         self.spot_lifetime.value())
        self.settings.set("max_spots",             self.max_spots.value())
        self.settings.set("auto_connect_flex",     self.auto_flex.isChecked())
        self.settings.set("auto_connect_clusters", self.auto_clusters.isChecked())
        self.settings.set("spot_click_tune",       self.spot_click_tune.isChecked())
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings        = Settings()
        self.flex_thread     = None
        self.cluster_threads = {}
        self.spots           = []

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(900, 600)
        self._build_ui()
        self._apply_dark_theme()

        if self.settings.get("auto_connect_flex") and self.settings.get("flex_ip"):
            QTimer.singleShot(500, self._connect_flex)
        if self.settings.get("auto_connect_clusters") and self.settings.get("callsign"):
            QTimer.singleShot(800, self._connect_active_clusters)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)

        # Row 1: connections + actions
        row1 = QHBoxLayout()

        flex_grp = QGroupBox("FlexRadio")
        fl = QHBoxLayout(flex_grp)
        self.flex_ip_edit = QLineEdit(self.settings.get("flex_ip", ""))
        self.flex_ip_edit.setPlaceholderText("Radio IP")
        self.flex_ip_edit.setMaximumWidth(130)
        fl.addWidget(self.flex_ip_edit)
        self.btn_flex = QPushButton("Connect")
        self.btn_flex.setCheckable(True)
        self.btn_flex.clicked.connect(self._toggle_flex)
        fl.addWidget(self.btn_flex)
        self.flex_status = QLabel("●")
        self.flex_status.setStyleSheet("color: #666; font-size: 18px;")
        fl.addWidget(self.flex_status)
        row1.addWidget(flex_grp)

        cluster_grp = QGroupBox("DX Clusters")
        cl = QHBoxLayout(cluster_grp)
        self.btn_clusters_connect = QPushButton("Connect Active")
        self.btn_clusters_connect.setCheckable(True)
        self.btn_clusters_connect.clicked.connect(self._toggle_clusters)
        cl.addWidget(self.btn_clusters_connect)
        self.cluster_status_label = QLabel("No clusters connected")
        self.cluster_status_label.setStyleSheet("color: #666;")
        cl.addWidget(self.cluster_status_label)
        btn_manage = QPushButton("Manage Clusters")
        btn_manage.clicked.connect(self._manage_clusters)
        cl.addWidget(btn_manage)
        row1.addWidget(cluster_grp)

        act_grp = QGroupBox("Actions")
        al = QHBoxLayout(act_grp)
        btn_clear = QPushButton("Clear Spots")
        btn_clear.clicked.connect(self._clear_spots)
        al.addWidget(btn_clear)
        btn_settings = QPushButton("Settings")
        btn_settings.clicked.connect(self._open_settings)
        al.addWidget(btn_settings)
        row1.addWidget(act_grp)
        row1.addStretch()
        main_layout.addLayout(row1)

        # Row 2: filters
        row2 = QHBoxLayout()

        mode_grp = QGroupBox("Mode Filter")
        ml = QHBoxLayout(mode_grp)
        ml.setSpacing(10)
        self.mode_checks = {}
        saved_modes = self.settings.get("mode_filter", ["SSB"])
        for group_name in MODE_GROUPS:
            cb = QCheckBox(group_name)
            cb.setChecked(group_name in saved_modes)
            cb.stateChanged.connect(self._on_filter_changed)
            self.mode_checks[group_name] = cb
            ml.addWidget(cb)
        row2.addWidget(mode_grp)

        band_grp = QGroupBox("Band Filter")
        bl = QHBoxLayout(band_grp)
        bl.setSpacing(8)
        self.band_checks = {}
        saved_bands = self.settings.get("band_filter", [])
        for band in BAND_RANGES:
            cb = QCheckBox(band)
            cb.setChecked(band in saved_bands if saved_bands else True)
            cb.stateChanged.connect(self._on_filter_changed)
            self.band_checks[band] = cb
            bl.addWidget(cb)
        row2.addWidget(band_grp)
        row2.addStretch()
        main_layout.addLayout(row2)

        # Spots table + log
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["Time","Callsign","Freq (MHz)","Band","Mode","Spotter","Source","Comment"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 95)
        self.table.setColumnWidth(3, 55)
        self.table.setColumnWidth(4, 55)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 70)
        splitter.addWidget(self.table)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumHeight(140)
        self.log_console.setFont(QFont("Monospace", 9))
        splitter.addWidget(self.log_console)
        splitter.setSizes([420, 140])
        main_layout.addWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready - connect FlexRadio and clusters to start.")
        self.spot_count_label = QLabel("Spots: 0")
        self.status_bar.addPermanentWidget(self.spot_count_label)

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; }
            QGroupBox {
                border: 1px solid #45475a; border-radius: 4px;
                margin-top: 8px; padding: 4px;
                font-weight: bold; color: #89b4fa;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QPushButton {
                background-color: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 4px;
                padding: 4px 10px; min-width: 70px;
            }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:checked { background-color: #a6e3a1; color: #1e1e2e; font-weight: bold; }
            QCheckBox { color: #cdd6f4; spacing: 4px; }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border: 1px solid #45475a; border-radius: 2px;
                background-color: #313244;
            }
            QCheckBox::indicator:checked { background-color: #89b4fa; border-color: #89b4fa; }
            QLineEdit, QSpinBox {
                background-color: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 3px; padding: 3px 6px;
            }
            QTableWidget {
                background-color: #181825; color: #cdd6f4;
                gridline-color: #313244; border: none;
                alternate-background-color: #1e1e2e;
            }
            QTableWidget::item:selected { background-color: #89b4fa; color: #1e1e2e; }
            QHeaderView::section {
                background-color: #313244; color: #89b4fa;
                border: 1px solid #45475a; padding: 4px; font-weight: bold;
            }
            QTextEdit {
                background-color: #11111b; color: #a6e3a1;
                border: 1px solid #313244; font-family: monospace;
            }
            QStatusBar { background-color: #181825; color: #6c7086; }
            QListWidget {
                background-color: #181825; color: #cdd6f4; border: 1px solid #45475a;
            }
            QListWidget::item:selected { background-color: #89b4fa; color: #1e1e2e; }
            QTabWidget::pane { border: 1px solid #45475a; }
            QTabBar::tab {
                background-color: #313244; color: #cdd6f4;
                padding: 6px 14px; border: 1px solid #45475a;
            }
            QTabBar::tab:selected { background-color: #89b4fa; color: #1e1e2e; }
            QDialog { background-color: #1e1e2e; color: #cdd6f4; }
        """)

    def _get_active_modes(self):
        active = {n for n, cb in self.mode_checks.items() if cb.isChecked()}
        return active if active else set(MODE_GROUPS.keys())

    def _get_active_bands(self):
        active = {n for n, cb in self.band_checks.items() if cb.isChecked()}
        return active if active else set(BAND_RANGES.keys())

    def _spot_passes_filter(self, spot):
        return (SpotParser.mode_group(spot.get("mode","USB")) in self._get_active_modes() and
                spot.get("band","OOB") in self._get_active_bands())

    def _on_filter_changed(self):
        self.settings.set("mode_filter", [n for n,cb in self.mode_checks.items() if cb.isChecked()])
        self.settings.set("band_filter",  [n for n,cb in self.band_checks.items()  if cb.isChecked()])
        am = self._get_active_modes(); ab = self._get_active_bands()
        for row in range(self.table.rowCount()):
            bi = self.table.item(row,3); mi = self.table.item(row,4)
            band = bi.text() if bi else ""; mode = mi.text() if mi else ""
            self.table.setRowHidden(row,
                SpotParser.mode_group(mode) not in am or band not in ab)

    def _toggle_flex(self, checked):
        if checked: self._connect_flex()
        else:
            if self.flex_thread: self.flex_thread.stop()

    def _connect_flex(self):
        ip = self.flex_ip_edit.text().strip()
        if not ip:
            self._log("Enter FlexRadio IP address first.")
            self.btn_flex.setChecked(False); return
        self.settings.set("flex_ip", ip)
        self._log(f"Connecting to FlexRadio at {ip}:{FLEX_PORT}...")
        self.flex_thread = FlexThread(ip, FLEX_PORT)
        self.flex_thread.connected.connect(self._on_flex_connected)
        self.flex_thread.disconnected.connect(self._on_flex_disconnected)
        self.flex_thread.log_message.connect(self._log)
        self.flex_thread.start()

    def _on_flex_connected(self):
        self.flex_status.setStyleSheet("color: #a6e3a1; font-size: 18px;")
        self.btn_flex.setChecked(True); self._log("FlexRadio connected.")

    def _on_flex_disconnected(self, reason):
        self.flex_status.setStyleSheet("color: #f38ba8; font-size: 18px;")
        self.btn_flex.setChecked(False); self._log(f"FlexRadio disconnected: {reason}")

    def _toggle_clusters(self, checked):
        if checked: self._connect_active_clusters()
        else: self._disconnect_all_clusters()

    def _connect_active_clusters(self):
        callsign = self.settings.get("callsign","")
        if not callsign:
            self._log("Set your callsign in Settings first.")
            self.btn_clusters_connect.setChecked(False)
            self._open_settings(); return
        active_names    = self.settings.get("active_clusters", [])
        clusters        = self.settings.get("clusters", DEFAULT_CLUSTERS)
        active_clusters = [c for c in clusters if c["name"] in active_names]
        if not active_clusters:
            self._log("No clusters selected. Use Manage Clusters.")
            self.btn_clusters_connect.setChecked(False); return
        for c in active_clusters:
            if c["name"] not in self.cluster_threads:
                self._start_cluster(c, callsign)
        self.btn_clusters_connect.setChecked(True)

    def _start_cluster(self, c, callsign):
        t = ClusterThread(c["name"], c["host"], c["port"], callsign)
        t.connected.connect(self._on_cluster_connected)
        t.disconnected.connect(self._on_cluster_disconnected)
        t.spot_received.connect(self._on_spot_received)
        t.raw_line.connect(self._log)
        t.start()
        self.cluster_threads[c["name"]] = t
        self._log(f"Connecting to {c['name']} ({c['host']}:{c['port']})...")

    def _disconnect_all_clusters(self):
        for t in self.cluster_threads.values(): t.stop()
        self.cluster_threads.clear()
        self.btn_clusters_connect.setChecked(False)
        self.cluster_status_label.setText("No clusters connected")
        self.cluster_status_label.setStyleSheet("color: #666;")

    def _on_cluster_connected(self, name):
        self._log(f"{name}: Connected.")
        self._update_cluster_status()

    def _on_cluster_disconnected(self, name, reason):
        self._log(f"{name}: Disconnected - {reason}")
        if name in self.cluster_threads: del self.cluster_threads[name]
        self._update_cluster_status()
        if self.btn_clusters_connect.isChecked():
            self._log(f"{name}: Auto-reconnecting in 30 seconds...")
            callsign = self.settings.get("callsign","")
            clusters = self.settings.get("clusters", DEFAULT_CLUSTERS)
            c = next((x for x in clusters if x["name"] == name), None)
            if c and callsign:
                QTimer.singleShot(30000, lambda: self._start_cluster(c, callsign))

    def _update_cluster_status(self):
        count = len(self.cluster_threads)
        if count == 0:
            self.cluster_status_label.setText("No clusters connected")
            self.cluster_status_label.setStyleSheet("color: #666;")
        elif count == 1:
            name = list(self.cluster_threads.keys())[0]
            self.cluster_status_label.setText(f"● {name}")
            self.cluster_status_label.setStyleSheet("color: #a6e3a1;")
        else:
            self.cluster_status_label.setText(f"● {count} connected")
            self.cluster_status_label.setStyleSheet("color: #a6e3a1;")

    def _manage_clusters(self):
        ClusterManagerDialog(self.settings, self).exec()

    def _on_spot_received(self, spot):
        if not self._spot_passes_filter(spot): return
        max_spots = self.settings.get("max_spots", 200)
        if len(self.spots) >= max_spots: self.spots.pop(0)
        self.spots.append(spot)
        self._add_table_row(spot)
        self._push_to_flex(spot)
        self.spot_count_label.setText(f"Spots: {len(self.spots)}")

    def _add_table_row(self, spot):
        self.table.insertRow(0)
        items = [
            spot.get("time",""), spot.get("callsign",""),
            f"{spot['freq_mhz']:.3f}", spot.get("band",""),
            spot.get("mode",""), spot.get("spotter",""),
            spot.get("source",""), spot.get("comment",""),
        ]
        color = self._spot_color(spot)
        for col, text in enumerate(items):
            item = QTableWidgetItem(text)
            item.setForeground(QBrush(QColor(color)))
            if col == 1:
                f = item.font(); f.setBold(True); item.setFont(f)
            self.table.setItem(0, col, item)
        while self.table.rowCount() > self.settings.get("max_spots", 200):
            self.table.removeRow(self.table.rowCount() - 1)

    def _spot_color(self, spot):
        mode = spot.get("mode","")
        if mode == "CW": return "#FAD05B"
        if mode in ("FT8","FT4","RTTY","DIGI","PSK"): return "#89dceb"
        return "#00BFFF"

    def _push_to_flex(self, spot):
        if self.flex_thread and self.flex_thread.isRunning():
            self.flex_thread.send_spot(spot,
                tune_action=self.settings.get("spot_click_tune", False),
                color=self._spot_color(spot),
                lifetime=self.settings.get("spot_lifetime", 1800))

    def _clear_spots(self):
        self.spots.clear(); self.table.setRowCount(0)
        self.spot_count_label.setText("Spots: 0")
        if self.flex_thread and self.flex_thread.isRunning():
            self.flex_thread.clear_spots()
        self._log("Spots cleared.")

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self.flex_ip_edit.setText(self.settings.get("flex_ip",""))

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_console.append(f"[{ts}] {msg}")
        self.log_console.verticalScrollBar().setValue(
            self.log_console.verticalScrollBar().maximum())

    def closeEvent(self, event):
        if self.flex_thread: self.flex_thread.stop()
        self._disconnect_all_clusters()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
