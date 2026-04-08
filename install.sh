#!/bin/bash
# ─── Ham Radio Control Center - New Machine Setup ─────────────────────────────
# Run this script on a fresh Linux Mint installation.
# Usage: bash install.sh

set -e

REPO_URL="https://github.com/N4ASA/HamRadio-Linux.git"
AETHER_VERSION="0.8.6"
AETHER_APPIMAGE="AetherSDR-v${AETHER_VERSION}-x86_64.AppImage"
AETHER_PATH="$HOME/apps/aethersdr-new/$AETHER_APPIMAGE"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
section() { echo -e "\n${GREEN}══════════════════════════════════════════${NC}"; \
            echo -e "  $1"; \
            echo -e "${GREEN}══════════════════════════════════════════${NC}"; }

section "Ham Radio Control Center - New Machine Setup"
echo "  This will set up your complete ham radio environment."
echo ""

# ── 1. System Packages ────────────────────────────────────────────────────────
section "1/9  Installing system packages"
sudo apt update -qq
sudo apt install -y \
    python3-pip \
    wmctrl \
    zenity \
    openssh-client \
    firefox \
    netcat-openbsd \
    curl \
    git
info "System packages installed"

# ── 2. Python Packages ────────────────────────────────────────────────────────
section "2/9  Installing Python packages"
pip3 install --user PyQt6 requests mysql-connector-python
info "Python packages installed"

# ── 3. Clone Repository ───────────────────────────────────────────────────────
section "3/9  Cloning hamradio-linux repository"
if [ -d "$HOME/hamradio-linux/.git" ]; then
    warn "~/hamradio-linux already exists — pulling latest..."
    git -C "$HOME/hamradio-linux" pull
else
    git clone "$REPO_URL" "$HOME/hamradio-linux"
fi
chmod +x "$HOME/hamradio-linux/aether-menu.sh"
chmod +x "$HOME/hamradio-linux/launch-hamradio.sh"
info "Repository ready at ~/hamradio-linux"

# ── 4. Directory Structure ────────────────────────────────────────────────────
section "4/9  Creating directory structure"
mkdir -p "$HOME/apps/aethersdr-new"
mkdir -p "$HOME/.config/AetherSDR-new-0820/AetherSDR"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
info "Directories created"

# ── 5. AetherSDR AppImage ─────────────────────────────────────────────────────
section "5/9  AetherSDR v${AETHER_VERSION}"
if [ -f "$AETHER_PATH" ]; then
    info "AetherSDR AppImage already present"
else
    warn "AetherSDR AppImage not found at: $AETHER_PATH"
    echo ""
    echo "  Download $AETHER_APPIMAGE from the AetherSDR GitHub releases page"
    echo "  and place it at:"
    echo "    $AETHER_PATH"
    echo ""
    echo "  Or copy it from your old machine:"
    echo "    scp OLD_MACHINE_IP:~/apps/aethersdr-new/$AETHER_APPIMAGE $AETHER_PATH"
    echo ""
    read -rp "  Press Enter once the file is in place (or Ctrl+C to skip)..."
fi

if [ -f "$AETHER_PATH" ]; then
    chmod +x "$AETHER_PATH"
    info "AetherSDR AppImage ready"
fi

# Extract icon from AppImage if needed
ICON_PATH="$HOME/apps/aethersdr-new/aethersdr.png"
if [ ! -f "$ICON_PATH" ] && [ -f "$AETHER_PATH" ]; then
    cd "$HOME/apps/aethersdr-new"
    "$AETHER_PATH" --appimage-extract "aethersdr.png" 2>/dev/null && \
        mv squashfs-root/aethersdr.png "$ICON_PATH" && \
        rm -rf squashfs-root 2>/dev/null || true
    cd "$HOME"
    [ -f "$ICON_PATH" ] && info "AetherSDR icon extracted"
fi

