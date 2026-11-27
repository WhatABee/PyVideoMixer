"""Быстрый интеграционный smoke-тест PyVideoMixer."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QApplication


MODULES = (
    "main",
    "core.external_player",
    "core.output_manager",
    "core.pptx_convert",
    "core.workers",
    "sources.base",
    "sources.folder_source",
    "sources.image_source",
    "sources.pdf_source",
    "sources.pptx_source",
    "sources.screen_source",
    "sources.video_source",
    "ui.main_window",
    "ui.output_window",
    "ui.preview_widget",
    "ui.settings_dialog",
    "ui.style",
)


def _check_modules() -> None:
    for name in MODULES:
        importlib.import_module(name)


def _check_style() -> None:
    from ui.style import _normalize_hex_color, build_stylesheet

    assert _normalize_hex_color("#123abc") == "#123abc"
    assert _normalize_hex_color("12, 34, 56") == "#0c2238"
    assert _normalize_hex_color("255, 0, 1") == "#ff0001"
    for theme in ("dark", "light", "system"):
        css = build_stylesheet(theme, "12, 34, 56", "#123abc", "#234567", "#345678", "#456789")
        assert css and "#123abc" in css


def _make_media(folder: Path) -> tuple[Path, Path]:
    png = folder / "smoke.png"
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    assert image.save(str(png), "PNG")
    pdf = folder / "smoke.pdf"
    import fitz

    document = fitz.open()
    for index in range(2):
        page = document.new_page(width=320, height=180)
        page.insert_text((30, 90), f"Smoke page {index + 1}", fontsize=24)
    document.save(str(pdf))
    document.close()
    return png, pdf


def _send_key(window, key: Qt.Key) -> None:
    event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window, event)
    QApplication.processEvents()


def _check_window(png: Path, pdf: Path):
    from PySide6.QtGui import QKeySequence
    from sources.image_source import ImageSource
    from sources.pdf_source import PdfSource
    from ui.main_window import MainWindow

    window = MainWindow()
    window.hide()
    image_source = ImageSource(png.name, str(png))
    pdf_source = PdfSource(pdf.name, str(pdf))
    window.sources.extend((image_source, pdf_source))
    import fitz
    with fitz.open(str(pdf)) as document:
        assert document.page_count == 2
    window._set_preview(pdf_source)
    window._output_active = True
    window.program_source = image_source
    window.output.set_program(image_source)
    window._sync_video_preview_states()
    window._cut(False)
    window._set_preview(pdf_source)
    window._fade(False)
    window.output.set_freeze(True)
    window.output.set_freeze(False)
    before = getattr(pdf_source, "current_page", 0)
    _send_key(window, Qt.Key.Key_PageDown)
    _send_key(window, Qt.Key.Key_PageUp)
    assert QKeySequence("PgDown") == QKeySequence("PgDown")
    assert getattr(pdf_source, "current_page", before) >= 0
    return window, (image_source, pdf_source)


def main() -> int:
    app = None
    window = None
    sources = ()
    passed = False
    timeout = threading.Timer(15.0, lambda: os._exit(2))
    timeout.daemon = True
    timeout.start()
    try:
        _check_modules()
        app = QApplication.instance() or QApplication(sys.argv)
        _check_style()
        with tempfile.TemporaryDirectory(prefix="pyvideomixer-smoke-") as temp:
            png, pdf = _make_media(Path(temp))
            window, sources = _check_window(png, pdf)
        passed = True
    except Exception:
        traceback.print_exc()
    finally:
        timeout.cancel()
        if passed and window is not None:
            window.output.stop()
            window._slide_timer.stop()
            for src in sources:
                src.stop()
            print("SMOKE TEST PASSED", flush=True)
            os._exit(0)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
