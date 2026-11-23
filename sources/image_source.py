"""Источник изображения: PNG, JPG, WEBP (через QImage)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from sources.base import BaseSource


class ImageSource(BaseSource):
    kind = "image"

    def __init__(self, name: str, path: str, parent=None) -> None:
        super().__init__(name, path, parent)

    def start(self) -> None:
        super().start()
        if self.get_latest() is not None:
            return
        image = QImage(self.path)
        if image.isNull():
            raise RuntimeError(f"не удалось загрузить изображение: {self.path}")
        if image.width() > 1920 or image.height() > 1080:
            scaled = image.scaled(
                1920, 1080,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            del image
            image = scaled
        self._publish(image)

    def stop(self) -> None:
        super().stop()
        self._frame = None

    def info(self) -> str:
        image = self.get_latest()
        if image is None:
            return self.name
        return f"{self.name} · {image.width()}×{image.height()}"

    def frame(self) -> Optional[QImage]:
        return self.get_latest()
