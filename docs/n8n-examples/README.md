# Workflows de ejemplo para n8n

Flujos listos para importar que usan tu **IA local (Ollama)** desde n8n. No
necesitan ninguna API key externa: hablan con Ollama en `http://localhost:11434`.

## `rss-hermes-resumen.json`

**RSS → resumen con Hermes 3 → salida estructurada.** Lee un feed RSS (por
defecto la portada de Hacker News), coge las 5 primeras noticias y pide a
**Hermes 3** (`hermes3:8b`) un resumen de 2 frases en español de cada una.
Salida: `titulo`, `resumen`, `enlace`.

Es la base de un agente útil: cambia el destino final por **Send Email**,
**Telegram** o **Slack** y tienes un boletín automático.

### Requisitos
- Ollama corriendo y el modelo descargado: en el panel → **Modelos** baja
  `hermes3:8b` (o `ollama pull hermes3:8b`).
- n8n corriendo: panel → **Integraciones → Automatización → Iniciar**.

### Cómo importarlo
1. Abre n8n (`http://localhost:5678`).
2. Menú **⋮ → Import from File…** y elige este `.json`.
3. Pulsa **Execute Workflow** (botón "Al hacer clic").

### Notas
- El nodo HTTP construye el cuerpo con `JSON.stringify(...)` para que los
  títulos con comillas no rompan el JSON.
- Para cambiar el feed: nodo **Leer RSS** → `url`.
- Para otro modelo: nodo **Resumir con Hermes 3** → cambia `hermes3:8b` en el
  cuerpo (p. ej. `gemma3:4b`).
- Hermes 3 está afinado para *tool calling*, así que este flujo escala bien a
  agentes con herramientas.
