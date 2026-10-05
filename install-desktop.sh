#!/bin/sh
# Adds a "Fanatec Pitbox" entry to the application launcher, running from this checkout.
# (The AUR package installs the same entry and icons system-wide.)
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
mkdir -p "$APPS" "$DATA/icons/hicolor/scalable/apps"
cp "$DIR/fanatec_pitbox/icons/fanatec-pitbox.svg" "$DATA/icons/hicolor/scalable/apps/fanatec-pitbox.svg"
# small sizes use the simplified icon, pre-rendered for the fixed-size theme folders
for size in 16 22 24 32; do
    mkdir -p "$DATA/icons/hicolor/${size}x${size}/apps"
    cp "$DIR/packaging/icons/fanatec-pitbox-$size.png" "$DATA/icons/hicolor/${size}x${size}/apps/fanatec-pitbox.png"
done
sed -e "s|^Exec=.*|Exec=$DIR/fanatec-pitbox|" -e "/^Exec=/a Path=$DIR" \
    "$DIR/packaging/fanatec-pitbox.desktop" > "$APPS/fanatec-pitbox.desktop"
gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
echo "Installed $APPS/fanatec-pitbox.desktop"
