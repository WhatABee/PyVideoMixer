"""Базовый класс источника: потокобезопасный слот последнего кадра."""

from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QImage


class BaseSource(QObject):
    """Любой источник отдает последний готовый кадр через get_latest().

    Декодирование/рендер/захват происходит в собственном потоке источника,
    а _publish() вызывается уже в GUI-потоке (queued connection), поэтому
    блокировка защищает только короткую операцию копирования ссылки.
    """

    kind = "base"

    error = Signal(str)
    changed = Signal()

    def __init__(
        self,
        name: str,
        path: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self.path = path
        self._lock = threading.Lock()
        self._frame: Optional[QImage] = None
        self._active = False

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @Slot(QImage)
    def _publish(self, image: QImage) -> None:
        if image is None or image.isNull():
            return
        with self._lock:
            self._frame = image

    def get_latest(self) -> Optional[QImage]:
        with self._lock:
            return self._frame

    def info(self) -> str:
        return self.name

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self.error.emit(f"{self.name}: {message}")
