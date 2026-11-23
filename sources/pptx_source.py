"""PowerPoint source with live COM slideshow and PDF fallback."""

from __future__ import annotations

import queue
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Set

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtGui import QImage

from core.pptx_convert import PptxConvertWorker
from sources.base import BaseSource
from sources.pdf_source import PdfSource

_TEMP_DIRS: Set[Path] = set()
_ORPHAN_THREADS: list[QThread] = []


def cleanup_temp_dirs() -> None:
    for thread in list(_ORPHAN_THREADS):
        try:
            if thread.isRunning():
                thread.quit()
                thread.wait(1000)
            _ORPHAN_THREADS.remove(thread)
        except Exception:  # noqa: BLE001
            pass
    for directory in sorted(_TEMP_DIRS):
        shutil.rmtree(directory, ignore_errors=True)
    _TEMP_DIRS.clear()


def _retire_thread(thread: QThread) -> None:
    if not thread.isRunning():
        return
    _ORPHAN_THREADS.append(thread)
    thread.finished.connect(lambda _=None, t=thread: _forget_thread(t))


def _forget_thread(thread: QThread) -> None:
    try:
        _ORPHAN_THREADS.remove(thread)
    except ValueError:
        pass


class PowerPointWorker(QThread):
    frame_ready = Signal(QImage)
    opened = Signal(int)
    position_changed = Signal(int, int)
    failed = Signal(str)

    def __init__(self, path: str, fps: int = 10, parent=None) -> None:
        super().__init__(parent)
        self.path = str(Path(path).resolve())
        self.fps = max(1, int(fps))
        self._commands: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        self._started = threading.Event()
        self.start_error: Optional[str] = None

    def command(self, name: str, value=None) -> None:
        self._commands.put((name, value))

    def run(self) -> None:
        pythoncom = app = pres = ss_win = None
        try:
            import pythoncom as _pythoncom
            import win32com.client
            import win32con
            import win32gui
            import win32ui
            import ctypes

            pythoncom = _pythoncom
            pythoncom.CoInitialize()
            app = win32com.client.DispatchEx("PowerPoint.Application")
            app.DisplayAlerts = 1
            pres = app.Presentations.Open(
                self.path, ReadOnly=True, Untitled=False, WithWindow=True
            )
            win32gui.ShowWindow(int(app.HWND), 0)
            settings = pres.SlideShowSettings
            settings.ShowType = 2
            settings.ShowWithAnimation = True
            ss_win = settings.Run()
            print("[PPTX] COM started", flush=True)
            hwnd = int(ss_win.HWND)
            # Keep the slideshow inside the virtual desktop so DWM continues rendering it.
            virtual_left = win32gui.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            virtual_top = win32gui.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            virtual_width = win32gui.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            virtual_height = win32gui.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_BOTTOM, virtual_left, virtual_top,
                virtual_width, virtual_height,
                win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
            )
            ss_win.View.First()
            self._started.set()

            user32 = ctypes.windll.user32
            user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            user32.PrintWindow.restype = ctypes.c_bool
            interval = 1.0 / self.fps
            next_frame = time.perf_counter()
            while not self._stopping.is_set():
                while True:
                    try:
                        command, value = self._commands.get_nowait()
                    except queue.Empty:
                        break
                    view = ss_win.View
                    if command == "next":
                        view.Next()
                    elif command == "previous":
                        view.Previous()
                    elif command == "goto":
                        view.GotoSlide(max(1, min(int(value), int(pres.Slides.Count))))
                    elif command == "first":
                        view.First()
                    else:
                        continue

                    current_slide = int(view.Slide.SlideIndex)
                    slide_count = int(pres.Slides.Count)
                    self.position_changed.emit(current_slide, slide_count)
                    self._capture_frame(
                        hwnd, win32gui, win32ui, user32, current_slide, slide_count
                    )

                try:
                    view = ss_win.View
                    current_slide = int(view.Slide.SlideIndex)
                    slide_count = int(pres.Slides.Count)
                    self.position_changed.emit(current_slide, slide_count)
                    self._capture_frame(
                        hwnd, win32gui, win32ui, user32, current_slide, slide_count
                    )
                except Exception as exc:  # noqa: BLE001
                    self.failed.emit(str(exc))
                    break
                next_frame += interval
                self._stopping.wait(max(0, next_frame - time.perf_counter()))
        except Exception as exc:  # noqa: BLE001
            print(f"[PPTX] Fallback to PDF: {exc}", flush=True)
            self.start_error = str(exc)
            self.failed.emit(self.start_error)
        finally:
            self._started.set()
            if ss_win is not None:
                try:
                    ss_win.View.Exit()
                except Exception:
                    pass
            if pres is not None:
                try:
                    pres.Close()
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _capture_frame(self, hwnd, win32gui, win32ui, user32, current_slide, slide_count) -> None:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        dc_handle = win32gui.GetWindowDC(hwnd)
        window_dc = win32ui.CreateDCFromHandle(dc_handle)
        memory_dc = window_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(window_dc, width, height)
        old_bitmap = memory_dc.SelectObject(bitmap)
        try:
            if user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 2):
                info = bitmap.GetInfo()
                bits = bitmap.GetBitmapBits(True)
                image = QImage(bits, info["bmWidth"], info["bmHeight"],
                               info["bmWidthBytes"], QImage.Format.Format_RGB32).copy()
                self.frame_ready.emit(image)
        finally:
            memory_dc.SelectObject(old_bitmap)
            win32gui.DeleteObject(bitmap.GetHandle())
            memory_dc.DeleteDC()
            window_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, dc_handle)

    def stop(self) -> None:
        self._stopping.set()
        self.wait(5000)


