"""Ventanas auxiliares: asistente IA y configuración de Gemini."""
from __future__ import annotations

from google.genai import types
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
)

from .settings import DEFAULT_MODEL
from .workers import ChatWorker, keep_alive

MODELS = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",   # el más rápido
    "gemini-pro-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]


# ============================================================
#  Asistente IA
# ============================================================
class AIAssistantDialog(QDialog):
    MAX_CONTEXT_CHARS = 15000

    def __init__(self, parent, api_key, model_name, context_text):
        super().__init__(parent)
        self.setWindowTitle("🤖 Asistente IA del PDF")
        self.resize(500, 600)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)

        self.api_key = api_key
        self.model_name = model_name
        self.system_instruction = (
            "Eres un asistente experto que ayuda a entender un documento PDF. "
            "Este es el texto de la página que el usuario está leyendo:\n\n"
            f"--- INICIO ---\n{context_text[:self.MAX_CONTEXT_CHARS]}\n--- FIN ---\n\n"
            "Responde de forma clara, precisa y concisa, en el mismo idioma de la pregunta. "
            "Si preguntan por fórmulas o conceptos, explícalos con detalle."
        )
        self.history = []            # turnos enviados al modelo (memoria de la conversación)
        self.transcript = [("Sistema", "¡Hola! He leído el texto de esta página. ¿En qué te puedo ayudar? "
                                       "Puedes preguntarme por fórmulas, conceptos o pedirme un resumen.")]
        self.worker = None
        self._answer = ""
        self._closed = False

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._render)

        layout = QVBoxLayout(self)
        self.chat_history = QTextBrowser()
        self.chat_history.setOpenExternalLinks(True)
        self.chat_history.setStyleSheet("""
            QTextBrowser {
                background-color: #1e1e24; color: #e6e6e6;
                border: 1px solid #4a4a52; border-radius: 4px;
                padding: 8px; font-family: 'Noto Sans', Arial; font-size: 13px;
            }
        """)
        layout.addWidget(self.chat_history)

        row = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Pregunta algo sobre el texto de la página...")
        self.input_field.setStyleSheet("""
            QLineEdit { background-color: #2b2b36; color: white; border: 1px solid #4a4a52;
                        border-radius: 4px; padding: 6px; font-size: 13px; }
        """)
        self.input_field.returnPressed.connect(self._send_message)

        self.send_btn = QPushButton("Enviar")
        self.send_btn.setStyleSheet("""
            QPushButton { background-color: #2980b9; color: white; border: none;
                          border-radius: 4px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background-color: #3498db; }
            QPushButton:disabled { background-color: #555; color: #aaa; }
        """)
        self.send_btn.clicked.connect(self._send_message)
        row.addWidget(self.input_field)
        row.addWidget(self.send_btn)
        layout.addLayout(row)

        self._render()

    # ---------- render ----------
    def _render(self):
        md = "\n\n---\n\n".join(f"**{who}**\n\n{text}" for who, text in self.transcript)
        bar = self.chat_history.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        old = bar.value()
        try:
            self.chat_history.setMarkdown(md)
        except AttributeError:                   # Qt < 5.14
            self.chat_history.setPlainText(md)
        bar.setValue(bar.maximum() if at_bottom else old)

    def _schedule_render(self):
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _set_busy(self, busy):
        self.send_btn.setEnabled(not busy)
        self.send_btn.setText("Pensando..." if busy else "Enviar")

    # ---------- envío / respuesta ----------
    def _send_message(self):
        question = self.input_field.text().strip()
        if not question or (self.worker and self.worker.isRunning()):
            return
        self.input_field.clear()

        self.transcript.append(("Tú", question))
        self.transcript.append(("Asistente", "_Pensando…_"))
        self.history.append(types.Content(role="user", parts=[types.Part(text=question)]))
        self._answer = ""
        self._set_busy(True)
        self._render()

        self.worker = ChatWorker(self.api_key, self.model_name, self.system_instruction, list(self.history))
        self.worker.chunk.connect(self._on_chunk)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        keep_alive(self.worker)
        self.worker.start()

    def _on_chunk(self, text):
        if self._closed:
            return
        self._answer += text
        self.transcript[-1] = ("Asistente", self._answer)
        self._schedule_render()

    def _on_done(self):
        if self._closed:
            return
        if not self._answer.strip():
            self._answer = "_(sin respuesta)_"
            self.transcript[-1] = ("Asistente", self._answer)
        self.history.append(types.Content(role="model", parts=[types.Part(text=self._answer)]))
        self._render()
        self._set_busy(False)

    def _on_failed(self, message):
        if self._closed:
            return
        if self.history and self.history[-1].role == "user":
            self.history.pop()                   # evita dos turnos de usuario seguidos
        self.transcript[-1] = ("Asistente", f"⚠️ Error al conectar con la IA: {message}")
        self._render()
        self._set_busy(False)

    def closeEvent(self, event):
        self._closed = True
        if self.worker:
            self.worker.cancel()
        super().closeEvent(event)


# ============================================================
#  Configuración
# ============================================================
class SettingsDialog(QDialog):
    def __init__(self, parent=None, current_key="", current_model=DEFAULT_MODEL):
        super().__init__(parent)
        self.setWindowTitle("Configuración de Gemini AI")
        self.resize(450, 220)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setText(current_key)
        self.key_input.setPlaceholderText("Pega tu API Key aquí...")
        form.addRow("API Key:", self.key_input)

        self.show_key_cb = QCheckBox("Mostrar clave")
        self.show_key_cb.toggled.connect(self._toggle_echo)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(MODELS)
        self.model_combo.setCurrentText(current_model)
        form.addRow("Modelo:", self.model_combo)

        layout.addLayout(form)
        layout.addWidget(self.show_key_cb)
        hint = QLabel("Para máxima velocidad usa un modelo «flash-lite».\n"
                      "Deja la clave vacía para borrarla y usar los traductores de respaldo.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setStyleSheet("""
            QDialog { background-color: #1e1e24; color: white; }
            QLabel { color: #e6e6e6; }
            QLineEdit, QComboBox { background-color: #2b2b36; color: white; border: 1px solid #4a4a52; padding: 4px; border-radius: 3px; }
            QCheckBox { color: #cfcfcf; }
            QPushButton { background-color: #33343a; color: white; padding: 4px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #4a4a55; }
            QComboBox QAbstractItemView { background-color: #2b2b36; color: white; selection-background-color: #4a4a55; }
        """)

    def _toggle_echo(self, checked):
        self.key_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def get_key(self):
        return self.key_input.text().strip()

    def get_model(self):
        return self.model_combo.currentText().strip() or DEFAULT_MODEL
