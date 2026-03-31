#!/usr/bin/env python3
"""
Ham Radio Control Panel v1.3
-----------------------------
Controls SPE Expert 1.5K amplifier and Yaesu G-450A rotator.
Runs on Raspberry Pi, accessible via VNC.

Requires: PyQt5, pyserial
"""

import sys
import socket
import serial
import threading
import time
import math
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTabWidget, QGroupBox, QLineEdit,
    QStatusBar, QSpinBox, QFormLayout, QTextEdit
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush

SPE_PORT = "/dev/ttyUSB0"
SPE_BAUD = 9600
ROT_PORT = "/dev/ttyUSB1"
ROT_BAUD = 9600
ROTOR_UDP_PORT = 12345

BANDS = {
    "00": "160m", "01": "80m", "02": "60m", "03": "40m",
    "04": "30m", "05": "20m", "06": "17m", "07": "15m",
    "08": "12m", "09": "10m", "10": "6m",  "11": "4m"
}

def parse_spe_status(raw_str):
    try:
        parts = raw_str.strip().strip(",").split(",")
        parts = [p.strip() for p in parts]
        if parts and parts[0] == "":
            parts = parts[1:]
        if len(parts) < 18:
            return None
        return {
            "model":       parts[0],
            "operate":     parts[1] == "O",
            "tx":          parts[2] == "T",
            "bank":        parts[3],
            "input":       parts[4],
            "band":        BANDS.get(parts[5], parts[5]),
            "antenna":     parts[6],
            "power_level": parts[8],
            "pa_watts":    float(parts[9]) if parts[9].strip("0") else 0.0,
            "swr_atu":     float(parts[10]),
            "swr_ant":     float(parts[11]),
            "v_pa":        float(parts[12]),
            "i_pa":        float(parts[13]),
            "temp_upr":    int(parts[14]),
            "temp_lwr":    int(parts[15]),
            "temp_cmb":    int(parts[16]),
            "warnings":    parts[17],
            "alarms":      parts[18] if len(parts) > 18 else "N",
        }
    except Exception:
        return None


class SPEThread(QThread):
    status_received = pyqtSignal(dict)
    connected       = pyqtSignal()
    disconnected    = pyqtSignal(str)
    log_message     = pyqtSignal(str)

    def __init__(self, port, baud):
        super().__init__()
        self.port     = port
        self.baud     = baud
        self._ser     = None
        self._running = False
        self._lock    = threading.Lock()

    def run(self):
        self._running = True
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=2)
            time.sleep(0.5)
            self.log_message.emit(f"SPE connected on {self.port}")
            self.connected.emit()
            self._reader_loop()
        except Exception as e:
            self.disconnected.emit(str(e))
        finally:
            if self._ser:
                try: self._ser.close()
                except Exception: pass
            self._running = False

    def _reader_loop(self):
        while self._running:
            try:
                self.request_status()
                time.sleep(0.3)
                data = self._ser.read(200)
                if data:
                    for i in range(len(data) - 3):
                        if data[i] == 0xAA and data[i+1] == 0xAA and data[i+2] == 0xAA:
                            cnt = data[i+3]
                            payload = data[i+4:i+4+cnt]
                            text = payload.decode("ascii", errors="replace")
                            result = parse_spe_status(text)
                            if result:
                                self.status_received.emit(result)
                            break
                time.sleep(1.7)
            except Exception as e:
                self.disconnected.emit(str(e))
                return

    def _send(self, cmd):
        with self._lock:
            try:
                if self._ser and self._ser.is_open:
                    self._ser.write(cmd)
            except Exception as e:
                self.log_message.emit(f"SPE send error: {e}")

    def request_status(self):
        self._send(bytes([0x55, 0x55, 0x55, 0x01, 0x90, 0x90]))

    def send_operate(self):
        self._send(bytes([0x55, 0x55, 0x55, 0x01, 0x0D, 0x0D]))

    def send_tune(self):
        self._send(bytes([0x55, 0x55, 0x55, 0x01, 0x09, 0x09]))

    def send_power(self):
        self._send(bytes([0x55, 0x55, 0x55, 0x01, 0x0B, 0x0B]))

    def stop(self):
        self._running = False
        if self._ser:
            try: self._ser.close()
            except Exception: pass


