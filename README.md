# localai-control

> Una estación de control GTK con icono en la barra del sistema para tu IA local sobre **Ollama**. Encender/apagar el servicio, liberar VRAM al instante para jugar, descargar modelos, monitorizar GPU/RAM, leer los logs en vivo y lanzar integraciones (Open WebUI, opencode, Aider) — **todo desde un solo sitio**.

> *English version below 🇬🇧*

![GTK](https://img.shields.io/badge/GTK-3-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Ollama](https://img.shields.io/badge/runtime-Ollama-black) ![Status](https://img.shields.io/badge/status-alpha-orange)

---

## 🇪🇸 ¿Qué es?

En Mac y Windows Ollama trae una app de bandeja oficial; en Linux no. **`localai-control`** rellena ese hueco y añade unas cuantas cosas que faltaban: gestión de modelos, monitorización de GPU AMD/Intel/NVIDIA, perfiles para jugar / trabajar, y atajos al ecosistema de apps que ya usas con tus modelos locales.

### Características

- 🎯 **Icono en la barra** con estado en vivo (🟢 encendida · 🟡 reposo · 🔴 apagada) y menú rápido.
- 🎮 **Liberar VRAM con 1 clic** — descarga todos los modelos cargados sin apagar el servicio. Ideal antes de jugar.
- 🟢/🔴 **Encender/apagar** el servicio `ollama` de systemd vía `pkexec` (diálogo gráfico de contraseña).
- 📦 **Gestión de modelos** — descargar con barra de progreso real, borrar con confirmación, catálogo curado de modelos recomendados.
- 📊 **Monitor de recursos** — VRAM, GPU activa %, RAM del sistema, carga CPU y RAM del proceso `ollama`, todo en vivo (lectura de `sysfs amdgpu` sin herramientas externas).
- 📜 **Logs en vivo** — `journalctl -fu ollama` integrado en una pestaña.
- 🔌 **Integraciones** — un clic para instalar/iniciar/abrir **Open WebUI** (chat web con memoria y RAG), **opencode** y **Aider** preconfigurados con tu modelo local.
- 🎚️ **Perfiles** — *Modo juego* libera VRAM; *Modo trabajo* precarga `qwen2.5-coder`; *Modo estudio* precarga `gemma3` y abre Open WebUI.

### Capturas

> *(añadir en `docs/screenshots/`)*

### Instalación

```bash
git clone https://github.com/SebastianAlvarezGajardo/localai-control.git
cd localai-control
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
localai-control/
├── localai_control.py            # app principal (un único archivo)
├── data/
│   ├── localai-control.desktop           # lanzador del menú
│   └── localai-control-autostart.desktop # arranque automático
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

Ollama ships an official menu-bar app on macOS and Windows; not on Linux. **`localai-control`** fills that gap and adds a few things that were missing: model management, GPU/RAM monitoring, gaming/work profiles, and shortcuts to the local-AI ecosystem you already use.

### Features

- 🎯 **System-tray icon** with live status (🟢 on · 🟡 idle · 🔴 off) and a quick menu.
- 🎮 **Free VRAM in one click** — unload every loaded model without stopping the service. Perfect right before launching a game.
- 🟢/🔴 **Start/stop** the systemd `ollama` service via `pkexec`.
- 📦 **Model management** — pull with real progress bar, delete with confirmation, curated quick-pick of recommended models.
- 📊 **Resource monitor** — VRAM, GPU busy %, system RAM, CPU load and Ollama process RAM in real time (reads AMD GPU stats directly from `sysfs`, no extra tools needed).
- 📜 **Live logs** — embedded `journalctl -fu ollama`.
- 🔌 **Ecosystem integrations** — one-click install/launch for **Open WebUI** (web chat with memory + RAG), **opencode** and **Aider** preconfigured with your local model.
- 🎚️ **Profiles** — *Gaming* frees VRAM; *Work* preloads `qwen2.5-coder`; *Study* preloads `gemma3` and opens Open WebUI.

### Install

```bash
git clone https://github.com/SebastianAlvarezGajardo/localai-control.git
cd localai-control
./install.sh
```

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