class PptxSource(PdfSource):
    kind = "pptx"
    converted = Signal(str)
    stage_changed = Signal(str)

    def __init__(self, name: str, path: str, dpi: int = 150, parent=None) -> None:
        super().__init__(name, path, dpi, parent)
        self.stage = ""
        self._temp_dir: Optional[Path] = Path(tempfile.mkdtemp(prefix="pvm_pptx_"))
        _TEMP_DIRS.add(self._temp_dir)
        self._convert_thread: Optional[QThread] = None
        self._converter: Optional[PptxConvertWorker] = None
        self._converting = False
        self._ppt_worker: Optional[PowerPointWorker] = None
        self._use_com = False

    def start(self) -> None:
        BaseSource.start(self)
        if self._closed or self._ppt_worker is not None or self._converting:
            return
        worker = PowerPointWorker(self.path)
        worker.frame_ready.connect(self._publish)
        worker.opened.connect(self._on_ppt_opened)
        worker.position_changed.connect(self._on_ppt_position)
        worker.failed.connect(self._on_ppt_failed)
        self._ppt_worker = worker
        self._use_com = True
        worker.start()
        if not worker._started.wait(15) or worker.start_error:
            self._ppt_worker = None
            self._use_com = False
            worker.stop()
            self._fallback_to_pdf()

    def _fallback_to_pdf(self) -> None:
        self._use_com = False
        worker = self._ppt_worker
        self._ppt_worker = None
        if worker is not None:
            worker.stop()
        self._start_pdf_fallback()

    def _start_pdf_fallback(self) -> None:
        if self._closed or self._converting or self._temp_dir is None:
            return
        self._converting = True
        self.stage = ""
        self._converter = PptxConvertWorker(self.path, str(self._temp_dir))
        self._convert_thread = QThread()
        self._converter.moveToThread(self._convert_thread)
        self._converter.stage_changed.connect(self._on_stage_changed)
        self._converter.converted.connect(self._on_converted)
        self._converter.failed.connect(self._on_conversion_failed)
        self._convert_thread.started.connect(self._converter.convert)
        self._convert_thread.finished.connect(self._converter.deleteLater)
        self._convert_thread.start()

    def stop(self) -> None:
        worker = self._ppt_worker
        self._ppt_worker = None
        self._use_com = False
        if worker is not None:
            worker.stop()
        PdfSource.stop(self)
        self._frame = None
        thread = self._convert_thread
        self._convert_thread = None
        self._converter = None
        self._converting = False
        finished = True
        if thread is not None and thread.isRunning():
            thread.quit()
            finished = thread.wait(3000)
            if not finished:
                _retire_thread(thread)
                thread.finished.connect(self._remove_temp_dir)
        if finished:
            self._remove_temp_dir()

    close = stop

    def next_slide(self) -> None:
        if self._use_com and self._ppt_worker is not None:
            self._ppt_worker.command("next")
        else:
            super().next_page()

    def prev_slide(self) -> None:
        if self._use_com and self._ppt_worker is not None:
            self._ppt_worker.command("previous")
        else:
            super().prev_page()

    def goto_slide(self, idx: int) -> None:
        if self._use_com and self._ppt_worker is not None:
            self._ppt_worker.command("goto", idx)
        else:
            super().go_to_page(int(idx) - 1)

    def restart(self) -> None:
        if self._use_com and self._ppt_worker is not None:
            self._ppt_worker.command("first")
        else:
            super().restart()

    def _on_ppt_opened(self, count: int) -> None:
        self.page_count = count
        self.page_index = 0
        self.page_changed.emit(0, count)
        self.changed.emit()

    def _on_ppt_position(self, slide: int, count: int) -> None:
        index = max(0, slide - 1)
        if index != self.page_index or count != self.page_count:
            self.page_index, self.page_count = index, count
            self.page_changed.emit(index, count)
            self.changed.emit()

    def _on_ppt_failed(self, _message: str) -> None:
        if self._ppt_worker is not None and self._ppt_worker.start_error:
            return
        if self._use_com and self._ppt_worker is not None:
            self._fallback_to_pdf()

    def info(self) -> str:
        if self.page_count:
            return f"{self.name} · PPTX · стр. {self.page_index + 1}/{self.page_count}"
        if self.stage:
            return f"{self.name} · конвертация: {self.stage}"
        return self.name

    @Slot(str)
    def _on_stage_changed(self, stage: str) -> None:
        self.stage = stage
        self.stage_changed.emit(stage)
        self.changed.emit()

    @Slot(str)
    def _on_converted(self, pdf_path: str) -> None:
        self._converting = False
        self._stop_convert_thread()
        if self._closed:
            self._remove_temp_dir()
            return
        self.stage = ""
        self._create_worker(pdf_path)
        self.page_index = 0
        self._requested_page = 0
        self._thread.start()
        self.converted.emit(pdf_path)
        self.changed.emit()

    @Slot(str)
    def _on_conversion_failed(self, message: str) -> None:
        self._converting = False
        self._stop_convert_thread()
        self.stage = ""
        self._on_error(message)
        self.changed.emit()

    def _stop_convert_thread(self) -> None:
        thread = self._convert_thread
        self._convert_thread = None
        self._converter = None
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(3000)

    def _remove_temp_dir(self) -> None:
        directory = self._temp_dir
        self._temp_dir = None
        if directory is None:
            return
        _TEMP_DIRS.discard(directory)
        shutil.rmtree(directory, ignore_errors=True)

    def go_to_page(self, index: int) -> None:
        if self._use_com and self._ppt_worker is not None:
            self._ppt_worker.command("goto", index + 1)
        else:
            super().go_to_page(index)

    def next_page(self) -> None:
        self.next_slide()

    def prev_page(self) -> None:
        self.prev_slide()

    def first_page(self) -> None:
        if self._use_com and self._ppt_worker is not None:
            self._ppt_worker.command("first")
        else:
            super().first_page()
