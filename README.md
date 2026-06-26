# local-ai-control

> Una estación de control GTK con icono en la barra del sistema para tu IA local sobre **Ollama**. Encender/apagar el servicio, liberar VRAM al instante para jugar, descargar modelos, monitorizar GPU/RAM, leer los logs en vivo y lanzar integraciones (Open WebUI, opencode, Aider) — **todo desde un solo sitio**.

> *English version below 🇬🇧*

[![CI](https://github.com/SebastianAlvarezGajardo/local-ai-control/actions/workflows/ci.yml/badge.svg)](https://github.com/SebastianAlvarezGajardo/local-ai-control/actions/workflows/ci.yml)
![GTK](https://img.shields.io/badge/GTK-3-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Ollama](https://img.shields.io/badge/runtime-Ollama-black) ![Status](https://img.shields.io/badge/status-alpha-orange)

---

## 🇪🇸 ¿Qué es?

En Mac y Windows Ollama trae una app de bandeja oficial; en Linux no. **`local-ai-control`** rellena ese hueco y añade unas cuantas cosas que faltaban: gestión de modelos, monitorización de GPU AMD/Intel/NVIDIA, perfiles para jugar / trabajar, y atajos al ecosistema de apps que ya usas con tus modelos locales.

### Características

- 🎯 **Icono en la barra** con estado en vivo (🟢 encendida · 🟡 reposo · 🔴 apagada) y menú rápido.
- 🎮 **Liberar VRAM con 1 clic** — descarga todos los modelos cargados sin apagar el servicio. Ideal antes de jugar.
- 🟢/🔴 **Encender/apagar** el servicio `ollama` de systemd vía `pkexec` (diálogo gráfico de contraseña).
- 📦 **Gestión de modelos** — descargar con barra de progreso real, borrar con confirmación, catálogo curado de modelos recomendados.
- 📊 **Monitor de recursos** — VRAM, GPU activa %, RAM del sistema, carga CPU y RAM del proceso `ollama`, todo en vivo (lectura de `sysfs amdgpu` sin herramientas externas).
- 📜 **Logs en vivo** — `journalctl -fu ollama` integrado en una pestaña.
- 🔌 **Integraciones** — un clic para instalar/iniciar/abrir **Open WebUI** (chat web con memoria y RAG), **opencode**, **Aider** y **ComfyUI** (generación de imagen local con SDXL/SD 1.5/Flux).
- 🎚️ **Perfiles** — *Modo juego* libera VRAM; *Modo trabajo* precarga `qwen2.5-coder`; *Modo estudio* precarga `gemma3` y abre Open WebUI.

### Capturas

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/screenshots/01-tray-menu.png" alt="Icono y menú del tray" width="100%"><br>
      <sub><b>Icono en la barra + menú rápido</b></sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/screenshots/02-tab-estado.png" alt="Pestaña Estado" width="100%"><br>
      <sub><b>Estado · modelos cargados</b></sub>
    </td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/03-tab-modelos.png" alt="Pestaña Modelos" width="100%"><br><sub><b>Modelos · descarga con progreso</b></sub></td>
    <td align="center"><img src="docs/screenshots/04-tab-recursos.png" alt="Pestaña Recursos" width="100%"><br><sub><b>Recursos · VRAM/CPU/RAM en vivo</b></sub></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/screenshots/05-tab-logs.png" alt="Pestaña Logs" width="100%"><br><sub><b>Logs · journalctl -fu ollama</b></sub></td>
    <td align="center"><img src="docs/screenshots/06-tab-integraciones.png" alt="Pestaña Integraciones" width="100%"><br><sub><b>Integraciones · Open WebUI · opencode · Aider</b></sub></td>
  </tr>
</table>

> Las capturas vivirán en `docs/screenshots/` — ver el README de esa carpeta para los nombres y el comando `gnome-screenshot` a usar.

### Instalación

```bash
git clone https://github.com/SebastianAlvarezGajardo/local-ai-control.git
cd local-ai-control
./install.sh
```

`install.sh` instala el script en `~/.local/bin/`, crea el lanzador en `~/.local/share/applications/` (aparece en el menú de apps) y la entrada de autostart en `~/.config/autostart/` para que el icono salga al iniciar sesión.

### Requisitos

- Linux con escritorio basado en GNOME (probado en Ubuntu GNOME 24.x).
- Ollama instalado y configurado como servicio systemd (`sudo systemctl enable ollama`).
- Paquetes del sistema: `python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1`, `pkexec`, `notify-send`.
- Extensión GNOME **AppIndicator** activa (suele venir por defecto en Ubuntu GNOME).

En Ubuntu, instala lo que falte con:

```bash
sudo apt install -y python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 \
  policykit-1 libnotify-bin
```

### Desinstalación

```bash
./uninstall.sh
```

### Estructura del proyecto

```
local-ai-control/
├── local_ai_control.py            # app principal (un único archivo)
├── data/
│   ├── local-ai-control.desktop           # lanzador del menú
│   └── local-ai-control-autostart.desktop # arranque automático
├── install.sh / uninstall.sh
├── README.md  ·  LICENSE  ·  .gitignore
└── docs/screenshots/
```

### Contribuir

PRs y issues bienvenidos. Lo importante: el ámbito es **un único archivo Python sin dependencias pip** (todo se hace contra `python3-gi` y la API HTTP de Ollama). Cualquier funcionalidad nueva debería respetar esa restricción para mantener la instalación trivial.

### Licencia

MIT — © 2026 Sebastián Álvarez Gajardo

---

## 🇬🇧 What is it?

Ollama ships an official menu-bar app on macOS and Windows; not on Linux. **`local-ai-control`** fills that gap and adds a few things that were missing: model management, GPU/RAM monitoring, gaming/work profiles, and shortcuts to the local-AI ecosystem you already use.

### Features

- 🎯 **System-tray icon** with live status (🟢 on · 🟡 idle · 🔴 off) and a quick menu.
- 🎮 **Free VRAM in one click** — unload every loaded model without stopping the service. Perfect right before launching a game.
- 🟢/🔴 **Start/stop** the systemd `ollama` service via `pkexec`.
- 📦 **Model management** — pull with real progress bar, delete with confirmation, curated quick-pick of recommended models.
- 📊 **Resource monitor** — VRAM, GPU busy %, system RAM, CPU load and Ollama process RAM in real time (reads AMD GPU stats directly from `sysfs`, no extra tools needed).
- 📜 **Live logs** — embedded `journalctl -fu ollama`.
- 🔌 **Ecosystem integrations** — one-click install/launch for **Open WebUI** (web chat with memory + RAG), **opencode**, **Aider** and **ComfyUI** (local image generation with SDXL/SD 1.5/Flux).
- 🎚️ **Profiles** — *Gaming* frees VRAM; *Work* preloads `qwen2.5-coder`; *Study* preloads `gemma3` and opens Open WebUI.

### Install

```bash
git clone https://github.com/SebastianAlvarezGajardo/local-ai-control.git
cd local-ai-control
./install.sh
```

#### Optional: skip the password prompt when starting/stopping the service

By default each start/stop fires a polkit dialog. To skip it (only for `ollama.service`, nothing else), install the narrow polkit rule shipped in the repo:

```bash
sudo bash setup-polkit.sh
```

The rule only allows your current user to `start/stop/restart/reload` `ollama.service`. Undo with `sudo rm /etc/polkit-1/rules.d/50-local-ai-control.rules`.

### Requirements

- GNOME-based Linux desktop (tested on Ubuntu GNOME 24.x).
- Ollama installed and running as a systemd service.
- System packages: `python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1`, `pkexec`, `notify-send`.
- GNOME **AppIndicator** extension enabled (default on Ubuntu GNOME).

On Ubuntu:

```bash
sudo apt install -y python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 \
  policykit-1 libnotify-bin
```

### Uninstall

```bash
./uninstall.sh
```

### Contributing

PRs and issues welcome. Project rule: **single Python file, no pip dependencies** (everything goes through `python3-gi` and the Ollama HTTP API). New features should respect that to keep installation trivial.

### License

MIT — © 2026 Sebastián Álvarez Gajardo