class RotatorThread(QThread):
    position_received = pyqtSignal(float)
    connected         = pyqtSignal()
    disconnected      = pyqtSignal(str)
    log_message       = pyqtSignal(str)

    def __init__(self, port, baud):
        super().__init__()
        self.port     = port
        self.baud     = baud
        self._ser     = None
        self._running = False
        self._lock    = threading.Lock()

    def run(self):
        self._running = True
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=2)
            time.sleep(0.5)
            self.log_message.emit(f"Rotator connected on {self.port}")
            self.connected.emit()
            self._poll_loop()
        except Exception as e:
            self.disconnected.emit(str(e))
        finally:
            if self._ser:
                try: self._ser.close()
                except Exception: pass
            self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                self._ser.write(b"C2\r\n")
                time.sleep(0.5)
                response = self._ser.read(20).decode("ascii", errors="ignore")
                response = response.strip()
                if "+" in response:
                    idx = response.index("+")
                    az_str = response[idx+1:idx+5]
                    try:
                        az = float(az_str)
                        self.position_received.emit(az)
                    except ValueError:
                        pass
                time.sleep(1)
            except Exception as e:
                self.disconnected.emit(str(e))
                return

    def rotate_to(self, azimuth):
        with self._lock:
            try:
                if self._ser and self._ser.is_open:
                    cmd = f"M{int(azimuth):03d}\r\n"
                    self._ser.write(cmd.encode())
                    self.log_message.emit(f"Rotating to {azimuth} degrees")
            except Exception as e:
                self.log_message.emit(f"Rotator error: {e}")

    def stop_rotation(self):
        with self._lock:
            try:
                if self._ser and self._ser.is_open:
                    self._ser.write(b"S\r\n")
                    self.log_message.emit("Rotation stopped")
            except Exception as e:
                self.log_message.emit(f"Rotator stop error: {e}")

    def stop(self):
        self._running = False
        if self._ser:
            try: self._ser.close()
            except Exception: pass


class CompassWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.azimuth = 0.0
        self.target  = None
        self.setMinimumSize(280, 280)

    def set_azimuth(self, az):
        self.azimuth = az
        self.update()

    def set_target(self, az):
        self.target = az
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 20
        painter.setBrush(QBrush(QColor("#181825")))
        painter.setPen(QPen(QColor("#45475a"), 2))
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        painter.setPen(QPen(QColor("#89b4fa")))
        painter.setFont(QFont("Sans", 10, QFont.Bold))
        for label, angle in [("N", 0), ("E", 90), ("S", 180), ("W", 270)]:
            rad = math.radians(angle - 90)
            x = cx + int((r - 15) * math.cos(rad))
            y = cy + int((r - 15) * math.sin(rad))
            painter.drawText(x - 8, y + 6, label)
        painter.setPen(QPen(QColor("#45475a"), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg - 90)
            length = 10 if deg % 90 == 0 else 5
            x1 = cx + int((r - 25) * math.cos(rad))
            y1 = cy + int((r - 25) * math.sin(rad))
            x2 = cx + int((r - 25 + length) * math.cos(rad))
            y2 = cy + int((r - 25 + length) * math.sin(rad))
            painter.drawLine(x1, y1, x2, y2)
        if self.target is not None:
            rad = math.radians(self.target - 90)
            x = cx + int((r - 30) * math.cos(rad))
            y = cy + int((r - 30) * math.sin(rad))
            painter.setPen(QPen(QColor("#f38ba8"), 2, Qt.DashLine))
            painter.drawLine(cx, cy, x, y)
        rad = math.radians(self.azimuth - 90)
        tip_x = cx + int((r - 35) * math.cos(rad))
        tip_y = cy + int((r - 35) * math.sin(rad))
        painter.setPen(QPen(QColor("#a6e3a1"), 3))
        painter.drawLine(cx, cy, tip_x, tip_y)
        painter.setBrush(QBrush(QColor("#a6e3a1")))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(cx - 5, cy - 5, 10, 10)
        painter.setPen(QPen(QColor("#cdd6f4")))
        painter.setFont(QFont("Sans", 12, QFont.Bold))
        painter.drawText(cx - 30, cy + r - 5, f"{self.azimuth:.0f} deg")


class SPETab(QWidget):
    def __init__(self):
        super().__init__()
        self.spe_thread = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        conn_grp = QGroupBox("Connection")
        cl = QHBoxLayout(conn_grp)
        self.port_edit = QLineEdit(SPE_PORT)
        self.port_edit.setMaximumWidth(150)
        cl.addWidget(QLabel("Port:"))
        cl.addWidget(self.port_edit)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setCheckable(True)
        self.btn_connect.clicked.connect(self._toggle_connect)
        cl.addWidget(self.btn_connect)
        self.conn_status = QLabel("●")
        self.conn_status.setStyleSheet("color: #666; font-size: 18px;")
        cl.addWidget(self.conn_status)
        cl.addStretch()
        layout.addWidget(conn_grp)

        status_grp = QGroupBox("Amplifier Status")
        sl = QHBoxLayout(status_grp)

        # Left column - control buttons
        btn_layout = QVBoxLayout()

        self.btn_operate = QPushButton("STANDBY")
        self.btn_operate.setCheckable(True)
        self.btn_operate.setMinimumSize(130, 55)
        self.btn_operate.setFont(QFont("Sans", 12, QFont.Bold))
        self.btn_operate.clicked.connect(self._toggle_operate)
        self.btn_operate.setEnabled(False)
        self.btn_operate.setStyleSheet(
            "background-color: #f38ba8; color: #1e1e2e; font-weight: bold;")
        btn_layout.addWidget(self.btn_operate)

        self.btn_tune = QPushButton("TUNE")
        self.btn_tune.setMinimumSize(130, 45)
        self.btn_tune.setFont(QFont("Sans", 11, QFont.Bold))
        self.btn_tune.clicked.connect(self._send_tune)
        self.btn_tune.setEnabled(False)
        self.btn_tune.setStyleSheet(
            "background-color: #89b4fa; color: #1e1e2e; font-weight: bold;")
        btn_layout.addWidget(self.btn_tune)

        self.btn_power = QPushButton("POWER")
        self.btn_power.setMinimumSize(130, 45)
        self.btn_power.setFont(QFont("Sans", 11, QFont.Bold))
        self.btn_power.clicked.connect(self._send_power)
        self.btn_power.setEnabled(False)
        self.btn_power.setStyleSheet(
            "background-color: #fab387; color: #1e1e2e; font-weight: bold;")
        btn_layout.addWidget(self.btn_power)

        self.tx_label = QLabel("RX")
        self.tx_label.setAlignment(Qt.AlignCenter)
        self.tx_label.setFont(QFont("Sans", 12, QFont.Bold))
        self.tx_label.setStyleSheet("color: #a6e3a1;")
        btn_layout.addWidget(self.tx_label)
        sl.addLayout(btn_layout)

        # Right column - meters
        meters = QFormLayout()
        self.band_label = QLabel("---")
        self.band_label.setFont(QFont("Sans", 16, QFont.Bold))
        self.band_label.setStyleSheet("color: #89b4fa;")
        meters.addRow("Band:", self.band_label)

        self.power_label = QLabel("0 W")
        self.power_label.setFont(QFont("Sans", 16, QFont.Bold))
        self.power_label.setStyleSheet("color: #a6e3a1;")
        meters.addRow("Output:", self.power_label)

        self.swr_label = QLabel("---")
        self.swr_label.setFont(QFont("Sans", 16, QFont.Bold))
        self.swr_label.setStyleSheet("color: #a6e3a1;")
        meters.addRow("SWR:", self.swr_label)

        self.temp_label = QLabel("--- F")
        self.temp_label.setFont(QFont("Sans", 12))
        meters.addRow("Temp:", self.temp_label)

        self.level_label = QLabel("---")
        self.level_label.setFont(QFont("Sans", 12))
        meters.addRow("Power Level:", self.level_label)

        self.warn_label = QLabel("W:N A:N")
        self.warn_label.setFont(QFont("Sans", 12))
        meters.addRow("Warnings:", self.warn_label)

        sl.addLayout(meters)
        layout.addWidget(status_grp)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setFont(QFont("Monospace", 9))
        layout.addWidget(self.log)

    def _toggle_connect(self, checked):
        if checked: self._connect()
        else: self._disconnect()

    def _connect(self):
        port = self.port_edit.text().strip()
        self.spe_thread = SPEThread(port, SPE_BAUD)
        self.spe_thread.connected.connect(self._on_connected)
        self.spe_thread.disconnected.connect(self._on_disconnected)
        self.spe_thread.status_received.connect(self._on_status)
        self.spe_thread.log_message.connect(self._log)
        self.spe_thread.start()

    def _disconnect(self):
        if self.spe_thread: self.spe_thread.stop()
        self.conn_status.setStyleSheet("color: #666; font-size: 18px;")
        self.btn_connect.setChecked(False)
        self.btn_operate.setEnabled(False)
        self.btn_tune.setEnabled(False)
        self.btn_power.setEnabled(False)

    def _on_connected(self):
        self.conn_status.setStyleSheet("color: #a6e3a1; font-size: 18px;")
        self.btn_connect.setChecked(True)
        self.btn_operate.setEnabled(True)
        self.btn_tune.setEnabled(True)
        self.btn_power.setEnabled(True)
        self._log("SPE 1.5K connected")

    def _on_disconnected(self, reason):
        self.conn_status.setStyleSheet("color: #f38ba8; font-size: 18px;")
        self.btn_connect.setChecked(False)
        self.btn_operate.setEnabled(False)
        self.btn_tune.setEnabled(False)
        self.btn_power.setEnabled(False)
        self._log(f"SPE disconnected: {reason}")

    def _on_status(self, s):
        operate = s.get("operate", False)
        self.btn_operate.setChecked(operate)
        self.btn_operate.setText("OPERATE" if operate else "STANDBY")
        self.btn_operate.setStyleSheet(
            "background-color: #a6e3a1; color: #1e1e2e; font-weight: bold;" if operate
            else "background-color: #f38ba8; color: #1e1e2e; font-weight: bold;"
        )
        tx = s.get("tx", False)
        self.tx_label.setText("TX" if tx else "RX")
        self.tx_label.setStyleSheet("color: #f38ba8;" if tx else "color: #a6e3a1;")
        self.band_label.setText(s.get("band", "---"))
        pa = s.get("pa_watts", 0)
        self.power_label.setText(f"{pa:.0f} W")
        swr = s.get("swr_ant", 0.0)
        self.swr_label.setText(f"{swr:.2f}" if swr > 0 else "---")
        if swr > 2.0:
            self.swr_label.setStyleSheet("color: #f38ba8;")
        else:
            self.swr_label.setStyleSheet("color: #a6e3a1;")
        temp = s.get("temp_upr", 0)
        self.temp_label.setText(f"{temp} F")
        if temp > 160:
            self.temp_label.setStyleSheet("color: #f38ba8;")
        else:
            self.temp_label.setStyleSheet("color: #cdd6f4;")
        levels = {"L": "LOW", "M": "MID", "H": "HIGH"}
        self.level_label.setText(levels.get(s.get("power_level", ""), "---"))
        warn = s.get("warnings", "N")
        alarms = s.get("alarms", "N")
        self.warn_label.setText(f"W:{warn} A:{alarms}")
        if warn != "N" or alarms != "N":
            self.warn_label.setStyleSheet("color: #f38ba8; font-weight: bold;")
        else:
            self.warn_label.setStyleSheet("color: #a6e3a1;")

    def _toggle_operate(self, checked):
        if self.spe_thread:
            self.spe_thread.send_operate()

    def _send_tune(self):
        if self.spe_thread:
            self.spe_thread.send_tune()
            self._log("TUNE command sent")

    def _send_power(self):
        if self.spe_thread:
            self.spe_thread.send_power()
            self._log("POWER command sent")

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())


