"""Виджет монитора оператора: Preview или Program."""

from __future__ import annotations

from time import monotonic
from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class ScreenWidget(QWidget):
    """Рисует кадр с сохранением пропорций на черном фоне."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._image: Optional[QImage] = None
        self._rendering_enabled = True
        self._frame_interval = 0.0
        self._last_frame_time = 0.0
        # Минимум должен помещаться в центральную колонку при окне 1120 px
        # (две панели по 300 px оставляют монитору ~233 px).
        self.setMinimumSize(200, 112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return max(1, width * 9 // 16)

    def sizeHint(self):  # noqa: N802 - Qt API
        return self.minimumSizeHint()

    def set_rendering_enabled(self, enabled: bool) -> None:
        self._rendering_enabled = enabled

    def set_fps_limit(self, fps: float) -> None:
        self._frame_interval = 0.0 if fps <= 0 else 1.0 / fps
        self._last_frame_time = 0.0

    def set_frame(self, image: QImage) -> None:
        if not self._rendering_enabled:
            return
        now = monotonic()
        if self._frame_interval and now - self._last_frame_time < self._frame_interval:
            return
        self._last_frame_time = now
        self._image = None if image.isNull() else image
        self.update()

    def set_image(self, image: Optional[QImage]) -> None:
        if image is None:
            self._image = None
            self.update()
            return
        self.set_frame(image)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#050607"))

        image = self._image
        if image is None:
            painter.setPen(QColor("#3a424d"))
            font = painter.font()
            font.setPointSize(11)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "NO SIGNAL",
            )
            painter.end()
            return

        size = image.size().scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(
            QRect(
                (self.width() - size.width()) // 2,
                (self.height() - size.height()) // 2,
                size.width(),
                size.height(),
            ),
            image,
        )
        painter.end()
