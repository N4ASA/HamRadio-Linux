#!/usr/bin/env python3
"""
CQRLOG to QRZ Uploader v3.0
----------------------------
Watches the CQRLOG database for new QSOs and uploads them to QRZ.com.
Uses a simple QTimer-based poll instead of threads to avoid sync issues.

Requires: PyQt6, mysql-connector-python, requests
    sudo apt install python3-pyqt6
    pip3 install mysql-connector-python requests --break-system-packages
"""

import sys
import json
import time
import requests
import threading
from datetime import datetime
from pathlib import Path

import mysql.connector
from mysql.connector import Error as MySQLError

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton, QLineEdit,
    QGroupBox, QStatusBar, QHeaderView, QTextEdit, QFormLayout,
    QDialog, QDialogButtonBox, QTabWidget, QSystemTrayIcon, QMenu,
    QSplitter
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QFont, QBrush, QIcon, QAction

APP_NAME    = "CQRLOG to QRZ Uploader"
APP_VERSION = "3.0.0"
CONFIG_FILE = Path.home() / ".config" / "cqrlog-qrz" / "config.json"
QRZ_API_URL = "https://logbook.qrz.com/api"
POLL_INTERVAL_MS = 5000
MAX_RETRIES      = 5
RETRY_DELAYS     = [10, 30, 60, 120, 300]


