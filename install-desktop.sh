#!/bin/sh
# Adds a "Fanatec Pitbox" entry to the application launcher, running from this checkout.
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/fanatec-pitbox.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Fanatec Pitbox
Comment=Wheel base tuning and per-game profiles
Exec=$DIR/fanatec-pitbox
Path=$DIR
Icon=input-gaming
Categories=Game;Settings;HardwareSettings;
Terminal=false
DESKTOP
echo "Installed $APPS/fanatec-pitbox.desktop"
