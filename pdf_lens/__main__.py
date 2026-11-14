"""Punto de entrada: python -m pdf_lens archivo.pdf"""
import argparse
import importlib.util
import sys

REQUIRED = {
    "requests": "requests", "mss": "mss", "PIL": "Pillow", "pytesseract": "pytesseract",
    "PyQt5": "PyQt5", "google.genai": "google-genai",
}


def _missing_packages():
    missing = []
    for module, pip_name in REQUIRED.items():
        try:
            if importlib.util.find_spec(module) is None:
                missing.append(pip_name)
        except (ImportError, ValueError):
            missing.append(pip_name)
    if not (importlib.util.find_spec("pymupdf") or importlib.util.find_spec("fitz")):
        missing.append("pymupdf")
    return missing


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lente traductora flotante para PDF")
    parser.add_argument("pdf", help="Ruta al archivo PDF")
    parser.add_argument("--src", default="auto", help="Idioma de origen (por defecto: auto)")
    parser.add_argument("--dest", default="es", help="Idioma de destino (por defecto: es)")
    parser.add_argument("--ocr-lang", default="eng",
                        help="Idioma(s) de Tesseract, p. ej. eng, fra, eng+spa (por defecto: eng)")
    parser.add_argument("--group-by", choices=["line", "paragraph"], default="line",
                        help="Cómo se agrupa el texto: line (por defecto, igual que la versión original) "
                             "o paragraph (menos bloques, pero la visualización cambia)")
    args = parser.parse_args(argv)

    missing = _missing_packages()
    if missing:
        print("Faltan librerías. Instálalas con:\n  pip install -r requirements.txt")
        print("(faltan: " + ", ".join(missing) + ")")
        sys.exit(1)

    import pytesseract
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        print("No se encontró Tesseract OCR. Instálalo (ver README.md) y asegúrate de que esté en el PATH.")
        sys.exit(1)

    from PyQt5.QtWidgets import QApplication
    from .overlay import PDFTranslationOverlay

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    overlay = PDFTranslationOverlay(args.pdf, args.src, args.dest, args.ocr_lang, args.group_by)
    overlay.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
