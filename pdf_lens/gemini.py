"""Capa mínima sobre google-genai.

Optimizaciones respecto a la versión original:
  * El cliente se crea una sola vez por API key (no en cada petición).
  * Se desactiva el "thinking" en modelos Flash (mucha menos latencia).
  * Traducción con salida JSON estructurada (sin limpiar bloques ```json).
  * Respuestas del chat en streaming (el texto aparece al instante).
"""
from __future__ import annotations

import json
import threading
from typing import Iterator, List, Optional

from google import genai
from google.genai import types

_clients: dict = {}
_clients_lock = threading.Lock()
# Modelos que rechazaron thinking_budget=0: no volvemos a intentarlo con ellos.
_no_thinking_models: set = set()


def get_client(api_key: str) -> "genai.Client":
    with _clients_lock:
        client = _clients.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            _clients[api_key] = client
        return client


def _can_disable_thinking(model: str) -> bool:
    name = model.lower()
    return "flash" in name and name not in _no_thinking_models


def _is_thinking_error(exc: Exception) -> bool:
    return "thinking" in str(exc).lower()


def _config(model: str, system_instruction: Optional[str] = None,
            as_json_list: bool = False, fast: bool = True) -> "types.GenerateContentConfig":
    kwargs: dict = {}
    if system_instruction:
        kwargs["system_instruction"] = system_instruction
    if as_json_list:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = list[str]
        kwargs["temperature"] = 0.0
    if fast and _can_disable_thinking(model):
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(**kwargs)


# ------------------------------------------------------------------
#  Chat (streaming)
# ------------------------------------------------------------------
def stream_text(api_key: str, model: str, contents, system_instruction: Optional[str] = None) -> Iterator[str]:
    """Genera la respuesta fragmento a fragmento."""
    client = get_client(api_key)
    fast = True
    while True:
        started = False
        try:
            stream = client.models.generate_content_stream(
                model=model,
                contents=contents,
                config=_config(model, system_instruction, fast=fast),
            )
            for chunk in stream:
                text = chunk.text
                if text:
                    started = True
                    yield text
            return
        except Exception as exc:
            if fast and not started and _can_disable_thinking(model) and _is_thinking_error(exc):
                _no_thinking_models.add(model.lower())
                fast = False
                continue
            raise


# ------------------------------------------------------------------
#  Traducción por lotes
# ------------------------------------------------------------------
def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _translate_once(api_key: str, model: str, texts: List[str], src: str, dest: str) -> List[str]:
    client = get_client(api_key)
    source = "the auto-detected language" if src == "auto" else f"language code '{src}'"
    prompt = (
        f"Translate every string of this JSON array from {source} to language code '{dest}'. "
        f"Return a JSON array of strings with exactly {len(texts)} items, in the same order. "
        f"The strings come from OCR and may contain noise. Keep numbers, formulas, symbols "
        f"and proper names unchanged. No explanations.\n\n"
        f"{json.dumps(texts, ensure_ascii=False)}"
    )
    fast = True
    while True:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=_config(model, as_json_list=True, fast=fast),
            )
            break
        except Exception as exc:
            if fast and _can_disable_thinking(model) and _is_thinking_error(exc):
                _no_thinking_models.add(model.lower())
                fast = False
                continue
            raise
    data = json.loads(_strip_fences(response.text or "[]"))
    if not isinstance(data, list):
        raise ValueError("Gemini no devolvió una lista JSON")
    return [str(item) for item in data]


def translate_list(api_key: str, model: str, texts: List[str], src: str, dest: str) -> List[str]:
    """Traduce una lista; si Gemini devuelve un nº distinto de elementos, divide el lote."""
    if not texts:
        return []
    out = _translate_once(api_key, model, texts, src, dest)
    if len(out) == len(texts):
        return out
    if len(texts) == 1:
        return [" ".join(out)]
    mid = len(texts) // 2
    return (translate_list(api_key, model, texts[:mid], src, dest)
            + translate_list(api_key, model, texts[mid:], src, dest))
