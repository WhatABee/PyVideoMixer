"""Диалог настроек вывода видео."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLineEdit, QSpinBox, QWidget


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
        return {"video_output_mode": self.mode.currentData(), "external_player": self.player.text().strip(), "preview_fps": self.preview_fps.value()}

    def accept(self) -> None:
        self._path.write_text(json.dumps(self.settings(), ensure_ascii=False, indent=2), encoding="utf-8")
        super().accept()

    @classmethod
    def load_settings(cls) -> dict:
        path = cls._settings_path()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"video_output_mode": "qvideowidget", "external_player": cls._discover(), "preview_fps": 12}
