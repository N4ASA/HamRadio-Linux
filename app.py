from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, Response
import subprocess
import serial
import time
import os
import json
import threading
import queue as _queue

app = Flask(__name__)

# ----------- LOAD ENV FILE -----------
def load_env():
    env_path = os.path.expanduser("~/ham-web-control/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if "=" in line:
                    key, val = line.strip().split("=", 1)
                    os.environ[key] = val

load_env()

USERNAME = os.getenv("HAM_USERNAME", "dparker100")
PASSWORD = os.getenv("HAM_PASSWORD", "changeme")
app.secret_key = os.getenv("HAM_SECRET_KEY", "fallback-secret")

ROT_PORT = "/dev/rotor"
ROT_BAUD = 9600

SPE_PORT = "/dev/spe_amp"
SPE_BAUD = 9600

ROTOR_TARGET_UDP_PORT = 12346
_target_az = None
_target_lock = threading.Lock()

def _udp_target_listener():
    import socket as _socket
    with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as sock:
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(('', ROTOR_TARGET_UDP_PORT))
        sock.settimeout(1.0)
        while True:
            try:
                data, _ = sock.recvfrom(64)
                msg = data.decode('ascii', errors='ignore').strip()
                if msg.startswith('GOTO:'):
                    global _target_az
                    az = float(msg[5:])
                    with _target_lock:
                        _target_az = az
                    threading.Thread(target=rotate_to, args=(az,), daemon=True).start()
            except Exception:
                pass

threading.Thread(target=_udp_target_listener, daemon=True).start()

# ----------- AUTH -----------
def is_logged_in():
    return session.get("logged_in", False)

def require_login():
    if not is_logged_in():
        return redirect(url_for("login"))
    return None


# ----------- HARDWARE AVAILABILITY -----------
def check_port_available(port, baud):
    """Check if serial port device exists without opening it."""
    import os
    return os.path.exists(port)

def get_hardware_availability():
    if vh_status() == "WINDOWS CONTROL":
        return {"rotator": "windows", "amp": "windows"}
    return {
        "rotator": check_port_available(ROT_PORT, ROT_BAUD),
        "amp": check_port_available(SPE_PORT, SPE_BAUD)
    }


# ----------- HARDWARE STATUS -----------
_rot_az = None
_rot_az_lock = threading.Lock()
_rot_cmd_queue = _queue.Queue()

def _rot_monitor():
    global _rot_az
    while True:
        try:
            ser = serial.Serial(ROT_PORT, ROT_BAUD, timeout=2)
            time.sleep(0.25)
            while True:
                try:
                    while not _rot_cmd_queue.empty():
                        try:
                            cmd = _rot_cmd_queue.get_nowait()
                            ser.write(cmd)
                            time.sleep(0.3)
                        except Exception:
                            pass
                    ser.write(b"C2\r\n")
                    time.sleep(0.35)
                    response = ser.read(20).decode(errors="ignore")
                    if "+" in response:
                        idx = response.index("+")
                        az = response[idx + 1:idx + 5].strip()
                        with _rot_az_lock:
                            _rot_az = az if az else "---"
                    time.sleep(0.5)
                except Exception:
                    break
        except Exception:
            with _rot_az_lock:
                _rot_az = "ERR"
            time.sleep(3)

threading.Thread(target=_rot_monitor, daemon=True).start()

def get_rotator_position():
    with _rot_az_lock:
        return _rot_az if _rot_az is not None else "---"



_spe_state = None
_spe_cmd_queue = _queue.Queue()
_spe_state_lock = threading.Lock()
_SPE_FAILURE = {
    "operate": "---", "tx": "---", "band": "---", "power": "---",
    "fwd_watts": "---", "swr": "---", "temp": "---", "warnings": "---", "alarms": "---"
}

def _parse_spe(data):
    # Full packet: C,15K,{operate},{tx},A,1,{band},1a,0r,{power},{fwdW_int},{swr},{ref},{?},{?},{temp},{?},{?},{warn},{alarm},{cksum}
    text = data.decode("ascii", errors="ignore")
    idx = text.find("C,")
    if idx == -1:
        raise ValueError("No C, found in response")
    line = text[idx:].split("\r")[0].split("\n")[0]
    parts = [p.strip() for p in line.split(",")]
    band_map = {
        "00": "160m", "01": "80m", "02": "60m", "03": "40m",
        "04": "30m", "05": "20m", "06": "17m", "07": "15m",
        "08": "12m", "09": "10m", "10": "6m", "11": "4m"
    }
    try: fwd_watts = str(int(parts[10])) + "W" if len(parts) > 10 else "---"
    except Exception: fwd_watts = "---"
    try: temp = str(int(float(parts[15]))) if len(parts) > 15 else "---"
    except Exception: temp = "---"
    return {
        "operate": "OPERATE" if parts[2] == "O" else "STANDBY",
        "tx": "TX" if parts[3] == "T" else "RX",
        "band": band_map.get(parts[6], parts[6] + "m"),
        "power": {"L": "LOW", "M": "MID", "H": "HIGH"}.get(parts[9], parts[9]),
        "fwd_watts": fwd_watts,
        "swr": parts[11] if len(parts) > 11 else "---",
        "temp": temp,
        "warnings": parts[18] if len(parts) > 18 else "N",
        "alarms": parts[19] if len(parts) > 19 else "N",
    }

def _spe_monitor():
    global _spe_state
    while True:
        try:
            ser = serial.Serial(SPE_PORT, SPE_BAUD, timeout=0.5)
            time.sleep(1.0)
            failures = 0
            while True:
                try:
                    # Send any queued commands with inter-command spacing
                    cmd_sent = False
                    while not _spe_cmd_queue.empty():
                        try:
                            cmd = _spe_cmd_queue.get_nowait()
                            ser.write(cmd)
                            cmd_sent = True
                            time.sleep(0.3)  # gap between consecutive commands
                        except Exception:
                            pass
                    if cmd_sent:
                        time.sleep(0.8)          # let amp finish + ACK arrive
                        ser.reset_input_buffer() # discard ACK + any auto-status
                    # Poll: read lines until we find the C, status packet
                    ser.write(bytes([0x55, 0x55, 0x55, 0x01, 0x90, 0x90]))
                    c_line = b""
                    for _ in range(8):
                        line = ser.readline()
                        if b"C," in line:
                            c_line = line
                            break
                    if not c_line:
                        raise ValueError("No C, response")
                    result = _parse_spe(c_line)
                    with _spe_state_lock:
                        _spe_state = result
                    failures = 0
                    time.sleep(0.3)
                except Exception:
                    failures += 1
                    if failures >= 10:
                        break
                    time.sleep(0.1)
        except Exception:
            pass
        with _spe_state_lock:
            _spe_state = None
        time.sleep(2)

threading.Thread(target=_spe_monitor, daemon=True).start()

def get_spe_status():
    with _spe_state_lock:
        return _spe_state if _spe_state is not None else _SPE_FAILURE

_vh_debounce = {"state": "LINUX CONTROL", "pending": None, "count": 0}
_vh_lock = threading.Lock()

def vh_status():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "virtualhere"],
            capture_output=True, text=True
        )
        new = "WINDOWS CONTROL" if result.stdout.strip() == "active" else "LINUX CONTROL"
    except Exception:
        with _vh_lock:
            return _vh_debounce["state"]

    with _vh_lock:
        if new == _vh_debounce["state"]:
            _vh_debounce["pending"] = None
            _vh_debounce["count"] = 0
        else:
            if new == _vh_debounce["pending"]:
                _vh_debounce["count"] += 1
            else:
                _vh_debounce["pending"] = new
                _vh_debounce["count"] = 1
            if _vh_debounce["count"] >= 2:
                _vh_debounce["state"] = new
                _vh_debounce["pending"] = None
                _vh_debounce["count"] = 0
        return _vh_debounce["state"]

