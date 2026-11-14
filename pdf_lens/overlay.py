"""Panel flotante principal (lente traductora)."""
from __future__ import annotations

import hashlib
import time

import mss
from PIL import Image
from PyQt5.QtCore import QEvent, QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPainterPath, QPen,
)
from PyQt5.QtWidgets import (
    QAction, QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMenu,
    QPushButton, QShortcut, QSizeGrip, QSpinBox, QStyle, QSystemTrayIcon, QWidget,
)

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from .dialogs import AIAssistantDialog, SettingsDialog
from .settings import AppSettings
from .workers import ProcessWorker, keep_alive

IDIOMAS = [
    ("Español", "es"), ("Inglés", "en"), ("Francés", "fr"),
    ("Alemán", "de"), ("Italiano", "it"), ("Portugués", "pt"),
    ("Ruso", "ru"), ("Japonés", "ja"), ("Chino Simplificado", "zh-CN"),
    ("Chino Tradicional", "zh-TW"), ("Coreano", "ko"), ("Árabe", "ar"),
    ("Hindi", "hi"), ("Turco", "tr"), ("Neerlandés", "nl"),
    ("Polaco", "pl"), ("Sueco", "sv"), ("Griego", "el"),
    ("Hebreo", "he"), ("Vietnamita", "vi"), ("Ucraniano", "uk"),
    ("Indonesio", "id"),
]


