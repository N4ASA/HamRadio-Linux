#!/bin/bash

# ─── Ham Radio Control Center ─────────────────────────────────────────────────
# Set LOCAL_MODE=true when operating from Maine (no SSH tunnel needed)
LOCAL_MODE=true

FLEX_IP="192.168.1.29"
PI_IP="192.168.1.38"
PI_WEB="http://${PI_IP}:5000"
AETHER="$HOME/Applications/AetherSDR.AppImage"

FLEXSPOTS="$HOME/hamradio-linux/flexspots.py"
BRIDGE="$HOME/hamradio-linux/flex-to-log4om.py"

CHOICES=$(zenity --list --checklist \
  --title="Ham Radio Control Center" \
  --text="Select programs to launch:" \
  --column="Pick" --column="Program" \
  FALSE "🚀 Full Station Startup" \
  FALSE "📡 AetherSDR" \
  FALSE "🎯 FlexSpots for Linux" \
  FALSE "📝 Flex to Log4OM Bridge" \
  FALSE "📋 CQRLOG" \
  FALSE "📋 Startup (CQRLOG & QRZ Uploader)" \
  FALSE "🖥️ Start Pi Web Control" \
  FALSE "🌐 Web Amp/Rotor Control" \
  FALSE "⛔ Shut Down All" \
  --height=550 --width=500 2>/dev/null)

[ -z "$CHOICES" ] && exit 0

# ─── Helper: ensure SSH tunnel is running ─────────────────────────────────────
start_tunnel() {
  $LOCAL_MODE && return  # No tunnel needed when operating locally
  if ! nc -z 127.0.0.1 4992 2>/dev/null; then
    ssh -f -i ~/.ssh/ham_radio -L 4992:${FLEX_IP}:4992 pi@${PI_IP} -N -o StrictHostKeyChecking=no
    sleep 2
    zenity --info --text="SSH tunnel started to Flex radio" --timeout=2 2>/dev/null &
  fi
}

# ─── Helper: stop SSH tunnel ──────────────────────────────────────────────────
stop_tunnel() {
  $LOCAL_MODE && return  # No tunnel to stop when operating locally
  pkill -f "ssh.*4992:${FLEX_IP}:4992" 2>/dev/null
}

# ─── Helper: hand control back to Windows via Pi web API ──────────────────────
hand_to_windows() {
  curl -s -X POST "${PI_WEB}/windows" >/dev/null 2>&1 || true
}

# ─── Helper: open PI_WEB in a browser window ─────────────────────────────────
open_pi_web() {
  google-chrome --new-window "${PI_WEB}" >/dev/null 2>&1 &
}

close_pi_web() {
  for wid in $(xdotool search --name "Ham Radio" 2>/dev/null); do
    xdotool windowclose "$wid" 2>/dev/null
  done
}

# ─── Full Station Startup ─────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Full Station Startup"; then
  start_tunnel

  # Start Pi web control via systemd (reliable across SSH disconnect)
  ssh -i ~/.ssh/ham_radio -o ConnectTimeout=5 -o BatchMode=yes pi@${PI_IP} \
    "sudo systemctl stop virtualhere 2>/dev/null; sudo systemctl start ham-web-control" \
    >/dev/null 2>&1

  if ! pgrep -f flex-to-log4om.py > /dev/null; then
    nohup python3 "$BRIDGE" >/dev/null 2>&1 &
  fi

  if ! pgrep -f AetherSDR.AppImage > /dev/null; then
    nohup "$AETHER" >/dev/null 2>&1 &
  fi

  "$HOME/hamradio-linux/launch-hamradio.sh" &

  # Wait until app.py is actually responding (up to 15 seconds)
  for i in $(seq 1 15); do
    if curl -s --max-time 1 "${PI_WEB}" >/dev/null 2>&1; then break; fi
    sleep 1
  done

  close_pi_web
  open_pi_web
  zenity --info --text="✅ Full station startup complete!\n\nStarted:\n• Flex to Log4OM Bridge\n• AetherSDR\n• CQRLOG + QRZ Uploader\n• Web Amp/Rotor Control" --timeout=4 2>/dev/null &
  exit 0
fi

# ─── AetherSDR ───────────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "AetherSDR"; then
  if pgrep -f AetherSDR.AppImage > /dev/null; then
    wmctrl -a AetherSDR 2>/dev/null
  else
    nohup "$AETHER" >/dev/null 2>&1 &
  fi
fi


# ─── FlexSpots ───────────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "FlexSpots for Linux"; then
  if pgrep -f flexspots.py > /dev/null; then
    wmctrl -a FlexSpots 2>/dev/null
  else
    start_tunnel
    nohup python3 "$FLEXSPOTS" >/dev/null 2>&1 &
  fi
fi

# ─── Flex to Log4OM Bridge ───────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Flex to Log4OM Bridge"; then
  if pgrep -f flex-to-log4om.py > /dev/null; then
    zenity --info --text="Bridge is already running" --timeout=2 2>/dev/null &
  else
    start_tunnel
    nohup python3 "$BRIDGE" >/dev/null 2>&1 &
    zenity --info --text="✅ Flex to Log4OM Bridge started" --timeout=2 2>/dev/null &
  fi
fi

# ─── CQRLOG ──────────────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "^CQRLOG$"; then
  if pgrep -x cqrlog > /dev/null; then
    wmctrl -a CQRLOG 2>/dev/null
  else
    cqrlog &
  fi
fi

# ─── Startup (CQRLOG & QRZ Uploader) ─────────────────────────────────────────
if echo "$CHOICES" | grep -q "Startup (CQRLOG & QRZ Uploader)"; then
  if pgrep -x cqrlog > /dev/null; then
    wmctrl -a "CQRLOG" 2>/dev/null
  else
    "$HOME/hamradio-linux/launch-hamradio.sh" &
  fi
fi

# ─── Start Pi Web Control ────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Start Pi Web Control"; then
  ssh -i ~/.ssh/ham_radio -o ConnectTimeout=5 -o BatchMode=yes pi@${PI_IP} \
    "sudo systemctl start ham-web-control" \
    >/dev/null 2>&1 &
  zenity --info --text="✅ Pi Web Control starting..." --timeout=2 2>/dev/null &
fi

# ─── Web Amp/Rotor Control ───────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Web Amp/Rotor Control"; then
  open_pi_web
fi

# ─── Shut Down All ───────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Shut Down All"; then
  zenity --question \
    --title="Shut Down All" \
    --text="This will stop all ham radio processes and hand control back to Windows.\n\nAre you sure?" \
    --ok-label="Yes, Shut Down" \
    --cancel-label="Cancel" 2>/dev/null || exit 0

  # Kill all local ham radio processes first
  pkill -f AetherSDR        2>/dev/null
  pkill -f flexspots.py     2>/dev/null
  pkill -f flex-to-log4om   2>/dev/null
  pkill -f cqrlog_qrz.py    2>/dev/null
  pkill -x cqrlog           2>/dev/null
  pkill -f launch-hamradio  2>/dev/null

  # Close Pi web browser tab
  close_pi_web

  # Hand USB control back to Windows (3s timeout)
  curl -s --max-time 3 -X POST "${PI_WEB}/windows" >/dev/null 2>&1 || true

  # Stop app.py on Pi via systemd
  ssh -i ~/.ssh/ham_radio -o ConnectTimeout=5 pi@${PI_IP} "sudo systemctl stop ham-web-control" 2>/dev/null || true

  stop_tunnel

  zenity --info --text="✅ All processes stopped.\nControl handed back to Windows." --timeout=3 2>/dev/null &
  exit 0
fi

wait
