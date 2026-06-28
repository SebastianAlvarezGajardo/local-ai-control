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
import re
import shutil
import shlex
import signal
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
VERSION = "0.8.0"
API = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OPEN_WEBUI = os.environ.get("OPEN_WEBUI_URL", "http://localhost:8080")
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://localhost:8188")
COMFYUI_DIR = os.path.expanduser(os.environ.get("COMFYUI_DIR", "~/ComfyUI"))
# Catálogo de checkpoints descargables a models/checkpoints/. El primero es el
# por defecto (el "mejor"): FLUX.1-schnell fp8 — all-in-one (T5+CLIP+VAE → carga
# directo con el CheckpointLoader estándar), Apache-2.0 (uso comercial libre),
# 4 pasos. ~16 GB no entra en 8 GB de VRAM pero ComfyUI lo corre con offload a
# RAM (más lento, asumido). Flux-dev daría algo más de calidad pero es no
# comercial. Los SDXL son alternativas ligeras que sí caben en VRAM. URLs
# verificadas (HTTP 200) 2026-06-28.
COMFY_MODELS = (
    {
        "key": "flux-schnell",
        "label": "FLUX.1 schnell · ~16 GB · el mejor",
        "filename": "flux1-schnell-fp8.safetensors",
        "url": "https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors",
        "note": "Mejor calidad, Apache-2.0 (uso comercial). Corre con offload a RAM.",
        "wf": {"steps": 4, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "w": 1024, "h": 1024},
    },
    {
        "key": "sdxl-base",
        "label": "SDXL base 1.0 · ~6,5 GB · equilibrado",
        "filename": "sd_xl_base_1.0.safetensors",
        "url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
        "note": "Gran calidad y entra en 8 GB de VRAM. Mil LoRAs disponibles.",
        "wf": {"steps": 30, "cfg": 7.0, "sampler": "dpmpp_2m", "scheduler": "karras", "w": 1024, "h": 1024},
    },
    {
        "key": "sdxl-turbo",
        "label": "SDXL Turbo · ~6,5 GB · rapidísimo (1 paso)",
        "filename": "sd_xl_turbo_1.0_fp16.safetensors",
        "url": "https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors",
        "note": "El más rápido (1 paso), ideal para iterar. Calidad algo menor.",
        "wf": {"steps": 1, "cfg": 1.0, "sampler": "euler_ancestral", "scheduler": "normal", "w": 512, "h": 512},
    },
)
N8N_URL = os.environ.get("N8N_URL", "http://localhost:5678")
# Whisper.cpp: transcripción/voz local. El server trae web UI propia. Usamos
# el :8910 para NO chocar con Open WebUI (:8080, el default de whisper-server).
# Modelo multilingüe `base` (no base.en) para que entienda español.
WHISPER_DIR = os.path.expanduser(os.environ.get("WHISPER_DIR", "~/whisper.cpp"))
WHISPER_PORT = 8910
WHISPER_URL = os.environ.get("WHISPER_URL", f"http://localhost:{WHISPER_PORT}")
WHISPER_MODEL = "base"
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
    # — agentes / tool-calling (úsalos con n8n) —
    ("hermes3:3b", "Hermes 3 3B (Nous) — agente ligero con tool-calling"),
    ("hermes3:8b", "Hermes 3 8B (Nous) — agente para n8n / function calling"),
    # — utilidad —
    ("nomic-embed-text", "Embeddings (para RAG) — ~274 MB"),
]

# Best general-purpose default per VRAM bracket — preload the strongest model the
# hardware can comfortably run alongside the desktop. This is the SINGLE knob for
# "preload the best you can run": bump these as lighter/stronger models ship (e.g.
# a future 4B that beats today's 12B) and every user auto-upgrades on next onboard.
RECOMMENDED_BY_VRAM: list[tuple[float, str]] = [
    (15.0, "gemma3:12b"),  # ~16 GB+ VRAM
    (7.0, "gemma3:4b"),    # ~8 GB VRAM (e.g. RX 7600 / 7700S)
    (0.0, "gemma3:1b"),    # CPU-only / small GPU
]

