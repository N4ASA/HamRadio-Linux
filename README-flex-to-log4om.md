# Flex to Log4OM Bridge

Point-and-click QSO logging for FlexRadio users on Linux.

## What it does

1. Monitors your Flex 8400 (or other Flex radio) for frequency changes
2. When you click a DX spot on the AetherSDR panadapter, a confirmation dialog appears
3. The dialog shows the callsign, frequency, mode, and QRZ data (name, country, grid, CQ zone)
4. You enter RST reports and click Log QSO
5. The QSO is written to your Log4OM SQLite database and synced to Google Drive
6. The worked spot changes color on the panadapter so you know it's been logged
7. Log4OM on Windows picks up the new entry automatically

## Requirements

### Hardware
- FlexRadio 6000 or 8000 series transceiver
- Raspberry Pi (or similar Linux device) on the same network as the Flex
- Raspberry Pi must be accessible via Tailscale from your Linux machine

### Software
- Linux Mint / Ubuntu 22.04 or later
- AetherSDR (https://github.com/ten9876/AetherSDR)
- FlexSpots for Linux (https://github.com/N4ASA/HamRadio-Linux)
- Log4OM Next Generation (Windows, with SQLite database on Google Drive)
- QRZ.com Premium membership (for XML API callsign lookup)

## Installation

### Step 1 - Install dependencies
```bash
sudo apt install python3-pyqt6 rclone sqlite3 -y
```

### Step 2 - Download the script
```bash
cd ~
wget https://raw.githubusercontent.com/N4ASA/HamRadio-Linux/master/flex-to-log4om.py
wget https://raw.githubusercontent.com/N4ASA/HamRadio-Linux/master/flex-to-log4om.conf.example
```

### Step 3 - Create your config file
```bash
cp ~/flex-to-log4om.conf.example ~/.flex-to-log4om.conf
nano ~/.flex-to-log4om.conf
```

Edit the following fields:
- `MY_CALLSIGN` - your callsign
- `MY_GRID` - your grid square (e.g. FN43)
- `MY_LAT` / `MY_LON` - your latitude and longitude
- `MY_CITY` - your city
- `QRZ_USERNAME` / `QRZ_PASSWORD` - your QRZ.com login
- `DB_LOCAL` - path to your Log4OM SQLite database
- `FLEX_IP` - local IP of your Flex radio
- `PI_IP` - Tailscale IP of your Raspberry Pi

### Step 4 - Set up Google Drive with rclone
```bash
rclone config
```

- Choose `n` for new remote
- Name it `Google_Drive`
- Choose `18` for Google Drive
- Follow the prompts to authenticate

### Step 5 - Download your Log4OM database
```bash
rclone copy "Google_Drive:Log4OM/YourDatabase.SQLite" ~/
```

Update `DB_LOCAL` in your config to point to this file.

### Step 6 - Set up SSH tunnel to your Flex radio

Add this to your `~/.bashrc` or create a startup script:
```bash
ssh -f -L 4992:YOUR_FLEX_IP:4992 pi@YOUR_PI_TAILSCALE_IP -N
```

Replace `YOUR_FLEX_IP` with your Flex radio's local IP (e.g. 192.168.1.29)
Replace `YOUR_PI_TAILSCALE_IP` with your Pi's Tailscale IP (e.g. 100.76.124.28)

### Step 7 - Start FlexSpots for Linux

Make sure FlexSpots is running and connected to your Flex radio so spots appear on the panadapter.

### Step 8 - Run the bridge
```bash
python3 ~/flex-to-log4om.py
```

## Usage

1. Start AetherSDR and connect to your Flex radio
2. Start FlexSpots for Linux and connect to DX clusters
3. Run `python3 ~/flex-to-log4om.py`
4. Click any spot on the AetherSDR panadapter
5. A dialog will appear showing the callsign and QRZ data
6. Enter RST reports and click **✓ Log QSO**
7. The QSO is logged and synced to Google Drive automatically
8. The spot turns green on the panadapter to show it's been worked

## Multiple spots

If several spots are close together, a picker dialog appears listing all nearby
stations. Click the one you want to log.

## Worked before indicator

When the script starts, it checks your Log4OM database for QSOs made in the
last 24 hours and automatically recolors those spots green on the panadapter.

## Configuration options

| Option | Description | Default |
|--------|-------------|---------|
| MY_CALLSIGN | Your callsign | N4ASA |
| MY_GRID | Your grid square | FN43 |
| MY_LAT | Your latitude | 44.0 |
| MY_LON | Your longitude | -70.0 |
| MY_CITY | Your city | |
| MY_COUNTRY | Your country | United States |
| QRZ_USERNAME | QRZ.com username | |
| QRZ_PASSWORD | QRZ.com password | |
| DB_LOCAL | Path to Log4OM SQLite database | |
| GDRIVE_REMOTE | rclone remote and folder | Google_Drive:Log4OM/ |
| FLEX_IP | Flex radio local IP | 192.168.1.29 |
| PI_IP | Raspberry Pi Tailscale IP | |
| WORKED_COLOR | Color for worked spots | #00FF7F |

## Credits

Developed by N4ASA with assistance from Claude (Anthropic AI).
