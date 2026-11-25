"""PDF-источник на PyMuPDF: страницы рендерятся в отдельном потоке.

Поток рендера создается лениво (при первом start()), поэтому наследники
(например, PptxSource) успевают подготовить PDF-файл конвертацией.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtGui import QImage

from core.workers import PdfRenderWorker
from sources.base import BaseSource


class PdfSource(BaseSource):
    kind = "pdf"

    page_changed = Signal(int, int)  # index (0-based), page_count
    _render_request = Signal(int)
    _preview_render_request = Signal(int)
    _shutdown_request = Signal()

    def __init__(self, name: str, path: str, dpi: int = 150, parent=None) -> None:
        super().__init__(name, path, parent)
        self.page_index = 0
        self.page_count = 0
        self._requested_page = 0
        self._dpi = max(72, int(dpi))
        self._thread: Optional[QThread] = None
        self._worker: Optional[PdfRenderWorker] = None
        self._next_preview_frame: Optional[QImage] = None
        self._closed = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        super().start()
        if self._closed:
            return
        if self._thread is None:
            if not self.path:
                return
            self._create_worker(self.path)
        if self._thread is not None and not self._thread.isRunning():
            self._thread.start()

    def _create_worker(self, pdf_path: str) -> None:
        """Создает поток рендера для готового PDF-файла."""
        if self._thread is not None:
            return
        self._thread = QThread()
        self._worker = PdfRenderWorker(pdf_path, self._dpi)
        self._worker.moveToThread(self._thread)

        self._render_request.connect(self._worker.render_page)
        self._preview_render_request.connect(self._worker.render_preview_page)
        self._shutdown_request.connect(self._worker.shutdown)

        self._worker.frame_ready.connect(self._publish)
        self._worker.preview_frame_ready.connect(self._on_next_preview_ready)
        self._worker.document_opened.connect(self._on_document_opened)
        self._worker.page_rendered.connect(self._on_page_rendered)
        self._worker.failed.connect(self._on_error)
        self._worker.closed.connect(self._thread.quit)
        self._thread.started.connect(self._worker.open_document)
        self._thread.finished.connect(self._worker.deleteLater)

    def stop(self) -> None:
        super().stop()
        self._closed = True
        self._next_preview_frame = None
        thread = self._thread
        if thread is None or not thread.isRunning():
            return
        self._shutdown_request.emit()
        if not thread.wait(5000):
            thread.quit()
            thread.wait(2000)

    # ------------------------------------------------------------------
    def go_to_page(self, index: int) -> None:
        if self.page_count <= 0:
            return
        index = max(0, min(int(index), self.page_count - 1))
        self._requested_page = index
        self.page_index = index
        self.page_changed.emit(index, self.page_count)
        self._render_request.emit(index)
        if index + 1 < self.page_count:
            self._preview_render_request.emit(index + 1)
        else:
            self._next_preview_frame = None

    def get_next_preview(self) -> Optional[QImage]:
        return self._next_preview_frame

    def next_page(self) -> None:
        if self.page_count <= 0 or self.is_last_page:
            return
        self.go_to_page(self.page_index + 1)

    def prev_page(self) -> None:
        if self.page_count <= 0 or self.is_first_page:
            return
        self.go_to_page(self.page_index - 1)

    def first_page(self) -> None:
        self.go_to_page(0)

    def last_page(self) -> None:
        if self.page_count > 0:
            self.go_to_page(self.page_count - 1)

    @property
    def is_first_page(self) -> bool:
        return self.page_index <= 0

    @property
    def is_last_page(self) -> bool:
        return self.page_count > 0 and self.page_index >= self.page_count - 1

    def info(self) -> str:
        if self.page_count:
            return f"{self.name} · стр. {self.page_index + 1}/{self.page_count}"
        return self.name

    # ------------------------------------------------------------------
    @Slot(int)
    def _on_document_opened(self, page_count: int) -> None:
        self.page_count = page_count
        self.page_index = 0
        self._requested_page = 0
        self.page_changed.emit(0, page_count)
        if page_count > 1:
            self._preview_render_request.emit(1)
        else:
            self._next_preview_frame = None

    @Slot(QImage)
    def _on_next_preview_ready(self, img: QImage) -> None:
        self._next_preview_frame = img

    @Slot(int, int)
    def _on_page_rendered(self, index: int, page_count: int) -> None:
        # Пока листали быстро, могли прийти сигналы от уже неактуальных
        # страниц — не откатываем счётчик назад.
        if index != self._requested_page:
            return
        changed = index != self.page_index or page_count != self.page_count
        self.page_index = index
        self.page_count = page_count
        if changed:
            self.page_changed.emit(index, page_count)

    def _on_error(self, message: str) -> None:
        super()._on_error(message)
