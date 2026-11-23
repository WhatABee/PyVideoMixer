"""Захват монитора или окна Windows: dxcam с фолбэком на mss."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Slot

from core.workers import CaptureWorker
from sources.base import BaseSource


class ScreenSource(BaseSource):
    kind = "screen"

    def __init__(
        self,
        name: str,
        monitor_index: int = 0,
        hwnd: Optional[int] = None,
        fps: int = 30,
        parent=None,
    ) -> None:
        super().__init__(name, None, parent)
        self.monitor_index = monitor_index
        self.hwnd = hwnd
        self.fps = fps
        self.backend_name = "запуск…"

        self._worker = CaptureWorker(
            monitor_index=monitor_index,
            hwnd=hwnd,
            fps=fps,
        )
        self._worker.frame_ready.connect(self._publish)
        self._worker.backend_changed.connect(self._on_backend_changed)
        self._worker.capture_error.connect(self._on_error)

    # ------------------------------------------------------------------
    def start(self) -> None:
        super().start()
        if not self._worker.isRunning():
            self._worker.start()

    def stop(self) -> None:
        super().stop()
        if not self._worker.isRunning():
            return
        self._worker.request_stop()
        self._worker.wait(1500)

    def info(self) -> str:
        target = "окно" if self.hwnd is not None else f"экран {self.monitor_index + 1}"
        return f"{self.name} · {target} · {self.backend_name}"

    @Slot(str)
    def _on_backend_changed(self, backend: str) -> None:
        self.backend_name = backend
        self.changed.emit()