def swr_class(value):
    try:
        return "alert" if float(value) > 2.0 else "ok"
    except Exception:
        return "neutral"

def temp_class(value):
    try:
        return "alert" if float(value) > 160 else "ok"
    except Exception:
        return "neutral"

def get_status_payload():
    vh = vh_status()
    windows_mode = (vh == "WINDOWS CONTROL")

    if windows_mode:
        az = "---"
        spe = {
            "operate": "---", "tx": "---", "band": "---", "power": "---",
            "fwd_watts": "---", "swr": "---", "temp": "---", "warnings": "---", "alarms": "---"
        }
        hw = {"rotator": "windows", "amp": "windows"}
    else:
        az = get_rotator_position()
        spe = get_spe_status()
        hw = get_hardware_availability()

    try:
        az_num = float(az)
    except Exception:
        az_num = 0
    with _target_lock:
        tgt = _target_az
    target_str = f"{tgt:.0f}°" if tgt is not None else "---"
    return {
        "az": az,
        "needle_deg": az_num,
        "spe": spe,
        "vh": vh,
        "swr_color": swr_class(spe["swr"]),
        "temp_color": temp_class(spe["temp"]),
        "hw": hw,
        "target_az": target_str
    }


# ----------- HTML: LOGIN -----------
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Ham Radio Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            background: #1e1e2e;
            color: #cdd6f4;
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 12px;
        }
        .login-box {
            background: #313244;
            border: 1px solid #45475a;
            border-radius: 12px;
            padding: 24px;
            width: 100%;
            max-width: 340px;
            box-sizing: border-box;
        }
        h1 {
            color: #89b4fa;
            text-align: center;
            margin-top: 0;
            font-size: 24px;
        }
        input {
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            border-radius: 8px;
            border: 1px solid #45475a;
            background: #1e1e2e;
            color: #cdd6f4;
            box-sizing: border-box;
        }
        button {
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 8px;
            background: #89b4fa;
            color: #1e1e2e;
            font-weight: bold;
            cursor: pointer;
            margin-top: 10px;
        }
        .msg {
            text-align: center;
            color: #f38ba8;
            min-height: 20px;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>Ham Radio Login</h1>
        <form method="post" action="/login">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Sign In</button>
        </form>
        <div class="msg">{{ msg }}</div>
    </div>
</body>
</html>
"""


# ----------- HTML: MAIN -----------
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Ham Radio Remote Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            background: #1e1e2e;
            color: #cdd6f4;
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 10px;
            max-width: 1000px;
            margin-left: auto;
            margin-right: auto;
        }
        h1 {
            text-align: center;
            color: #89b4fa;
            margin-bottom: 14px;
            font-size: 26px;
        }
        .topbar {
            display: flex;
            justify-content: flex-end;
            margin-bottom: 10px;
        }
        .logout-btn {
            background: #a6e3a1;
            color: #1e1e2e;
            border: none;
            border-radius: 8px;
            padding: 10px 14px;
            font-weight: bold;
            cursor: pointer;
        }

        /* ---- Hardware indicator bar ---- */
        .hw-indicator-bar {
            display: flex;
            gap: 18px;
            justify-content: center;
            margin-bottom: 14px;
        }
        .hw-indicator {
            display: flex;
            align-items: center;
            gap: 7px;
            background: #313244;
            border: 1px solid #45475a;
            border-radius: 20px;
            padding: 5px 14px 5px 10px;
            font-size: 13px;
            font-weight: bold;
            color: #a6adc8;
        }
        .hw-dot {
            width: 11px;
            height: 11px;
            border-radius: 50%;
            flex-shrink: 0;
            transition: background 0.4s ease, box-shadow 0.4s ease;
            background: #45475a;
        }
        .hw-dot.connected {
            background: #a6e3a1;
            box-shadow: 0 0 6px 2px rgba(166, 227, 161, 0.55);
        }
        .hw-dot.disconnected {
            background: #f38ba8;
            box-shadow: 0 0 6px 2px rgba(243, 139, 168, 0.45);
        }
        .hw-dot.windows {
            background: #6c7086;
            box-shadow: none;
        }

        .status-bar {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 10px;
            margin-bottom: 14px;
        }
        .status-card {
            background: #313244;
            border: 1px solid #45475a;
            border-radius: 10px;
            padding: 10px;
            text-align: center;
        }
        .status-label {
            font-size: 12px;
            color: #a6adc8;
            margin-bottom: 6px;
        }
        .status-value {
            font-size: 18px;
            font-weight: bold;
            color: #f5e0dc;
        }
        .main-grid {
            display: grid;
            grid-template-columns: 340px 1fr;
            gap: 14px;
            margin-bottom: 14px;
            align-items: start;
        }
        .panel {
            background: #313244;
            border: 1px solid #45475a;
            border-radius: 12px;
            padding: 14px;
        }
        .panel h2 {
            margin-top: 0;
            color: #a6e3a1;
            text-align: center;
            font-size: 20px;
        }
        .button-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-top: 12px;
        }
        button {
            width: 100%;
            padding: 11px;
            font-size: 14px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
        }
        .blue { background: #89b4fa; color: #1e1e2e; }
        .blue:hover { background: #74c7ec; }
        .red { background: #f38ba8; color: #1e1e2e; }
        .red:hover { background: #eba0ac; }
        .orange { background: #fab387; color: #1e1e2e; }
        .orange:hover { background: #f9c58d; }
        .green { background: #a6e3a1; color: #1e1e2e; }
        .green:hover { background: #94e2d5; }
        input[type="number"] {
            width: 100%;
            padding: 10px;
            font-size: 14px;
            text-align: center;
            border-radius: 8px;
            border: 1px solid #45475a;
            background: #1e1e2e;
            color: #cdd6f4;
            box-sizing: border-box;
        }
        select {
            padding: 6px;
            border-radius: 6px;
            font-size: 14px;
        }
        .footer-panel {
            background: #313244;
            border: 1px solid #45475a;
            border-radius: 12px;
            padding: 14px;
        }
        .footer-panel h2 {
            margin-top: 0;
            color: #89b4fa;
            text-align: center;
            font-size: 20px;
        }
        .footer-panel.vh-error {
            border-color: #f38ba8;
        }
        .footer-panel.vh-error h2 {
            color: #f38ba8;
        }
        .msg {
            margin-top: 14px;
            text-align: center;
            color: #a6e3a1;
            font-weight: bold;
            min-height: 20px;
            font-size: 14px;
        }
        .compass-wrap {
            display: flex;
            justify-content: center;
            margin: 10px 0 14px 0;
        }
        .compass {
            width: 190px;
            height: 190px;
            border: 2px solid #45475a;
            border-radius: 50%;
            position: relative;
            background: #1e1e2e;
            cursor: pointer;
            user-select: none;
        }
        .compass-center {
            position: absolute;
            width: 12px;
            height: 12px;
            background: #a6e3a1;
            border-radius: 50%;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
        }
        .needle {
            position: absolute;
            width: 4px;
            height: 72px;
            background: #a6e3a1;
            left: 50%;
            top: 22px;
            transform-origin: 50% 73px;
            border-radius: 4px;
            transition: transform 0.35s ease-out;
        }
        .card-small-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-bottom: 14px;
        }
        .ok { color: #a6e3a1 !important; }
        .alert { color: #f38ba8 !important; }
        .neutral { color: #f5e0dc !important; }
        .dir {
            position: absolute;
            font-weight: bold;
            color: #89b4fa;
            font-size: 14px;
        }
        .dir-n { top: 6px; left: 50%; transform: translateX(-50%); }
        .dir-e { right: 10px; top: 50%; transform: translateY(-50%); }
        .dir-s { bottom: 6px; left: 50%; transform: translateX(-50%); }
        .dir-w { left: 10px; top: 50%; transform: translateY(-50%); }
        @media (max-width: 700px) {
            .status-bar,
            .main-grid,
            .button-grid,
            .card-small-grid {
                grid-template-columns: 1fr;
            }
            body {
                max-width: 100%;
                padding: 8px;
            }
            h1 {
                font-size: 22px;
            }
            .compass {
                width: 150px;
                height: 150px;
            }
            .needle {
                height: 56px;
                top: 18px;
                transform-origin: 50% 57px;
            }
            .hw-indicator-bar {
                flex-wrap: wrap;
            }
        }
    </style>
</head>
<body>
    <div class="topbar">
        <form method="post" action="/logout">
            <button class="logout-btn" type="submit">Log Out</button>
        </form>
    </div>

    <h1>Ham Radio Remote Control</h1>

    <!-- Hardware connection indicators -->
    <div class="hw-indicator-bar">
        <div class="hw-indicator">
            <div class="hw-dot" id="dot-rotator"></div>
            Rotator
        </div>
        <div class="hw-indicator">
            <div class="hw-dot" id="dot-amp"></div>
            SPE Amp
        </div>
    </div>

    <div class="status-bar">
        <div class="status-card">
            <div class="status-label">Rotor Position</div>
            <div class="status-value" id="az-value">{{ az }}°</div>
        </div>
        <div class="status-card">
            <div class="status-label">Moving To</div>
            <div class="status-value" id="target-az-value" style="color:#f9e2af;">{{ target_az }}</div>
        </div>
        <div class="status-card">
            <div class="status-label">Amp Band</div>
            <div class="status-value" id="band-value">{{ spe.band }}</div>
        </div>
        <div class="status-card">
            <div class="status-label">Amp Output</div>
            <div class="status-value" id="power-value">{{ spe.power }}</div>
        </div>
        <div class="status-card">
            <div class="status-label">Pwr Out</div>
            <div class="status-value" id="fwd-watts-value">{{ spe.fwd_watts }}</div>
        </div>
        <div class="status-card">
            <div class="status-label">VirtualHere</div>
            <div class="status-value" id="vh-value">{{ vh }}</div>
        </div>
    </div>

    <div class="main-grid">
        <div class="panel">
            <h2>Rotator Control</h2>

            <div class="compass-wrap">
                <div class="compass" onclick="rotateFromClick(event)">
                    <div class="dir dir-n">N</div>
                    <div class="dir dir-e">E</div>
                    <div class="dir dir-s">S</div>
                    <div class="dir dir-w">W</div>
                    <div class="needle" id="needle"></div>
                    <div class="compass-center"></div>
                </div>
            </div>

            <div style="text-align:center; margin-top:10px;">
                <label>Step Size:</label>
                <select id="step-size">
                    <option value="1">1°</option>
                    <option value="2">2°</option>
                    <option value="5" selected>5°</option>
                    <option value="10">10°</option>
                </select>
            </div>

            <div class="button-grid" style="margin-top:12px;">
                <button type="button" class="green" onclick="nudge(-1)">⬅️ Left</button>
                <button type="button" class="green" onclick="nudge(1)">Right ➡️</button>
            </div>

            <div style="margin-top:12px;">
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
                    <input id="manual-azimuth" type="number" min="0" max="360" placeholder="Heading">
                    <button type="button" class="blue" onclick="manualRotate()">Rotate</button>
                </div>
            </div>

            <div style="margin-top:12px;">
                <button type="button" class="red" onclick="stopRotor()">STOP</button>
            </div>
        </div>

        <div class="panel">
            <h2>SPE Amplifier</h2>

            <div class="card-small-grid">
                <div class="status-card">
                    <div class="status-label">Mode</div>
                    <div class="status-value" id="operate-value">{{ spe.operate }}</div>
                </div>
                <div class="status-card">
                    <div class="status-label">TX / RX</div>
                    <div class="status-value" id="tx-value">{{ spe.tx }}</div>
                </div>
                <div class="status-card">
                    <div class="status-label">SWR</div>
                    <div class="status-value {{ swr_color }}" id="swr-value">{{ spe.swr }}</div>
                </div>
                <div class="status-card">
                    <div class="status-label">Temp</div>
                    <div class="status-value {{ temp_color }}" id="temp-value">{{ spe.temp }} F</div>
                </div>
            </div>

            <div class="status-card" style="margin-bottom: 14px;">
                <div class="status-label">Warnings / Alarms</div>
                <div class="status-value {% if spe.warnings != 'N' or spe.alarms != 'N' %}alert{% else %}ok{% endif %}" id="warn-value">
                    W: {{ spe.warnings }} &nbsp; A: {{ spe.alarms }}
                </div>
            </div>

            <div class="button-grid">
                <button type="button" id="operate-btn" class="{% if spe.operate == 'OPERATE' %}green{% else %}red{% endif %}" onclick="sendSimpleCommand('/operate')">
                    {% if spe.operate == 'OPERATE' %}OPERATE{% else %}STANDBY{% endif %}
                </button>
                <button type="button" id="tune-btn" class="{% if spe.tx == 'TX' %}red{% else %}orange{% endif %}" onclick="sendSimpleCommand('/tune')">Tune{% if spe.tx == 'TX' %} (TUNING){% endif %}</button>
                <button type="button" class="orange" onclick="sendSimpleCommand('/power')">Power Level</button>
            </div>
        </div>
    </div>

    <div class="footer-panel{% if vh == 'UNKNOWN' %} vh-error{% endif %}" id="vh-panel">
        <h2>VirtualHere Control</h2>
        <div class="button-grid">
            <button type="button" class="blue" onclick="sendSimpleCommand('/linux')">Use Linux Control</button>
            <button type="button" class="blue" onclick="sendSimpleCommand('/windows')">Use Windows Control</button>
        </div>
        <div class="msg" id="msg">{{ msg }}</div>
    </div>

<script>
let currentAz = {{ needle_deg|tojson }};
let pollInFlight = false;

function updateUI(data) {
    if (data.error) {
        updateDot("dot-rotator", false);
        updateDot("dot-amp", false);
        return;
    }
    setText("az-value", data.az + "°");
    if (data.target_az !== undefined) setText("target-az-value", data.target_az);
    setText("band-value", data.spe.band);
    setText("power-value", data.spe.power);
    setText("fwd-watts-value", data.spe.fwd_watts);
    setText("vh-value", data.vh);
            const vhPanel = document.getElementById("vh-panel");
            if (vhPanel) vhPanel.classList.toggle("vh-error", data.vh === "UNKNOWN");
    setText("operate-value", data.spe.operate);
    const operateBtn = document.getElementById("operate-btn");
    if (operateBtn) {
        const isOperate = data.spe.operate === "OPERATE";
        operateBtn.textContent = isOperate ? "OPERATE" : "STANDBY";
        operateBtn.className = isOperate ? "green" : "red";
    }
    const txEl = document.getElementById("tx-value");
    if (txEl) {
        txEl.textContent = data.spe.tx;
        txEl.style.color = data.spe.tx === "TX" ? "#f38ba8" : "";
    }
    const tuneBtn = document.getElementById("tune-btn");
    if (tuneBtn) {
        const isTx = data.spe.tx === "TX";
        tuneBtn.textContent = isTx ? "Tune (TUNING)" : "Tune";
        tuneBtn.className = isTx ? "red" : "orange";
    }
    setText("swr-value", data.spe.swr);
    setText("temp-value", data.spe.temp + " F");
    setText("warn-value", "W: " + data.spe.warnings + "   A: " + data.spe.alarms);
    setClass("swr-value", data.swr_color);
    setClass("temp-value", data.temp_color);
    setClass("warn-value", (data.spe.warnings !== "N" || data.spe.alarms !== "N") ? "alert" : "ok");
    const azNum = parseFloat(data.az);
    if (!isNaN(azNum)) setNeedle(azNum);
    if (data.hw) {
        updateDot("dot-rotator", data.hw.rotator);
        updateDot("dot-amp", data.hw.amp);
    }
}

function normalizeAngle(angle) {
    angle = angle % 360;
    if (angle < 0) angle += 360;
    return angle;
}

function setNeedle(angle) {
    currentAz = normalizeAngle(angle);
    const needle = document.getElementById("needle");
    if (needle) {
        needle.style.transform = "translateX(-50%) rotate(" + currentAz + "deg)";
    }
}

function getStep() {
    return parseInt(document.getElementById("step-size").value);
}

function nudge(direction) {
    const step = getStep();
    let newAz = currentAz + (direction * step);
    if (newAz < 0) newAz += 360;
    if (newAz >= 360) newAz -= 360;
    sendRotate(Math.round(newAz));
}

function manualRotate() {
    const val = document.getElementById("manual-azimuth").value;
    if (val !== "") {
        sendRotate(val);
    }
}

function stopRotor() {
    fetch("/stop", { method: "POST" }).then(() => {
        showMessage("Rotator stopped");
        pollStatus();
    });
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function setClass(id, className) {
    const el = document.getElementById(id);
    if (el) {
        el.classList.remove("ok", "alert", "neutral");
        el.classList.add(className);
    }
}

function showMessage(msg) {
    const el = document.getElementById("msg");
    if (el) el.textContent = msg;
}

function updateDot(id, state) {
    const dot = document.getElementById(id);
    if (!dot) return;
    dot.classList.remove("connected", "disconnected", "windows");
    if (state === "windows") dot.classList.add("windows");
    else dot.classList.add(state ? "connected" : "disconnected");
}

function rotateFromClick(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;

    const dx = event.clientX - cx;
    const dy = cy - event.clientY;

    let angle = Math.atan2(dx, dy) * (180 / Math.PI);
    if (angle < 0) angle += 360;

    sendRotate(Math.round(angle));
}

function sendRotate(angle) {
    currentAz = normalizeAngle(parseFloat(angle));
    setNeedle(currentAz);
    showMessage("Rotating to " + angle + "°");

    fetch("/rotate", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "azimuth=" + encodeURIComponent(angle)
    }).then(() => {
        pollStatus();
    });
}

function sendSimpleCommand(path) {
    fetch(path, { method: "POST" }).then(() => {
        pollStatus();
    });
}

function pollStatus() {
    if (pollInFlight) return;
    pollInFlight = true;
    fetch("/status")
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(data => updateUI(data))
        .catch(() => { updateDot("dot-rotator", false); updateDot("dot-amp", false); })
        .finally(() => { pollInFlight = false; });
}

setNeedle(currentAz);

const evtSource = new EventSource("/stream");
evtSource.onmessage = function(e) {
    try { updateUI(JSON.parse(e.data)); } catch(_) {}
};
evtSource.onerror = function() {
    updateDot("dot-rotator", false);
    updateDot("dot-amp", false);
};
</script>
</body>
</html>
"""



def rotate_to(azimuth):
    cmd = ("M" + str(int(float(azimuth))).zfill(3) + "\r\n").encode()
    _rot_cmd_queue.put(cmd)

def stop_rotator():
    _rot_cmd_queue.put(b"S\r\n")

def spe_send(cmd):
    _spe_cmd_queue.put(cmd)

# ----------- RENDER -----------
def render_page(msg=""):
    data = get_status_payload()
    return render_template_string(
        HTML,
        az=data["az"],
        spe=data["spe"],
        vh=data["vh"],
        msg=msg,
        needle_deg=data["needle_deg"],
        swr_color=data["swr_color"],
        temp_color=data["temp_color"],
        hw=data["hw"]
    )


# ----------- ROUTES -----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == USERNAME and request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("home"))
        return render_template_string(LOGIN_HTML, msg="Invalid username or password")
    return render_template_string(LOGIN_HTML, msg="")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def home():
    auth = require_login()
    if auth:
        return auth
    return render_page("")

@app.route("/status")
def status():
    auth = require_login()
    if auth:
        return jsonify({"error": "not_logged_in"}), 401
    return jsonify(get_status_payload())

@app.route("/stream")
def stream():
    if not is_logged_in():
        return jsonify({"error": "not_logged_in"}), 401
    def generate():
        while True:
            try:
                data = get_status_payload()
                yield f"data: {json.dumps(data)}\n\n"
                time.sleep(.025)
            except GeneratorExit:
                break
            except Exception:
                yield f"data: {json.dumps({'error': 'status_error'})}\n\n"
                time.sleep(.025)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/rotate", methods=["POST"])
def rotate():
    auth = require_login()
    if auth:
        return auth
    az = request.form["azimuth"]
    rotate_to(az)
    global _target_az
    with _target_lock:
        _target_az = float(az)
    return ("", 204)

@app.route("/stop", methods=["POST"])
def stop():
    auth = require_login()
    if auth:
        return auth
    stop_rotator()
    return ("", 204)

@app.route("/operate", methods=["POST"])
def operate():
    auth = require_login()
    if auth:
        return auth
    spe_send(bytes([0x55, 0x55, 0x55, 0x01, 0x0D, 0x0D]))
    return ("", 204)

@app.route("/tune", methods=["POST"])
def tune():
    auth = require_login()
    if auth:
        return auth
    spe_send(bytes([0x55, 0x55, 0x55, 0x01, 0x09, 0x09]))
    return ("", 204)

@app.route("/power", methods=["POST"])
def power():
    auth = require_login()
    if auth:
        return auth
    spe_send(bytes([0x55, 0x55, 0x55, 0x01, 0x0B, 0x0B]))
    return ("", 204)

@app.route("/linux", methods=["POST"])
def linux():
    auth = require_login()
    if auth:
        return auth
    import glob as _glob
    import time as _time

    subprocess.run(["sudo", "systemctl", "stop", "virtualhere"])
    _time.sleep(2)

    ftdi_driver = "/sys/bus/usb/drivers/ftdi_sio"

    # Find all FTDI USB devices (vendor 0403) connected to the Pi
    ftdi_ifaces = []
    for vendor_file in _glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            with open(vendor_file) as f:
                if f.read().strip() == "0403":
                    dev_name = os.path.basename(os.path.dirname(vendor_file))
                    ftdi_ifaces.append(f"{dev_name}:1.0")
        except Exception:
            pass

    # Unbind any currently bound FTDI interfaces
    for iface in ftdi_ifaces:
        if os.path.exists(f"{ftdi_driver}/{iface}"):
            subprocess.run(["sudo", "tee", f"{ftdi_driver}/unbind"],
                           input=iface, text=True, capture_output=True)

    _time.sleep(1)

    # Rebind all FTDI interfaces to the driver
    for iface in ftdi_ifaces:
        subprocess.run(["sudo", "tee", f"{ftdi_driver}/bind"],
                       input=iface, text=True, capture_output=True)

    return ("", 204)

@app.route("/windows", methods=["POST"])
def windows():
    auth = require_login()
    if auth:
        return auth
    subprocess.run(["sudo", "systemctl", "start", "virtualhere"])
    return ("", 204)


@app.route("/spe-raw")
def spe_raw():
    try:
        ser = serial.Serial(SPE_PORT, SPE_BAUD, timeout=2)
        time.sleep(0.25)
        ser.write(bytes([0x55, 0x55, 0x55, 0x01, 0x90, 0x90]))
        time.sleep(0.35)
        data = ser.read(200)
        ser.close()
        text = data.decode("ascii", errors="ignore")
        idx = text.find("C,")
        if idx == -1:
            return jsonify({"error": "No C, found", "raw": text})
        text = text[idx:]
        parts = [p.strip() for p in text.strip().split(",")]
        return jsonify({str(i): v for i, v in enumerate(parts)})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
