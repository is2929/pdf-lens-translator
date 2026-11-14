"""Motor de traducción: caché + lotes paralelos + respaldo (Lingva / MyMemory)."""
from __future__ import annotations

import random
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import requests

from . import gemini

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


def _has_letters(text: str) -> bool:
    return any(c.isalpha() for c in text)


class Translator:
    CHUNK_SIZE = 40        # textos por petición a Gemini
    MAX_WORKERS = 4        # peticiones Gemini en paralelo
    FALLBACK_WORKERS = 6   # peticiones de respaldo en paralelo
    CACHE_MAX = 5000

    def __init__(self) -> None:
        self._cache: "OrderedDict[tuple, str]" = OrderedDict()
        self._lock = threading.Lock()
        self._session = requests.Session()
        self.last_error = ""

    # ---------------- caché ----------------
    def _cache_get(self, key):
        with self._lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def _cache_put(self, key, value: str) -> None:
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self.CACHE_MAX:
                self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # ---------------- API pública ----------------
    def translate(self, texts: List[str], src: str = "auto", dest: str = "es",
                  api_key: str = "", model: str = "gemini-flash-latest") -> List[str]:
        self.last_error = ""
        src = (src or "auto").strip().lower()
        dest = (dest or "es").strip().lower()

        results = [""] * len(texts)
        pending: Dict[str, List[int]] = {}

        for i, text in enumerate(texts):
            if not text.strip():
                continue
            if not _has_letters(text):          # números/símbolos: no hace falta traducir
                results[i] = text
                continue
            cached = self._cache_get((src, dest, text))
            if cached is not None:
                results[i] = cached
                continue
            pending.setdefault(text, []).append(i)

        unique = list(pending.keys())
        if unique:
            translated = (self._translate_gemini(unique, src, dest, api_key, model)
                          if api_key else self._fallback_many(unique, src, dest))
            for original, value in zip(unique, translated):
                if value:
                    self._cache_put((src, dest, original), value)
                for idx in pending[original]:
                    results[idx] = value
        return results

    # ---------------- Gemini ----------------
    def _translate_gemini(self, unique: List[str], src: str, dest: str, api_key: str, model: str) -> List[str]:
        chunks = [unique[i:i + self.CHUNK_SIZE] for i in range(0, len(unique), self.CHUNK_SIZE)]

        def run(chunk: List[str]) -> List[str]:
            try:
                return gemini.translate_list(api_key, model, chunk, src, dest)
            except Exception as exc:
                self.last_error = str(exc)
                print(f"Error con Gemini: {exc}")
                return self._fallback_many(chunk, src, dest)

        if len(chunks) == 1:
            parts = [run(chunks[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(self.MAX_WORKERS, len(chunks))) as pool:
                parts = list(pool.map(run, chunks))
        return [t for part in parts for t in part]

    # ---------------- respaldo ----------------
    def _lingva(self, text: str, src: str, dest: str) -> str:
        url = f"https://lingva.ml/api/v1/{src}/{dest}/{requests.utils.quote(text, safe='')}"
        r = self._session.get(url, headers={"User-Agent": random.choice(_USER_AGENTS)}, timeout=(5, 15))
        r.raise_for_status()
        value = r.json().get("translation", "").strip()
        if not value:
            raise ValueError("Lingva devolvió texto vacío")
        return value

    def _mymemory(self, text: str, src: str, dest: str) -> str:
        source = "en" if src == "auto" else src
        r = self._session.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:480], "langpair": f"{source}|{dest}"},
            headers={"User-Agent": random.choice(_USER_AGENTS)},
            timeout=(5, 15),
        )
        r.raise_for_status()
        value = r.json().get("responseData", {}).get("translatedText", "").strip()
        if not value or value.casefold() == text.casefold():
            raise ValueError("MyMemory devolvió el texto original o vacío")
        return value

    def _fallback_one(self, text: str, src: str, dest: str) -> str:
        try:
            return self._lingva(text, src, dest)
        except Exception:
            try:
                return self._mymemory(text, src, dest)
            except Exception as exc:
                self.last_error = str(exc)
                return ""

    def _fallback_many(self, texts: List[str], src: str, dest: str) -> List[str]:
        if len(texts) == 1:
            return [self._fallback_one(texts[0], src, dest)]
        with ThreadPoolExecutor(max_workers=min(self.FALLBACK_WORKERS, len(texts))) as pool:
            return list(pool.map(lambda t: self._fallback_one(t, src, dest), texts))


# Instancia compartida (la caché vive mientras la app esté abierta)
translator = Translator()
