#!/bin/bash

# ─── Ham Radio Control Center ─────────────────────────────────────────────────
FLEX_IP="192.168.1.29"
PI_IP="100.76.124.28"
AETHER_072="$HOME/apps/aethersdr-new/AetherSDR-v0.7.2-x86_64.AppImage"
AETHER_061="$HOME/apps/aethersdr-new/AetherSDR-v0.6.1-x86_64.AppImage"
AETHER_053="$HOME/Pictures/AetherSDR-v0.5.3-x86_64.AppImage"
FLEXSPOTS="$HOME/hamradio-linux/flexspots.py"
BRIDGE="$HOME/flex-to-log4om.py"

CHOICES=$(zenity --list --checklist \
  --title="Ham Radio Control Center" \
  --text="Select programs to launch:" \
  --column="Pick" --column="Program" \
  FALSE "🚀 Full Station Startup" \
  FALSE "📡 AetherSDR v0.7.2" \
  FALSE "📡 AetherSDR v0.6.1" \
  FALSE "📡 AetherSDR v0.5.3" \
  FALSE "🎯 FlexSpots for Linux" \
  FALSE "📝 Flex to Log4OM Bridge" \
  FALSE "📋 CQRLOG" \
  FALSE "📋 Startup (CQRLOG & QRZ Uploader)" \
  --height=450 --width=500 2>/dev/null)

[ -z "$CHOICES" ] && exit 0

# ─── Helper: ensure SSH tunnel is running ─────────────────────────────────────
start_tunnel() {
  if ! nc -z 127.0.0.1 4992 2>/dev/null; then
    ssh -f -L 4992:${FLEX_IP}:4992 pi@${PI_IP} -N 2>/dev/null
    sleep 2
    zenity --info --text="SSH tunnel started to Flex radio" --timeout=2 2>/dev/null &
  fi
}

# ─── Full Station Startup ─────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "Full Station Startup"; then
  # Start tunnel
  start_tunnel

  # Start FlexSpots
  if ! pgrep -f flexspots.py > /dev/null; then
    nohup python3 "$FLEXSPOTS" >/dev/null 2>&1 &
    sleep 2
  fi

  # Start bridge
  if ! pgrep -f flex-to-log4om.py > /dev/null; then
    nohup python3 "$BRIDGE" >/dev/null 2>&1 &
    sleep 1
  fi

  # Start AetherSDR v0.7.2
  if ! pgrep -f AetherSDR-v0.7.2 > /dev/null; then
    nohup env XDG_CONFIG_HOME="$HOME/.config/AetherSDR-new-072" \
    "$AETHER_072" >/dev/null 2>&1 &
  fi

  zenity --info --text="✅ Full station startup complete!\n\nStarted:\n• SSH Tunnel\n• FlexSpots\n• Flex to Log4OM Bridge\n• AetherSDR v0.7.2" --timeout=4 2>/dev/null &
  exit 0
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

# ─── AetherSDR v0.6.1 ────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "AetherSDR v0.6.1"; then
  nohup env XDG_CONFIG_HOME="$HOME/.config/AetherSDR-new" \
  "$AETHER_061" >/dev/null 2>&1 &
fi

# ─── AetherSDR v0.5.3 ────────────────────────────────────────────────────────
if echo "$CHOICES" | grep -q "AetherSDR v0.5.3"; then
  if pgrep -f AetherSDR-v0.5.3 > /dev/null; then
    wmctrl -a AetherSDR 2>/dev/null
  else
    "$AETHER_053" &
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

wait
