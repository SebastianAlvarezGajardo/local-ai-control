# Screenshots

Las capturas que muestra el README principal viven aquí. Para que se rendericen,
**guárdalas con estos nombres exactos**:

| Archivo | Qué capturar |
| --- | --- |
| `01-tray-menu.png` | Clic en el icono de la barra superior con el menú desplegado (Encender/Apagar/Liberar VRAM). |
| `02-tab-estado.png` | Pestaña **Estado** con al menos un modelo cargado, mostrando "GPU %". |
| `03-tab-modelos.png` | Pestaña **Modelos** durante una descarga (barra de progreso visible) o con varios instalados. |
| `04-tab-recursos.png` | Pestaña **Recursos** con las barras de VRAM, GPU activa, RAM y carga CPU. |
| `05-tab-logs.png` | Pestaña **Logs** con `journalctl -fu ollama` mostrando líneas reales. |
| `06-tab-integraciones.png` | Pestaña **Integraciones** con Open WebUI / opencode / Aider. |
| `07-tab-perfiles.png` | Pestaña **Perfiles** con las tres tarjetas (juego / trabajo / estudio). |

## Cómo capturar

En GNOME:

```bash
# Captura por selección (recomendado)
gnome-screenshot -a -f 01-tray-menu.png

# Ventana entera de la app
gnome-screenshot -w -f 02-tab-estado.png
```

Resolución recomendada: la nativa de la ventana (la app abre a 720x560).
Formato: **PNG**. Pesos ideales: < 200 KB por captura (`pngquant 01-*.png` si pesan demás).
