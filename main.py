"""Точка входа PyVideoMixer — видеомикшера для мероприятий."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import workers  # noqa: E402
from sources.pptx_source import cleanup_temp_dirs  # noqa: E402
from sources.screen_source import ScreenSource  # noqa: E402
from sources.video_source import VideoSource  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.style import STYLE_SHEET  # noqa: E402


def _pump(app: QApplication, timeout: float, predicate: Optional[Callable[[], bool]] = None) -> bool:
    """Крутит очередь событий Qt, пока не сработает predicate или не истечет timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(0.01)
    return bool(predicate is not None and predicate())


def run_selftest(report_path: str, video_path: Optional[str]) -> int:
    """Диагностика собранного exe: главное окно, QMediaPlayer, захват экрана.

    У windowed-сборки нет консоли, поэтому результат пишется в JSON-файл.
    Код возврата 0 — все проверки прошли.
    """
    app = QApplication([sys.argv[0]])
    app.setApplicationName("PyVideoMixer")
    app.setOrganizationName("PyVideoMixer")
    app.setStyleSheet(STYLE_SHEET)

    report: dict = {"checks": {}, "details": {}, "ok": False}
    checks = report["checks"]
    details = report["details"]
    window = None

    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget  # noqa: PLC0415

        if QApplication.instance() is not None:
            opengl_widget = QOpenGLWidget()
            opengl_widget.close()
            del opengl_widget
        checks["qopenglwidgets"] = True
    except Exception as exc:  # noqa: BLE001
        checks["qopenglwidgets"] = False
        details["qopenglwidgets_error"] = repr(exc)
        report["ok"] = False

    try:
        from ui.output_window import VideoOutputWindow  # noqa: PLC0415

        video_output = VideoOutputWindow()
        video_output.close()
        del video_output
        checks["qvideowidget"] = True
    except Exception as exc:  # noqa: BLE001
        checks["qvideowidget"] = False
        details["qvideowidget_error"] = repr(exc)
        report["ok"] = False

    try:
        window = MainWindow()
        window.show()
        _pump(app, 1.5)
        checks["main_window"] = window.isVisible()
        details["window_title"] = window.windowTitle()
        details["platform"] = app.platformName()

        if video_path:
            errors: list = []
            video = VideoSource(Path(video_path).name, video_path, loop=True)
            video.error.connect(errors.append)
            video.start()
            checks["qmediaplayer"] = _pump(
                app, 12.0, lambda: video.get_latest() is not None
            )
            frame = video.get_latest()
            details["video_frame"] = (
                f"{frame.width()}x{frame.height()}" if frame is not None else None
            )
            video.stop()
            if errors:
                details["video_errors"] = errors
        else:
            checks["qmediaplayer"] = None
            details["video_skipped"] = "--video не передан"

        # PyMuPDF: рендер страницы (проверяет DLL mupdf в бандле).
        try:
            import pymupdf  # noqa: PLC0415

            doc = pymupdf.open()
            page = doc.new_page(width=320, height=180)
            page.insert_text((20, 40), "selftest", fontsize=14)
            pix = page.get_pixmap(dpi=72)
            doc.close()
            checks["pymupdf"] = pix.width > 0 and pix.height > 0
        except Exception as exc:  # noqa: BLE001
            checks["pymupdf"] = False
            details["pymupdf_error"] = repr(exc)

        # python-pptx: Presentation() читает встроенный templates/default.pptx.
        pptx_bytes = None
        try:
            import io  # noqa: PLC0415

            from pptx import Presentation  # noqa: PLC0415
            from pptx.util import Inches  # noqa: PLC0415

            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
            box.text_frame.text = "selftest"
            buffer = io.BytesIO()
            deck.save(buffer)
            pptx_bytes = buffer.getvalue()
            checks["python_pptx"] = len(pptx_bytes) > 0
        except Exception as exc:  # noqa: BLE001
            checks["python_pptx"] = False
            details["pptx_error"] = repr(exc)

        # COM/pywin32: проверяем, что hiddenimports попали в бандл, даже если
        # на этой машине нет PowerPoint/LibreOffice.
        try:
            import comtypes  # noqa: F401,PLC0415
            import pythoncom  # noqa: F401,PLC0415
            import pywintypes  # noqa: F401,PLC0415
            import win32com.client  # noqa: F401,PLC0415

            checks["com_imports"] = True
        except Exception as exc:  # noqa: BLE001
            checks["com_imports"] = False
            details["com_error"] = repr(exc)

        # Автономный рендерер PPTX->PDF (python-pptx + PyMuPDF, без Office).
        if pptx_bytes:
            try:
                import shutil  # noqa: PLC0415
                import tempfile  # noqa: PLC0415

                from core.pptx_convert import _PptxRenderer  # noqa: PLC0415

                work = Path(tempfile.mkdtemp(prefix="pvm_selftest_"))
                source = work / "probe.pptx"
                target = work / "probe.pdf"
                try:
                    source.write_bytes(pptx_bytes)
                    pages = _PptxRenderer(str(source), str(target)).render()
                    checks["pptx_to_pdf"] = pages >= 1 and target.stat().st_size > 0
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            except Exception as exc:  # noqa: BLE001
                checks["pptx_to_pdf"] = False
                details["pptx_to_pdf_error"] = repr(exc)

        checks["dxcam_module"] = workers._dxcam is not None
        errors = []
        capture = ScreenSource("selftest", monitor_index=0, fps=10)
        capture.error.connect(errors.append)
        capture.start()
        checks["screen_capture"] = _pump(
            app, 12.0, lambda: capture.get_latest() is not None
        )
        frame = capture.get_latest()
        details["capture_frame"] = (
            f"{frame.width()}x{frame.height()}" if frame is not None else None
        )
        details["capture_backend"] = capture.backend_name
        if frame is not None:
            try:
                capture_png = Path(report_path).with_name(
                    f"{Path(report_path).stem}_capture.png"
                )
                frame.save(str(capture_png))
                details["capture_png"] = str(capture_png)
            except Exception:  # noqa: BLE001 - картинка не влияет на вердикт
                pass
        capture.stop()
        if errors:
            details["capture_errors"] = errors

        report["ok"] = all(value for value in checks.values() if value is not None)
    except Exception as exc:  # noqa: BLE001 - отчёт важнее трассировки
        report["error"] = repr(exc)
    finally:
        if window is not None:
            window.close()
            _pump(app, 0.3)
        try:
            Path(report_path).write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"Не удалось записать отчёт: {exc}", file=sys.stderr)

    return 0 if report.get("ok") else 1


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        report_path = args[1] if len(args) > 1 else str(Path.cwd() / "selftest.json")
        video_path = args[2] if len(args) > 2 else None
        return run_selftest(report_path, video_path)

    if getattr(sys, "frozen", False):
        try:
            import os

            log_dir = Path(os.environ["LOCALAPPDATA"]) / "PyVideoMixer"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "pvm.log"
            if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                log_path.with_name("pvm.log.1").unlink(missing_ok=True)
                log_path.replace(log_path.with_name("pvm.log.1"))
            log_file = log_path.open("a", encoding="utf-8", errors="replace", buffering=1)
            sys.stdout = sys.stderr = log_file
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("PyVideoMixer")
    app.setOrganizationName("PyVideoMixer")
    app.setStyleSheet(STYLE_SHEET)

    # Временные PDF от PowerPoint удаляем даже при аварийном закрытии окна.
    app.aboutToQuit.connect(cleanup_temp_dirs)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
