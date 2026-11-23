"""Фоновые воркеры для PDF и захвата экрана/окна.

Видеоплеер находится в GUI-потоке в VideoSource; декодирование выполняется
нативными потоками Qt Multimedia.
"""

from __future__ import annotations

import gc
import time
from typing import Optional

from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink

# dxcam/COM важно импортировать ДО создания QApplication: иначе COM-объекты
# dxcam и OLE-инициализация Qt конфликтуют при завершении процесса
# (access violation на выходе). core.workers импортируется из main.py
# до QApplication, поэтому ранняя загрузка здесь безопасна.
try:
    import dxcam as _dxcam  # type: ignore  # noqa: PLC0415
except Exception:  # noqa: BLE001 - dxcam необязателен, есть фолбэк mss
    _dxcam = None


def _import_fitz():
    """PyMuPDF 1.24+ предпочитает `pymupdf`, но `fitz` пока работает."""
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        import fitz  # type: ignore
    return fitz


class VideoWorker(QObject):
    """Держит QMediaPlayer/QAudioOutput/QVideoSink внутри своего потока."""

    frame_ready = Signal(QImage)
    error = Signal(str)
    stopped = Signal()
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playback_state_changed = Signal(bool)

    def __init__(self, path: str, loop: bool = True, volume: float = 1.0) -> None:
        super().__init__()
        self._path = path
        self._loop = loop
        self._volume = max(0.0, min(1.0, float(volume)))
        self._player: Optional[QMediaPlayer] = None
        self._audio: Optional[QAudioOutput] = None
        self._sink: Optional[QVideoSink] = None
        self._full_frame: Optional[QImage] = None
        self._initializing_preview = True
        self._preview_interval = 1.0 / 12.0
        self._last_preview_time = 0.0

    @Slot()
    def start(self) -> None:
        try:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._audio.setVolume(self._volume)
            self._audio.setMuted(True)
            self._player.setAudioOutput(self._audio)

            self._sink = QVideoSink(self)
            self._sink.videoFrameChanged.connect(self._on_video_frame)
            self._player.setVideoSink(self._sink)
            self._player.errorOccurred.connect(self._on_player_error)
            self._player.positionChanged.connect(self.position_changed.emit)
            self._player.durationChanged.connect(self.duration_changed.emit)
            self._player.playbackStateChanged.connect(self._on_playback_state_changed)
            self._player.mediaStatusChanged.connect(self._on_media_status_changed)

            self._apply_loop()
            self._player.setSource(QUrl.fromLocalFile(self._path))
            self._player.play()
        except Exception as exc:  # noqa: BLE001 - сообщаем оператору
            self.error.emit(f"Не удалось запустить видео: {exc}")

    @property
    def player(self) -> Optional[QMediaPlayer]:
        return self._player

    def _apply_loop(self) -> None:
        if self._player is None:
            return
        loops = QMediaPlayer.Loops.Infinite if self._loop else 1
        self._player.setLoops(loops)

    @Slot(QMediaPlayer.MediaStatus)
    def _on_media_status_changed(self, status) -> None:
        if self._initializing_preview and status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.error.emit("Не удалось загрузить видео")

    def _pause_for_preview(self) -> None:
        if not self._initializing_preview or self._player is None:
            return
        self._initializing_preview = False
        self._player.pause()
        self._player.setPosition(0)

    @Slot(QVideoFrame)
    def _on_video_frame(self, frame: QVideoFrame) -> None:
        # QVideoFrame remains in the native playback path; create preview images
        # only at a bounded rate so 1080p playback does not allocate per frame.
        now = time.monotonic()
        if now - self._last_preview_time < self._preview_interval:
            return
        self._last_preview_time = now
        image = frame.toImage()
        if not image.isNull():
            self._full_frame = image.copy()
            target_format = QImage.Format.Format_ARGB32_Premultiplied
            if image.format() != target_format:
                image = image.convertToFormat(target_format)
            image = image.scaled(
                960,
                540,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.frame_ready.emit(image)
            self._pause_for_preview()

    def _on_player_error(self, _error, message: str) -> None:
        self.error.emit(message or "Ошибка воспроизведения видео")

    @Slot(bool)
    def set_loop(self, loop: bool) -> None:
        self._loop = bool(loop)
        self._apply_loop()

    @Slot(object)
    def set_native_output(self, widget) -> None:
        if self._player is None or self._sink is None:
            return
        if widget is not None:
            self._player.setVideoSink(None)
            self._player.setVideoOutput(widget)
        else:
            self._player.setVideoOutput(None)
            self._player.setVideoSink(self._sink)

    def grab_full_frame(self) -> Optional[QImage]:
        if self._full_frame is None or self._full_frame.isNull():
            return None
        return self._full_frame.copy()

    @Slot()
    def restore_video_sink(self) -> None:
        self.set_native_output(None)

    @Slot()
    def play(self) -> None:
        if self._player is not None:
            self._player.play()

    @Slot()
    def pause(self) -> None:
        if self._player is not None:
            self._player.pause()

    @Slot()
    def toggle_play_pause(self) -> None:
        if self._player is None:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @Slot()
    def reset_to_start(self) -> None:
        if self._player is not None:
            self._player.pause()
            self._player.setPosition(0)

    @Slot(int)
    def seek(self, position_ms: int) -> None:
        if self._player is not None:
            self._player.setPosition(max(0, int(position_ms)))

    @Slot(float)
    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._audio is not None:
            self._audio.setVolume(self._volume)

    @Slot(bool)
    def set_muted(self, muted: bool) -> None:
        if self._audio is not None:
            self._audio.setMuted(bool(muted))

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state) -> None:
        if self._initializing_preview:
            return
        self.playback_state_changed.emit(
            state == QMediaPlayer.PlaybackState.PlayingState
        )

    @Slot()
    def stop(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:  # noqa: BLE001 - на выходе не критично
                pass
        self.stopped.emit()


class PdfRenderWorker(QObject):
    """Открывает документ и рендерит страницы PyMuPDF в фоновом потоке."""

    frame_ready = Signal(QImage)
    page_rendered = Signal(int, int)
    document_opened = Signal(int)
    failed = Signal(str)
    closed = Signal()

    def __init__(self, path: str, dpi: int = 150) -> None:
        super().__init__()
        self._path = path
        self._dpi = max(72, int(dpi))
        self._doc = None

    @Slot()
    def open_document(self) -> None:
        try:
            fitz = _import_fitz()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"PyMuPDF не установлен: {exc}")
            return

        try:
            self._doc = fitz.open(self._path)
            count = int(self._doc.page_count)
            if count <= 0:
                raise RuntimeError("PDF не содержит страниц")
            self.document_opened.emit(count)
            self.render_page(0)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Не удалось открыть PDF: {exc}")

    @Slot(int)
    def render_page(self, index: int) -> None:
        if self._doc is None:
            return
        try:
            fitz = _import_fitz()
            count = int(self._doc.page_count)
            index = max(0, min(int(index), count - 1))
            page = self._doc.load_page(index)
            zoom = self._dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format.Format_RGB888,
            ).copy()
            del pix
            if image.width() > 1920 or image.height() > 1080:
                image = image.scaled(
                    1920,
                    1080,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            gc.collect()
            self.frame_ready.emit(image)
            self.page_rendered.emit(index, count)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Не удалось отрисовать страницу PDF: {exc}")

    @Slot()
    def shutdown(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:  # noqa: BLE001
                pass
            self._doc = None
        self.closed.emit()


class CaptureWorker(QThread):
    """Захват монитора или окна: dxcam, при ошибке — mss."""

    frame_ready = Signal(QImage)
    backend_changed = Signal(str)
    capture_error = Signal(str)

    def __init__(
        self,
        monitor_index: int = 0,
        hwnd: Optional[int] = None,
        fps: int = 30,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.monitor_index = max(0, int(monitor_index))
        self.hwnd = hwnd
        self.fps = max(1, int(fps))
        self._running = True

    def request_stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D401 - точка входа QThread
        try:
            if self.hwnd is not None:
                self._capture_window()
                return
            try:
                self._capture_dxcam()
            except Exception as dxcam_exc:  # noqa: BLE001
                if not self._running:
                    return
                try:
                    self._capture_mss()
                except Exception as mss_exc:  # noqa: BLE001
                    self.capture_error.emit(
                        f"Захват недоступен. dxcam: {dxcam_exc}; mss: {mss_exc}"
                    )
        except Exception as exc:  # noqa: BLE001
            self.capture_error.emit(f"Ошибка захвата: {exc}")

    # ------------------------------------------------------------------
    def _capture_dxcam(self) -> None:
        dxcam = _dxcam  # noqa: PLC0415 - загружен заранее, до QApplication
        if dxcam is None:
            raise RuntimeError("dxcam не установлен")

        # BGRA, а не RGB: RGB-режим dxcam конвертирует кадр через OpenCV,
        # а cv2 в бандл не входит. BGRA отдается как есть, без лишней
        # зависимости и копирования; формат совпадает с QImage ARGB32
        # (в памяти Windows little-endian это те же байты B,G,R,A).
        camera = dxcam.create(output_idx=self.monitor_index, output_color="BGRA")
        if camera is None:
            raise RuntimeError("dxcam.create() вернул None")
        self.backend_changed.emit("dxcam")

        interval = 1.0 / self.fps
        next_time = time.perf_counter()
        consecutive_failures = 0
        try:
            while self._running:
                try:
                    frame = camera.grab()
                except Exception:  # noqa: BLE001 - переключаемся на mss после серии сбоев
                    frame = None
                if frame is not None:
                    consecutive_failures = 0
                    height, width = int(frame.shape[0]), int(frame.shape[1])
                    image = QImage(
                        frame.tobytes(),
                        width,
                        height,
                        width * 4,
                        QImage.Format.Format_ARGB32,
                    ).copy()
                    del frame
                    if image.width() > 1920 or image.height() > 1080:
                        image = image.scaled(
                            1920,
                            1080,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.FastTransformation,
                        )
                    self.frame_ready.emit(image)
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        raise RuntimeError(
                            "dxcam не вернул кадр 5 раз подряд; переключение на mss"
                        )
                next_time = self._pace(next_time, interval)
        finally:
            try:
                camera.release()
            except Exception:  # noqa: BLE001
                pass

    def _capture_mss(self) -> None:
        import mss  # noqa: PLC0415

        self.backend_changed.emit("mss")
        interval = 1.0 / self.fps
        next_time = time.perf_counter()
        with mss.mss() as sct:
            monitors = sct.monitors
            index = self.monitor_index + 1
            monitor = monitors[index] if index < len(monitors) else monitors[0]
            while self._running:
                try:
                    shot = sct.grab(monitor)
                    image = QImage(
                        shot.bgra,
                        shot.width,
                        shot.height,
                        QImage.Format.Format_ARGB32,
                    ).copy()
                    del shot
                    if image.width() > 1920 or image.height() > 1080:
                        image = image.scaled(
                            1920,
                            1080,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.FastTransformation,
                        )
                    self.frame_ready.emit(image)
                except Exception as exc:  # noqa: BLE001 - не роняем поток
                    self.capture_error.emit(f"mss: {exc}")
                    time.sleep(0.2)
                next_time = self._pace(next_time, interval)

    def _capture_window(self) -> None:
        try:
            import ctypes  # noqa: PLC0415
            import win32con  # noqa: PLC0415
            import win32gui  # noqa: PLC0415
            import win32ui  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"pywin32 недоступен: {exc}") from exc

        self.backend_changed.emit("PrintWindow")
        interval = 1.0 / self.fps
        next_time = time.perf_counter()
        user32 = ctypes.windll.user32
        user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        user32.PrintWindow.restype = ctypes.c_bool

        while self._running:
            if not win32gui.IsWindow(self.hwnd):
                self.capture_error.emit("Захваченное окно было закрыто")
                return
            if win32gui.IsIconic(self.hwnd):
                next_time = self._pace(next_time, interval)
                continue

            window_dc = None
            window_dc_handle = None
            save_dc = None
            bitmap = None
            old_bitmap = None
            try:
                left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
                width = int(right - left)
                height = int(bottom - top)
                if width <= 0 or height <= 0:
                    next_time = self._pace(next_time, interval)
                    continue

                window_dc_handle = win32gui.GetWindowDC(self.hwnd)
                if not window_dc_handle:
                    raise RuntimeError("GetWindowDC вернул нулевой дескриптор")
                window_dc = win32ui.CreateDCFromHandle(window_dc_handle)
                save_dc = window_dc.CreateCompatibleDC()
                bitmap = win32ui.CreateBitmap()
                bitmap.CreateCompatibleBitmap(window_dc, width, height)
                old_bitmap = save_dc.SelectObject(bitmap)

                if not user32.PrintWindow(int(self.hwnd), save_dc.GetSafeHdc(), 2):
                    next_time = self._pace(next_time, interval)
                    continue

                bits = bitmap.GetBitmapBits(True)
                expected_size = width * height * 4
                if len(bits) < expected_size:
                    raise RuntimeError(
                        f"PrintWindow вернул неполный буфер: {len(bits)} из {expected_size}"
                    )
                image = QImage(
                    bits,
                    width,
                    height,
                    width * 4,
                    QImage.Format.Format_ARGB32,
                ).copy().mirrored(False, True)
                self.frame_ready.emit(image)
            except Exception as exc:  # noqa: BLE001 - продолжаем захват
                self.capture_error.emit(f"PrintWindow: {exc}")
                time.sleep(0.2)
            finally:
                if save_dc is not None and old_bitmap is not None:
                    try:
                        save_dc.SelectObject(old_bitmap)
                    except Exception:  # noqa: BLE001
                        pass
                if bitmap is not None:
                    try:
                        win32gui.DeleteObject(bitmap.GetHandle())
                    except Exception:  # noqa: BLE001
                        pass
                if save_dc is not None:
                    try:
                        save_dc.DeleteDC()
                    except Exception:  # noqa: BLE001
                        pass
                if window_dc is not None:
                    try:
                        window_dc.DeleteDC()
                    except Exception:  # noqa: BLE001
                        pass
                if window_dc_handle:
                    try:
                        win32gui.ReleaseDC(self.hwnd, window_dc_handle)
                    except Exception:  # noqa: BLE001
                        pass

            next_time = self._pace(next_time, interval)

    @staticmethod
    def _pace(next_time: float, interval: float) -> float:
        next_time += interval
        delay = next_time - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
            return next_time
        return time.perf_counter()