# First-run state (so the onboarding flow shows exactly once).
STATE_DIR = os.path.expanduser("~/.config/local-ai-control")
STATE_FILE = os.path.join(STATE_DIR, "state.json")


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(d: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(d, f, indent=2)
    except OSError:
        pass


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


def recommended_model() -> str:
    """Strongest catalog model that fits the detected GPU. See RECOMMENDED_BY_VRAM."""
    g = gpu_stats()
    total_gb = (g[0] / 1e9) if g else 0.0
    for need, model in RECOMMENDED_BY_VRAM:
        if total_gb >= need:
            return model
    return "gemma3:1b"


def launch_chat(model: str) -> None:
    """Drop the user straight into a chat: Open WebUI if running, else a terminal."""
    if webui_running():
        webbrowser.open(OPEN_WEBUI)
    else:
        open_terminal(f"ollama run {shlex.quote(model)}")


def set_default_model(name: str) -> None:
    """Remember the user's pick. It becomes the default for chat / «Empezar a usar»."""
    st = load_state()
    st["model"] = name
    save_state(st)


def default_model() -> str | None:
    """Model to use by default: the user's saved pick if still installed, else the
    best one for this GPU, else whatever's installed, else None. The saved pick
    always wins — the recommendation is only the fallback when nothing was chosen."""
    installed = [m.get("name", "") for m in installed_models()]
    pref = load_state().get("model")
    if pref and pref in installed:
        return pref
    rec = recommended_model()
    if rec in installed:
        return rec
    return installed[0] if installed else None


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


def find_binary(name: str, extra_dirs: list[str] | None = None) -> str | None:
    """Find an executable by name — robust against autostart PATH gotchas.

    GNOME autostart entries don't get the user's interactive-shell PATH
    (no `.bashrc` is sourced for non-shell processes), so binaries that
    live under `~/.local/bin`, `~/.<tool>/bin` or inside an nvm Node
    install are often invisible to `shutil.which()`. We try PATH first
    and fall back to a curated list of common per-user install dirs plus
    any caller-provided ones.
    """
    if hit := shutil.which(name):
        return hit
    import glob
    candidates: list[str] = [
        os.path.expanduser(f"~/.local/bin/{name}"),
        os.path.expanduser(f"~/.{name}/bin/{name}"),  # e.g. ~/.opencode/bin/opencode
    ]
    # nvm-installed Node tools live in ~/.nvm/versions/node/<version>/bin/
    candidates += glob.glob(os.path.expanduser(f"~/.nvm/versions/node/*/bin/{name}"))
    if extra_dirs:
        candidates += [os.path.join(os.path.expanduser(d), name) for d in extra_dirs]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def webui_installed() -> bool:
    return find_binary("open-webui") is not None


def webui_running() -> bool:
    try:
        urllib.request.urlopen(OPEN_WEBUI, timeout=1)
        return True
    except Exception:
        return False


def comfyui_installed() -> bool:
    return os.path.isfile(os.path.join(COMFYUI_DIR, "main.py"))


def comfy_workflow_json(model: dict) -> str:
    """Genera un workflow ComfyUI (formato litegraph, arrastrable/abrible) para
    un modelo del catálogo. Los tres usan el mismo grafo básico —
    CheckpointLoaderSimple → CLIPTextEncode×2 → KSampler → VAEDecode → SaveImage—
    y solo cambian los widgets (checkpoint, pasos, cfg, sampler, scheduler,
    tamaño). Estructura validada contra /prompt (node_errors: {})."""
    wf = model["wf"]
    graph = {
        "last_node_id": 9,
        "last_link_id": 9,
        "nodes": [
            {"id": 7, "type": "CLIPTextEncode", "pos": [413, 389], "size": [425, 180],
             "flags": {}, "order": 3, "mode": 0,
             "inputs": [{"name": "clip", "type": "CLIP", "link": 5}],
             "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [6], "slot_index": 0}],
             "properties": {"Node name for S&R": "CLIPTextEncode"}, "widgets_values": [""]},
            {"id": 6, "type": "CLIPTextEncode", "pos": [415, 186], "size": [422, 164],
             "flags": {}, "order": 2, "mode": 0,
             "inputs": [{"name": "clip", "type": "CLIP", "link": 3}],
             "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [4], "slot_index": 0}],
             "properties": {"Node name for S&R": "CLIPTextEncode"},
             "widgets_values": ["a cinematic photo of a red fox in a misty forest at golden hour, highly detailed"]},
            {"id": 5, "type": "EmptyLatentImage", "pos": [473, 609], "size": [315, 106],
             "flags": {}, "order": 0, "mode": 0, "inputs": [],
             "outputs": [{"name": "LATENT", "type": "LATENT", "links": [2], "slot_index": 0}],
             "properties": {"Node name for S&R": "EmptyLatentImage"},
             "widgets_values": [wf["w"], wf["h"], 1]},
            {"id": 3, "type": "KSampler", "pos": [863, 186], "size": [315, 262],
             "flags": {}, "order": 4, "mode": 0,
             "inputs": [
                 {"name": "model", "type": "MODEL", "link": 1},
                 {"name": "positive", "type": "CONDITIONING", "link": 4},
                 {"name": "negative", "type": "CONDITIONING", "link": 6},
                 {"name": "latent_image", "type": "LATENT", "link": 2}],
             "outputs": [{"name": "LATENT", "type": "LATENT", "links": [7], "slot_index": 0}],
             "properties": {"Node name for S&R": "KSampler"},
             "widgets_values": [0, "randomize", wf["steps"], wf["cfg"],
                                wf["sampler"], wf["scheduler"], 1]},
            {"id": 8, "type": "VAEDecode", "pos": [1209, 188], "size": [210, 46],
             "flags": {}, "order": 5, "mode": 0,
             "inputs": [{"name": "samples", "type": "LATENT", "link": 7},
                        {"name": "vae", "type": "VAE", "link": 8}],
             "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [9], "slot_index": 0}],
             "properties": {"Node name for S&R": "VAEDecode"}},
            {"id": 9, "type": "SaveImage", "pos": [1451, 189], "size": [210, 270],
             "flags": {}, "order": 6, "mode": 0,
             "inputs": [{"name": "images", "type": "IMAGE", "link": 9}],
             "outputs": [], "properties": {}, "widgets_values": ["ComfyUI"]},
            {"id": 4, "type": "CheckpointLoaderSimple", "pos": [26, 474], "size": [315, 98],
             "flags": {}, "order": 1, "mode": 0, "inputs": [],
             "outputs": [
                 {"name": "MODEL", "type": "MODEL", "links": [1], "slot_index": 0},
                 {"name": "CLIP", "type": "CLIP", "links": [3, 5], "slot_index": 1},
                 {"name": "VAE", "type": "VAE", "links": [8], "slot_index": 2}],
             "properties": {"Node name for S&R": "CheckpointLoaderSimple"},
             "widgets_values": [model["filename"]]},
        ],
        "links": [
            [1, 4, 0, 3, 0, "MODEL"], [2, 5, 0, 3, 3, "LATENT"],
            [3, 4, 1, 6, 0, "CLIP"], [4, 6, 0, 3, 1, "CONDITIONING"],
            [5, 4, 1, 7, 0, "CLIP"], [6, 7, 0, 3, 2, "CONDITIONING"],
            [7, 3, 0, 8, 0, "LATENT"], [8, 4, 2, 8, 1, "VAE"],
            [9, 8, 0, 9, 0, "IMAGE"],
        ],
        "groups": [], "config": {}, "extra": {}, "version": 0.4,
    }
    return json.dumps(graph, indent=2)


def comfy_install_workflow(model: dict) -> str | None:
    """Escribe el workflow del modelo en user/default/workflows/ (si no existe)
    para que ComfyUI abra con un flujo funcional. Devuelve la ruta o None."""
    wf_dir = os.path.join(COMFYUI_DIR, "user", "default", "workflows")
    path = os.path.join(wf_dir, f"{model['key']}.json")
    try:
        os.makedirs(wf_dir, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(comfy_workflow_json(model))
        return path
    except OSError:
        return None


def comfyui_has_checkpoint() -> bool:
    """True si hay al menos un modelo real en models/checkpoints/.

    ComfyUI arranca sin checkpoint pero no puede generar nada — distinguimos
    el placeholder `put_checkpoints_here` de un .safetensors/.ckpt de verdad.
    """
    ckpt_dir = os.path.join(COMFYUI_DIR, "models", "checkpoints")
    try:
        return any(
            f.endswith((".safetensors", ".ckpt"))
            for f in os.listdir(ckpt_dir)
        )
    except OSError:
        return False


def comfyui_running() -> bool:
    try:
        urllib.request.urlopen(COMFYUI_URL, timeout=1)
        return True
    except Exception:
        return False


def pid_on_port(port: int) -> int | None:
    """PID of the process LISTENING on a TCP port.

    The most reliable 'is this web service up, and which PID do I stop?' —
    independent of how it was launched (relative `python main.py`, a bash
    wrapper, a system vs nvm install…). Returns the listener itself, so
    stopping it frees the port instead of killing a wrapper and orphaning
    the real server. Only sees PIDs the current user can read (fine: these
    run as us). None if nothing listens or `ss` is unavailable.
    """
    try:
        r = subprocess.run(
            ["ss", "-ltnHp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        m = re.search(r"pid=(\d+)", r.stdout)
        if m:
            return int(m.group(1))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def find_pid_matching(pattern: str) -> int | None:
    """First PID whose full command line matches `pattern` (regex via pgrep -f).

    We exclude our own PID just in case the pattern were loose enough to match
    a status line in this app's own command line. Returns None if nothing found.
    """
    try:
        r = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=2
        )
        for line in r.stdout.strip().splitlines():
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            return pid
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def stop_pid(pid: int, timeout: float = 3.0) -> bool:
    """Polite stop: SIGTERM, wait up to `timeout`, escalate to SIGKILL if alive."""
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            os.kill(pid, 0)
            time.sleep(0.2)
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    return True


def webui_pid() -> int | None:
    """Running open-webui server, if any. Port first (robust), then cmdline
    so the '🟡 arrancando' window before it binds :8080 still shows."""
    return pid_on_port(8080) or find_pid_matching(
        r"open[-_]webui.*serve|uvicorn.*open_webui"
    )


def comfyui_pid() -> int | None:
    """Running ComfyUI server, if any. Port first: the real python is just
    `python main.py --listen` (launched via `cd ~/ComfyUI && …`, so the path
    isn't in its args) — only the listener on :8188 is reliable. Cmdline
    fallback covers the bind window before the port is up."""
    return pid_on_port(8188) or find_pid_matching(
        r"ComfyUI.*main\.py|python3?\s+main\.py.*--listen"
    )


def n8n_installed() -> bool:
    return find_binary("n8n") is not None


def n8n_running() -> bool:
    try:
        urllib.request.urlopen(N8N_URL, timeout=1)
        return True
    except Exception:
        return False


def n8n_pid() -> int | None:
    """Find the running n8n server, whatever the install path.

    Matches both our nvm install (`node …/n8n/bin/n8n`) and a system one
    (`node /usr/bin/n8n`). The leading `node \\S*/bin/n8n` deliberately won't
    match the task-runner child (`node … task-runner/dist/start.js`), so we
    return the real server PID, not its helper.
    """
    return pid_on_port(5678) or find_pid_matching(
        r"node\s+\S*/bin/n8n(\s|$)|n8n\s+start\b"
    )


def whisper_server_bin() -> str | None:
    """Ruta al binario whisper-server compilado, si existe."""
    p = os.path.join(WHISPER_DIR, "build", "bin", "whisper-server")
    return p if os.path.isfile(p) else None


def whisper_model_path() -> str:
    return os.path.join(WHISPER_DIR, "models", f"ggml-{WHISPER_MODEL}.bin")


def whisper_installed() -> bool:
    """Necesita el server compilado Y el modelo descargado para ser usable."""
    return whisper_server_bin() is not None and os.path.isfile(whisper_model_path())


def whisper_running() -> bool:
    try:
        urllib.request.urlopen(WHISPER_URL, timeout=1)
        return True
    except Exception:
        return False


def whisper_pid() -> int | None:
    return pid_on_port(WHISPER_PORT) or find_pid_matching(r"whisper-server")


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
            subtitle="«chatear» abre un terminal y fija el modelo · ★ marca el que se usa por defecto",
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
            cur = default_model()
            for m in installed:
                name = m.get("name", "?")
                size = human_size(m.get("size", 0))
                is_def = name == cur
                bstar = Gtk.Button(label="★" if is_def else "☆")
                bstar.set_tooltip_text(
                    "Es tu modelo por defecto" if is_def
                    else "Fijar como modelo por defecto (chat / «Empezar a usar»)"
                )
                bstar.set_sensitive(not is_def)
                bstar.connect("clicked", lambda _w, n=name: self._set_default(n))
                bchat = Gtk.Button(label="chatear")
                bchat.set_tooltip_text(
                    f"Abre un terminal con  ollama run {name}  (y lo fija por defecto)"
                )
                bchat.connect("clicked", lambda _w, n=name: self._chat(n))
                bdel = Gtk.Button(label="🗑")
                bdel.set_tooltip_text(f"Borrar {name} del disco")
                bdel.connect("clicked", lambda _w, n=name: self._del(n))
                extra = "  · <b>por defecto</b>" if is_def else ""
                self.installed_box.pack_start(
                    model_row(name, size, extra=extra, buttons=[bdel, bstar, bchat]),
                    False, False, 0,
                )
        self.show_all()

    def _set_default(self, name: str) -> None:
        set_default_model(name)
        notify("Modelo por defecto", name)
        self.app.refresh_all()

    def _chat(self, name: str) -> None:
        set_default_model(name)  # elegir = usar: queda definido como por defecto
        open_terminal(f"ollama run {shlex.quote(name)}")
        self.app.refresh_all()

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
        cat_auto = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=8, margin_start=6
        )
        cat_audio = Gtk.Box(
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
        self.btn_webui_start.connect("clicked", self._toggle_webui)
        self.btn_webui_open = Gtk.Button(label="Abrir en navegador")
        self.btn_webui_open.connect("clicked", lambda _: webbrowser.open(OPEN_WEBUI))
        # so set_visible() actually sticks past show_all() — see refresh()
        self.btn_webui_install.set_no_show_all(True)
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
        self.btn_oc_launch = Gtk.Button(label="💻 Lanzar en carpeta…")
        self.btn_oc_launch.set_tooltip_text(
            "Elige una carpeta de proyecto. Se abre un terminal en ella con opencode listo."
        )
        self.btn_oc_launch.connect("clicked", self._launch_opencode)
        self.btn_oc_docs = Gtk.Button(label="Docs")
        self.btn_oc_docs.set_tooltip_text("Abre opencode.ai en el navegador")
        self.btn_oc_docs.connect("clicked", lambda _: webbrowser.open("https://opencode.ai"))
        self.opencode_status = Gtk.Label(xalign=0)
        cat_code.pack_start(
            self._card(
                "⌨️ opencode",
                "asistente de código en terminal",
                self.opencode_status,
                [self.btn_oc_launch, self.btn_oc_docs],
            ),
            False,
            False,
            0,
        )

        # — Aider —
        self.btn_aider_install = Gtk.Button(label="Instalar (pipx)")
        self.btn_aider_install.connect(
            "clicked",
            lambda _: open_terminal(
                "(command -v pipx >/dev/null || (sudo apt update && sudo apt install -y pipx && pipx ensurepath)) && pipx install aider-chat"
            ),
        )
        self.btn_aider_launch = Gtk.Button(label="💻 Lanzar en carpeta…")
        self.btn_aider_launch.set_tooltip_text(
            "Elige una carpeta de proyecto. Se abre un terminal en ella con aider + qwen2.5-coder."
        )
        self.btn_aider_launch.connect("clicked", self._launch_aider)
        self.btn_aider_install.set_no_show_all(True)
        self.aider_status = Gtk.Label(xalign=0)
        cat_code.pack_start(
            self._card(
                "🤝 Aider",
                "pair programming con IA en terminal",
                self.aider_status,
                [self.btn_aider_install, self.btn_aider_launch],
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
            # IMPORTANT: install torch + torchvision + torchaudio together from
            # the ROCm index, otherwise requirements.txt pulls a CUDA-built
            # torchaudio from PyPI and ComfyUI fails on import with
            # 'libcudart.so.13: cannot open shared object file'.
            "pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.2 && "
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
        # HSA_OVERRIDE_GFX_VERSION ayuda a que PyTorch ROCm reconozca GPUs
        # AMD relativamente nuevas que aún no están listadas explícitamente
        # en algunos wheels (Navi 33 / gfx1102 es el caso típico). Inofensivo
        # en otros casos. Si tu user no está en los grupos render/video la
        # GPU sigue siendo inaccesible — esto NO lo arregla. Documentado
        # en el README.
        comfy_start_cmd = (
            f"cd {COMFYUI_DIR} && "
            "source venv/bin/activate && "
            "HSA_OVERRIDE_GFX_VERSION=11.0.0 python main.py --listen"
        )
        self._comfy_start_cmd = comfy_start_cmd  # remembered for the toggle button
        self.btn_comfy_install = Gtk.Button(label="Instalar")
        self.btn_comfy_install.connect("clicked", lambda _: open_terminal(comfy_install_cmd))
        self.btn_comfy_start = Gtk.Button(label="Iniciar servicio")
        self.btn_comfy_start.connect("clicked", self._toggle_comfyui)
        self.btn_comfy_open = Gtk.Button(label="Abrir en navegador")
        self.btn_comfy_open.connect("clicked", lambda _: webbrowser.open(COMFYUI_URL))
        # Desplegable de modelo + botón de descarga. El primero del catálogo
        # (FLUX.1 schnell, el mejor) queda preseleccionado; el tooltip muestra
        # la nota del modelo elegido y se actualiza al cambiar la selección.
        self.combo_comfy_model = Gtk.ComboBoxText()
        for m in COMFY_MODELS:
            self.combo_comfy_model.append(m["key"], m["label"])
        self.combo_comfy_model.set_active(0)
        self.combo_comfy_model.connect("changed", self._on_comfy_model_changed)
        self.btn_comfy_model = Gtk.Button(label="⬇ Descargar")
        self.btn_comfy_model.connect("clicked", self._download_comfy_model)
        self.btn_comfy_install.set_no_show_all(True)
        self.btn_comfy_model.set_no_show_all(True)
        self.combo_comfy_model.set_no_show_all(True)
        self._on_comfy_model_changed(self.combo_comfy_model)  # tooltip inicial
        self.comfyui_status = Gtk.Label(xalign=0)
        cat_image.pack_start(
            self._card(
                "🎨 ComfyUI",
                "generación de imagen local · SDXL, SD 1.5, Flux schnell",
                self.comfyui_status,
                [
                    self.btn_comfy_install,
                    self.combo_comfy_model,
                    self.btn_comfy_model,
                    self.btn_comfy_start,
                    self.btn_comfy_open,
                ],
            ),
            False,
            False,
            0,
        )

        # — n8n (workflows / agentes) —
        # Las llamadas a Node se hacen vía  '. ~/.nvm/nvm.sh && …'  porque
        # nvm hookea el PATH solo en shells interactivas; lo cargamos
        # explícitamente para no depender de qué shell abrió el terminal.
        n8n_install_cmd = (
            "set -e && "
            "echo '── Instalando n8n vía npm (global, sin sudo gracias a nvm) ──' && "
            "echo 'Tarda ~1-2 min, descarga ~200 MB.' && echo && "
            ". ~/.nvm/nvm.sh && "
            "npm install -g n8n && "
            "echo && echo '✅ n8n instalado.' && "
            "echo && "
            "echo 'Próximo paso: vuelve al panel y pulsa \"Iniciar servicio\".' && "
            "echo 'La primera vez tardará ~30s en arrancar.'"
        )
        n8n_start_cmd = ". ~/.nvm/nvm.sh && n8n"

        self.n8n_install_cmd = n8n_install_cmd
        self._n8n_start_cmd = n8n_start_cmd

        self.btn_n8n_install = Gtk.Button(label="Instalar (npm)")
        self.btn_n8n_install.connect("clicked", lambda _: open_terminal(n8n_install_cmd))
        self.btn_n8n_start = Gtk.Button(label="Iniciar servicio")
        self.btn_n8n_start.connect("clicked", self._toggle_n8n)
        self.btn_n8n_open = Gtk.Button(label="Abrir en navegador")
        self.btn_n8n_open.connect("clicked", lambda _: webbrowser.open(N8N_URL))
        self.btn_n8n_install.set_no_show_all(True)
        self.n8n_status = Gtk.Label(xalign=0)
        cat_auto.pack_start(
            self._card(
                "🔁 n8n",
                "workflows visuales · nodo nativo de Ollama · perfecto con hermes3 para agentes",
                self.n8n_status,
                [self.btn_n8n_install, self.btn_n8n_start, self.btn_n8n_open],
            ),
            False,
            False,
            0,
        )

        # — Whisper (transcripción / voz) —
        # whisper.cpp: clona + compila con cmake + baja el modelo multilingüe
        # `base`. El server trae su propia web UI; lo servimos en :8910 para no
        # chocar con Open WebUI (:8080).
        whisper_install_cmd = (
            "set -e && "
            "echo '── Instalando whisper.cpp (transcripción local) ──' && "
            "echo 'Compila con cmake y baja el modelo base (~150 MB). Tarda unos minutos.' && "
            "echo && "
            "command -v cmake >/dev/null || { echo '❌ Falta cmake. Instala: sudo apt install cmake build-essential'; exit 1; } && "
            f"git clone https://github.com/ggml-org/whisper.cpp {shlex.quote(WHISPER_DIR)} 2>/dev/null || echo '(ya clonado, sigo)' && "
            f"cd {shlex.quote(WHISPER_DIR)} && "
            "cmake -B build && "
            "cmake --build build --config Release -j && "
            f"bash ./models/download-ggml-model.sh {WHISPER_MODEL} && "
            "echo && echo '✅ whisper.cpp listo. Vuelve al panel y pulsa \"Iniciar servicio\".'"
        )
        whisper_start_cmd = (
            f"cd {shlex.quote(WHISPER_DIR)} && "
            f"./build/bin/whisper-server -m {shlex.quote(whisper_model_path())} "
            f"--host 0.0.0.0 --port {WHISPER_PORT}"
        )
        self._whisper_install_cmd = whisper_install_cmd
        self._whisper_start_cmd = whisper_start_cmd
        self.btn_whisper_install = Gtk.Button(label="Instalar")
        self.btn_whisper_install.connect(
            "clicked", lambda _: open_terminal(whisper_install_cmd)
        )
        self.btn_whisper_start = Gtk.Button(label="Iniciar servicio")
        self.btn_whisper_start.connect("clicked", self._toggle_whisper)
        self.btn_whisper_open = Gtk.Button(label="Abrir en navegador")
        self.btn_whisper_open.connect("clicked", lambda _: webbrowser.open(WHISPER_URL))
        self.btn_whisper_install.set_no_show_all(True)
        self.whisper_status = Gtk.Label(xalign=0)
        cat_audio.pack_start(
            self._card(
                "🎙️ Whisper",
                "transcripción de voz local · whisper.cpp · multilingüe (español)",
                self.whisper_status,
                [self.btn_whisper_install, self.btn_whisper_start, self.btn_whisper_open],
            ),
            False,
            False,
            0,
        )

        # Wrap each category in its own collapsible expander.
        # Default: only "Chat y memoria" expanded — the rest collapse to one
        # line so the tab fits in one screen. Click to expand on demand.
        outer.pack_start(
            self._category(
                "chat-message-new-symbolic", "Chat y memoria", cat_chat, expanded=True
            ),
            False, False, 0,
        )
        outer.pack_start(
            self._category(
                "applications-development-symbolic", "Código", cat_code, expanded=False
            ),
            False, False, 0,
        )
        outer.pack_start(
            self._category(
                "applications-graphics-symbolic", "Imagen", cat_image, expanded=False
            ),
            False, False, 0,
        )
        outer.pack_start(
            self._category(
                "system-run-symbolic", "Automatización", cat_auto, expanded=False
            ),
            False, False, 0,
        )
        outer.pack_start(
            self._category(
                "audio-input-microphone-symbolic", "Audio / voz", cat_audio, expanded=False
            ),
            False, False, 0,
        )

        self._ensure_comfy_workflows()
        self.refresh()

    def _ensure_comfy_workflows(self) -> None:
        """Para cada modelo cuyo checkpoint ya esté descargado, deja su workflow
        en user/default/workflows/ (una vez). Cubre modelos bajados antes de
        existir esta feature, como el Flux de la 1ª descarga."""
        if not comfyui_installed():
            return
        ckpt_dir = os.path.join(COMFYUI_DIR, "models", "checkpoints")
        for m in COMFY_MODELS:
            if os.path.isfile(os.path.join(ckpt_dir, m["filename"])):
                comfy_install_workflow(m)

    def _pick_folder(self, title: str) -> str | None:
        """Open a GTK folder picker centered on our window. Returns path or None."""
        dlg = Gtk.FileChooserDialog(
            title=title,
            transient_for=self.app.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(
            "Cancelar", Gtk.ResponseType.CANCEL,
            "Abrir aquí", Gtk.ResponseType.ACCEPT,
        )
        dlg.set_local_only(True)
        dlg.set_current_folder(os.path.expanduser("~"))
        response = dlg.run()
        chosen = dlg.get_filename() if response == Gtk.ResponseType.ACCEPT else None
        dlg.destroy()
        return chosen

    def _launch_opencode(self, _w: Gtk.Button) -> None:
        # Resolve the absolute path: gnome-terminal's `bash -c` is non-login/
        # non-interactive and never sources .bashrc/.profile, so ~/.opencode/bin
        # isn't on its PATH → bare `opencode` gives "orden no encontrada".
        binary = find_binary("opencode")
        if not binary:
            return
        folder = self._pick_folder("Carpeta de proyecto para opencode")
        if folder:
            open_terminal(f"cd {shlex.quote(folder)} && {shlex.quote(binary)}")

    def _launch_aider(self, _w: Gtk.Button) -> None:
        # Same PATH caveat as opencode — use the absolute path find_binary found.
        binary = find_binary("aider")
        if not binary:
            return
        folder = self._pick_folder("Carpeta de proyecto para Aider")
        if folder:
            open_terminal(
                f"cd {shlex.quote(folder)} && "
                f"{shlex.quote(binary)} "
                "--model ollama_chat/qwen2.5-coder:7b --no-show-model-warnings"
            )

    def _open_when_ready(self, url: str, ready_fn, timeout: float = 120.0) -> None:
        """Poll a just-started web service in the background and open the
        browser once its HTTP endpoint answers — so 'Iniciar' means
        enciende-y-usa without hunting for the Abrir button afterwards.

        Runs in a daemon thread (HTTP polling would freeze the GTK loop);
        all UI/browser calls are bounced back via GLib.idle_add.
        """

        def open_once() -> bool:
            # CRÍTICO: idle_add RE-EJECUTA el callback mientras devuelva True, y
            # webbrowser.open() devuelve True al abrir → pasarlo tal cual reabría
            # la pestaña en bucle infinito (bug "mil pestañas"). Devolver False
            # quita el handler tras una sola apertura.
            webbrowser.open(url)
            return False

        def worker() -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if ready_fn():
                    GLib.idle_add(open_once)
                    GLib.idle_add(self.app.refresh_all)
                    return
                time.sleep(1.5)

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_webui(self, _w: Gtk.Button) -> None:
        """One button to rule both: start if stopped, stop if running."""
        if pid := webui_pid():
            ok = stop_pid(pid)
            notify("Open WebUI parado" if ok else "No se pudo parar Open WebUI", f"PID {pid}")
            GLib.idle_add(self.app.refresh_all)
        else:
            open_terminal("open-webui serve")
            self._open_when_ready(OPEN_WEBUI, webui_running)

    def _selected_comfy_model(self) -> dict:
        """El modelo elegido en el desplegable (o el primero como fallback)."""
        key = self.combo_comfy_model.get_active_id()
        for m in COMFY_MODELS:
            if m["key"] == key:
                return m
        return COMFY_MODELS[0]

    def _on_comfy_model_changed(self, combo: Gtk.ComboBoxText) -> None:
        m = self._selected_comfy_model()
        self.btn_comfy_model.set_tooltip_text(
            f"Descarga {m['filename']} en models/checkpoints/. {m['note']}"
        )

    def _download_comfy_model(self, _w: Gtk.Button) -> None:
        model = self._selected_comfy_model()
        ckpt_dir = os.path.join(COMFYUI_DIR, "models", "checkpoints")
        dest = os.path.join(ckpt_dir, model["filename"])
        # wget -c reanuda si se cortó; --show-progress da barra en el terminal.
        # Al terminar refrescamos el panel para reflejar que ya hay checkpoint.
        cmd = (
            f"mkdir -p {shlex.quote(ckpt_dir)} && "
            f"echo {shlex.quote('── Descargando ' + model['label'] + ' ──')} && "
            f"echo 'Destino: {dest}' && echo && "
            f"wget -c --show-progress -O {shlex.quote(dest)} "
            f"{shlex.quote(model['url'])} && "
            "echo && echo '✅ Modelo listo. Vuelve al panel y pulsa \"Iniciar servicio\".'"
        )
        open_terminal(cmd)
        # Deja un workflow funcional para este modelo → ComfyUI abre listo para
        # generar (sin el lío de plantillas que piden modelos que no tienes).
        comfy_install_workflow(model)

        def watch() -> None:
            # Espera (hasta 1h) a que el .safetensors exista y deje de crecer.
            deadline = time.monotonic() + 3600
            last = -1
            while time.monotonic() < deadline:
                try:
                    size = os.path.getsize(dest)
                except OSError:
                    size = -1
                if size > 0 and size == last and comfyui_has_checkpoint():
                    GLib.idle_add(self.app.refresh_all)
                    return
                last = size
                time.sleep(5)

        threading.Thread(target=watch, daemon=True).start()

    def _toggle_comfyui(self, _w: Gtk.Button) -> None:
        if pid := comfyui_pid():
            ok = stop_pid(pid)
            notify("ComfyUI parado" if ok else "No se pudo parar ComfyUI", f"PID {pid}")
            GLib.idle_add(self.app.refresh_all)
        else:
            open_terminal(self._comfy_start_cmd)
            self._open_when_ready(COMFYUI_URL, comfyui_running)

    def _toggle_n8n(self, _w: Gtk.Button) -> None:
        if pid := n8n_pid():
            ok = stop_pid(pid)
            notify("n8n parado" if ok else "No se pudo parar n8n", f"PID {pid}")
            GLib.idle_add(self.app.refresh_all)
        else:
            open_terminal(self._n8n_start_cmd)
            self._open_when_ready(N8N_URL, n8n_running)

    def _toggle_whisper(self, _w: Gtk.Button) -> None:
        if pid := whisper_pid():
            ok = stop_pid(pid)
            notify("Whisper parado" if ok else "No se pudo parar Whisper", f"PID {pid}")
            GLib.idle_add(self.app.refresh_all)
        else:
            open_terminal(self._whisper_start_cmd)
            self._open_when_ready(WHISPER_URL, whisper_running)

    def _category(
        self,
        icon_name: str,
        title: str,
        content: Gtk.Widget,
        expanded: bool = False,
    ) -> Gtk.Expander:
        """A collapsible group: icon · title (bold) → content below.

        Uses a named system icon so it matches the user's theme (light/dark/HC)
        instead of a fixed emoji. Caller chooses default expand state — we
        keep "Chat y memoria" open and the rest collapsed so the tab fits
        in one screen by default.
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
        exp.set_expanded(expanded)
        exp.add(content)
        return exp

    def _card(
        self,
        title: str,
        subtitle: str,
        status_widget: Gtk.Label,
        buttons: list[Gtk.Button],
    ) -> Gtk.Frame:
        """A consistent 'integration card': title · subtitle · status · buttons.

        Compact margin (10) inside the expander parent — total padding stays
        readable but the tab doesn't feel like it eats the whole screen.
        """
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.get_style_context().add_class("card")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=10)

        t = Gtk.Label(xalign=0)
        t.set_markup(f"<big><b>{title}</b></big>")
        box.pack_start(t, False, False, 0)

        s = Gtk.Label(xalign=0, wrap=True)
        s.set_markup(f"<span alpha='65%'>{subtitle}</span>")
        box.pack_start(s, False, False, 0)

        status_widget.set_xalign(0)
        status_widget.set_line_wrap(True)
        box.pack_start(status_widget, False, False, 2)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btns.set_no_show_all(False)
        for b in buttons:
            btns.pack_start(b, True, True, 0)
        box.pack_start(btns, False, False, 0)

        frame.add(box)
        return frame

    def refresh(self) -> None:
        # ── Open WebUI ──
        if webui_installed():
            pid = webui_pid()
            http_ok = webui_running() if pid else False
            if pid and http_ok:
                self.webui_status.set_markup(
                    f"✅ instalado · 🟢 corriendo en :8080 · PID {pid}"
                )
                self.btn_webui_start.set_label("⏹ Parar")
            elif pid:
                self.webui_status.set_markup(
                    f"✅ instalado · 🟡 arrancando (PID {pid})…"
                )
                self.btn_webui_start.set_label("⏹ Parar")
            else:
                self.webui_status.set_markup("✅ instalado · 🔴 parado")
                self.btn_webui_start.set_label("Iniciar servicio")
            self.btn_webui_install.set_visible(False)  # ya está, fuera ruido
            self.btn_webui_start.set_sensitive(True)
            self.btn_webui_open.set_sensitive(bool(http_ok))
        else:
            self.webui_status.set_markup("❌ no instalado")
            self.btn_webui_start.set_label("Iniciar servicio")
            self.btn_webui_install.set_visible(True)
            self.btn_webui_install.set_sensitive(True)
            self.btn_webui_start.set_sensitive(False)
            self.btn_webui_open.set_sensitive(False)

        # ── opencode / Aider (no son servicios persistentes — solo "instalado") ──
        # Cuando ya están instalados ocultamos el botón Install para que el
        # único call-to-action visible sea "Lanzar en carpeta…" → 2 clicks
        # y la tool está usable.
        oc_installed = bool(find_binary("opencode"))
        self.opencode_status.set_markup(
            "✅ instalado"
            if oc_installed
            else "❌ no instalado · <tt>curl -fsSL https://opencode.ai/install | bash</tt>"
        )
        self.btn_oc_launch.set_sensitive(oc_installed)

        aider_installed = bool(find_binary("aider"))
        self.aider_status.set_markup(
            "✅ instalado" if aider_installed else "❌ no instalado · pulsa Instalar (pipx)"
        )
        self.btn_aider_install.set_visible(not aider_installed)
        self.btn_aider_launch.set_sensitive(aider_installed)

        # ── ComfyUI ──
        if comfyui_installed():
            pid = comfyui_pid()
            http_ok = comfyui_running() if pid else False
            has_model = comfyui_has_checkpoint()
            no_model = "" if has_model else " · ⚠️ sin modelo (Descargar)"
            if pid and http_ok:
                self.comfyui_status.set_markup(
                    f"✅ instalado · 🟢 corriendo en :8188 · PID {pid}{no_model}"
                )
                self.btn_comfy_start.set_label("⏹ Parar")
            elif pid:
                self.comfyui_status.set_markup(
                    f"✅ instalado · 🟡 arrancando (PID {pid})…"
                )
                self.btn_comfy_start.set_label("⏹ Parar")
            else:
                self.comfyui_status.set_markup(
                    f"✅ instalado · 🔴 parado{no_model}"
                )
                self.btn_comfy_start.set_label("Iniciar servicio")
            self.btn_comfy_install.set_visible(False)  # ya está
            # Desplegable + descarga siempre visibles con ComfyUI instalado:
            # sirve para el 1er modelo y para añadir/cambiar después.
            self.combo_comfy_model.set_visible(True)
            self.btn_comfy_model.set_visible(True)
            self.btn_comfy_start.set_sensitive(True)
            self.btn_comfy_open.set_sensitive(bool(http_ok))
        else:
            self.comfyui_status.set_markup(
                "❌ no instalado · pesa ~10 GB con dependencias + un modelo"
            )
            self.btn_comfy_start.set_label("Iniciar servicio")
            self.btn_comfy_install.set_visible(True)
            self.btn_comfy_install.set_sensitive(True)
            self.combo_comfy_model.set_visible(False)  # sin ComfyUI no hay dónde ponerlo
            self.btn_comfy_model.set_visible(False)
            self.btn_comfy_start.set_sensitive(False)
            self.btn_comfy_open.set_sensitive(False)

        # ── n8n ──
        if n8n_installed():
            pid = n8n_pid()
            http_ok = n8n_running() if pid else False
            if pid and http_ok:
                self.n8n_status.set_markup(
                    f"✅ instalado · 🟢 corriendo en :5678 · PID {pid}"
                )
                self.btn_n8n_start.set_label("⏹ Parar")
            elif pid:
                self.n8n_status.set_markup(
                    f"✅ instalado · 🟡 arrancando (PID {pid})…"
                )
                self.btn_n8n_start.set_label("⏹ Parar")
            else:
                self.n8n_status.set_markup("✅ instalado · 🔴 parado")
                self.btn_n8n_start.set_label("Iniciar servicio")
            self.btn_n8n_install.set_visible(False)
            self.btn_n8n_start.set_sensitive(True)
            self.btn_n8n_open.set_sensitive(bool(http_ok))
        else:
            self.n8n_status.set_markup("❌ no instalado · pesa ~200 MB en node_modules")
            self.btn_n8n_start.set_label("Iniciar servicio")
            self.btn_n8n_install.set_visible(True)
            self.btn_n8n_install.set_sensitive(True)
            self.btn_n8n_start.set_sensitive(False)
            self.btn_n8n_open.set_sensitive(False)

        # ── Whisper ──
        if whisper_installed():
            pid = whisper_pid()
            http_ok = whisper_running() if pid else False
            if pid and http_ok:
                self.whisper_status.set_markup(
                    f"✅ instalado · 🟢 corriendo en :{WHISPER_PORT} · PID {pid}"
                )
                self.btn_whisper_start.set_label("⏹ Parar")
            elif pid:
                self.whisper_status.set_markup(
                    f"✅ instalado · 🟡 arrancando (PID {pid})…"
                )
                self.btn_whisper_start.set_label("⏹ Parar")
            else:
                self.whisper_status.set_markup("✅ instalado · 🔴 parado")
                self.btn_whisper_start.set_label("Iniciar servicio")
            self.btn_whisper_install.set_visible(False)
            self.btn_whisper_start.set_sensitive(True)
            self.btn_whisper_open.set_sensitive(bool(http_ok))
        else:
            self.whisper_status.set_markup(
                "❌ no instalado · compila whisper.cpp + modelo (~150 MB)"
            )
            self.btn_whisper_start.set_label("Iniciar servicio")
            self.btn_whisper_install.set_visible(True)
            self.btn_whisper_install.set_sensitive(True)
            self.btn_whisper_start.set_sensitive(False)
            self.btn_whisper_open.set_sensitive(False)


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
class OnboardingWindow(Gtk.Window):
    """First-run flow: turn Ollama on, preload the best model this GPU can run, then
    drop straight into chat. Zero decisions — this is the 'enciende y usa' promise.

    Shown once (guarded by state['onboarded']). All blocking work runs in a worker
    thread; UI touches go through GLib.idle_add so the panel never freezes.
    """

    def __init__(self, app: "App"):
        super().__init__(title="Bienvenido · local-ai-control")
        self.app = app
        self.set_default_size(480, 360)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("local-ai-control")
        # Respect a prior pick if there is one; otherwise the best model for this GPU.
        self.model = default_model() or recommended_model()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
        self.add(outer)

        title = Gtk.Label(xalign=0)
        title.set_markup(
            "<span size='22000'>👋</span>  <big><b>Tu IA local, lista en un momento</b></big>"
        )
        outer.pack_start(title, False, False, 0)
        sub = Gtk.Label(xalign=0, wrap=True)
        sub.set_markup(
            "<span alpha='70%'>No tienes que configurar nada. Enciendo el servicio, "
            "preparo el mejor modelo que admite tu equipo y empiezas a usarlo.</span>"
        )
        outer.pack_start(sub, False, False, 0)

        steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8)
        self.step_service = Gtk.Label(xalign=0)
        self.step_model = Gtk.Label(xalign=0)
        steps.pack_start(self.step_service, False, False, 0)
        steps.pack_start(self.step_model, False, False, 0)
        outer.pack_start(steps, False, False, 0)
        self._set_step(self.step_service, "pending", "Encender el servicio Ollama")
        self._set_step(self.step_model, "pending", "Preparar el mejor modelo para tu GPU")

        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_text("")
        outer.pack_start(self.progress, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=8)
        self.btn_skip = Gtk.Button(label="Saltar e ir al panel")
        self.btn_skip.connect("clicked", lambda _: self._finish(open_chat=False))
        self.btn_start = Gtk.Button(label="🚀 Empezar a usar")
        self.btn_start.get_style_context().add_class("suggested-action")
        self.btn_start.set_sensitive(False)
        self.btn_start.connect("clicked", lambda _: self._finish(open_chat=True))
        btns.pack_start(self.btn_skip, False, False, 0)
        btns.pack_end(self.btn_start, False, False, 0)
        outer.pack_end(btns, False, False, 0)

        self.connect("delete-event", self._on_close)
        self.show_all()
        threading.Thread(target=self._run_steps, daemon=True).start()

    @staticmethod
    def _set_step(lbl: Gtk.Label, state: str, text: str) -> bool:
        icon = {"pending": "⏳", "active": "🔄", "done": "✓", "fail": "⚠"}.get(state, "•")
        lbl.set_markup(f"<big>{icon}</big>  {GLib.markup_escape_text(text)}")
        return False

    def _run_steps(self) -> None:
        # 1) service on (start it and wait up to ~10s for the API to answer)
        GLib.idle_add(
            self._set_step, self.step_service, "active", "Encendiendo el servicio Ollama…"
        )
        if not service_up():
            systemctl("start")
            for _ in range(20):
                if service_up():
                    break
                time.sleep(0.5)
        if not service_up():
            GLib.idle_add(
                self._set_step, self.step_service, "fail",
                "No se pudo encender Ollama (hazlo desde el panel)",
            )
            GLib.idle_add(self._fail, "No pude arrancar el servicio.")
            return
        GLib.idle_add(self._set_step, self.step_service, "done", "Servicio Ollama encendido")

        # 2) ensure a usable model — never re-download if one already exists
        installed = [m.get("name", "") for m in installed_models()]
        if self.model in installed:
            chosen = self.model
        elif installed:
            chosen = installed[0]  # already have something usable → zero wait
        else:
            chosen = None

        if chosen:
            self.model = chosen
            set_default_model(chosen)
            GLib.idle_add(self._set_step, self.step_model, "done", f"Modelo listo: {chosen}")
            GLib.idle_add(self._ready)
            return

        # nothing installed → download the recommended one with live progress
        GLib.idle_add(
            self._set_step, self.step_model, "active", f"Descargando {self.model}…"
        )

        def on_prog(status, pct):  # already marshalled to main loop by pull_model_stream
            if pct is not None:
                self.progress.set_fraction(pct)
                self.progress.set_text(f"{status} · {int(pct * 100)}%")
            else:
                self.progress.pulse()
                self.progress.set_text(status)
            return False

        pull_model_stream(self.model, on_prog, lambda *_: False)  # blocks until stream ends
        if any(m.get("name") == self.model for m in installed_models()):
            set_default_model(self.model)
            GLib.idle_add(self._set_step, self.step_model, "done", f"Modelo listo: {self.model}")
            GLib.idle_add(self._ready)
        else:
            GLib.idle_add(
                self._set_step, self.step_model, "fail", "No se pudo descargar el modelo"
            )
            GLib.idle_add(self._fail, "Falló la descarga. Puedes hacerlo luego en «Modelos».")

    def _ready(self) -> bool:
        self.progress.set_fraction(1.0)
        self.progress.set_text("Todo listo ✓")
        self.btn_start.set_sensitive(True)
        return False

    def _fail(self, msg: str) -> bool:
        self.progress.set_fraction(0)
        self.progress.set_text(f"⚠ {msg}")
        self.btn_skip.set_label("Ir al panel")
        return False

    def _on_close(self, *_) -> bool:
        self._finish(open_chat=False)
        return True

    def _finish(self, open_chat: bool) -> None:
        st = load_state()
        st["onboarded"] = True
        save_state(st)
        if open_chat:
            launch_chat(self.model)
        self.app._onboarding = None
        self.destroy()
        if not open_chat:
            self.app.window.show_all()
            self.app.window.present()
        self.app.refresh_all()


class ControlWindow(Gtk.Window):
    def __init__(self, app: "App"):
        super().__init__(title=f"local-ai-control · v{VERSION}")
        self.set_default_size(860, 640)
        self.set_icon_name("local-ai-control")
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
        # First run: hide the panel and run the zero-friction onboarding instead.
        self._onboarding = None
        if not load_state().get("onboarded"):
            self.window.hide()
            self._onboarding = OnboardingWindow(self)
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
    if show and not app._onboarding:
        app.window.present()
    Gtk.main()


if __name__ == "__main__":
    main()
