#!/usr/bin/env bash
# localai-control installer — copies the script, registers the launcher
# and sets up autostart. Idempotent: safe to re-run after pulling updates.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
BIN="$BIN_DIR/localai-control"

echo "→ Creando directorios"
mkdir -p "$BIN_DIR" "$APP_DIR" "$AUTOSTART_DIR"

echo "→ Copiando script a $BIN"
install -m 755 "$HERE/localai_control.py" "$BIN"

echo "→ Registrando lanzador (menú de apps)"
sed "s|__BIN__|$BIN|g" "$HERE/data/localai-control.desktop" \
  > "$APP_DIR/localai-control.desktop"

echo "→ Registrando autostart (icono al iniciar sesión)"
sed "s|__BIN__|$BIN|g" "$HERE/data/localai-control-autostart.desktop" \
  > "$AUTOSTART_DIR/localai-control.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi

echo
echo "✅ Instalación lista."
echo "   - Ejecuta ahora:    $BIN          (deja el icono en la barra)"
echo "   - Atajo abre panel: $BIN --show"
echo "   - Aparecerá automáticamente al iniciar sesión."
