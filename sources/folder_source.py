"""Источник слайд-шоу из изображений выбранной папки."""

from __future__ import annotations

from pathlib import Path
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from sources.base import BaseSource
from sources.pdf_source import PdfSource


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class FolderSource(PdfSource):
    """Слайд-шоу из изображений, отсортированных по имени файла."""

    kind = "folder"

    def __init__(self, name: str, path: str, parent=None) -> None:
        super().__init__(name, path, parent=parent)
        folder = Path(path)
        self._images: List[Path] = sorted(
            (
                item
                for item in folder.iterdir()
                if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
            ),
            key=lambda item: item.name.lower(),
        )
        if not self._images:
            raise ValueError(
                "В выбранной папке нет изображений PNG, JPG, JPEG, WEBP или BMP"
            )
        self.page_count = len(self._images)

    def start(self) -> None:
        BaseSource.start(self)
        self.go_to_page(self.page_index)

    def stop(self) -> None:
        BaseSource.stop(self)
        self._frame = None

    def go_to_page(self, index: int) -> None:
        index = max(0, min(int(index), self.page_count - 1))
        self.page_index = index
        image = QImage(str(self._images[index]))
        if image.isNull():
            raise RuntimeError(f"не удалось загрузить изображение: {self._images[index]}")
        if image.width() > 1920 or image.height() > 1080:
            scaled = image.scaled(
                1920, 1080,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            del image
            image = scaled
        self._publish(image)
        self.page_changed.emit(index, self.page_count)

    def info(self) -> str:
        return f"{self.name} · {self.page_index + 1}/{self.page_count}"
