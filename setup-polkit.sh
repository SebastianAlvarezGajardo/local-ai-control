#!/usr/bin/env bash
# Installs a narrow polkit rule so the current user can start/stop the
# Ollama systemd service from local-ai-control without a password prompt.
# Re-run any time it's safe and idempotent.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/data/polkit/50-local-ai-control.rules"
DST="/etc/polkit-1/rules.d/50-local-ai-control.rules"
USER_TO_ALLOW="${SUDO_USER:-$USER}"

if [ ! -f "$SRC" ]; then
  echo "❌ No se encuentra $SRC" >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "Esta acción necesita root (escribe en /etc/polkit-1)."
  echo "Relánzalo con:  sudo bash $0"
  exit 1
fi

echo "→ Instalando regla para el usuario: $USER_TO_ALLOW"
echo "→ Destino: $DST"
sed "s|__USER__|$USER_TO_ALLOW|g" "$SRC" > "$DST"
chmod 644 "$DST"
chown root:root "$DST"

# polkit recoge cambios al vuelo, no hace falta reiniciar nada.
echo
echo "✅ Hecho. Ahora encender/apagar Ollama desde local-ai-control NO pedirá contraseña."
echo "   Para deshacer:  sudo rm $DST"
