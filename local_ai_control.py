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
import time
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
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────
APP_NAME = "local-ai-control"
VERSION = "0.5.0"
API = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OPEN_WEBUI = os.environ.get("OPEN_WEBUI_URL", "http://localhost:8080")
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://localhost:8188")
COMFYUI_DIR = os.path.expanduser(os.environ.get("COMFYUI_DIR", "~/ComfyUI"))
REFRESH_MS = 4000
STATS_REFRESH_MS = 2000
GPU_CARDS = ("/sys/class/drm/card1/device", "/sys/class/drm/card0/device")

# Curated quick-pick catalog: (model, description). Easy to extend.
CATALOG: list[tuple[str, str]] = [
    # — texto / general —
    ("gemma3:1b", "Gemma 3 mini (Google) — ~1 GB, instantáneo"),
    ("gemma3:4b", "Gemma 3 4B (Google) — ~3 GB, equilibrado"),
    ("gemma3:12b", "Gemma 3 12B (Google) — ~8 GB, más capaz"),
    ("qwen2.5:7b", "Qwen2.5 7B — general"),
    ("llama3.2:3b", "Llama 3.2 3B (Meta) — ~2 GB, ligero"),
    ("llama3.1:8b", "Llama 3.1 8B (Meta) — general"),
    ("mistral:7b", "Mistral 7B — general"),
    # — código —
    ("qwen2.5-coder:1.5b", "Qwen2.5 Coder 1.5B — código ligero"),
    ("qwen2.5-coder:7b", "Qwen2.5 Coder 7B — para programar"),
    # — razonamiento —
    ("deepseek-r1:8b", "DeepSeek-R1 8B — razonamiento paso a paso"),
    ("phi4:14b", "Phi-4 14B (Microsoft) — razona, ~9 GB"),
    # — multimodal (visión: «lee» imágenes) —
    ("moondream:1.8b", "Moondream 1.8B — visión ultra-ligera (~1.7 GB)"),
    ("llava:7b", "LLaVA 7B — visión clásica (~4.7 GB)"),
    ("llama3.2-vision:11b", "Llama 3.2 Vision 11B (Meta) — visión moderna (~8 GB)"),
    # — utilidad —
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


def comfyui_installed() -> bool:
    return os.path.isfile(os.path.join(COMFYUI_DIR, "main.py"))


def comfyui_running() -> bool:
    try:
        urllib.request.urlopen(COMFYUI_URL, timeout=1)
        return True
    except Exception:
        return False


def human_size(b: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


# ── UI: theme ─────────────────────────────────────────────────────────────
def _install_css() -> None:
    """Apply a small CSS so the whole app feels consistent and not crammed.

    Uses `currentColor` based alpha so we play nicely with both light and
    dark GTK themes. Cards get a subtle border + rounded corners; buttons
    and progress bars match the same radius. Hover gently lifts the card.
    """
    # IMPORTANT: every selector is scoped to `.card` descendants. Earlier
    # versions used bare `button { … }` and accidentally restyled the window
    # title-bar buttons (close/min/max). Lesson: never style generic widgets
    # globally in a GTK app you don't own end-to-end.
    css = b"""
    frame.card {
        border: 1px solid alpha(currentColor, 0.18);
        border-radius: 10px;
        background-color: alpha(currentColor, 0.035);
        padding: 0;
    }
    frame.card:hover {
        background-color: alpha(currentColor, 0.07);
    }
    frame.card button {
        border-radius: 7px;
        padding: 4px 12px;
    }
    frame.card progressbar trough,
    frame.card progressbar progress {
        border-radius: 6px;
        min-height: 6px;
    }
    notebook > header > tabs > tab {
        padding: 6px 14px;
    }
    expander > title {
        padding: 6px 0;
    }
    expander.category {
        margin-top: 4px;
    }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


# ── UI: helpers ───────────────────────────────────────────────────────────
def make_card(
    title: str | None = None, subtitle: str | None = None
) -> tuple[Gtk.Frame, Gtk.Box]:
    """Return (frame, content_box). Pack your widgets into content_box.

    Provides the visual chrome used everywhere: a `.card` Frame with title,
    optional dim subtitle, and a vertical content area. One single styling
    contract across the whole app — change CSS once, everything reflows.
    """
    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    frame.get_style_context().add_class("card")

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
    if title is not None:
        t = Gtk.Label(xalign=0)
        t.set_markup(f"<big><b>{title}</b></big>")
        outer.pack_start(t, False, False, 0)
    if subtitle is not None:
        s = Gtk.Label(xalign=0, wrap=True)
        s.set_markup(f"<span alpha='65%'>{subtitle}</span>")
        outer.pack_start(s, False, False, 0)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.pack_start(content, True, True, 0)
    frame.add(outer)
    return frame, content


def model_row(
    name: str,
    size_human: str,
    extra: str = "",
    buttons: list[Gtk.Button] | None = None,
) -> Gtk.Box:
    """A compact model line: 📦 <b>name</b> · size · extra   [buttons →]"""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    info = Gtk.Label(xalign=0, wrap=True)
    tail = f"  <span alpha='65%'>· {size_human}{extra}</span>"
    info.set_markup(f"📦 <b>{name}</b>{tail}")
    row.pack_start(info, True, True, 0)
    if buttons:
        for b in buttons:
            row.pack_end(b, False, False, 0)
    return row


def empty_state(text: str) -> Gtk.Label:
    """Friendly placeholder for empty lists."""
    lbl = Gtk.Label(xalign=0)
    lbl.set_markup(f"<span alpha='50%'>{text}</span>")
    return lbl


# ── UI: tabs ──────────────────────────────────────────────────────────────
class DashboardTab(Gtk.Box):
    """Landing tab: state + actions + resources + loaded models, all in one.

    Replaces the old StatusTab + StatsTab — fewer clicks to see what matters.
    """

    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=14)
        scrolled.add(outer)
        self.pack_start(scrolled, True, True, 0)

        # — Hero: state + actions in one card —
        hero_frame, hero_box = make_card()
        hero_box.set_spacing(12)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.hero_emoji = Gtk.Label()
        self.hero_emoji.set_markup("<span size='30000'>⏳</span>")
        head.pack_start(self.hero_emoji, False, False, 0)
        head_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.hero_state = Gtk.Label(xalign=0)
        self.hero_state.set_markup("<big><b>cargando…</b></big>")
        self.hero_summary = Gtk.Label(xalign=0, wrap=True)
        head_text.pack_start(self.hero_state, False, False, 0)
        head_text.pack_start(self.hero_summary, False, False, 0)
        head.pack_start(head_text, True, True, 0)
        # Tiny "last updated Xs ago" badge — quietly confirms the panel is live
        self.last_update_lbl = Gtk.Label(xalign=1, yalign=0)
        self.last_update_lbl.set_markup("<small><span alpha='50%'>—</span></small>")
        head.pack_end(self.last_update_lbl, False, False, 0)
        hero_box.pack_start(head, False, False, 0)
        self._last_refresh = time.monotonic()
        GLib.timeout_add(1000, self._tick_badge)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_on = Gtk.Button(label="🟢 Encender")
        self.btn_on.set_tooltip_text("Arranca el servicio Ollama (systemd)")
        self.btn_off = Gtk.Button(label="🔴 Apagar")
        self.btn_off.set_tooltip_text("Detiene el servicio Ollama por completo")
        self.btn_free = Gtk.Button(label="🎮 Liberar VRAM")
        self.btn_free.set_tooltip_text(
            "Descarga todos los modelos en memoria. La GPU vuelve a estar libre "
            "para juegos o edición de vídeo. El servicio sigue activo."
        )
        self.btn_on.connect(
            "clicked", lambda _: app._do(lambda: systemctl("start"), "Encendiendo…")
        )
        self.btn_off.connect(
            "clicked", lambda _: app._do(lambda: systemctl("stop"), "Apagando…")
        )
        self.btn_free.connect(
            "clicked", lambda _: app._do(app._free_all, "Liberando VRAM…")
        )
        for b in (self.btn_on, self.btn_off, self.btn_free):
            actions.pack_start(b, True, True, 0)
        hero_box.pack_start(actions, False, False, 0)
        outer.pack_start(hero_frame, False, False, 0)

        # — Recursos: GPU + Sistema en fila (2 columnas) —
        res_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        gpu_frame, gpu_box = make_card("GPU", subtitle="Memoria y carga gráfica")
        self.gpu_summary = Gtk.Label(xalign=0)
        gpu_box.pack_start(self.gpu_summary, False, False, 0)
        self.vram_bar = Gtk.ProgressBar(show_text=True)
        gpu_box.pack_start(self.vram_bar, False, False, 0)
        self.busy_bar = Gtk.ProgressBar(show_text=True)
        gpu_box.pack_start(self.busy_bar, False, False, 0)
        res_row.pack_start(gpu_frame, True, True, 0)

        sys_frame, sys_box = make_card("Sistema", subtitle="RAM, CPU y proceso Ollama")
        self.ram_bar = Gtk.ProgressBar(show_text=True)
        sys_box.pack_start(self.ram_bar, False, False, 0)
        self.cpu_lbl = Gtk.Label(xalign=0)
        sys_box.pack_start(self.cpu_lbl, False, False, 0)
        self.ollama_lbl = Gtk.Label(xalign=0)
        sys_box.pack_start(self.ollama_lbl, False, False, 0)
        res_row.pack_start(sys_frame, True, True, 0)

        outer.pack_start(res_row, False, False, 0)

        # — Modelos cargados (en VRAM/RAM ahora) —
        ld_frame, ld_box = make_card(
            "Modelos cargados ahora mismo",
            subtitle="Lo que tienes ocupando RAM/VRAM en este momento",
        )
        self.loaded_box = ld_box
        outer.pack_start(ld_frame, False, False, 0)

        GLib.timeout_add(STATS_REFRESH_MS, self._stats_tick)
        self._stats_tick()

    def _tick_badge(self) -> bool:
        elapsed = int(time.monotonic() - self._last_refresh)
        if elapsed < 60:
            txt = f"actualizado hace {elapsed}s"
        elif elapsed < 3600:
            txt = f"hace {elapsed // 60} min"
        else:
            txt = "hace > 1 h"
        self.last_update_lbl.set_markup(f"<small><span alpha='50%'>{txt}</span></small>")
        return True

    def refresh(self, up: bool, loaded: list[dict]) -> None:
        self._last_refresh = time.monotonic()
        if up:
            n = len(loaded)
            if n:
                self.hero_emoji.set_markup("<span size='30000'>🟢</span>")
                self.hero_state.set_markup("<big><b>Encendida</b></big>")
                names = " · ".join(
                    m.get("name") or m.get("model", "?") for m in loaded[:3]
                )
                more = f" (+{n - 3})" if n > 3 else ""
                self.hero_summary.set_markup(
                    f"<span alpha='65%'>{n} modelo(s) cargado(s):  {names}{more}</span>"
                )
            else:
                self.hero_emoji.set_markup("<span size='30000'>🟡</span>")
                self.hero_state.set_markup("<big><b>En reposo</b></big>")
                self.hero_summary.set_markup(
                    "<span alpha='65%'>Servicio activo, sin modelos en VRAM (consumo ~0)</span>"
                )
        else:
            self.hero_emoji.set_markup("<span size='30000'>🔴</span>")
            self.hero_state.set_markup("<big><b>Apagada</b></big>")
            self.hero_summary.set_markup(
                "<span alpha='65%'>El servicio Ollama está detenido</span>"
            )

        self.btn_on.set_sensitive(not up)
        self.btn_off.set_sensitive(up)
        self.btn_free.set_sensitive(up and bool(loaded))

        for c in self.loaded_box.get_children():
            self.loaded_box.remove(c)
        if not up:
            self.loaded_box.pack_start(
                empty_state("(servicio apagado)"), False, False, 0
            )
        elif not loaded:
            self.loaded_box.pack_start(
                empty_state("— ningún modelo cargado —"), False, False, 0
            )
        else:
            for m in loaded:
                name = m.get("name") or m.get("model", "?")
                size = human_size(m.get("size", 0))
                extra = ""
                if "size_vram" in m and m.get("size"):
                    pct = round(100 * m["size_vram"] / m["size"])
                    extra = f" · 🎮 GPU {pct}%"
                btn = Gtk.Button(label="descargar")
                btn.set_tooltip_text(f"Descarga {name} de RAM/VRAM (vuelve solo al usarlo)")
                btn.connect(
                    "clicked",
                    lambda _w, n=name: self.app._do(
                        lambda: stop_model(n), f"Descargando {n}…"
                    ),
                )
                self.loaded_box.pack_start(
                    model_row(name, size, extra, [btn]), False, False, 0
                )
        self.show_all()

    def _stats_tick(self) -> bool:
        gs = gpu_stats()
        if gs:
            total, used, busy = gs
            self.gpu_summary.set_markup(
                f"<small><span alpha='65%'>{human_size(used)} / {human_size(total)} VRAM</span></small>"
            )
            self.vram_bar.set_fraction(min(1.0, used / total))
            self.vram_bar.set_text(f"VRAM  {int(100 * used / total)}%")
            self.busy_bar.set_fraction(min(1.0, busy / 100))
            self.busy_bar.set_text(f"GPU  {busy}%")
        else:
            self.gpu_summary.set_markup(
                "<small><span alpha='65%'>(sin lectura de GPU vía sysfs)</span></small>"
            )

        m = mem_stats()
        ram_total = m.get("MemTotal", 1)
        ram_used = ram_total - m.get("MemAvailable", 0)
        self.ram_bar.set_fraction(min(1.0, ram_used / ram_total))
        self.ram_bar.set_text(f"RAM  {human_size(ram_used)} / {human_size(ram_total)}")
        self.cpu_lbl.set_markup(
            f"<small><span alpha='65%'>CPU load 1 min: {cpu_load():.2f}</span></small>"
        )

        rss = ollama_proc_rss()
        if rss:
            self.ollama_lbl.set_markup(
                f"<small><span alpha='65%'>Ollama: {human_size(rss)} RAM en proceso</span></small>"
            )
        else:
            self.ollama_lbl.set_markup(
                "<small><span alpha='65%'>Ollama: servicio detenido</span></small>"
            )
        return True


class ModelsTab(Gtk.Box):
    """Download · list · delete models. Same card lenguage as the rest."""

    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=14)
        scrolled.add(outer)
        self.pack_start(scrolled, True, True, 0)

        # — Descargar —
        dl_frame, dl_box = make_card(
            "Descargar modelo nuevo",
            subtitle="Catálogo recomendado o cualquier modelo de ollama.com/library",
        )

        self.combo = Gtk.ComboBoxText()
        self.combo.append_text("— elige uno recomendado —")
        for name, desc in CATALOG:
            self.combo.append_text(f"{name}  ·  {desc}")
        self.combo.set_active(0)
        self.combo.connect("changed", self._on_pick)
        dl_box.pack_start(self.combo, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry = Gtk.Entry(placeholder_text="…o escribe nombre:tag (p.ej. llava:7b)")
        self.btn_pull = Gtk.Button(label="Descargar")
        self.btn_pull.set_tooltip_text("Descarga al disco. No carga en VRAM hasta usarlo.")
        self.btn_pull.connect("clicked", lambda _: self._start_pull())
        row.pack_start(self.entry, True, True, 0)
        row.pack_end(self.btn_pull, False, False, 0)
        dl_box.pack_start(row, False, False, 0)

        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_text("")
        dl_box.pack_start(self.progress, False, False, 0)
        outer.pack_start(dl_frame, False, False, 0)

        # — Instalados —
        ins_frame, ins_box = make_card(
            "Modelos instalados",
            subtitle="Pulsa «chatear» para un terminal con  <tt>ollama run NOMBRE</tt>",
        )
        self.installed_box = ins_box
        outer.pack_start(ins_frame, False, False, 0)

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
                empty_state("(servicio apagado)"), False, False, 0
            )
        elif not installed:
            self.installed_box.pack_start(
                empty_state(
                    "— ningún modelo instalado todavía · usa el bloque de arriba —"
                ),
                False,
                False,
                0,
            )
        else:
            for m in installed:
                name = m.get("name", "?")
                size = human_size(m.get("size", 0))
                bchat = Gtk.Button(label="chatear")
                bchat.set_tooltip_text(f"Abre un terminal con  ollama run {name}")
                bchat.connect("clicked", lambda _w, n=name: open_terminal(f"ollama run {n}"))
                bdel = Gtk.Button(label="🗑")
                bdel.set_tooltip_text(f"Borrar {name} del disco")
                bdel.connect("clicked", lambda _w, n=name: self._del(n))
                self.installed_box.pack_start(
                    model_row(name, size, buttons=[bdel, bchat]), False, False, 0
                )
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


class LogsTab(Gtk.Box):
    """Live tail of journalctl -fu ollama with filters (text + errors-only).

    Architecture: we keep the raw stream in `self._lines` and render only the
    matching subset into the TextView. New lines append both places; toggling
    filters re-renders from the kept buffer (bounded to 5k lines).
    """

    ERROR_KEYWORDS = ("error", "warn", "critical", "fail", "panic")

    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, margin=14)
        self.pack_start(outer, True, True, 0)

        frame, content = make_card(
            "Logs del servicio Ollama",
            subtitle="journalctl -fu ollama · streaming en vivo · autoscroll",
        )

        # — Toolbar: search + filters + actions —
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("🔎  filtrar por texto…")
        self.search_entry.set_tooltip_text(
            "Muestra solo líneas que contengan este texto (case-insensitive)"
        )
        self.search_entry.connect("changed", lambda _w: self._rerender())
        bar.pack_start(self.search_entry, True, True, 0)

        self.errors_toggle = Gtk.ToggleButton(label="solo errores")
        self.errors_toggle.set_tooltip_text(
            "Filtrar a líneas con error / warning / critical / fail / panic"
        )
        self.errors_toggle.connect("toggled", lambda _w: self._rerender())
        bar.pack_end(self.errors_toggle, False, False, 0)

        bbottom = Gtk.Button(label="↓")
        bbottom.set_tooltip_text("Saltar al final")
        bbottom.connect("clicked", lambda _: self._scroll_bottom())
        bar.pack_end(bbottom, False, False, 0)

        bclear = Gtk.Button(label="Limpiar")
        bclear.set_tooltip_text("Vacía el buffer (no afecta al journal real)")
        bclear.connect("clicked", lambda _: self._clear())
        bar.pack_end(bclear, False, False, 0)
        content.pack_start(bar, False, False, 0)

        # — TextView in a scroller —
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.set_min_content_height(280)
        self.view = Gtk.TextView(
            editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR
        )
        self.buf = self.view.get_buffer()
        sw.add(self.view)
        content.pack_start(sw, True, True, 0)
        outer.pack_start(frame, True, True, 0)

        self._lines: list[str] = []  # raw lines kept (cap 5k)
        self.proc: subprocess.Popen | None = None
        self._start_tail()

    # — filtering —
    def _matches(self, line: str) -> bool:
        ll = line.lower()
        if (s := self.search_entry.get_text().strip().lower()) and s not in ll:
            return False
        if self.errors_toggle.get_active() and not any(k in ll for k in self.ERROR_KEYWORDS):
            return False
        return True

    def _rerender(self) -> None:
        self.buf.set_text("")
        for line in self._lines:
            if self._matches(line):
                self.buf.insert(self.buf.get_end_iter(), line)
        self._scroll_bottom()

    def _clear(self) -> None:
        self._lines = []
        self.buf.set_text("")

    def _scroll_bottom(self) -> None:
        adj = self.view.get_vadjustment()
        if adj is not None:
            adj.set_value(adj.get_upper() - adj.get_page_size())

    # — tail —
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
        self._lines.append(line)
        if len(self._lines) > 5000:
            self._lines = self._lines[-4000:]
            # buffer gets the same haircut to stay in sync
            self.buf.delete(self.buf.get_start_iter(), self.buf.get_iter_at_line(1000))
        if self._matches(line):
            self.buf.insert(self.buf.get_end_iter(), line)
            mark = self.buf.create_mark(None, self.buf.get_end_iter(), False)
            self.view.scroll_mark_onscreen(mark)
        return False


class IntegrationsTab(Gtk.Box):
    """Integrations grouped by category in collapsible expanders.

    Each integration is still a uniform card; expanders bundle them by purpose
    (Chat · Código · Imagen · …) so the tab stops growing endlessly downward
    as we add more. Default state: every category open — collapse to taste.
    """

    def __init__(self, app: "App"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=14)
        scrolled.add(outer)
        self.pack_start(scrolled, True, True, 0)

        # Category containers — cards land here, then wrapped in expanders below.
        cat_chat = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=8, margin_start=6
        )
        cat_code = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=8, margin_start=6
        )
        cat_image = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=8, margin_start=6
        )

        # — Open WebUI —
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
        self.btn_webui_start.connect("clicked", lambda _: open_terminal("open-webui serve"))
        self.btn_webui_open = Gtk.Button(label="Abrir en navegador")
        self.btn_webui_open.connect("clicked", lambda _: webbrowser.open(OPEN_WEBUI))
        self.webui_status = Gtk.Label(xalign=0)
        cat_chat.pack_start(
            self._card(
                "💬 Open WebUI",
                "chat web con historial, memoria persistente y RAG (carga documentos)",
                self.webui_status,
                [self.btn_webui_install, self.btn_webui_start, self.btn_webui_open],
            ),
            False,
            False,
            0,
        )

        # — opencode —
        b1 = Gtk.Button(label="Lanzar")
        b1.connect("clicked", lambda _: open_terminal("opencode"))
        b2 = Gtk.Button(label="Web del proyecto")
        b2.connect("clicked", lambda _: webbrowser.open("https://opencode.ai"))
        self.opencode_status = Gtk.Label(xalign=0)
        cat_code.pack_start(
            self._card(
                "⌨️ opencode",
                "asistente de código en terminal",
                self.opencode_status,
                [b1, b2],
            ),
            False,
            False,
            0,
        )

        # — Aider —
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
        self.aider_status = Gtk.Label(xalign=0)
        cat_code.pack_start(
            self._card(
                "🤝 Aider",
                "pair programming con IA en terminal",
                self.aider_status,
                [ai_install, ai_launch],
            ),
            False,
            False,
            0,
        )

        # — ComfyUI —
        comfy_install_cmd = (
            "set -e && "
            "echo '── Instalando ComfyUI con PyTorch ROCm para tu Radeon ──' && "
            "echo 'Esto descarga ~6 GB y tarda ~15-20 minutos. Puedes seguir con otras cosas.' && "
            "echo && "
            f"git clone https://github.com/comfyanonymous/ComfyUI {COMFYUI_DIR} && "
            f"cd {COMFYUI_DIR} && "
            "python3 -m venv venv && "
            "source venv/bin/activate && "
            "pip install --upgrade pip && "
            "pip install torch torchvision --index-url https://download.pytorch.org/whl/rocm6.2 && "
            "pip install -r requirements.txt && "
            "echo && echo '✅ ComfyUI instalado.' && "
            "echo && "
            "echo 'Siguiente paso (no automático):  descarga un checkpoint en' && "
            f"echo '  {COMFYUI_DIR}/models/checkpoints/' && "
            "echo 'Sugerencias para tu GPU 8GB:' && "
            "echo '  · SDXL Turbo (~6 GB, rapidísimo):  https://huggingface.co/stabilityai/sdxl-turbo' && "
            "echo '  · SD 1.5 (~4 GB, mil LoRAs):       https://huggingface.co/runwayml/stable-diffusion-v1-5' && "
            "echo && "
            "echo 'Cuando tengas un .safetensors ahí, vuelve al panel y pulsa \"Iniciar servicio\".'"
        )
        comfy_start_cmd = (
            f"cd {COMFYUI_DIR} && source venv/bin/activate && python main.py --listen"
        )
        self.btn_comfy_install = Gtk.Button(label="Instalar")
        self.btn_comfy_install.connect("clicked", lambda _: open_terminal(comfy_install_cmd))
        self.btn_comfy_start = Gtk.Button(label="Iniciar servicio")
        self.btn_comfy_start.connect("clicked", lambda _: open_terminal(comfy_start_cmd))
        self.btn_comfy_open = Gtk.Button(label="Abrir en navegador")
        self.btn_comfy_open.connect("clicked", lambda _: webbrowser.open(COMFYUI_URL))
        self.comfyui_status = Gtk.Label(xalign=0)
        cat_image.pack_start(
            self._card(
                "🎨 ComfyUI",
                "generación de imagen local · SDXL, SD 1.5, Flux schnell",
                self.comfyui_status,
                [self.btn_comfy_install, self.btn_comfy_start, self.btn_comfy_open],
            ),
            False,
            False,
            0,
        )

        # Wrap each category in its own collapsible expander
        outer.pack_start(
            self._category("chat-message-new-symbolic", "Chat y memoria", cat_chat),
            False, False, 0,
        )
        outer.pack_start(
            self._category("applications-development-symbolic", "Código", cat_code),
            False, False, 0,
        )
        outer.pack_start(
            self._category("applications-graphics-symbolic", "Imagen", cat_image),
            False, False, 0,
        )

        self.refresh()

    def _category(self, icon_name: str, title: str, content: Gtk.Widget) -> Gtk.Expander:
        """A collapsible group: icon · title (bold) → content below.

        Uses a named system icon so it matches the user's theme (light/dark/HC)
        instead of a fixed emoji. Expanded by default; user can fold to taste.
        """
        exp = Gtk.Expander()
        exp.get_style_context().add_class("category")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        head.pack_start(img, False, False, 0)
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup(f"<big><b>{title}</b></big>")
        head.pack_start(lbl, False, False, 0)
        exp.set_label_widget(head)
        exp.set_expanded(True)
        exp.add(content)
        return exp

    def _card(
        self,
        title: str,
        subtitle: str,
        status_widget: Gtk.Label,
        buttons: list[Gtk.Button],
    ) -> Gtk.Frame:
        """A consistent 'integration card': title · subtitle · status · buttons."""
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.get_style_context().add_class("card")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=14)

        t = Gtk.Label(xalign=0)
        t.set_markup(f"<big><b>{title}</b></big>")
        box.pack_start(t, False, False, 0)

        s = Gtk.Label(xalign=0, wrap=True)
        s.set_markup(f"<span alpha='65%'>{subtitle}</span>")
        box.pack_start(s, False, False, 0)

        status_widget.set_xalign(0)
        status_widget.set_line_wrap(True)
        box.pack_start(status_widget, False, False, 4)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for b in buttons:
            btns.pack_start(b, True, True, 0)
        box.pack_start(btns, False, False, 0)

        frame.add(box)
        return frame

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

        if comfyui_installed():
            running = comfyui_running()
            self.comfyui_status.set_markup(
                "✅ instalado · 🟢 corriendo en :8188"
                if running
                else "✅ instalado · 🔴 parado"
            )
            self.btn_comfy_install.set_sensitive(False)
            self.btn_comfy_start.set_sensitive(not running)
            self.btn_comfy_open.set_sensitive(running)
        else:
            self.comfyui_status.set_markup(
                "❌ no instalado · pesa ~10 GB con dependencias + un modelo"
            )
            self.btn_comfy_install.set_sensitive(True)
            self.btn_comfy_start.set_sensitive(False)
            self.btn_comfy_open.set_sensitive(False)


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
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.get_style_context().add_class("card")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=14)
        t = Gtk.Label(xalign=0)
        t.set_markup(f"<big><b>{title}</b></big>")
        d = Gtk.Label(xalign=0, wrap=True)
        d.set_markup(f"<span alpha='65%'>{desc}</span>")
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
        self.set_default_size(860, 640)
        self.set_icon_name("computer")
        self.app = app

        nb = Gtk.Notebook()
        self.add(nb)
        # Tab order optimised for daily use: home → manage → extend → presets → debug.
        # Logs goes last on purpose (you only open it when something's wrong).
        self.tab_dashboard = DashboardTab(app)
        self.tab_models = ModelsTab(app)
        self.tab_integrations = IntegrationsTab(app)
        self.tab_profiles = ProfilesTab(app)
        self.tab_logs = LogsTab(app)
        for w, name in (
            (self.tab_dashboard, "Dashboard"),
            (self.tab_models, "Modelos"),
            (self.tab_integrations, "Integraciones"),
            (self.tab_profiles, "Perfiles"),
            (self.tab_logs, "Logs"),
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
        self.tab_dashboard.refresh(up, loaded)
        self.tab_models.refresh(up, installed)
        self.tab_integrations.refresh()


class App:
    def __init__(self) -> None:
        _install_css()
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
        mi_about.connect("activate", lambda _: self._show_about())
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

    def _show_about(self) -> None:
        """Native GTK About dialog — title, version, license, links, credits."""
        dlg = Gtk.AboutDialog(transient_for=self.window, modal=True)
        dlg.set_program_name("local-ai-control")
        dlg.set_version(VERSION)
        dlg.set_comments(
            "Tray + control panel GTK para tu IA local sobre Ollama.\n"
            "Gestión, monitorización e integraciones — todo desde un sitio."
        )
        dlg.set_website("https://github.com/SebastianAlvarezGajardo/local-ai-control")
        dlg.set_website_label("Repositorio en GitHub")
        dlg.set_authors(["Sebastián Álvarez Gajardo"])
        dlg.set_license_type(Gtk.License.MIT_X11)
        dlg.set_logo_icon_name("computer")
        dlg.set_copyright("© 2026 Sebastián Álvarez Gajardo")
        # Acknowledge what we glue together — visible under "Credits"
        dlg.add_credit_section(
            "Construido sobre",
            ["Ollama — github.com/ollama/ollama", "GTK 3 + PyGObject", "AppIndicator (Ayatana)"],
        )
        dlg.add_credit_section(
            "Integraciones soportadas",
            [
                "Open WebUI — github.com/open-webui/open-webui",
                "ComfyUI — github.com/comfyanonymous/ComfyUI",
                "opencode — opencode.ai",
                "Aider — aider.chat",
            ],
        )
        dlg.run()
        dlg.destroy()

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
