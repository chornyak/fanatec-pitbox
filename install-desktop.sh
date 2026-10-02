#!/bin/sh
# Adds a "Fanatec Pitbox" entry to the application launcher, running from this checkout.
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cp "$DIR/fanatec_pitbox/icons/fanatec-pitbox.svg" "$ICONS/fanatec-pitbox.svg"
# small sizes use the simplified icon, rendered to PNG for the fixed-size theme folders
QT_QPA_PLATFORM=offscreen python3 - "$DIR/fanatec_pitbox/icons/fanatec-pitbox-small.svg" "$DATA/icons/hicolor" <<'PY'
import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication
app = QApplication([])
svg, root = QSvgRenderer(sys.argv[1]), Path(sys.argv[2])
for size in (16, 22, 24, 32):
    out = root / f"{size}x{size}" / "apps"
    out.mkdir(parents=True, exist_ok=True)
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    svg.render(p)
    p.end()
    img.save(str(out / "fanatec-pitbox.png"))
PY
cat > "$APPS/fanatec-pitbox.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Fanatec Pitbox
Comment=Tuning, setups and input test for Fanatec wheel bases
Exec=$DIR/fanatec-pitbox
Path=$DIR
Icon=fanatec-pitbox
Categories=Game;Settings;HardwareSettings;
Terminal=false
DESKTOP
gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
echo "Installed $APPS/fanatec-pitbox.desktop"
