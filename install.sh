#!/usr/bin/env bash
# local-ai-control installer — copies the script, registers the launcher
# and sets up autostart. Idempotent: safe to re-run after pulling updates.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
BIN="$BIN_DIR/local-ai-control"

echo "→ Creando directorios"
mkdir -p "$BIN_DIR" "$APP_DIR" "$AUTOSTART_DIR" "$ICON_DIR"

echo "→ Copiando script a $BIN"
install -m 755 "$HERE/local_ai_control.py" "$BIN"

echo "→ Instalando icono"
install -m 644 "$HERE/data/icons/local-ai-control.svg" "$ICON_DIR/local-ai-control.svg"

echo "→ Registrando lanzador (menú de apps)"
sed "s|__BIN__|$BIN|g" "$HERE/data/local-ai-control.desktop" \
  > "$APP_DIR/local-ai-control.desktop"

echo "→ Registrando autostart (icono al iniciar sesión)"
sed "s|__BIN__|$BIN|g" "$HERE/data/local-ai-control-autostart.desktop" \
  > "$AUTOSTART_DIR/local-ai-control.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo
echo "✅ Instalación lista."
echo "   - Ejecuta ahora:    $BIN          (deja el icono en la barra)"
echo "   - Atajo abre panel: $BIN --show"
echo "   - Aparecerá automáticamente al iniciar sesión."
