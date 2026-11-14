"""Almacenamiento de la configuración. La API key la escribe el usuario en la app.

Si el paquete `keyring` está disponible (y el sistema tiene un backend), la clave
se guarda en el almacén seguro del sistema operativo. Si no, se usa QSettings
(fuera del repositorio, en la configuración del usuario).
"""
from __future__ import annotations

from PyQt5.QtCore import QSettings

try:
    import keyring
except Exception:  # keyring no instalado o sin backend
    keyring = None

SERVICE = "PDFLensTranslator"
KEY_NAME = "gemini_api_key"
DEFAULT_MODEL = "gemini-flash-latest"


class AppSettings:
    def __init__(self) -> None:
        self._qs = QSettings("OpenSourceProject", "PDFTranslator")

    # ---- API key ----
    def load_api_key(self) -> str:
        if keyring is not None:
            try:
                key = keyring.get_password(SERVICE, KEY_NAME)
                if key:
                    return key
            except Exception:
                pass
        return str(self._qs.value(KEY_NAME, "") or "")

    def save_api_key(self, key: str) -> None:
        key = (key or "").strip()
        stored_in_keyring = False
        if keyring is not None:
            try:
                if key:
                    keyring.set_password(SERVICE, KEY_NAME, key)
                else:
                    try:
                        keyring.delete_password(SERVICE, KEY_NAME)
                    except Exception:
                        pass
                stored_in_keyring = True
            except Exception:
                stored_in_keyring = False

        if stored_in_keyring or not key:
            self._qs.remove(KEY_NAME)          # no dejar copias en texto plano
        else:
            self._qs.setValue(KEY_NAME, key)   # respaldo si no hay keyring
        self._qs.sync()

    # ---- Modelo ----
    def load_model(self) -> str:
        return str(self._qs.value("gemini_model_name", DEFAULT_MODEL) or DEFAULT_MODEL)

    def save_model(self, model: str) -> None:
        self._qs.setValue("gemini_model_name", model or DEFAULT_MODEL)
        self._qs.sync()
