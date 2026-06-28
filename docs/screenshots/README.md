# Screenshots

Las capturas que muestra el README principal viven aquí. Para que se rendericen,
**guárdalas con estos nombres exactos** (una por pestaña de la app):

| Archivo | Qué capturar |
| --- | --- |
| `01-dashboard.png` | Pestaña **Dashboard** con un modelo cargado: estado (Encendida), botones Encender/Apagar/Liberar VRAM y las cards de GPU y Sistema con barras en vivo. |
| `02-modelos.png` | Pestaña **Modelos**: catálogo recomendado, caja de descarga y lista de instalados (con la ★ del modelo por defecto). |
| `03-integraciones.png` | Pestaña **Integraciones** con las categorías expandidas: Open WebUI, opencode, Aider, ComfyUI (desplegable de modelo) y n8n. |
| `04-perfiles.png` | Pestaña **Perfiles** con las tres tarjetas (juego / trabajo / estudio). |
| `05-logs.png` | Pestaña **Logs** con `journalctl -fu ollama` mostrando líneas reales. |

## Cómo capturar

En GNOME:

```bash
# Ventana entera de la app (recomendado)
gnome-screenshot -w -f 01-dashboard.png

# Captura por selección
gnome-screenshot -a -f 03-integraciones.png
```

Formato: **PNG**. Pesos ideales: < 200 KB por captura (`pngquant 0*.png` si pesan de más).
