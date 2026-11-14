# 🔍 PDF Lens Translator

Lente flotante que se coloca sobre cualquier visor de PDF, lee el texto de la pantalla con **OCR (Tesseract)** y lo traduce con **Gemini** (con Lingva/MyMemory como respaldo). Incluye un **asistente de IA** para preguntar sobre el contenido de la página.

## Características

- Ventana flotante, redimensionable y siempre visible.
- Traducción a 22 idiomas, con modo automático (cada 5 s, solo si la pantalla cambió).
- Modo borrador 🧽 para quitar bloques de texto concretos.
- Asistente IA 🤖 con respuestas en *streaming* y memoria de conversación.
- La API key la introduce el usuario desde ⚙️; **nunca va en el código**.

## Requisitos

- Python 3.9+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) instalado y en el `PATH`
  - Ubuntu/Debian: `sudo apt install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: instalador de [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
- Una API key de [Google AI Studio](https://aistudio.google.com/apikey) (opcional, pero recomendada)

## Instalación

```bash
git clone https://github.com/<tu-usuario>/pdf-lens-translator.git
cd pdf-lens-translator
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```bash
python main.py documento.pdf
python main.py documento.pdf --dest en --ocr-lang spa      # texto en español → inglés
python main.py documento.pdf --ocr-lang eng+fra --group-by line
```

| Opción | Descripción |
|---|---|
| `--src` | Idioma de origen (`auto` por defecto) |
| `--dest` | Idioma de destino (`es` por defecto) |
| `--ocr-lang` | Idioma(s) de Tesseract (`eng`, `spa`, `eng+fra`…). Debe estar instalado el paquete de idioma. |
| `--group-by` | `paragraph` (por defecto, más rápido y mejor calidad) o `line` |

1. Abre el PDF en tu visor favorito y coloca la lente sobre el texto.
2. Pulsa ⚙️ y pega tu API key de Gemini.
3. Pulsa ↻ (o `Ctrl+R`) para traducir.

### Atajos

| Atajo | Acción |
|---|---|
| `Ctrl+R` | Escanear y traducir |
| `Ctrl+I` | Abrir asistente IA |
| `Ctrl+Q` | Salir |

## Dónde se guarda la API key

La clave **no está en el código ni en el repositorio**. Se guarda en el almacén seguro del sistema (paquete `keyring`: Credential Manager, Keychain, Secret Service). Si no hay backend disponible, se usa `QSettings` en la configuración del usuario:

- Windows: `HKEY_CURRENT_USER\Software\OpenSourceProject\PDFTranslator`
- Linux: `~/.config/OpenSourceProject/PDFTranslator.conf`
- macOS: `~/Library/Preferences/com.opensourceproject.PDFTranslator.plist`

Para borrarla: abre ⚙️, vacía el campo y pulsa **Save**.

## Optimizaciones de rendimiento

| Área | Mejora |
|---|---|
| Traducción | Caché de traducciones (el modo Auto no vuelve a traducir lo ya traducido) |
| | Lotes en paralelo y salida JSON estructurada (sin parseo frágil) |
| | `thinking` desactivado en modelos Flash (menor latencia) |
| | Textos sin letras (números, símbolos) no se envían a la API |
| | Traducción por párrafo: menos elementos, más contexto |
| | Respaldo en paralelo y sin `sleep` entre peticiones |
| OCR | Se omite todo el proceso si la pantalla no cambió |
| | Tesseract en modo LSTM (`--oem 1`) y reescalado bicúbico |
| | Preprocesado en el hilo de fondo; menor espera de captura |
| Asistente IA | Respuesta en *streaming* (se ve el texto desde el primer fragmento) |
| | Cliente de Gemini reutilizado y `thinking` desactivado en Flash |
| | Historial de conversación y contexto como `system_instruction` |
| Interfaz | Fuente y rectángulo de cada bloque calculados una sola vez, no en cada repintado |

Para la máxima velocidad, elige un modelo `gemini-flash-lite-latest` en ⚙️.

## Estructura

```
pdf-lens-translator/
├── main.py
├── requirements.txt
├── pdf_lens/
│   ├── __main__.py     # CLI y comprobaciones
│   ├── overlay.py      # ventana flotante
│   ├── dialogs.py      # asistente IA y configuración
│   ├── workers.py      # hilos: OCR + traducción, chat
│   ├── translation.py  # caché, lotes, respaldo
│   ├── gemini.py       # cliente Gemini (JSON y streaming)
│   └── settings.py     # almacenamiento de la API key
└── ...
```

## Licencia

MIT
