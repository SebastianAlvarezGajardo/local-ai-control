#!/usr/bin/env bash
# Removes everything installed by install.sh.
set -euo pipefail

BIN="$HOME/.local/bin/localai-control"
LAUNCHER="$HOME/.local/share/applications/localai-control.desktop"
AUTOSTART="$HOME/.config/autostart/localai-control.desktop"

# Stop the running tray (precise match on the binary path)
for pid in $(pgrep -af "$BIN" | awk '{print $1}'); do
  kill "$pid" 2>/dev/null || true
done

rm -fv "$BIN" "$LAUNCHER" "$AUTOSTART"

echo
echo "✅ Desinstalado. (Ollama y los modelos NO se han tocado.)"
