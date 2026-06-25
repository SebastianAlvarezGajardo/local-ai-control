#!/usr/bin/env python3
"""
local-ai-control — Tray + control panel for your local AI (Ollama)

A single GTK app that lives in the system tray and exposes the whole
local-AI lifecycle from one place: start/stop the service, free VRAM,
download/delete models, see live GPU/RAM stats, follow the service logs,
launch integrations (Open WebUI, opencode, aider) and switch profiles.

License: MIT — see LICENSE.
Homepage: https://github.com/SebastianAlvarezGajardo/local-ai-control
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3  # type: ignore[no-redef]
from gi.repository import GLib, Gtk  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────
APP_NAME = "local-ai-control"
VERSION = "0.2.0"
API = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OPEN_WEBUI = os.environ.get("OPEN_WEBUI_URL", "http://localhost:8080")
REFRESH_MS = 4000
STATS_REFRESH_MS = 2000
GPU_CARDS = ("/sys/class/drm/card1/device", "/sys/class/drm/card0/device")

# Curated quick-pick catalog: (model, description). Easy to extend.
CATALOG: list[tuple[str, str]] = [
    ("gemma3:1b", "Gemma 3 mini (Google) — ~1 GB, instantáneo"),
    ("gemma3:4b", "Gemma 3 4B (Google) — ~3 GB, equilibrado"),
    ("gemma3:12b", "Gemma 3 12B (Google) — ~8 GB, más capaz"),
    ("qwen2.5-coder:1.5b", "Qwen2.5 Coder 1.5B — código ligero"),
    ("qwen2.5-coder:7b", "Qwen2.5 Coder 7B — para programar"),
    ("qwen2.5:7b", "Qwen2.5 7B — general"),
    ("llama3.2:3b", "Llama 3.2 3B (Meta) — ~2 GB, ligero"),
    ("llama3.1:8b", "Llama 3.1 8B (Meta) — general"),
    ("deepseek-r1:8b", "DeepSeek-R1 8B — razonamiento paso a paso"),
    ("phi4:14b", "Phi-4 14B (Microsoft) — razona, ~9 GB"),
    ("mistral:7b", "Mistral 7B — general"),
    ("nomic-embed-text", "Embeddings (para RAG) — ~274 MB"),
]


# ── Backend: Ollama HTTP API ──────────────────────────────────────────────
def http_get(path: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(API + path, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError):
        return None


def http_json(method: str, path: str, payload: dict, timeout: float = 10.0):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    return urllib.request.urlopen(req, timeout=timeout)


def service_up() -> bool:
    return http_get("/api/version") is not None


def loaded_models() -> list[dict]:
    data = http_get("/api/ps")
    return data.get("models", []) if data else []


def installed_models() -> list[dict]:
    data = http_get("/api/tags")
    return data.get("models", []) if data else []


def stop_model(name: str) -> None:
    """Unload a model by sending keep_alive=0."""
    if not name:
        return
    try:
        http_json("POST", "/api/generate", {"model": name, "keep_alive": 0}, timeout=5).read()
    except Exception:
        pass


def delete_model(name: str) -> bool:
    try:
        http_json("DELETE", "/api/delete", {"model": name})
        return True
    except Exception:
        return False


def pull_model_stream(name, on_progress, on_done):
    """Stream `ollama pull` events back to GTK via GLib.idle_add."""
    try:
        req = urllib.request.Request(
            API + "/api/pull",
            data=json.dumps({"model": name, "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=600) as r:
            for line in r:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                status = ev.get("status", "")
                total, completed = ev.get("total"), ev.get("completed")
                pct = (completed / total) if total and completed else None
                GLib.idle_add(on_progress, status, pct)
                if ev.get("error"):
                    GLib.idle_add(on_done, False, ev["error"])
                    return
        GLib.idle_add(on_done, True, None)
    except Exception as e:
        GLib.idle_add(on_done, False, str(e))


# ── Backend: system helpers ───────────────────────────────────────────────
def systemctl(action: str) -> subprocess.CompletedProcess:
    """start/stop ollama via pkexec (graphical password dialog)."""
    return subprocess.run(["pkexec", "systemctl", action, "ollama"], capture_output=True, text=True)


def notify(title: str, body: str = "") -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "local-ai-control", title, body])


def open_terminal(cmd: str) -> None:
    """Open a terminal that runs cmd and waits for Enter before closing."""
    bash_cmd = f"{cmd}; echo; read -p 'Pulsa Enter para cerrar… '"
    for term in (
        ["gnome-terminal", "--", "bash", "-c", bash_cmd],
        ["konsole", "-e", "bash", "-c", bash_cmd],
        ["xterm", "-e", "bash", "-c", bash_cmd],
    ):
        if shutil.which(term[0]):
            subprocess.Popen(term)
            return


def gpu_stats() -> tuple[int, int, int] | None:
    """Return (vram_total_bytes, vram_used_bytes, gpu_busy_percent) for AMD GPU via sysfs."""
    for base in GPU_CARDS:
        try:
            total = int(open(f"{base}/mem_info_vram_total").read())
            used = int(open(f"{base}/mem_info_vram_used").read())
            busy = int(open(f"{base}/gpu_busy_percent").read())
            return total, used, busy
        except OSError:
            continue
    return None


def cpu_load() -> float:
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def mem_stats() -> dict[str, int]:
    out: dict[str, int] = {}
    for line in open("/proc/meminfo"):
        k, _, v = line.partition(":")
        out[k] = int(v.strip().split()[0]) * 1024
    return out


def ollama_proc_rss() -> int:
    """RSS bytes used by ollama processes."""
    try:
        r = subprocess.run(["pgrep", "-u", "ollama", "ollama"], capture_output=True, text=True)
        rss = 0
        for pid in r.stdout.strip().split():
            try:
                txt = open(f"/proc/{pid}/status").read()
                rss += int(txt.split("VmRSS:")[1].split()[0]) * 1024
            except (IndexError, OSError):
                pass
        return rss
    except OSError:
        return 0


def webui_installed() -> bool:
    return shutil.which("open-webui") is not None


def webui_running() -> bool:
    try:
        urllib.request.urlopen(OPEN_WEBUI, timeout=1)
        return True
    except Exception:
        return False


def human_size(b: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


# ── UI: tabs ──────────────────────────────────────────────────────────────
class StatusTab(Gtk.Box):
    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        self.app = app
        self.status_lbl = Gtk.Label(xalign=0)
        self.status_lbl.set_markup("<big>…</big>")
        self.pack_start(self.status_lbl, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_on = Gtk.Button(label="🟢 Encender")
        self.btn_off = Gtk.Button(label="🔴 Apagar")
        self.btn_free = Gtk.Button(label="🎮 Liberar VRAM")
        self.btn_on.connect("clicked", lambda _: app._do(lambda: systemctl("start"), "Encendiendo…"))
        self.btn_off.connect("clicked", lambda _: app._do(lambda: systemctl("stop"), "Apagando…"))
        self.btn_free.connect("clicked", lambda _: app._do(app._free_all, "Liberando VRAM…"))
        for b in (self.btn_on, self.btn_off, self.btn_free):
            actions.pack_start(b, True, True, 0)
        self.pack_start(actions, False, False, 0)

        self._header("Modelos cargados ahora mismo")
        self.loaded_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pack_start(self.loaded_box, False, False, 0)

    def _header(self, t: str) -> None:
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(f"<b>{t}</b>")
        self.pack_start(lbl, False, False, 4)

    def refresh(self, up: bool, loaded: list[dict]) -> None:
        if up:
            n = len(loaded)
            self.status_lbl.set_markup(
                f"<big>🟢 <b>Encendida</b></big>  ·  {n} modelo(s) cargado(s)"
                if n
                else "<big>🟡 <b>Encendida en reposo</b></big>  ·  0 VRAM ocupada"
            )
        else:
            self.status_lbl.set_markup("<big>🔴 <b>Apagada</b></big>  ·  servicio detenido")
        self.btn_on.set_sensitive(not up)
        self.btn_off.set_sensitive(up)
        self.btn_free.set_sensitive(up and bool(loaded))

        for c in self.loaded_box.get_children():
            self.loaded_box.remove(c)
        if up:
            if not loaded:
                self.loaded_box.pack_start(
                    Gtk.Label(label="— ninguno —", xalign=0), False, False, 0
                )
            for m in loaded:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                name = m.get("name") or m.get("model", "?")
                tail = ""
                if "size_vram" in m and m.get("size"):
                    pct = round(100 * m["size_vram"] / m["size"])
                    tail = f"  ·  GPU {pct}%"
                row.pack_start(Gtk.Label(label=f"• {name}{tail}", xalign=0), True, True, 0)
                btn = Gtk.Button(label="descargar")
                btn.connect(
                    "clicked",
                    lambda _w, n=name: self.app._do(lambda: stop_model(n), f"Descargando {n}…"),
                )
                row.pack_end(btn, False, False, 0)
                self.loaded_box.pack_start(row, False, False, 0)
        self.show_all()


class ModelsTab(Gtk.Box):
    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        self.app = app

        h = Gtk.Label(xalign=0)
        h.set_markup("<b>Descargar modelo nuevo</b>")
        self.pack_start(h, False, False, 0)

        self.combo = Gtk.ComboBoxText()
        self.combo.append_text("— elige uno recomendado —")
        for name, desc in CATALOG:
            self.combo.append_text(f"{name}  ·  {desc}")
        self.combo.set_active(0)
        self.combo.connect("changed", self._on_pick)
        self.pack_start(self.combo, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry = Gtk.Entry(placeholder_text="…o escribe nombre:tag (p.ej. llava:7b)")
        self.btn_pull = Gtk.Button(label="Descargar")
        self.btn_pull.connect("clicked", lambda _: self._start_pull())
        row.pack_start(self.entry, True, True, 0)
        row.pack_end(self.btn_pull, False, False, 0)
        self.pack_start(row, False, False, 0)

        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_text("")
        self.pack_start(self.progress, False, False, 0)

        h2 = Gtk.Label(xalign=0)
        h2.set_markup("<b>Modelos instalados</b>")
        self.pack_start(h2, False, False, 8)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.installed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled.add(self.installed_box)
        self.pack_start(scrolled, True, True, 0)

    def _on_pick(self, combo: Gtk.ComboBoxText) -> None:
        idx = combo.get_active()
        if idx > 0:
            self.entry.set_text(CATALOG[idx - 1][0])

    def _start_pull(self) -> None:
        name = self.entry.get_text().strip()
        if not name:
            return
        self.btn_pull.set_sensitive(False)
        self.progress.set_fraction(0)
        self.progress.set_text(f"iniciando {name}…")

        def on_prog(status: str, pct: float | None):
            if pct is not None:
                self.progress.set_fraction(pct)
                self.progress.set_text(f"{status}  ·  {int(pct * 100)}%")
            else:
                self.progress.pulse()
                self.progress.set_text(status)
            return False

        def on_done(ok: bool, err: str | None):
            self.btn_pull.set_sensitive(True)
            if ok:
                self.progress.set_fraction(1)
                self.progress.set_text(f"✅ {name} descargado")
                notify("Modelo descargado", name)
            else:
                self.progress.set_fraction(0)
                self.progress.set_text(f"❌ error: {err}")
                notify("Error descargando", err or "")
            self.app.refresh_all()
            return False

        threading.Thread(
            target=pull_model_stream, args=(name, on_prog, on_done), daemon=True
        ).start()

    def refresh(self, up: bool, installed: list[dict]) -> None:
        for c in self.installed_box.get_children():
            self.installed_box.remove(c)
        if not up:
            self.installed_box.pack_start(
                Gtk.Label(label="(servicio apagado)", xalign=0), False, False, 0
            )
        else:
            for m in installed:
                name = m.get("name", "?")
                size = human_size(m.get("size", 0))
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.pack_start(
                    Gtk.Label(label=f"📦 {name}   ({size})", xalign=0), True, True, 0
                )
                bchat = Gtk.Button(label="chatear")
                bchat.connect("clicked", lambda _w, n=name: open_terminal(f"ollama run {n}"))
                bdel = Gtk.Button(label="🗑")
                bdel.connect("clicked", lambda _w, n=name: self._del(n))
                row.pack_end(bdel, False, False, 0)
                row.pack_end(bchat, False, False, 0)
                self.installed_box.pack_start(row, False, False, 0)
        self.show_all()

    def _del(self, name: str) -> None:
        d = Gtk.MessageDialog(
            transient_for=self.app.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"¿Borrar {name}?",
        )
        d.format_secondary_text("Liberará el espacio en disco. Podrás volver a descargarlo.")
        ok = d.run() == Gtk.ResponseType.YES
        d.destroy()
        if ok:
            success = delete_model(name)
            notify("Modelo borrado" if success else "Error al borrar", name)
            self.app.refresh_all()


class StatsTab(Gtk.Box):
    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        self.app = app

        h = Gtk.Label(xalign=0)
        h.set_markup("<b>GPU</b>")
        self.pack_start(h, False, False, 0)
        self.gpu_lbl = Gtk.Label(xalign=0)
        self.pack_start(self.gpu_lbl, False, False, 0)
        self.vram_bar = Gtk.ProgressBar(show_text=True)
        self.pack_start(self.vram_bar, False, False, 0)
        self.busy_bar = Gtk.ProgressBar(show_text=True)
        self.pack_start(self.busy_bar, False, False, 0)

        h2 = Gtk.Label(xalign=0)
        h2.set_markup("<b>Sistema</b>")
        self.pack_start(h2, False, False, 10)
        self.ram_bar = Gtk.ProgressBar(show_text=True)
        self.pack_start(self.ram_bar, False, False, 0)
        self.cpu_lbl = Gtk.Label(xalign=0)
        self.pack_start(self.cpu_lbl, False, False, 0)

        h3 = Gtk.Label(xalign=0)
        h3.set_markup("<b>Servicio Ollama</b>")
        self.pack_start(h3, False, False, 10)
        self.ollama_lbl = Gtk.Label(xalign=0)
        self.pack_start(self.ollama_lbl, False, False, 0)

        GLib.timeout_add(STATS_REFRESH_MS, self._tick)
        self._tick()

    def _tick(self) -> bool:
        gs = gpu_stats()
        if gs:
            total, used, busy = gs
            free = total - used
            self.gpu_lbl.set_text(
                f"Total {human_size(total)}  ·  Usado {human_size(used)}  ·  Libre {human_size(free)}"
            )
            self.vram_bar.set_fraction(min(1.0, used / total))
            self.vram_bar.set_text(f"VRAM  {int(100 * used / total)}%")
            self.busy_bar.set_fraction(min(1.0, busy / 100))
            self.busy_bar.set_text(f"GPU activa  {busy}%")
        else:
            self.gpu_lbl.set_text("(sin lectura de GPU vía sysfs; ¿no es AMD o sin permisos?)")

        m = mem_stats()
        ram_total = m.get("MemTotal", 1)
        ram_used = ram_total - m.get("MemAvailable", 0)
        self.ram_bar.set_fraction(min(1.0, ram_used / ram_total))
        self.ram_bar.set_text(f"RAM  {human_size(ram_used)} / {human_size(ram_total)}")
        self.cpu_lbl.set_text(f"Carga CPU (1 min): {cpu_load():.2f}")

        rss = ollama_proc_rss()
        if rss:
            self.ollama_lbl.set_text(f"Proceso vivo · RAM: {human_size(rss)}")
        else:
            self.ollama_lbl.set_text("Proceso no encontrado (servicio apagado)")
        return True


class LogsTab(Gtk.Box):
    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        self.app = app

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>journalctl -fu ollama</b>")
        bar.pack_start(lbl, True, True, 0)
        bclear = Gtk.Button(label="Limpiar")
        bclear.connect("clicked", lambda _: self.buf.set_text(""))
        bar.pack_end(bclear, False, False, 0)
        self.pack_start(bar, False, False, 0)

        sw = Gtk.ScrolledWindow(vexpand=True)
        self.view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.buf = self.view.get_buffer()
        sw.add(self.view)
        self.pack_start(sw, True, True, 0)

        self.proc: subprocess.Popen | None = None
        self._start_tail()

    def _start_tail(self) -> None:
        try:
            self.proc = subprocess.Popen(
                ["journalctl", "-fu", "ollama", "--no-pager", "-n", "200", "-o", "short"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self.buf.set_text("journalctl no disponible en este sistema.")
            return
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            GLib.idle_add(self._append, line)

    def _append(self, line: str) -> bool:
        end = self.buf.get_end_iter()
        self.buf.insert(end, line)
        mark = self.buf.create_mark(None, self.buf.get_end_iter(), False)
        self.view.scroll_mark_onscreen(mark)
        # cap at ~5000 lines so memory doesn't grow unbounded
        if self.buf.get_line_count() > 5000:
            self.buf.delete(self.buf.get_start_iter(), self.buf.get_iter_at_line(1000))
        return False


class IntegrationsTab(Gtk.Box):
    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=14)
        self.app = app

        # — Open WebUI —
        self._section_title("Open WebUI", "chat web con historial, memoria y RAG")
        self.webui_status = Gtk.Label(xalign=0)
        self.pack_start(self.webui_status, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_webui_install = Gtk.Button(label="Instalar (pipx)")
        self.btn_webui_install.connect(
            "clicked",
            lambda _: open_terminal(
                "echo 'Instalando Open WebUI con pipx (puede tardar unos minutos)...' && "
                "(command -v pipx >/dev/null || (sudo apt update && sudo apt install -y pipx && pipx ensurepath)) && "
                "pipx install open-webui"
            ),
        )
        self.btn_webui_start = Gtk.Button(label="Iniciar servicio")
        self.btn_webui_start.connect(
            "clicked", lambda _: open_terminal("open-webui serve")
        )
        self.btn_webui_open = Gtk.Button(label="Abrir en navegador")
        self.btn_webui_open.connect("clicked", lambda _: webbrowser.open(OPEN_WEBUI))
        for b in (self.btn_webui_install, self.btn_webui_start, self.btn_webui_open):
            row.pack_start(b, True, True, 0)
        self.pack_start(row, False, False, 0)

        # — opencode —
        self._section_title("opencode", "asistente de código en terminal", top=12)
        self.opencode_status = Gtk.Label(xalign=0)
        self.pack_start(self.opencode_status, False, False, 0)
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b1 = Gtk.Button(label="Lanzar")
        b1.connect("clicked", lambda _: open_terminal("opencode"))
        b2 = Gtk.Button(label="Web del proyecto")
        b2.connect("clicked", lambda _: webbrowser.open("https://opencode.ai"))
        row2.pack_start(b1, True, True, 0)
        row2.pack_start(b2, True, True, 0)
        self.pack_start(row2, False, False, 0)

        # — Aider —
        self._section_title("Aider", "pair programming con IA en terminal", top=12)
        self.aider_status = Gtk.Label(xalign=0)
        self.pack_start(self.aider_status, False, False, 0)
        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ai_install = Gtk.Button(label="Instalar (pipx)")
        ai_install.connect(
            "clicked",
            lambda _: open_terminal(
                "(command -v pipx >/dev/null || (sudo apt update && sudo apt install -y pipx && pipx ensurepath)) && pipx install aider-chat"
            ),
        )
        ai_launch = Gtk.Button(label="Lanzar con qwen2.5-coder")
        ai_launch.connect(
            "clicked",
            lambda _: open_terminal(
                "aider --model ollama_chat/qwen2.5-coder:7b --no-show-model-warnings"
            ),
        )
        row3.pack_start(ai_install, True, True, 0)
        row3.pack_start(ai_launch, True, True, 0)
        self.pack_start(row3, False, False, 0)

        self.refresh()

    def _section_title(self, title: str, subtitle: str, top: int = 0) -> None:
        h = Gtk.Label(xalign=0)
        h.set_markup(f"<big><b>{title}</b></big>  —  <span alpha='75%'>{subtitle}</span>")
        self.pack_start(h, False, False, top)

    def refresh(self) -> None:
        if webui_installed():
            running = webui_running()
            self.webui_status.set_markup(
                "✅ instalado · 🟢 corriendo en :8080" if running else "✅ instalado · 🔴 parado"
            )
            self.btn_webui_install.set_sensitive(False)
            self.btn_webui_start.set_sensitive(not running)
            self.btn_webui_open.set_sensitive(running)
        else:
            self.webui_status.set_markup("❌ no instalado")
            self.btn_webui_install.set_sensitive(True)
            self.btn_webui_start.set_sensitive(False)
            self.btn_webui_open.set_sensitive(False)

        self.opencode_status.set_markup(
            "✅ instalado"
            if shutil.which("opencode")
            else "❌ no instalado · <tt>curl -fsSL https://opencode.ai/install | bash</tt>"
        )
        self.aider_status.set_markup(
            "✅ instalado" if shutil.which("aider") else "❌ no instalado"
        )


class ProfilesTab(Gtk.Box):
    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=14)
        self.app = app

        intro = Gtk.Label(xalign=0, wrap=True)
        intro.set_markup(
            "<b>Perfiles</b> — presets que ejecutan varias acciones en cadena. "
            "Ideales para cambiar de contexto sin pensar."
        )
        self.pack_start(intro, False, False, 0)

        for label, desc, fn in (
            (
                "🎮 Modo juego",
                "Libera VRAM (descarga todos los modelos) para dejar la GPU al juego.",
                self._gaming,
            ),
            (
                "💻 Modo trabajo",
                "Precarga qwen2.5-coder:7b para usarlo desde opencode/aider al instante.",
                self._work,
            ),
            (
                "📚 Modo estudio",
                "Precarga gemma3:4b y abre Open WebUI (si está instalada) para chatear cómodo.",
                self._study,
            ),
        ):
            self.pack_start(self._card(label, desc, fn), False, False, 0)

    def _card(self, title: str, desc: str, fn) -> Gtk.Frame:
        frame = Gtk.Frame()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
        t = Gtk.Label(xalign=0)
        t.set_markup(f"<big><b>{title}</b></big>")
        d = Gtk.Label(xalign=0, wrap=True)
        d.set_text(desc)
        b = Gtk.Button(label="Activar")
        b.connect("clicked", lambda _w: self.app._do(fn, f"Activando {title}…"))
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.pack_end(b, False, False, 0)
        box.pack_start(t, False, False, 0)
        box.pack_start(d, False, False, 0)
        box.pack_start(bar, False, False, 0)
        frame.add(box)
        return frame

    # — actions —
    def _gaming(self) -> None:
        if service_up():
            for m in loaded_models():
                stop_model(m.get("name") or m.get("model", ""))
        notify("🎮 Modo juego activado", "VRAM liberada")

    def _work(self) -> None:
        if not service_up():
            notify("Modo trabajo", "Enciende la IA antes (Estado → Encender)")
            return
        try:
            http_json(
                "POST",
                "/api/generate",
                {"model": "qwen2.5-coder:7b", "prompt": "ok", "keep_alive": "30m", "stream": False},
                timeout=180,
            ).read()
            notify("💻 Modo trabajo", "qwen2.5-coder:7b precargado")
        except Exception as e:
            notify("Modo trabajo · error", str(e))

    def _study(self) -> None:
        if not service_up():
            notify("Modo estudio", "Enciende la IA antes (Estado → Encender)")
            return
        try:
            http_json(
                "POST",
                "/api/generate",
                {"model": "gemma3:4b", "prompt": "ok", "keep_alive": "30m", "stream": False},
                timeout=180,
            ).read()
        except Exception:
            pass
        if webui_installed():
            if not webui_running():
                subprocess.Popen(
                    ["open-webui", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            webbrowser.open(OPEN_WEBUI)
        notify("📚 Modo estudio", "Modelo precargado y WebUI abierta")


# ── UI: window + tray ─────────────────────────────────────────────────────
class ControlWindow(Gtk.Window):
    def __init__(self, app: "App"):
        super().__init__(title=f"local-ai-control · v{VERSION}")
        self.set_default_size(720, 560)
        self.set_icon_name("computer")
        self.app = app

        nb = Gtk.Notebook()
        self.add(nb)
        self.tab_status = StatusTab(app)
        self.tab_models = ModelsTab(app)
        self.tab_stats = StatsTab(app)
        self.tab_logs = LogsTab(app)
        self.tab_integrations = IntegrationsTab(app)
        self.tab_profiles = ProfilesTab(app)
        for w, name in (
            (self.tab_status, "Estado"),
            (self.tab_models, "Modelos"),
            (self.tab_stats, "Recursos"),
            (self.tab_logs, "Logs"),
            (self.tab_integrations, "Integraciones"),
            (self.tab_profiles, "Perfiles"),
        ):
            nb.append_page(w, Gtk.Label(label=name))

        # closing hides the window (the tray stays); use "Salir del icono" to quit
        self.connect("delete-event", lambda *_: self.hide() or True)

        # IMPORTANT: show all descendants so notebook + tabs render their content.
        # Without this the window opens but appears BLANK because individual
        # widgets default to "not visible". Calling show_all() once here makes
        # every child visible; subsequent open/close via the tray just hides
        # the window (delete-event handler) and `present()` brings it back.
        self.show_all()

    def refresh(self) -> None:
        up = service_up()
        loaded = loaded_models() if up else []
        installed = installed_models() if up else []
        self.tab_status.refresh(up, loaded)
        self.tab_models.refresh(up, installed)
        self.tab_integrations.refresh()


class App:
    def __init__(self) -> None:
        self.ind = AppIndicator3.Indicator.new(
            APP_NAME, "computer-fail", AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.window = ControlWindow(self)
        self._build_menu()
        self.refresh()
        GLib.timeout_add(REFRESH_MS, self._tick)

    def _build_menu(self) -> None:
        m = Gtk.Menu()
        self.mi_status = Gtk.MenuItem(label="…")
        self.mi_status.set_sensitive(False)
        mi_panel = Gtk.MenuItem(label="Abrir panel")
        self.mi_free = Gtk.MenuItem(label="🎮 Liberar VRAM")
        self.mi_on = Gtk.MenuItem(label="🟢 Encender")
        self.mi_off = Gtk.MenuItem(label="🔴 Apagar")
        mi_about = Gtk.MenuItem(label=f"Acerca de · v{VERSION}")
        mi_quit = Gtk.MenuItem(label="Salir del icono")

        mi_panel.connect(
            "activate",
            lambda _: (self.window.refresh(), self.window.show_all(), self.window.present()),
        )
        self.mi_free.connect("activate", lambda _: self._do(self._free_all, "Liberando…"))
        self.mi_on.connect(
            "activate", lambda _: self._do(lambda: systemctl("start"), "Encendiendo…")
        )
        self.mi_off.connect(
            "activate", lambda _: self._do(lambda: systemctl("stop"), "Apagando…")
        )
        mi_about.connect(
            "activate",
            lambda _: webbrowser.open(
                "https://github.com/SebastianAlvarezGajardo/local-ai-control"
            ),
        )
        mi_quit.connect("activate", lambda _: Gtk.main_quit())

        for it in (
            self.mi_status,
            Gtk.SeparatorMenuItem(),
            mi_panel,
            Gtk.SeparatorMenuItem(),
            self.mi_free,
            self.mi_on,
            self.mi_off,
            Gtk.SeparatorMenuItem(),
            mi_about,
            mi_quit,
        ):
            m.append(it)
        m.show_all()
        self.ind.set_menu(m)

    def _do(self, fn, busy_msg: str = "") -> None:
        if busy_msg:
            self.mi_status.set_label(f"⏳ {busy_msg}")

        def worker():
            try:
                fn()
            except Exception as e:
                notify("Error", str(e))
            GLib.idle_add(self.refresh_all)

        threading.Thread(target=worker, daemon=True).start()

    def _free_all(self) -> None:
        for m in loaded_models():
            stop_model(m.get("name") or m.get("model", ""))

    def refresh(self) -> bool:
        up = service_up()
        ms = loaded_models() if up else []
        if up and ms:
            txt = f"🟢 IA · {len(ms)} cargado(s)"
            self.ind.set_icon_full("computer", "encendida")
        elif up:
            txt = "🟡 IA · en reposo"
            self.ind.set_icon_full("computer", "reposo")
        else:
            txt = "🔴 IA · apagada"
            self.ind.set_icon_full("computer-fail", "apagada")
        self.mi_status.set_label(txt)
        self.ind.set_label(" IA", "")
        self.mi_on.set_sensitive(not up)
        self.mi_off.set_sensitive(up)
        self.mi_free.set_sensitive(up and bool(ms))
        return False

    def refresh_all(self) -> bool:
        self.refresh()
        if self.window.get_visible():
            self.window.refresh()
        return False

    def _tick(self) -> bool:
        self.refresh_all()
        return True


def main() -> None:
    show = "--show" in sys.argv
    app = App()
    if show:
        app.window.present()
    Gtk.main()


if __name__ == "__main__":
    main()