class Config:
    DEFAULTS = {
        "qrz_api_key":      "",
        "qrz_callsign":     "N4ASA",
        "db_host":          "localhost",
        "db_port":          64000,
        "db_user":          "cqrlog",
        "db_password":      "cqrlog",
        "db_name":          "cqrlog001",
        "last_uploaded_id": 0,
    }

    def __init__(self):
        self._data = dict(self.DEFAULTS)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self):
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE) as f:
                    self._data.update(json.load(f))
        except Exception:
            pass

    def save(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            print(f"Config save error: {e}")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()


def build_adif(qso: dict) -> str:
    def field(tag, value):
        value = str(value).strip() if value else ""
        return f"<{tag}:{len(value)}>{value} " if value else ""

    qso_date = str(qso.get("qsodate", "")).replace("-", "")[:8]
    time_on  = str(qso.get("time_on", "")).replace(":", "")[:6]
    if len(time_on) == 4:
        time_on += "00"

    return (
        field("CALL",       qso.get("callsign", "")) +
        field("BAND",       qso.get("band", "")) +
        field("MODE",       qso.get("mode", "")) +
        field("QSO_DATE",   qso_date) +
        field("TIME_ON",    time_on) +
        field("FREQ",       qso.get("freq", "")) +
        field("RST_SENT",   qso.get("rst_s", "59")) +
        field("RST_RCVD",   qso.get("rst_r", "59")) +
        field("NAME",       qso.get("name", "")) +
        field("QTH",        qso.get("qth", "")) +
        field("GRIDSQUARE", qso.get("loc", "")) +
        field("COUNTRY",    qso.get("country", "")) +
        field("COMMENT",    qso.get("remarks", "")) +
        "<EOR>"
    )


def upload_to_qrz(api_key: str, qso: dict) -> tuple:
    try:
        resp = requests.post(
            QRZ_API_URL,
            data={"KEY": api_key, "ACTION": "INSERT", "ADIF": build_adif(qso)},
            timeout=15
        )
        resp.raise_for_status()
        body = resp.text
        if "STATUS=OK" in body or "RESULT=OK" in body:
            logid = ""
            for part in body.replace("&", "\n").splitlines():
                if part.startswith("LOGID="):
                    logid = part.split("=", 1)[1]
            return True, f"OK (LOGID={logid})"
        else:
            reason = ""
            for part in body.replace("&", "\n").splitlines():
                if part.startswith("REASON="):
                    reason = part.split("=", 1)[1]
            return False, f"QRZ: {reason or body[:120]}"
    except requests.exceptions.Timeout:
        return False, "Timeout"
    except requests.exceptions.ConnectionError:
        return False, "Network error"
    except Exception as e:
        return False, str(e)


class Signals(QObject):
    upload_ok   = pyqtSignal(dict, str)
    upload_fail = pyqtSignal(dict, str, int)
    log_msg     = pyqtSignal(str)


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs   = QTabWidget()

        qrz_tab = QWidget()
        qf = QFormLayout(qrz_tab)
        self.callsign = QLineEdit()
        qf.addRow("Your callsign:", self.callsign)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        qf.addRow("QRZ API key:", self.api_key)
        show_key = QPushButton("Show/Hide Key")
        show_key.clicked.connect(self._toggle_key)
        qf.addRow("", show_key)
        tabs.addTab(qrz_tab, "QRZ")

        db_tab = QWidget()
        df = QFormLayout(db_tab)
        self.db_host = QLineEdit()
        df.addRow("Host:", self.db_host)
        self.db_port = QLineEdit()
        df.addRow("Port:", self.db_port)
        self.db_user = QLineEdit()
        df.addRow("Username:", self.db_user)
        self.db_pass = QLineEdit()
        self.db_pass.setEchoMode(QLineEdit.EchoMode.Password)
        df.addRow("Password:", self.db_pass)
        self.db_name = QLineEdit()
        df.addRow("Database:", self.db_name)
        tabs.addTab(db_tab, "Database")

        layout.addWidget(tabs)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._save_and_accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _toggle_key(self):
        mode = QLineEdit.EchoMode
        self.api_key.setEchoMode(
            mode.Normal if self.api_key.echoMode() == mode.Password else mode.Password
        )

    def _load(self):
        self.callsign.setText(self.config.get("qrz_callsign", ""))
        self.api_key.setText(self.config.get("qrz_api_key", ""))
        self.db_host.setText(self.config.get("db_host", "localhost"))
        self.db_port.setText(str(self.config.get("db_port", 64000)))
        self.db_user.setText(self.config.get("db_user", "cqrlog"))
        self.db_pass.setText(self.config.get("db_password", "cqrlog"))
        self.db_name.setText(self.config.get("db_name", "cqrlog001"))

    def _save_and_accept(self):
        self.config.set("qrz_callsign", self.callsign.text().strip().upper())
        self.config.set("qrz_api_key",  self.api_key.text().strip())
        self.config.set("db_host",       self.db_host.text().strip())
        self.config.set("db_port",       int(self.db_port.text().strip() or 64000))
        self.config.set("db_user",       self.db_user.text().strip())
        self.config.set("db_password",   self.db_pass.text())
        self.config.set("db_name",       self.db_name.text().strip())
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config  = Config()
        self.signals = Signals()
        self._conn   = None
        self._running = False
        self._count_uploaded = 0
        self._count_failed   = 0
        self._retry_queue    = []

        self._timer = QTimer()
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(820, 560)
        self._build_ui()
        self._apply_dark_theme()
        self._setup_tray()

        self.signals.upload_ok.connect(self._on_upload_ok)
        self.signals.upload_fail.connect(self._on_upload_fail)
        self.signals.log_msg.connect(self._log)

        if self.config.get("qrz_api_key"):
            QTimer.singleShot(1000, self._start)
        else:
            self._log("No QRZ API key configured. Click Settings to add it.")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)

        toolbar = QHBoxLayout()

        status_grp = QGroupBox("Uploader")
        sl = QHBoxLayout(status_grp)
        self.btn_start = QPushButton("Start")
        self.btn_start.setCheckable(True)
        self.btn_start.clicked.connect(self._toggle)
        sl.addWidget(self.btn_start)
        self.lbl_status = QLabel("Stopped")
        self.lbl_status.setStyleSheet("color: #666;")
        sl.addWidget(self.lbl_status)
        toolbar.addWidget(status_grp)

        stats_grp = QGroupBox("Statistics")
        stl = QHBoxLayout(stats_grp)
        self.lbl_uploaded = QLabel("Uploaded: 0")
        self.lbl_failed   = QLabel("Failed: 0")
        stl.addWidget(self.lbl_uploaded)
        stl.addWidget(self.lbl_failed)
        toolbar.addWidget(stats_grp)

        act_grp = QGroupBox("Actions")
        al = QHBoxLayout(act_grp)
        btn_settings = QPushButton("Settings")
        btn_settings.clicked.connect(self._open_settings)
        al.addWidget(btn_settings)
        btn_test = QPushButton("Test QRZ")
        btn_test.clicked.connect(self._test_qrz)
        al.addWidget(btn_test)
        toolbar.addWidget(act_grp)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Callsign", "Band", "Mode", "Date", "Status", "Message"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 55)
        self.table.setColumnWidth(3, 55)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 80)
        splitter.addWidget(self.table)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumHeight(150)
        self.log_console.setFont(QFont("Monospace", 9))
        splitter.addWidget(self.log_console)
        splitter.setSizes([370, 150])
        layout.addWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready.")

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
            QLineEdit {
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

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon.fromTheme("network-wireless"))
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: (self.show(), self.raise_())
            if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self.tray.show()

    def _toggle(self, checked):
        if checked:
            self._start()
        else:
            self._stop()

    def _start(self):
        if not self.config.get("qrz_api_key"):
            self._log("No QRZ API key. Open Settings first.")
            self.btn_start.setChecked(False)
            self._open_settings()
            return
        self._running = True
        self._timer.start()
        self.btn_start.setChecked(True)
        self.btn_start.setText("Stop")
        self.lbl_status.setText("Running")
        self.lbl_status.setStyleSheet("color: #a6e3a1;")
        self._log("Uploader started.")
        self._poll()

    def _stop(self):
        self._running = False
        self._timer.stop()
        self._close_db()
        self.btn_start.setChecked(False)
        self.btn_start.setText("Start")
        self.lbl_status.setText("Stopped")
        self.lbl_status.setStyleSheet("color: #666;")
        self._log("Uploader stopped.")

    def _ensure_connected(self) -> bool:
        try:
            if self._conn and self._conn.is_connected():
                return True
            self._log("Connecting to database...")
            self._conn = mysql.connector.connect(
                host               = self.config.get("db_host"),
                port               = self.config.get("db_port"),
                user               = self.config.get("db_user"),
                password           = self.config.get("db_password"),
                database           = self.config.get("db_name"),
                connection_timeout = 5,
            )
            self._log("Database connected.")
            return True
        except MySQLError as e:
            self._log(f"DB connection failed: {e}")
            self.lbl_status.setText("DB Error")
            self.lbl_status.setStyleSheet("color: #f38ba8;")
            return False

    def _close_db(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _poll(self):
        if not self._running:
            return

        self._process_retries()

        if not self._ensure_connected():
            return

        last_id = self.config.get("last_uploaded_id", 0)
        
        try:
            self._conn.commit()
            cursor = self._conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM cqrlog_main WHERE id_cqrlog_main > %s "
                "ORDER BY id_cqrlog_main ASC LIMIT 20",
                (last_id,)
            )
            rows = cursor.fetchall()
            cursor.close()
        except MySQLError as e:
            self._log(f"DB query error: {e}")
            self._conn = None
            return

        for row in rows:
            qso = {k: (str(v) if v is not None else "") for k, v in row.items()}
            self._log(f"New QSO: {qso.get('callsign','')} on {qso.get('band','')} {qso.get('mode','')}")
            self._upload_async(qso, attempt=1)

    def _process_retries(self):
        now  = time.time()
        keep = []
        for qso, attempt, retry_at in self._retry_queue:
            if now >= retry_at:
                self._log(f"Retrying {qso.get('callsign','')} attempt {attempt}/{MAX_RETRIES}...")
                self._upload_async(qso, attempt)
            else:
                keep.append((qso, attempt, retry_at))
        self._retry_queue = keep

    def _upload_async(self, qso: dict, attempt: int):
        api_key = self.config.get("qrz_api_key", "")
        signals = self.signals
        def _do_upload():
            success, message = upload_to_qrz(api_key, qso)
            if success:
                signals.upload_ok.emit(qso, message)
            else:
                signals.upload_fail.emit(qso, message, attempt)
        threading.Thread(target=_do_upload, daemon=True).start()

    def _on_upload_ok(self, qso: dict, message: str):
        self._count_uploaded += 1
        self.lbl_uploaded.setText(f"Uploaded: {self._count_uploaded}")
        self._add_row(qso, "OK", message, "#a6e3a1")
        self._log(f"Uploaded {qso.get('callsign','')} to QRZ: {message}")
        self.status_bar.showMessage(
            f"Last upload: {qso.get('callsign','')} at {datetime.now().strftime('%H:%M:%S')}"
        )
        self.tray.showMessage(
            "QRZ Upload",
            f"{qso.get('callsign','')}  {qso.get('band','')} {qso.get('mode','')}",
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )
        qso_id = int(qso.get("id_cqrlog_main", 0))
        if qso_id > self.config.get("last_uploaded_id", 0):
            self.config.set("last_uploaded_id", qso_id)

    def _on_upload_fail(self, qso: dict, reason: str, attempt: int):
        self._count_failed += 1
        self.lbl_failed.setText(f"Failed: {self._count_failed}")
        self._add_row(qso, f"Fail({attempt})", reason, "#f38ba8")
        self._log(f"Failed {qso.get('callsign','')} attempt {attempt}: {reason}")
        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            self._retry_queue.append((qso, attempt + 1, time.time() + delay))
            self._log(f"Will retry {qso.get('callsign','')} in {delay}s")

    def _add_row(self, qso: dict, status: str, message: str, color: str):
        self.table.insertRow(0)
        items = [
            datetime.now().strftime("%H:%M:%S"),
            qso.get("callsign", ""),
            qso.get("band", ""),
            qso.get("mode", ""),
            qso.get("qsodate", ""),
            status,
            message,
        ]
        for col, text in enumerate(items):
            item = QTableWidgetItem(str(text))
            if col in (1, 5):
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            if col == 5:
                item.setForeground(QBrush(QColor(color)))
            self.table.setItem(0, col, item)
        while self.table.rowCount() > 500:
            self.table.removeRow(self.table.rowCount() - 1)

    def _test_qrz(self):
        api_key = self.config.get("qrz_api_key", "")
        if not api_key:
            self._log("No API key configured.")
            return
        self._log("Testing QRZ connection...")
        signals = self.signals
        def _do_test():
            try:
                resp = requests.post(
                    QRZ_API_URL,
                    data={"KEY": api_key, "ACTION": "STATUS"},
                    timeout=10
                )
                if "STATUS=OK" in resp.text or "COUNT" in resp.text or "RESULT=OK" in resp.text:
                    signals.log_msg.emit("QRZ connection successful!")
                else:
                    signals.log_msg.emit(f"QRZ response: {resp.text[:200]}")
            except Exception as e:
                signals.log_msg.emit(f"QRZ test failed: {e}")
        threading.Thread(target=_do_test, daemon=True).start()

    def _open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            self._log("Settings saved.")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_console.append(f"[{ts}] {msg}")
        self.log_console.verticalScrollBar().setValue(
            self.log_console.verticalScrollBar().maximum()
        )

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            APP_NAME,
            "Still running in system tray. Right-click to quit.",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )

    def _quit(self):
        self._stop()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.hide()  # start hidden in system tray
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
