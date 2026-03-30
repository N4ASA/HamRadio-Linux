#!/bin/bash

# ─── Ham Radio Control Center ─────────────────────────────────────────────────
FLEX_IP="192.168.1.29"
PI_IP="100.76.124.28"
PI_WEB="http://${PI_IP}:5000"
AETHER_0710="$HOME/apps/aethersdr-new/AetherSDR-v0.7.10-x86_64.AppImage"
AETHER_0711="$HOME/apps/aethersdr-new/AetherSDR-v0.7.11-x86_64.AppImage"
AETHER_072="$HOME/apps/aethersdr-new/AetherSDR-v0.7.2-x86_64.AppImage"
FLEXSPOTS="$HOME/hamradio-linux/flexspots.py"
BRIDGE="$HOME/flex-to-log4om.py"

CHOICES=$(zenity --list --checklist \
  --title="Ham Radio Control Center" \
  --text="Select programs to launch:" \
  --column="Pick" --column="Program" \
  FALSE "🚀 Full Station Startup" \
  FALSE "📡 AetherSDR v0.7.11" \
  FALSE "📡 AetherSDR v0.7.10" \
  FALSE "📡 AetherSDR v0.7.2" \
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
  if ! nc -z 127.0.0.1 4992 2>/dev/null; then
    ssh -f -i ~/.ssh/ham_radio -L 4992:${FLEX_IP}:4992 pi@${PI_IP} -N -o StrictHostKeyChecking=no
    sleep 2
    zenity --info --text="SSH tunnel started to Flex radio" --timeout=2 2>/dev/null &
  fi
}

# ─── Helper: stop SSH tunnel ──────────────────────────────────────────────────
stop_tunnel() {
  pkill -f "ssh.*4992:${FLEX_IP}:4992" 2>/dev/null
}

# ─── Helper: hand control back to Windows via Pi web API ──────────────────────
hand_to_windows() {
  curl -s -X POST "${PI_WEB}/windows" >/dev/null 2>&1 || true
}

# ─── Full Station Startup ─────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Full Station Startup"; then
  start_tunnel

  ssh -i ~/.ssh/ham_radio pi@${PI_IP} \
    "pgrep -f app.py > /dev/null || nohup python3 ~/ham-web-control/app.py > /tmp/app.log 2>&1 &"

  if ! pgrep -f flex-to-log4om.py > /dev/null; then
    nohup python3 "$BRIDGE" >/dev/null 2>&1 &
    sleep 1
  fi

  if ! pgrep -f AetherSDR-v0.7.11 > /dev/null; then
    nohup env XDG_CONFIG_HOME="$HOME/.config/AetherSDR-new-0711" \
    "$AETHER_0711" >/dev/null 2>&1 &
  fi

  xdg-open "${PI_WEB}" 2>/dev/null &
  zenity --info --text="✅ Full station startup complete!\n\nStarted:\n• SSH Tunnel\n• Flex to Log4OM Bridge\n• AetherSDR v0.7.11\n• Web Amp/Rotor Control" --timeout=4 2>/dev/null &
  exit 0
fi

# ─── AetherSDR v0.7.11 ───────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "AetherSDR v0.7.11"; then
  if pgrep -f AetherSDR-v0.7.11 > /dev/null; then
    wmctrl -x -a AetherSDR-v0.7.11-x86_64.AppImage.AetherSDR 2>/dev/null
  else
    nohup env XDG_CONFIG_HOME="$HOME/.config/AetherSDR-new-0711" \
    "$AETHER_0711" >/dev/null 2>&1 &
  fi
fi

# ─── AetherSDR v0.7.10 ───────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "AetherSDR v0.7.10"; then
  if pgrep -f AetherSDR-v0.7.10 > /dev/null; then
    wmctrl -x -a AetherSDR-v0.7.10-x86_64.AppImage.AetherSDR 2>/dev/null
  else
    nohup env XDG_CONFIG_HOME="$HOME/.config/AetherSDR-new-0710" \
    "$AETHER_0710" >/dev/null 2>&1 &
  fi
fi

# ─── AetherSDR v0.7.2 ────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "AetherSDR v0.7.2"; then
  if pgrep -f AetherSDR-v0.7.2 > /dev/null; then
    wmctrl -x -a AetherSDR-v0.7.2-x86_64.AppImage.AetherSDR 2>/dev/null
  else
    nohup env XDG_CONFIG_HOME="$HOME/.config/AetherSDR-new-072" \
    "$AETHER_072" >/dev/null 2>&1 &
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
  if pgrep -f launch-hamradio.sh > /dev/null; then
    wmctrl -a CQRLOG 2>/dev/null
  else
    /home/dparker100/launch-hamradio.sh &
  fi
fi

# ─── Start Pi Web Control ────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Start Pi Web Control"; then
  if ssh -i ~/.ssh/ham_radio pi@${PI_IP} "pgrep -f app.py > /dev/null"; then
    zenity --info --text="ℹ️ Pi Web Control is already running." --timeout=2 2>/dev/null &
  else
    ssh -i ~/.ssh/ham_radio pi@${PI_IP} \
      "nohup python3 ~/ham-web-control/app.py > /tmp/app.log 2>&1 &"
    sleep 1
    zenity --info --text="✅ Pi Web Control started on port 5000." --timeout=2 2>/dev/null &
  fi
fi

# ─── Web Amp/Rotor Control ───────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Web Amp/Rotor Control"; then
  xdg-open "${PI_WEB}" 2>/dev/null &
fi

# ─── Shut Down All ───────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Shut Down All"; then
  zenity --question \
    --title="Shut Down All" \
    --text="This will stop all ham radio processes and hand control back to Windows.\n\nAre you sure?" \
    --ok-label="Yes, Shut Down" \
    --cancel-label="Cancel" 2>/dev/null || exit 0

  # Hand USB control back to Windows before killing anything
  hand_to_windows
  sleep 1

  # Stop app.py on Pi
  ssh -i ~/.ssh/ham_radio pi@${PI_IP} "pkill -f app.py" 2>/dev/null

  # Kill all ham radio processes
  pkill -f AetherSDR        2>/dev/null
  pkill -f flexspots.py     2>/dev/null
  pkill -f flex-to-log4om   2>/dev/null
  pkill -f cqrlog_qrz.py    2>/dev/null
  pkill -x cqrlog           2>/dev/null
  pkill -f launch-hamradio  2>/dev/null

  # Stop SSH tunnel last
  sleep 1
  stop_tunnel

  zenity --info --text="✅ All processes stopped.\nControl handed back to Windows." --timeout=3 2>/dev/null &
  exit 0
fi

wait
