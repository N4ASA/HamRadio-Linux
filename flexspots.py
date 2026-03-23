#!/usr/bin/env python3
"""
FlexSpots for Linux
-------------------
Connects to a DX Cluster via Telnet, parses incoming spots,
and pushes them to a FlexRadio SmartSDR radio via the TCP Spots API.
Spots appear as clickable callsigns on the panadapter.

Requires: PyQt6
    sudo apt install python3-pyqt6
"""

import sys
import socket
import threading
import re
import time
import json
import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton, QLineEdit,
    QSpinBox, QGroupBox, QStatusBar, QHeaderView, QComboBox,
    QCheckBox, QSplitter, QTextEdit, QFormLayout, QDialog,
    QDialogButtonBox, QMessageBox, QTabWidget, QFrame
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer
)
from PyQt6.QtGui import QColor, QFont, QBrush

APP_NAME    = "FlexSpots for Linux"
APP_VERSION = "1.0.0"
FLEX_PORT   = 4992
SETTINGS_FILE = Path.home() / ".config" / "flexspots" / "settings.json"

SPOT_COLORS = {
    "DX":      "#00BFFF",
    "Default": "#FFFFFF",
}

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

class Settings:
    DEFAULTS = {
        "flex_ip":               "",
        "cluster_host":          "dxc.w3lpl.net",
        "cluster_port":          7373,
        "callsign":              "",
        "spot_lifetime":         1800,
        "max_spots":             200,
        "auto_connect_flex":     False,
        "auto_connect_cluster":  False,
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
        if 14.070 <= freq_mhz <= 14.112:
            return "FT8"
        if 7.074 <= freq_mhz <= 7.078:
            return "FT8"
        return "USB"

    @staticmethod
    def _freq_to_band(freq_mhz: float) -> str:
        for band, (lo, hi) in BAND_RANGES.items():
            if lo <= freq_mhz <= hi:
                return band
        return "OOB"


class FlexThread(QThread):
    connected    = pyqtSignal()
    disconnected = pyqtSignal(str)
    log_message  = pyqtSignal(str)

    def __init__(self, host: str, port: int = FLEX_PORT):
        super().__init__()
        self.host     = host
        self.port     = port
        self._sock    = None
        self._seq     = 1
        self._running = False
        self._lock    = threading.Lock()

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
                try:
                    self._sock.close()
                except Exception:
                    pass
            self._running = False

    def _reader_loop(self):
        buf = ""
        while self._running:
            try:
                data = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        self.log_message.emit(f"[FLEX] {line}")
            except socket.timeout:
                continue
            except Exception as e:
                self.disconnected.emit(str(e))
                return
        self.disconnected.emit("Connection closed")

    def _send_cmd(self, command: str):
        seq = self._seq
        self._seq += 1
        msg = f"C{seq}|{command}\n"
        self.log_message.emit(f"[FLEX->] {msg.strip()}")
        with self._lock:
            try:
                self._sock.sendall(msg.encode("utf-8"))
            except Exception as e:
                self.log_message.emit(f"[FLEX] Send error: {e}")

    def send_spot(self, spot: dict, color: str = "#FFFFFF", lifetime: int = 1800):
        if not self._running or not self._sock:
            return
        freq     = f"{spot['freq_mhz']:.6f}"
        callsign = spot["callsign"]
        spotter  = spot.get("spotter", "")
        comment  = spot.get("comment", "")[:64]
        mode     = spot.get("mode", "USB")
        ssdr_mode = {
            "CW":"CW","USB":"USB","LSB":"LSB",
            "FT8":"DIGU","FT4":"DIGU","RTTY":"RTTY",
            "PSK":"DIGU","JS8":"DIGU","DIGI":"DIGU",
            "FM":"FM","AM":"AM",
        }.get(mode, "USB")
        cmd = (
            f"spot add rx_freq={freq} callsign={callsign} "
            f"mode={ssdr_mode} color={color} "
            f"source=FlexSpotsLinux spotter_callsign={spotter} "
            f"lifetime_seconds={lifetime} trigger_action=tune"
        )
        if comment:
            cmd += f" comment={comment!r}"
        self._send_cmd(cmd)

    def clear_spots(self):
        if self._running and self._sock:
            self._send_cmd("spot clear")

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


class ClusterThread(QThread):
    connected     = pyqtSignal()
    disconnected  = pyqtSignal(str)
    spot_received = pyqtSignal(dict)
    raw_line      = pyqtSignal(str)

    def __init__(self, host: str, port: int, callsign: str):
        super().__init__()
        self.host     = host
        self.port     = port
        self.callsign = callsign
        self._sock    = None
        self._running = False

    def run(self):
        self._running = True
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=15)
            self._sock.settimeout(60.0)
            self.raw_line.emit(f"[CLUSTER] Connected to {self.host}:{self.port}")
            self._login()
            self.connected.emit()
            self._reader_loop()
        except Exception as e:
            self.disconnected.emit(str(e))
        finally:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
            self._running = False

    def _login(self):
        buf = ""
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                data = self._sock.recv(1024).decode("utf-8", errors="replace")
                buf += data
                self.raw_line.emit(data.strip())
                if any(p in buf.lower() for p in ("call","login","enter","please")):
                    time.sleep(0.5)
                    self._sock.sendall((self.callsign + "\r\n").encode())
                    self.raw_line.emit(f"[CLUSTER->] {self.callsign}")
                    time.sleep(1)
                    break
            except socket.timeout:
                break

    def _reader_loop(self):
        buf = ""
        while self._running:
            try:
                data = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        self.raw_line.emit(line)
                        spot = SpotParser.parse(line)
                        if spot:
                            self.spot_received.emit(spot)
            except socket.timeout:
                try:
                    self._sock.sendall(b"\r\n")
                except Exception:
                    break
                continue
            except Exception as e:
                self.disconnected.emit(str(e))
                return
        self.disconnected.emit("Cluster connection closed")

    def send_command(self, cmd: str):
        if self._running and self._sock:
            try:
                self._sock.sendall((cmd + "\r\n").encode())
            except Exception:
                pass

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("FlexSpots Settings")
        self.setMinimumWidth(400)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        radio_tab = QWidget()
        rf = QFormLayout(radio_tab)
        self.flex_ip = QLineEdit()
        self.flex_ip.setPlaceholderText("192.168.1.x")
        rf.addRow("FlexRadio IP:", self.flex_ip)
        self.spot_lifetime = QSpinBox()
        self.spot_lifetime.setRange(60, 7200)
        self.spot_lifetime.setSuffix(" sec")
        rf.addRow("Spot lifetime:", self.spot_lifetime)
        self.max_spots = QSpinBox()
        self.max_spots.setRange(10, 2000)
        rf.addRow("Max spots:", self.max_spots)
        self.auto_flex = QCheckBox("Auto-connect on startup")
        rf.addRow("", self.auto_flex)
        tabs.addTab(radio_tab, "Radio")

        cluster_tab = QWidget()
        cf = QFormLayout(cluster_tab)
        self.cluster_host = QLineEdit()
        cf.addRow("Cluster host:", self.cluster_host)
        self.cluster_port = QSpinBox()
        self.cluster_port.setRange(1, 65535)
        cf.addRow("Port:", self.cluster_port)
        self.callsign = QLineEdit()
        self.callsign.setPlaceholderText("Your callsign")
        cf.addRow("Callsign:", self.callsign)
        self.auto_cluster = QCheckBox("Auto-connect on startup")
        cf.addRow("", self.auto_cluster)
        tabs.addTab(cluster_tab, "Cluster")

        layout.addWidget(tabs)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._save_and_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _load(self):
        self.flex_ip.setText(self.settings.get("flex_ip", ""))
        self.spot_lifetime.setValue(self.settings.get("spot_lifetime", 1800))
        self.max_spots.setValue(self.settings.get("max_spots", 200))
        self.auto_flex.setChecked(self.settings.get("auto_connect_flex", False))
        self.cluster_host.setText(self.settings.get("cluster_host", ""))
        self.cluster_port.setValue(self.settings.get("cluster_port", 7373))
        self.callsign.setText(self.settings.get("callsign", ""))
        self.auto_cluster.setChecked(self.settings.get("auto_connect_cluster", False))

    def _save_and_accept(self):
        self.settings.set("flex_ip",             self.flex_ip.text().strip())
        self.settings.set("spot_lifetime",       self.spot_lifetime.value())
        self.settings.set("max_spots",           self.max_spots.value())
        self.settings.set("auto_connect_flex",   self.auto_flex.isChecked())
        self.settings.set("cluster_host",        self.cluster_host.text().strip())
        self.settings.set("cluster_port",        self.cluster_port.value())
        self.settings.set("callsign",            self.callsign.text().strip().upper())
        self.settings.set("auto_connect_cluster",self.auto_cluster.isChecked())
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings       = Settings()
        self.flex_thread    = None
        self.cluster_thread = None
        self.spots          = []

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(900, 600)
        self._build_ui()
        self._apply_dark_theme()

        if self.settings.get("auto_connect_flex") and self.settings.get("flex_ip"):
            QTimer.singleShot(500, self._connect_flex)
        if self.settings.get("auto_connect_cluster") and self.settings.get("cluster_host"):
            QTimer.singleShot(800, self._connect_cluster)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)

        toolbar = QHBoxLayout()

        flex_grp = QGroupBox("FlexRadio")
        fl = QHBoxLayout(flex_grp)
        self.flex_ip_edit = QLineEdit(self.settings.get("flex_ip", ""))
        self.flex_ip_edit.setPlaceholderText("Radio IP")
        self.flex_ip_edit.setMaximumWidth(140)
        fl.addWidget(self.flex_ip_edit)
        self.btn_flex = QPushButton("Connect")
        self.btn_flex.setCheckable(True)
        self.btn_flex.clicked.connect(self._toggle_flex)
        fl.addWidget(self.btn_flex)
        self.flex_status = QLabel("●")
        self.flex_status.setStyleSheet("color: #666; font-size: 18px;")
        fl.addWidget(self.flex_status)
        toolbar.addWidget(flex_grp)

        cluster_grp = QGroupBox("DX Cluster")
        cl = QHBoxLayout(cluster_grp)
        self.cluster_host_edit = QLineEdit(self.settings.get("cluster_host", ""))
        self.cluster_host_edit.setPlaceholderText("cluster hostname")
        self.cluster_host_edit.setMinimumWidth(160)
        cl.addWidget(self.cluster_host_edit)
        self.btn_cluster = QPushButton("Connect")
        self.btn_cluster.setCheckable(True)
        self.btn_cluster.clicked.connect(self._toggle_cluster)
        cl.addWidget(self.btn_cluster)
        self.cluster_status = QLabel("●")
        self.cluster_status.setStyleSheet("color: #666; font-size: 18px;")
        cl.addWidget(self.cluster_status)
        toolbar.addWidget(cluster_grp)

        filter_grp = QGroupBox("Filter")
        flt = QHBoxLayout(filter_grp)
        flt.addWidget(QLabel("Band:"))
        self.band_combo = QComboBox()
        self.band_combo.addItem("All Bands")
        for b in BAND_RANGES:
            self.band_combo.addItem(b)
        self.band_combo.currentTextChanged.connect(self._apply_filters)
        flt.addWidget(self.band_combo)
        flt.addWidget(QLabel("Mode:"))
        self.mode_combo = QComboBox()
        for m in ["All Modes","CW","SSB","FT8","FT4","RTTY","DIGI"]:
            self.mode_combo.addItem(m)
        self.mode_combo.currentTextChanged.connect(self._apply_filters)
        flt.addWidget(self.mode_combo)
        toolbar.addWidget(filter_grp)

        act_grp = QGroupBox("Actions")
        al = QHBoxLayout(act_grp)
        btn_clear = QPushButton("Clear Spots")
        btn_clear.clicked.connect(self._clear_spots)
        al.addWidget(btn_clear)
        btn_settings = QPushButton("Settings")
        btn_settings.clicked.connect(self._open_settings)
        al.addWidget(btn_settings)
        toolbar.addWidget(act_grp)
        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Time","Callsign","Freq (MHz)","Band","Mode","Spotter","Comment"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 60)
        self.table.setColumnWidth(4, 60)
        self.table.setColumnWidth(5, 110)
        self.table.doubleClicked.connect(self._on_spot_double_clicked)
        splitter.addWidget(self.table)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumHeight(160)
        self.log_console.setFont(QFont("Monospace", 9))
        splitter.addWidget(self.log_console)
        splitter.setSizes([420, 160])
        main_layout.addWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready - configure Radio IP and Cluster, then connect.")
        self.spot_count_label = QLabel("Spots: 0")
        self.status_bar.addPermanentWidget(self.spot_count_label)

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QGroupBox {
                border: 1px solid #45475a;
                border-radius: 4px;
                margin-top: 8px;
                padding: 4px;
                font-weight: bold;
                color: #89b4fa;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 10px;
                min-width: 70px;
            }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:checked {
                background-color: #a6e3a1;
                color: #1e1e2e;
                font-weight: bold;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 3px;
                padding: 3px 6px;
            }
            QTableWidget {
                background-color: #181825;
                color: #cdd6f4;
                gridline-color: #313244;
                border: none;
                alternate-background-color: #1e1e2e;
            }
            QTableWidget::item:selected {
                background-color: #89b4fa;
                color: #1e1e2e;
            }
            QHeaderView::section {
                background-color: #313244;
                color: #89b4fa;
                border: 1px solid #45475a;
                padding: 4px;
                font-weight: bold;
            }
            QTextEdit {
                background-color: #11111b;
                color: #a6e3a1;
                border: 1px solid #313244;
                font-family: monospace;
            }
            QStatusBar {
                background-color: #181825;
                color: #6c7086;
            }
            QTabWidget::pane { border: 1px solid #45475a; }
            QTabBar::tab {
                background-color: #313244;
                color: #cdd6f4;
                padding: 6px 14px;
                border: 1px solid #45475a;
            }
            QTabBar::tab:selected {
                background-color: #89b4fa;
                color: #1e1e2e;
            }
            QDialog {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
        """)

    def _toggle_flex(self, checked: bool):
        if checked:
            self._connect_flex()
        else:
            self._disconnect_flex()

    def _connect_flex(self):
        ip = self.flex_ip_edit.text().strip()
        if not ip:
            self._log("Enter FlexRadio IP address first.")
            self.btn_flex.setChecked(False)
            return
        self.settings.set("flex_ip", ip)
        self._log(f"Connecting to FlexRadio at {ip}:{FLEX_PORT}...")
        self.flex_thread = FlexThread(ip, FLEX_PORT)
        self.flex_thread.connected.connect(self._on_flex_connected)
        self.flex_thread.disconnected.connect(self._on_flex_disconnected)
        self.flex_thread.log_message.connect(self._log)
        self.flex_thread.start()

    def _disconnect_flex(self):
        if self.flex_thread:
            self.flex_thread.stop()

    def _on_flex_connected(self):
        self.flex_status.setStyleSheet("color: #a6e3a1; font-size: 18px;")
        self.btn_flex.setChecked(True)
        self.status_bar.showMessage("FlexRadio connected.")
        self._log("FlexRadio connected.")

    def _on_flex_disconnected(self, reason: str):
        self.flex_status.setStyleSheet("color: #f38ba8; font-size: 18px;")
        self.btn_flex.setChecked(False)
        self._log(f"FlexRadio disconnected: {reason}")

    def _toggle_cluster(self, checked: bool):
        if checked:
            self._connect_cluster()
        else:
            self._disconnect_cluster()

    def _connect_cluster(self):
        host     = self.cluster_host_edit.text().strip()
        callsign = self.settings.get("callsign", "")
        port     = self.settings.get("cluster_port", 7373)
        if not host:
            self._log("Enter cluster hostname first.")
            self.btn_cluster.setChecked(False)
            return
        if not callsign:
            self._log("Set your callsign in Settings first.")
            self.btn_cluster.setChecked(False)
            self._open_settings()
            return
        self.settings.set("cluster_host", host)
        self._log(f"Connecting to cluster {host}:{port} as {callsign}...")
        self.cluster_thread = ClusterThread(host, port, callsign)
        self.cluster_thread.connected.connect(self._on_cluster_connected)
        self.cluster_thread.disconnected.connect(self._on_cluster_disconnected)
        self.cluster_thread.spot_received.connect(self._on_spot_received)
        self.cluster_thread.raw_line.connect(self._log)
        self.cluster_thread.start()

    def _disconnect_cluster(self):
        if self.cluster_thread:
            self.cluster_thread.stop()

    def _on_cluster_connected(self):
        self.cluster_status.setStyleSheet("color: #a6e3a1; font-size: 18px;")
        self.btn_cluster.setChecked(True)
        self._log("DX Cluster connected.")

    def _on_cluster_disconnected(self, reason: str):
        self.cluster_status.setStyleSheet("color: #f38ba8; font-size: 18px;")
        self.btn_cluster.setChecked(False)
        self._log(f"Cluster disconnected: {reason}")

    def _on_spot_received(self, spot: dict):
        sel_band = self.band_combo.currentText()
        sel_mode = self.mode_combo.currentText()
        if sel_band != "All Bands" and spot.get("band") != sel_band:
            return
        if sel_mode != "All Modes" and spot.get("mode") != sel_mode:
            return
        max_spots = self.settings.get("max_spots", 200)
        if len(self.spots) >= max_spots:
            self.spots.pop(0)
        self.spots.append(spot)
        self._add_table_row(spot)
        self._push_to_flex(spot)
        self.spot_count_label.setText(f"Spots: {len(self.spots)}")

    def _add_table_row(self, spot: dict):
        row = 0
        self.table.insertRow(row)
        items = [
            spot.get("time", ""),
            spot.get("callsign", ""),
            f"{spot['freq_mhz']:.3f}",
            spot.get("band", ""),
            spot.get("mode", ""),
            spot.get("spotter", ""),
            spot.get("comment", ""),
        ]
        color = self._spot_color(spot)
        for col, text in enumerate(items):
            item = QTableWidgetItem(text)
            item.setForeground(QBrush(QColor(color)))
            if col == 1:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.table.setItem(row, col, item)
        while self.table.rowCount() > self.settings.get("max_spots", 200):
            self.table.removeRow(self.table.rowCount() - 1)

    def _spot_color(self, spot: dict) -> str:
        mode = spot.get("mode", "")
        if mode == "CW":
            return "#FAD05B"
        if mode in ("FT8","FT4","RTTY","DIGI","PSK"):
            return "#89dceb"
        return "#00BFFF"

    def _push_to_flex(self, spot: dict):
        if self.flex_thread and self.flex_thread.isRunning():
            color = self._spot_color(spot)
            life  = self.settings.get("spot_lifetime", 1800)
            self.flex_thread.send_spot(spot, color=color, lifetime=life)

    def _on_spot_double_clicked(self, index):
        row = index.row()
        callsign_item = self.table.item(row, 1)
        freq_item     = self.table.item(row, 2)
        if callsign_item and freq_item:
            self._log(f"Tuning to {callsign_item.text()} @ {freq_item.text()} MHz")

    def _clear_spots(self):
        self.spots.clear()
        self.table.setRowCount(0)
        self.spot_count_label.setText("Spots: 0")
        if self.flex_thread and self.flex_thread.isRunning():
            self.flex_thread.clear_spots()
        self._log("Spots cleared.")

    def _apply_filters(self):
        sel_band = self.band_combo.currentText()
        sel_mode = self.mode_combo.currentText()
        for row in range(self.table.rowCount()):
            band_item = self.table.item(row, 3)
            mode_item = self.table.item(row, 4)
            hide = False
            if sel_band != "All Bands" and band_item and band_item.text() != sel_band:
                hide = True
            if sel_mode != "All Modes" and mode_item and mode_item.text() != sel_mode:
                hide = True
            self.table.setRowHidden(row, hide)

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self.flex_ip_edit.setText(self.settings.get("flex_ip", ""))
            self.cluster_host_edit.setText(self.settings.get("cluster_host", ""))

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_console.append(f"[{ts}] {msg}")
        sb = self.log_console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, event):
        if self.flex_thread:
            self.flex_thread.stop()
        if self.cluster_thread:
            self.cluster_thread.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
EOF
