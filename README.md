# Ham Radio Linux Tools

A collection of Linux-native tools for FlexRadio operators.
Built and tested on Linux Mint 22 / Ubuntu 24.04 with a FLEX-8600.

## What's Included

- **flexspots.py** - DX Cluster spot client. Connects to any DX cluster and pushes spots to your FlexRadio panadapter
- **cqrlog_qrz.py** - CQRLOG to QRZ Uploader. Automatically uploads every QSO logged in CQRLOG to QRZ.com within seconds
- **launch-hamradio.sh** - Launcher script. Opens CQRLOG and the QRZ Uploader in the correct order
- **Ham-Radio.desktop** - Desktop icon for the launcher

## Requirements

- FlexRadio FLEX-6000 or FLEX-8000 series transceiver
- Linux Mint 22, Ubuntu 24.04, or similar Debian-based distro
- Python 3.10+
- QRZ.com Premium/XML subscription
- HamQTH.com account (free at hamqth.com)

## Installation

Install dependencies:

    sudo apt install python3-pyqt6 cqrlog mariadb-server -y
    pip3 install mysql-connector-python requests --break-system-packages
    sudo systemctl start mariadb
    sudo systemctl enable mariadb

Clone this repository:

    git clone https://github.com/N4ASA/HamRadio-Linux.git
    cd HamRadio-Linux

Set up application folders:

    mkdir -p ~/flexspots ~/cqrlog-qrz
    cp flexspots.py ~/flexspots/
    cp cqrlog_qrz.py ~/cqrlog-qrz/
    cp launch-hamradio.sh ~/
    chmod +x ~/launch-hamradio.sh
    cp Ham-Radio.desktop ~/Desktop/
    chmod +x ~/Desktop/Ham-Radio.desktop

## FlexSpots

Connects to a DX Cluster via Telnet and pushes spots to your FlexRadio panadapter
using the SmartSDR TCP Spots API on port 4992. Spots appear as clickable callsigns
on the panadapter - clicking a spot tunes your active slice to that frequency.

### Running on a Raspberry Pi

If you operate remotely via SmartLink, run FlexSpots on a Raspberry Pi at your
radio site so it can reach the radio on the local network.

Install on the Pi:

    sudo apt install python3-pyqt6 libxcb-cursor0 -y
    mkdir -p ~/flexspots
    python3 ~/flexspots/flexspots.py

Auto-start on Pi boot:

    mkdir -p ~/.config/autostart
    cat > ~/.config/autostart/flexspots.desktop << EOF
    [Desktop Entry]
    Name=FlexSpots for Linux
    Exec=python3 /home/pi/flexspots/flexspots.py
    Type=Application
    X-GNOME-Autostart-enabled=true
    EOF

### Configuration

1. Click Settings in FlexSpots
2. Cluster tab - enter your callsign, host dxc.w3lpl.net, port 7373, check Auto-connect
3. Radio tab - enter your FlexRadio local IP e.g. 192.168.1.29, check Auto-connect
4. Click OK then Connect on both groups

### Recommended DX Clusters

- W3LPL: dxc.w3lpl.net port 7373
- K3LR: dxc.k3lr.com port 7373
- GB7MBC: gb7mbc.sp5bot.net port 7373

## CQRLOG Setup

First launch:

    cqrlog

When asked if you want to save data to the local machine, click Yes.

### Automatic Callsign Lookup

1. Go to File, Preferences, Callbook support
2. Select HamQTH.com and enter your username and password
3. Go to File, Preferences, New QSO
4. Check Enable auto search on HamQTH.com/QRZ.com
5. Click OK

Now when you type a callsign and press Tab, CQRLOG fills in name, QTH, grid and more.

## QRZ Uploader

Watches CQRLOG's database every 5 seconds and automatically uploads new QSOs to QRZ.com.
Runs in the system tray and retries failed uploads automatically.

### Get Your QRZ API Key

1. Log into QRZ.com
2. Go to My Account then XML Data
3. Copy your API key

### First Run

    python3 ~/cqrlog-qrz/cqrlog_qrz.py

1. Click Settings
2. QRZ tab - enter your callsign and API key
3. Database tab - Host localhost, Port 64000, Username cqrlog, Password cqrlog, Database cqrlog001
4. Click OK then Test QRZ then Start

IMPORTANT: CQRLOG must be open before starting the uploader.
Use the Ham Radio desktop launcher to open both in the correct order.

## Ham Radio Desktop Launcher

Double-click Ham Radio on your desktop to open CQRLOG and the QRZ Uploader
automatically in the correct order.

## Troubleshooting

FlexSpots cannot connect to radio:
- Make sure the Pi is on the same local network as the FlexRadio
- Verify the radio IP with ping 192.168.1.x
- FlexRadio must be powered on and running SmartSDR

QRZ Uploader shows DB Error:
- CQRLOG must be open before the uploader starts
- Use the Ham Radio desktop launcher

CQRLOG callsign lookup not working:
- Go to File, Preferences, New QSO
- Make sure Enable auto search is checked
- Verify your HamQTH credentials at hamqth.com

## License

GPL v3 - free and open source. Contributions welcome!

73 de N4ASA