class PDFTranslationOverlay(QWidget):
    TOOLBAR_HEIGHT = 38
    RESIZE_MARGIN = 10
    CAPTURE_DELAY = 0.2      # s que esperamos tras ocultar la ventana antes de capturar
    PAD_X = 6
    PAD_Y = 5

    def __init__(self, pdf_path, src_lang="auto", dest_lang="es", ocr_lang="eng", group_by="paragraph"):
        super().__init__()
        self.doc = fitz.open(pdf_path)
        self.src_lang = src_lang
        self.dest_lang = dest_lang
        self.ocr_lang = ocr_lang
        self.group_by = group_by

        self.settings = AppSettings()
        self.gemini_key = self.settings.load_api_key()
        self.gemini_model_name = self.settings.load_model()

        self.worker = None
        self.ai_dialog = None
        self._drag_pos = None
        self._resize_edge = 0
        self._start_geometry = None
        self._start_pos = None
        self._last_digest = None
        self._font_cache = {}

        self.ocr_blocks = []
        self.erase_mode = False
        self.hovered_block = None

        self._ocr_timer = QTimer(self)
        self._ocr_timer.timeout.connect(self._auto_scan)

        self._setup_window()
        self._setup_ui()
        self._setup_shortcuts()
        self._setup_tray_icon()

    # ------------------------------------------------------------
    #  UI
    # ------------------------------------------------------------
    def _setup_window(self):
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.resize(750, 550)
        self.move(200, 200)
        self.setWindowTitle("Lente Traductora")

    def _setup_ui(self):
        self.toolbar = QWidget(self)
        self.toolbar.setObjectName("toolbar")
        self.toolbar.setStyleSheet("""
            QWidget#toolbar {
                background-color: rgba(18,18,22,240);
                border: 1px solid rgba(255,220,0,180);
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
            QLabel { color: #e6e6e6; font-size: 11px; }
            QPushButton {
                background-color: #33343a; color: white;
                border: 1px solid #4a4a52; border-radius: 4px;
                padding: 2px 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4a4a55; }
            QPushButton:checked { background-color: #c0392b; border: 1px solid #e74c3c; }
            QSpinBox, QComboBox {
                background-color: #26272c; color: white;
                border: 1px solid #4a4a52; border-radius: 4px;
                padding: 1px 4px; min-width: 45px;
            }
            QCheckBox { color: #cfcfcf; font-size: 11px; }
        """)
        self.toolbar.setGeometry(0, 0, self.width(), self.TOOLBAR_HEIGHT)

        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(4)

        tb.addWidget(QLabel("Pág."))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(max(1, len(self.doc)))
        tb.addWidget(self.page_spin)

        tb.addWidget(QLabel("→"))
        self.lang_combo = QComboBox()
        for nombre, codigo in IDIOMAS:
            self.lang_combo.addItem(f"{nombre} ({codigo})", codigo)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(self.dest_lang)))
        self.lang_combo.currentIndexChanged.connect(self._change_destination_language)
        tb.addWidget(self.lang_combo)

        self.auto_cb = QCheckBox("Auto")
        self.auto_cb.setToolTip("Escaneos periódicos cada 5 s (solo si la pantalla cambió).")
        self.auto_cb.toggled.connect(self._toggle_ocr_timer)
        tb.addWidget(self.auto_cb)

        self.clean_cb = QCheckBox("Ocultar Imágenes")
        self.clean_cb.setToolTip("Pone un fondo blanco sólido ocultando las imágenes.")
        self.clean_cb.toggled.connect(lambda _=None: self.update())
        tb.addWidget(self.clean_cb)

        def tool_button(text, tip, slot, checkable=False):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setFixedWidth(24)
            if checkable:
                btn.setCheckable(True)
                btn.toggled.connect(slot)
            else:
                btn.clicked.connect(lambda _=False: slot())
            tb.addWidget(btn)
            return btn

        tool_button("↻", "Actualizar la traducción (Ctrl+R)", self._manual_scan)
        self.erase_btn = tool_button("🧽", "Modo borrador: haz clic en un texto para eliminarlo",
                                     self._toggle_erase_mode, checkable=True)
        tool_button("🗑️", "Borrar toda la traducción de la pantalla", self._clear_all_translations)
        tool_button("🤖", "Abrir Asistente IA (Ctrl+I)", self._open_ai_assistant)

        tb.addStretch()
        self.status = QLabel()
        self.status.setStyleSheet("color: #aaa; font-size: 10px;")
        tb.addWidget(self.status)

        tool_button("⚙️", "Configurar API Key y Modelo de Gemini", self._open_settings)
        tool_button("_", "Minimizar", self.showMinimized)
        tool_button("✕", "Cerrar", self.close)

        self.toolbar.installEventFilter(self)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setStyleSheet("background-color: transparent;")
        self.size_grip.resize(20, 20)

        if self.gemini_key:
            self._update_status(f"Listo. Usando {self.gemini_model_name}.")
        else:
            self._update_status("⚠️ Haz clic en ⚙️ para configurar Gemini AI.")

    def _setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = QIcon.fromTheme("accessories-dictionary")
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_FileDialogInfoView)
        self.tray_icon.setIcon(icon)
        menu = QMenu()
        action_show = QAction("Mostrar Lente", self)
        action_show.triggered.connect(self._restore_window)
        action_quit = QAction("Salir", self)
        action_quit.triggered.connect(self.close)
        menu.addAction(action_show)
        menu.addAction(action_quit)
        self.tray_icon.setContextMenu(menu)
        self._tray_menu = menu
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)
        QShortcut(QKeySequence("Ctrl+R"), self, self._manual_scan)
        QShortcut(QKeySequence("Ctrl+I"), self, self._open_ai_assistant)

    def _restore_window(self):
        self.showNormal()
        self.activateWindow()

    def _update_status(self, msg):
        self.status.setText(msg)

    # ------------------------------------------------------------
    #  Acciones
    # ------------------------------------------------------------
    def _open_ai_assistant(self):
        if not self.gemini_key:
            self._update_status("⚠️ Configura tu API Key en ⚙️ para usar el Asistente.")
            return
        if not self.ocr_blocks:
            self._update_status("⚠️ Escanea una página primero para que el Asistente tenga contexto.")
            return
        context_text = "\n".join(b["text"] for b in self.ocr_blocks)
        if self.ai_dialog is not None:
            self.ai_dialog.close()
        self.ai_dialog = AIAssistantDialog(self, self.gemini_key, self.gemini_model_name, context_text)
        self.ai_dialog.show()

    def _open_settings(self):
        dialog = SettingsDialog(self, self.gemini_key, self.gemini_model_name)
        if dialog.exec_() == QDialog.Accepted:
            self.gemini_key = dialog.get_key()
            self.gemini_model_name = dialog.get_model()
            self.settings.save_api_key(self.gemini_key)
            self.settings.save_model(self.gemini_model_name)
            if self.gemini_key:
                self._update_status(f"Guardado. Usando {self.gemini_model_name}.")
            else:
                self._update_status("API Key borrada. Usando traductores de respaldo.")

    def _toggle_erase_mode(self, checked):
        self.erase_mode = checked
        if checked:
            self.setCursor(Qt.CrossCursor)
            self._update_status("Modo borrador activo: haz clic en un texto para quitarlo.")
        else:
            self.setCursor(Qt.ArrowCursor)
            self.hovered_block = None
            self.update()
            self._update_status("Modo borrador desactivado.")

    def _clear_all_translations(self):
        self.ocr_blocks = []
        self.hovered_block = None
        self._last_digest = None
        self.erase_btn.setChecked(False)
        self.update()
        self._update_status("Se ha limpiado toda la traducción.")

    def _change_destination_language(self, index):
        self.dest_lang = self.lang_combo.itemData(index)
        self.ocr_blocks = []
        self._last_digest = None
        self.update()
        self._update_status("Idioma seleccionado. Pulsa ↻ para traducir.")

    def _toggle_ocr_timer(self, checked):
        if checked:
            self._ocr_timer.start(5000)
            self._update_status("Autoescaneo activo.")
        else:
            self._ocr_timer.stop()
            self._update_status("Modo fijo.")

    # ------------------------------------------------------------
    #  Captura + OCR + traducción
    # ------------------------------------------------------------
    def _manual_scan(self):
        self._scan(force=True)

    def _auto_scan(self):
        self._scan(force=False)

    def _grab(self, capture_rect):
        mss_cls = getattr(mss, "MSS", None) or mss.mss
        with mss_cls() as sct:
            shot = sct.grab(capture_rect)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img.convert("L")

    def _scan(self, force):
        if self.worker and self.worker.isRunning():
            return
        if not self.isVisible():
            return

        self.erase_btn.setChecked(False)
        rect = self.frameGeometry()
        capture_rect = {
            "left": rect.x(),
            "top": rect.y() + self.TOOLBAR_HEIGHT,
            "width": rect.width(),
            "height": rect.height() - self.TOOLBAR_HEIGHT,
        }
        if capture_rect["width"] <= 0 or capture_rect["height"] <= 0:
            return

        self._update_status("Capturando y traduciendo…")
        self.hide()
        QApplication.processEvents()
        time.sleep(self.CAPTURE_DELAY)

        gray = None
        try:
            gray = self._grab(capture_rect)
        except Exception as exc:
            print("Error capturando:", exc)
        finally:
            self.show()
            self.raise_()
            self.activateWindow()

        if gray is None:
            self._update_status("No se pudo capturar la página.")
            return

        # Si la pantalla no cambió, nos ahorramos OCR + traducción
        digest = hashlib.md5(gray.tobytes()).hexdigest()
        if not force and digest == self._last_digest and self.ocr_blocks:
            self._update_status("Sin cambios en pantalla.")
            return
        self._last_digest = digest

        self.worker = ProcessWorker(gray, self.src_lang, self.dest_lang, self.gemini_key,
                                    self.gemini_model_name, self.ocr_lang, self.group_by)
        self.worker.result_ready.connect(self._on_ocr_translated)
        keep_alive(self.worker)
        self.worker.start()

    def _on_ocr_translated(self, blocks, ocr_count, error):
        if ocr_count == 0:
            self._update_status("No se detectó texto legible.")
            return

        valid = [b for b in blocks if b.get("translated", "").strip()]
        if valid:
            for b in valid:
                self._layout_block(b)
            valid.sort(key=lambda b: b["rect"].y())
            failed = len(blocks) - len(valid)
            self.ocr_blocks = valid
            self.hovered_block = None
            self.update()
            if failed:
                self._update_status(f"Éxito: {len(valid)} bloques; {failed} fallaron.")
            else:
                self._update_status(f"Traducción completa: {len(valid)} bloques.")
        else:
            self._last_digest = None
            self.update()
            detail = error or "Fallo desconocido."
            if "API Key" in detail or "NotFound" in detail or "invalid" in detail:
                detail = "API Key o Modelo incorrecto. Revisa ⚙️."
            self._update_status(f"OCR leyó {ocr_count} bloques, pero falló la traducción: {detail[:80]}")

    # ------------------------------------------------------------
    #  Layout / dibujo (se calcula una vez por bloque, no en cada repaint)
    # ------------------------------------------------------------
    def _font_for(self, size):
        font = self._font_cache.get(size)
        if font is None:
            font = QFont("Noto Sans", size)
            font.setStyleHint(QFont.SansSerif)
            self._font_cache[size] = font
        return font

    def _best_font(self, text, rect):
        avail_w = max(20, rect.width() - 2 * self.PAD_X)
        for size in range(16, 8, -1):
            font = self._font_for(size)
            br = QFontMetrics(font).boundingRect(QRect(0, 0, avail_w, 1000), Qt.TextWordWrap, text)
            if br.height() <= rect.height() * 1.5:
                return font
        return self._font_for(9)

    def _layout_block(self, block):
        rect, text = block["rect"], block["translated"]
        font = self._best_font(text, rect)
        avail_w = max(20, rect.width() - 2 * self.PAD_X)
        br = QFontMetrics(font).boundingRect(QRect(0, 0, avail_w, 1000), Qt.TextWordWrap, text)
        needed = max(rect.height(), br.height() + 2 * self.PAD_Y)
        block["font"] = font
        block["draw_rect"] = QRect(rect.x(), rect.y() + self.TOOLBAR_HEIGHT, rect.width(), needed)

    def _get_block_at(self, pos):
        for block in reversed(self.ocr_blocks):
            if block["draw_rect"].contains(pos):
                return block
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            clean = self.clean_cb.isChecked()

            if clean and self.ocr_blocks:
                painter.fillRect(0, self.TOOLBAR_HEIGHT, self.width(),
                                 self.height() - self.TOOLBAR_HEIGHT, QColor(255, 255, 255, 255))

            for block in self.ocr_blocks:
                draw_rect = block["draw_rect"]
                if not clean:
                    path = QPainterPath()
                    path.addRoundedRect(QRectF(draw_rect), 6, 6)
                    painter.fillPath(path, QColor(255, 255, 255, 235))
                    if self.erase_mode and self.hovered_block is block:
                        painter.setPen(QPen(QColor(231, 76, 60), 2))
                    else:
                        painter.setPen(QPen(QColor(180, 180, 180, 120), 1))
                    painter.drawPath(path)

                painter.setFont(block["font"])
                painter.setPen(QColor(30, 30, 30))
                text_rect = draw_rect.adjusted(self.PAD_X, self.PAD_Y, -self.PAD_X, -self.PAD_Y)
                painter.drawText(text_rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter,
                                 block["translated"])

            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 140, 0), 2))
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

            painter.setPen(QPen(QColor(255, 200, 0, 150), 1, Qt.DashLine))
            painter.drawLine(0, self.TOOLBAR_HEIGHT, self.width(), self.TOOLBAR_HEIGHT)

            painter.setPen(QPen(QColor(255, 140, 0), 2))
            w, h = self.width(), self.height()
            painter.drawLine(w - 15, h - 5, w - 5, h - 15)
            painter.drawLine(w - 10, h - 5, w - 5, h - 10)
        finally:
            painter.end()

    # ------------------------------------------------------------
    #  Ratón / redimensionado
    # ------------------------------------------------------------
    def _get_edge(self, pos):
        m = self.RESIZE_MARGIN
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        left, right = x < m, x > w - m
        top, bottom = y < m, y > h - m
        if top and left: return 1
        if top and right: return 3
        if bottom and left: return 7
        if bottom and right: return 5
        if left: return 8
        if right: return 4
        if top: return 2
        if bottom: return 6
        return 0

    def _update_cursor(self, pos):
        if self.erase_mode:
            self.setCursor(Qt.CrossCursor)
            return
        edge = self._get_edge(pos)
        if edge in (1, 5): self.setCursor(Qt.SizeFDiagCursor)
        elif edge in (3, 7): self.setCursor(Qt.SizeBDiagCursor)
        elif edge in (2, 6): self.setCursor(Qt.SizeVerCursor)
        elif edge in (4, 8): self.setCursor(Qt.SizeHorCursor)
        else: self.setCursor(Qt.ArrowCursor)

    def _do_resize(self, global_pos):
        dx = global_pos.x() - self._start_pos.x()
        dy = global_pos.y() - self._start_pos.y()
        geo = self._start_geometry
        x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
        if self._resize_edge in (1, 7, 8): x += dx; w -= dx
        if self._resize_edge in (3, 5, 4): w += dx
        if self._resize_edge in (1, 2, 3): y += dy; h -= dy
        if self._resize_edge in (7, 6, 5): h += dy
        if w >= 300 and h >= 200:
            self.setGeometry(x, y, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toolbar.setGeometry(0, 0, self.width(), self.TOOLBAR_HEIGHT)
        self.size_grip.move(self.width() - 20, self.height() - 20)

    def eventFilter(self, obj, event):
        if obj is self.toolbar:
            if event.type() == QEvent.MouseMove:
                pos = self.mapFromGlobal(event.globalPos())
                if not self._resize_edge:
                    self._update_cursor(pos)
                if self._resize_edge and (event.buttons() & Qt.LeftButton):
                    self._do_resize(event.globalPos())
                    return True
            elif event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    pos = self.mapFromGlobal(event.globalPos())
                    edge = self._get_edge(pos)
                    if edge:
                        self._resize_edge = edge
                        self._start_geometry = self.geometry()
                        self._start_pos = event.globalPos()
                        self._update_cursor(pos)
                        return True
            elif event.type() == QEvent.MouseButtonRelease:
                if self._resize_edge:
                    self._resize_edge = 0
                    self.setCursor(Qt.ArrowCursor)
                    return True

            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                child = self.toolbar.childAt(event.pos())
                if not isinstance(child, (QSpinBox, QComboBox, QPushButton, QCheckBox)):
                    self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                    return True
            elif event.type() == QEvent.MouseMove and self._drag_pos:
                self.move(event.globalPos() - self._drag_pos)
                return True
            elif event.type() == QEvent.MouseButtonRelease:
                self._drag_pos = None
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.erase_mode:
                block = self._get_block_at(event.pos())
                if block is not None:
                    self.ocr_blocks = [b for b in self.ocr_blocks if b is not block]
                    self.hovered_block = None
                    self.update()
                return
            edge = self._get_edge(event.pos())
            if edge:
                self._resize_edge = edge
                self._start_geometry = self.geometry()
                self._start_pos = event.globalPos()
                event.accept()
                return
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.erase_mode:
            new_hover = self._get_block_at(event.pos())
            if new_hover is not self.hovered_block:
                self.hovered_block = new_hover
                self.update()
            return
        if self._resize_edge:
            self._do_resize(event.globalPos())
            return
        if self._drag_pos and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_pos)
            return
        self._update_cursor(event.pos())

    def mouseReleaseEvent(self, event):
        self._resize_edge = 0
        self._drag_pos = None
        if not self.erase_mode:
            self.setCursor(Qt.ArrowCursor)

    def closeEvent(self, event):
        self._ocr_timer.stop()
        if self.ai_dialog is not None:
            self.ai_dialog.close()
        if self.worker and self.worker.isRunning():
            self.worker.wait(5000)
        self.tray_icon.hide()
        try:
            self.doc.close()
        except Exception:
            pass
        event.accept()
