"""Диалог настроек вывода видео и дизайна пульта."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget, QKeySequenceEdit


_COLOR_PRESETS = ("#2563eb", "#0f766e", "#7c3aed", "#15803d", "#ea580c", "#dc2626", "#db2777")


def _create_color_picker_row(initial_hex: str) -> tuple[QWidget, QLineEdit]:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    buttons = []
    for color in _COLOR_PRESETS:
        button = QPushButton()
        button.setFixedSize(24, 24)
        button.setStyleSheet(f"background-color: {color}; border: 1px solid #64748b; border-radius: 3px;")
        buttons.append((color, button))
        layout.addWidget(button)

    input_field = QLineEdit(initial_hex)
    input_field.setMaximumWidth(130)
    input_field.setPlaceholderText("#hex / rgb")
    layout.addWidget(input_field)

    from ui.style import _normalize_hex_color

    def update_active_cube(text: str = "") -> None:
        current = _normalize_hex_color(text)
        for color, button in buttons:
            border = "2px solid #ffffff" if current == color else "1px solid #64748b"
            button.setStyleSheet(
                f"background-color: {color}; border: {border}; border-radius: 3px;"
            )

    for color, button in buttons:
        button.clicked.connect(lambda _checked=False, value=color: input_field.setText(value))
    input_field.textChanged.connect(update_active_cube)
    update_active_cube(input_field.text())
    return row, input_field


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)
        self._path = self._settings_path()
        data = self._load()
        form = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItem("Аппаратный плеер (QVideoWidget — рекомендуется)", "qvideowidget")
        self.mode.addItem("Внешний плеер (запасной)", "external")
        self.mode.setCurrentIndex(max(0, self.mode.findData(data.get("video_output_mode", "qvideowidget"))))
        self.player = QLineEdit(data.get("external_player", "") or self._discover())
        browse = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply)
        browse.button(QDialogButtonBox.StandardButton.Apply).setText("Обзор...")
        browse.clicked.connect(self._browse)
        self.preview_fps = QSpinBox()
        self.preview_fps.setRange(1, 60)
        self.preview_fps.setValue(int(data.get("preview_fps", 12)))
        self.preview_fps.setSuffix(" FPS")
        form.addRow("Режим вывода видео:", self.mode)
        form.addRow("Внешний плеер:", self.player)
        form.addRow("", browse)
        form.addRow("Лимит предпросмотра:", self.preview_fps)

        form.addRow(QLabel("Дизайн"))
        self.theme_combo = QComboBox()
        for label, value in (("Тёмная", "dark"), ("Светлая", "light"), ("Система", "system")):
            self.theme_combo.addItem(label, value)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(data.get("theme", "dark"))))
        form.addRow("Тема:", self.theme_combo)

        self.accent_preset = QComboBox()
        presets = (("Синий (#2563eb)", "#2563eb"), ("Бирюзовый (#0f766e)", "#0f766e"), ("Фиолетовый (#7c3aed)", "#7c3aed"), ("Изумрудный (#15803d)", "#15803d"), ("Оранжевый (#ea580c)", "#ea580c"), ("Свой HEX / RGB", "custom"))
        for label, value in presets:
            self.accent_preset.addItem(label, value)
        accent = data.get("accent_color", "#2563eb")
        preset_index = self.accent_preset.findData(accent.lower())
        self.accent_preset.setCurrentIndex(preset_index if preset_index >= 0 else self.accent_preset.findData("custom"))
        self.accent_input = QLineEdit(accent)
        self.accent_input.setPlaceholderText("#2563eb или 37, 99, 235")
        self.accent_preview = QLabel("   ")
        self.accent_preview.setFixedWidth(28)
        self._update_accent_preview()
        accent_row = QWidget()
        from PySide6.QtWidgets import QHBoxLayout
        accent_layout = QHBoxLayout(accent_row)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.addWidget(self.accent_input, 1)
        accent_layout.addWidget(self.accent_preview)
        form.addRow("Акцентный цвет:", self.accent_preset)
        form.addRow("Свой цвет:", accent_row)
        color_defaults = (
            ("Цвет (Плавно):", "fade_color", "#0f766e"),
            ("Цвет (Плавно (пауза)):", "fade_pause_color", "#0d9488"),
            ("Цвет (Резко):", "cut_color", "#2563eb"),
            ("Цвет (Резко (пауза)):", "cut_pause_color", "#1d4ed8"),
        )
        for label, name, default in color_defaults:
            row, field = _create_color_picker_row(data.get(name, default))
            setattr(self, f"{name}_input", field)
            form.addRow(label, row)
        self.accent_preset.currentIndexChanged.connect(self._on_accent_preset_changed)
        self.accent_input.textChanged.connect(self._update_accent_preview)

        form.addRow(QLabel("Управление слайдами (кликер / клавиши)"))
        self.next_slide_edit = QKeySequenceEdit(QKeySequence(data.get("next_slide_key", "PgDown")))
        self.prev_slide_edit = QKeySequenceEdit(QKeySequence(data.get("prev_slide_key", "PgUp")))
        reset_keys_btn = QPushButton("Сбросить на PgDown / PgUp")
        reset_keys_btn.clicked.connect(lambda: (
            self.next_slide_edit.setKeySequence(QKeySequence("PgDown")),
            self.prev_slide_edit.setKeySequence(QKeySequence("PgUp")),
        ))
        form.addRow("Слайд вперёд:", self.next_slide_edit)
        form.addRow("Слайд назад:", self.prev_slide_edit)
        form.addRow("", reset_keys_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("ОК")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _settings_path() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "settings.json"
        return Path(__file__).resolve().parent.parent / "settings.json"

    @staticmethod
    def _discover() -> str:
        return shutil.which("mpv") or shutil.which("vlc") or ""

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _on_accent_preset_changed(self, _index: int) -> None:
        value = self.accent_preset.currentData()
        if value and value != "custom":
            self.accent_input.setText(value)

    def _update_accent_preview(self, _text: str = "") -> None:
        from ui.style import _normalize_hex_color
        color = _normalize_hex_color(self.accent_input.text())
        self.accent_preview.setStyleSheet(f"background-color: {color}; border: 1px solid #64748b;")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите внешний плеер",
            "",
            "Исполняемые файлы (*.exe);;Все файлы (*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            self.player.setText(path)

    def settings(self) -> dict:
        from ui.style import _normalize_hex_color
        return {
            "video_output_mode": self.mode.currentData(),
            "external_player": self.player.text().strip(),
            "preview_fps": self.preview_fps.value(),
            "theme": self.theme_combo.currentData(),
            "accent_color": _normalize_hex_color(self.accent_input.text()),
            "fade_color": _normalize_hex_color(self.fade_color_input.text(), "#0f766e"),
            "fade_pause_color": _normalize_hex_color(self.fade_pause_color_input.text(), "#0d9488"),
            "cut_color": _normalize_hex_color(self.cut_color_input.text()),
            "cut_pause_color": _normalize_hex_color(self.cut_pause_color_input.text(), "#1d4ed8"),
            "next_slide_key": self.next_slide_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText) or "PgDown",
            "prev_slide_key": self.prev_slide_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText) or "PgUp",
        }

    def accept(self) -> None:
        self._path.write_text(json.dumps(self.settings(), ensure_ascii=False, indent=2), encoding="utf-8")
        super().accept()

    @classmethod
    def load_settings(cls) -> dict:
        path = cls._settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        defaults = {"video_output_mode": "qvideowidget", "external_player": cls._discover(), "preview_fps": 12, "theme": "dark", "accent_color": "#2563eb", "fade_color": "#0f766e", "fade_pause_color": "#0d9488", "cut_color": "#2563eb", "cut_pause_color": "#1d4ed8", "next_slide_key": "PgDown", "prev_slide_key": "PgUp"}
        return {**defaults, **data}