# ── 6. AetherSDR Settings ─────────────────────────────────────────────────────
section "6/9  AetherSDR settings"
SETTINGS_FILE="$HOME/.config/AetherSDR-new-0820/AetherSDR/AetherSDR.settings"
if [ -f "$SETTINGS_FILE" ]; then
    info "AetherSDR settings already present"
else
    warn "No AetherSDR settings found."
    echo ""
    echo "  Copy your settings from your old machine:"
    echo "    scp OLD_MACHINE_IP:~/.config/AetherSDR-new-0820/AetherSDR/AetherSDR.settings \\"
    echo "        $SETTINGS_FILE"
    echo ""
    echo "  (AetherSDR will create default settings on first launch if you skip this)"
    read -rp "  Press Enter to continue..."
fi

# ── 7. SSH Key for Pi ─────────────────────────────────────────────────────────
section "7/9  SSH key setup (Pi access)"
SSH_KEY="$HOME/.ssh/ham_radio"
if [ -f "$SSH_KEY" ]; then
    info "SSH key already present"
else
    warn "SSH key ~/.ssh/ham_radio not found."
    echo ""
    echo "  Copy your private key from your old machine:"
    echo "    scp OLD_MACHINE_IP:~/.ssh/ham_radio $SSH_KEY"
    echo "    chmod 600 $SSH_KEY"
    echo ""
    read -rp "  Press Enter once the key is in place (or Ctrl+C to skip)..."
fi

if [ -f "$SSH_KEY" ]; then
    chmod 600 "$SSH_KEY"
    info "SSH key permissions set"
fi

# ── 8. Tailscale ──────────────────────────────────────────────────────────────
section "8/9  Tailscale"
if command -v tailscale &>/dev/null; then
    info "Tailscale already installed"
else
    curl -fsSL https://tailscale.com/install.sh | sh
    info "Tailscale installed"
fi
warn "Remember to connect Tailscale after setup: sudo tailscale up"

# ── 9. Desktop Shortcut & Config ──────────────────────────────────────────────
section "9/9  Desktop shortcut and bridge config"

# Desktop launcher
DESKTOP_FILE="$HOME/Desktop/AetherSDR-Menu.desktop"
cat > "$DESKTOP_FILE" << DESKTOP
#!/usr/bin/env xdg-open
[Desktop Entry]
Name=Ham Radio Control Center
Comment=Launch ham radio programs
Exec=sh -c 'nohup $HOME/hamradio-linux/aether-menu.sh >/dev/null 2>&1 &'
Icon=$HOME/apps/aethersdr-new/aethersdr.png
Terminal=false
Type=Application
Categories=AudioVideo;
DESKTOP
chmod +x "$DESKTOP_FILE"
info "Desktop shortcut created"

# flex-to-log4om config
CONF_FILE="$HOME/.flex-to-log4om.conf"
if [ -f "$CONF_FILE" ]; then
    info "Bridge config already present at ~/.flex-to-log4om.conf"
else
    cp "$HOME/hamradio-linux/flex-to-log4om.conf.example" "$CONF_FILE"
    warn "Bridge config created at ~/.flex-to-log4om.conf"
    warn "Edit this file with your callsign, QRZ credentials, grid, and Log4OM path"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
section "Setup complete!"
echo ""
echo "  Remaining manual steps:"
echo ""
echo "  1. Connect to Tailscale:"
echo "       sudo tailscale up"
echo ""
echo "  2. Test Pi connection:"
echo "       ssh -i ~/.ssh/ham_radio pi@100.76.124.28"
echo ""
echo "  3. Edit your bridge config:"
echo "       nano ~/.flex-to-log4om.conf"
echo "     Fill in: MY_CALLSIGN, MY_GRID, QRZ credentials, DB_LOCAL path"
echo ""
echo "  4. Install CQRLOG if needed:"
echo "       sudo apt install cqrlog"
echo ""
echo "  5. Double-click 'Ham Radio Control Center' on the Desktop"
echo ""
