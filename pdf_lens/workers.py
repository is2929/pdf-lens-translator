"""Hilos de fondo: chat con streaming y OCR + traducción."""
from __future__ import annotations

from typing import List

from PIL import Image, ImageOps
from PyQt5.QtCore import QRect, QThread, pyqtSignal

from . import gemini
from .translation import translator

try:
    import pytesseract
except ImportError:  # se comprueba en __main__
    pytesseract = None

_ALIVE: set = set()


def keep_alive(thread: QThread) -> None:
    """Evita que Python destruya un QThread que todavía está corriendo."""
    _ALIVE.add(thread)
    thread.finished.connect(lambda t=thread: _ALIVE.discard(t))


# ============================================================
#  Asistente IA (streaming)
# ============================================================
class ChatWorker(QThread):
    chunk = pyqtSignal(str)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, api_key, model_name, system_instruction, history):
        super().__init__()
        self.api_key = api_key
        self.model_name = model_name
        self.system_instruction = system_instruction
        self.history = history
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            for text in gemini.stream_text(self.api_key, self.model_name,
                                           self.history, self.system_instruction):
                if self._cancel:
                    return
                self.chunk.emit(text)
            if not self._cancel:
                self.done.emit()
        except Exception as exc:
            if not self._cancel:
                self.failed.emit(str(exc))


# ============================================================
#  OCR + Traducción
# ============================================================
class ProcessWorker(QThread):
    result_ready = pyqtSignal(list, int, str)   # bloques, nº de textos OCR, último error
    OCR_SCALE = 2

    def __init__(self, gray_img, src, dest, api_key, model_name, ocr_lang="eng", group_by="line"):
        super().__init__()
        self.gray_img = gray_img
        self.src = src
        self.dest = dest
        self.api_key = api_key
        self.model_name = model_name
        self.ocr_lang = ocr_lang
        self.group_by = group_by

    def _group_words(self, data):
        groups = {}
        for i in range(len(data["text"])):
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                continue
            if conf < 15:
                continue
            word = data["text"][i].strip()
            if not word:
                continue

            if self.group_by == "line":
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            else:
                key = (data["block_num"][i], data["par_num"][i])

            x0, y0 = data["left"][i], data["top"][i]
            x1, y1 = x0 + data["width"][i], y0 + data["height"][i]
            g = groups.get(key)
            if g is None:
                groups[key] = {"text": [word], "x0": x0, "y0": y0, "x1": x1, "y1": y1}
            else:
                g["text"].append(word)
                g["x0"], g["y0"] = min(g["x0"], x0), min(g["y0"], y0)
                g["x1"], g["y1"] = max(g["x1"], x1), max(g["y1"], y1)
        return groups

    def run(self):
        try:
            s = self.OCR_SCALE
            img = ImageOps.autocontrast(self.gray_img)
            resample = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((img.width * s, img.height * s), resample)

            # OEM 1 = solo LSTM (más rápido que el modo por defecto)
            data = pytesseract.image_to_data(
                img, lang=self.ocr_lang, config="--oem 1 --psm 3",
                output_type=pytesseract.Output.DICT,
            )

            blocks, texts = [], []
            for g in self._group_words(data).values():
                full_text = " ".join(g["text"])
                if len(full_text) < 2:
                    continue
                texts.append(full_text)
                x, y = g["x0"] // s, g["y0"] // s
                w, h = (g["x1"] - g["x0"]) // s, (g["y1"] - g["y0"]) // s
                blocks.append({"rect": QRect(x, y - 2, w, h + 4), "text": full_text, "translated": ""})

            if texts:
                translated = translator.translate(texts, self.src, self.dest,
                                                  self.api_key, self.model_name)
                for block, value in zip(blocks, translated):
                    block["translated"] = value

            self.result_ready.emit(blocks, len(texts), translator.last_error)
        except Exception as exc:
            print(f"Error en OCR/traducción: {exc}")
            self.result_ready.emit([], 0, str(exc))