class RotorUDPListener(QThread):
    bearing_received = pyqtSignal(float)

    def __init__(self, port):
        super().__init__()
        self.port = port
        self._running = False

    def run(self):
        self._running = True
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('', self.port))
                sock.settimeout(1.0)
                while self._running:
                    try:
                        data, _ = sock.recvfrom(64)
                        msg = data.decode('ascii', errors='ignore').strip()
                        if msg.startswith('GOTO:'):
                            bearing = float(msg[5:])
                            self.bearing_received.emit(bearing)
                    except socket.timeout:
                        continue
                    except Exception:
                        pass
        except Exception as e:
            print(f"Rotor UDP listener error: {e}")

    def stop(self):
        self._running = False


class RotatorTab(QWidget):
    def __init__(self):
        super().__init__()
        self.rot_thread = None
        self._build_ui()
        self.udp_listener = RotorUDPListener(ROTOR_UDP_PORT)
        self.udp_listener.bearing_received.connect(self._on_remote_bearing)
        self.udp_listener.start()
        QTimer.singleShot(1000, self._connect)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self.compass = CompassWidget()
        left.addWidget(self.compass)
        layout.addLayout(left)

        right = QVBoxLayout()
        conn_grp = QGroupBox("Connection")
        cl = QHBoxLayout(conn_grp)
        self.port_edit = QLineEdit(ROT_PORT)
        self.port_edit.setMaximumWidth(150)
        cl.addWidget(QLabel("Port:"))
        cl.addWidget(self.port_edit)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setCheckable(True)
        self.btn_connect.clicked.connect(self._toggle_connect)
        cl.addWidget(self.btn_connect)
        self.conn_status = QLabel("●")
        self.conn_status.setStyleSheet("color: #666; font-size: 18px;")
        cl.addWidget(self.conn_status)
        right.addWidget(conn_grp)

        pos_grp = QGroupBox("Position")
        pl = QFormLayout(pos_grp)
        self.az_label = QLabel("---")
        self.az_label.setFont(QFont("Sans", 20, QFont.Bold))
        self.az_label.setStyleSheet("color: #a6e3a1;")
        pl.addRow("Current:", self.az_label)
        self.target_label = QLabel("---")
        self.target_label.setFont(QFont("Sans", 16, QFont.Bold))
        self.target_label.setStyleSheet("color: #f9e2af;")
        pl.addRow("Moving to:", self.target_label)
        right.addWidget(pos_grp)

        ctrl_grp = QGroupBox("Control")
        ctl = QVBoxLayout(ctrl_grp)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Rotate to:"))
        self.target_spin = QSpinBox()
        self.target_spin.setRange(0, 360)
        self.target_spin.setSuffix(" deg")
        self.target_spin.setMinimumWidth(90)
        target_row.addWidget(self.target_spin)
        btn_go = QPushButton("GO")
        btn_go.setStyleSheet(
            "background-color: #89b4fa; color: #1e1e2e; font-weight: bold;")
        btn_go.clicked.connect(self._rotate_to)
        target_row.addWidget(btn_go)
        ctl.addLayout(target_row)

        btn_stop = QPushButton("STOP")
        btn_stop.setMinimumHeight(50)
        btn_stop.setFont(QFont("Sans", 12, QFont.Bold))
        btn_stop.setStyleSheet(
            "background-color: #f38ba8; color: #1e1e2e; font-weight: bold;")
        btn_stop.clicked.connect(self._stop)
        ctl.addWidget(btn_stop)

        preset_row = QHBoxLayout()
        for label, bearing in [("N", 0), ("NE", 45), ("E", 90), ("SE", 135),
                                ("S", 180), ("SW", 225), ("W", 270), ("NW", 315)]:
            btn = QPushButton(label)
            btn.setMinimumWidth(40)
            btn.clicked.connect(lambda _, b=bearing: self._preset(b))
            preset_row.addWidget(btn)
        ctl.addLayout(preset_row)
        right.addWidget(ctrl_grp)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        self.log.setFont(QFont("Monospace", 9))
        right.addWidget(self.log)
        right.addStretch()
        layout.addLayout(right)

    def _toggle_connect(self, checked):
        if checked: self._connect()
        else: self._disconnect()

    def _connect(self):
        port = self.port_edit.text().strip()
        self.rot_thread = RotatorThread(port, ROT_BAUD)
        self.rot_thread.connected.connect(self._on_connected)
        self.rot_thread.disconnected.connect(self._on_disconnected)
        self.rot_thread.position_received.connect(self._on_position)
        self.rot_thread.log_message.connect(self._log)
        self.rot_thread.start()

    def _disconnect(self):
        if self.rot_thread: self.rot_thread.stop()
        self.conn_status.setStyleSheet("color: #666; font-size: 18px;")
        self.btn_connect.setChecked(False)

    def _on_connected(self):
        self.conn_status.setStyleSheet("color: #a6e3a1; font-size: 18px;")
        self.btn_connect.setChecked(True)
        self._log("Rotator connected")

    def _on_disconnected(self, reason):
        self.conn_status.setStyleSheet("color: #f38ba8; font-size: 18px;")
        self.btn_connect.setChecked(False)
        self._log(f"Rotator disconnected: {reason}")
        QTimer.singleShot(5000, self._connect)

    def _on_position(self, az):
        self.az_label.setText(f"{az:.0f} deg")
        self.compass.set_azimuth(az)

    def _rotate_to(self):
        if self.rot_thread:
            az = self.target_spin.value()
            self.compass.set_target(az)
            self.target_label.setText(f"{az} deg")
            self.rot_thread.rotate_to(az)

    def _preset(self, bearing):
        self.target_spin.setValue(bearing)
        if self.rot_thread:
            self.compass.set_target(bearing)
            self.target_label.setText(f"{bearing} deg")
            self.rot_thread.rotate_to(bearing)

    def _stop(self):
        if self.rot_thread:
            self.rot_thread.stop_rotation()
            self.compass.set_target(None)
            self.target_label.setText("---")

    def _on_remote_bearing(self, bearing):
        self.target_spin.setValue(int(round(bearing)))
        self.compass.set_target(bearing)
        self.target_label.setText(f"{bearing:.0f} deg")
        if self.rot_thread:
            self.rot_thread.rotate_to(bearing)
        self._log(f"Remote: rotating to {bearing:.0f}°")

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ham Radio Control Panel v1.3")
        self.setMinimumSize(800, 600)
        self._build_ui()
        self._apply_dark_theme()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        tabs = QTabWidget()
        self.spe_tab = SPETab()
        self.rot_tab = RotatorTab()
        tabs.addTab(self.spe_tab, "SPE 1.5K Amplifier")
        tabs.addTab(self.rot_tab, "Yaesu G-450A Rotator")
        layout.addWidget(tabs)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready - connect SPE and Rotator")

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; }
            QGroupBox {
                border: 1px solid #45475a; border-radius: 4px;
                margin-top: 8px; padding: 6px;
                font-weight: bold; color: #89b4fa;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QPushButton {
                background-color: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 4px;
                padding: 5px 12px; min-width: 60px;
            }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:checked { background-color: #a6e3a1; color: #1e1e2e; font-weight: bold; }
            QLineEdit, QSpinBox {
                background-color: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 3px; padding: 3px 6px;
            }
            QTextEdit {
                background-color: #11111b; color: #a6e3a1;
                border: 1px solid #313244; font-family: monospace;
            }
            QTabWidget::pane { border: 1px solid #45475a; }
            QTabBar::tab {
                background-color: #313244; color: #cdd6f4;
                padding: 8px 20px; border: 1px solid #45475a;
            }
            QTabBar::tab:selected { background-color: #89b4fa; color: #1e1e2e; }
            QStatusBar { background-color: #181825; color: #6c7086; }
            QLabel { color: #cdd6f4; }
        """)

    def closeEvent(self, event):
        if self.spe_tab.spe_thread: self.spe_tab.spe_thread.stop()
        if self.rot_tab.rot_thread: self.rot_tab.rot_thread.stop()
        self.rot_tab.udp_listener.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ham Radio Control Panel")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
