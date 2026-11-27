"""Генератор тестовых материалов: картинки, PDF и видео.

Запуск:
    python tools/make_test_media.py

Материалы складываются в папку test_media рядом с проектом.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT_DIR / "test_media"


def _ensure_app() -> None:
    from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

    if QGuiApplication.instance() is None:
        QGuiApplication(sys.argv[:1])


def make_color_bars(path: Path) -> bool:
    from PySide6.QtCore import QRect, Qt  # noqa: PLC0415
    from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: PLC0415

    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    colors = [
        "#ffffff", "#ffe600", "#00e5ff", "#00c853",
        "#e91e63", "#d500f9", "#f44336", "#1565c0",
    ]
    bar_width = image.width() // len(colors)
    for index, color in enumerate(colors):
        painter.fillRect(
            index * bar_width, 0, bar_width, image.height(), QColor(color)
        )
    painter.fillRect(0, 850, image.width(), 230, QColor(0, 0, 0, 210))

    font = QFont("Segoe UI", 84, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(
        QRect(0, 850, image.width(), 230),
        Qt.AlignmentFlag.AlignCenter,
        "COLOR BARS · PROGRAM",
    )
    painter.end()
    return image.save(str(path))


def make_gradient(path: Path, title: str, color_a: str, color_b: str) -> bool:
    from PySide6.QtCore import QRect, Qt  # noqa: PLC0415
    from PySide6.QtGui import (  # noqa: PLC0415
        QColor,
        QFont,
        QImage,
        QLinearGradient,
        QPainter,
    )

    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    painter = QPainter(image)

    gradient = QLinearGradient(0, 0, image.width(), image.height())
    gradient.setColorAt(0.0, QColor(color_a))
    gradient.setColorAt(1.0, QColor(color_b))
    painter.fillRect(image.rect(), gradient)

    font = QFont("Segoe UI", 110, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, title)

    font = QFont("Segoe UI", 36)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255, 190))
    painter.drawText(
        QRect(0, 780, image.width(), 120),
        Qt.AlignmentFlag.AlignCenter,
        "Preview source · PyVideoMixer",
    )
    painter.end()
    return image.save(str(path))


def make_pdf(path: Path) -> bool:
    try:
        import pymupdf as fitz  # noqa: PLC0415
    except ImportError:
        import fitz  # type: ignore # noqa: PLC0415

    palette = [
        (0.07, 0.10, 0.18),
        (0.16, 0.07, 0.22),
        (0.05, 0.17, 0.13),
        (0.22, 0.11, 0.05),
        (0.10, 0.10, 0.12),
        (0.05, 0.14, 0.20),
    ]
    document = fitz.open()
    try:
        for index in range(6):
            page = document.new_page(width=1280, height=720)
            page.draw_rect(page.rect, color=None, fill=palette[index % len(palette)])
            page.insert_text(
                (90, 330),
                f"PDF PAGE {index + 1}",
                fontsize=120,
                color=(1, 1, 1),
            )
            page.insert_text(
                (90, 430),
                "Next / Prev page →  ← / → arrow keys",
                fontsize=34,
                color=(0.85, 0.88, 0.95),
            )
            page.insert_text(
                (90, 640),
                f"page {index + 1} of 6",
                fontsize=28,
                color=(0.65, 0.70, 0.80),
            )
        document.save(str(path))
    finally:
        document.close()
    return path.exists()


def _find_ffmpeg() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - необязательная зависимость
        return None


def make_video(path: Path) -> bool:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print(
            "ffmpeg не найден — MP4 не создан. "
            "Установите ffmpeg или imageio-ffmpeg, либо добавьте видео вручную."
        )
        return False

    command = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-t",
        "10",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False)
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось запустить ffmpeg: {exc}")
        return False

    if result.returncode != 0:
        print("ffmpeg завершился с ошибкой — MP4 не создан.")
        return False
    return path.exists()


def make_60fps_video(path: Path) -> bool:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print(
            "ffmpeg не найден — 60 FPS MP4 не создан. "
            "Установите ffmpeg или imageio-ffmpeg."
        )
        return False

    filter_graph = (
        "drawbox=x='(w-140)*(0.5+0.5*sin(2*PI*t/2))':y=440:"
        "w=140:h=140:color=red@1:t=fill,"
        "drawtext=fontcolor=white:fontsize=52:box=1:boxcolor=black@0.65:"
        "x=50:y=50:text='FRAME %{n}  TIME %{pts\\:hms}'"
    )
    command = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1920x1080:rate=60",
        "-vf",
        filter_graph,
        "-t",
        "8",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "60",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False)
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось запустить ffmpeg для 60 FPS видео: {exc}")
        return False

    if result.returncode != 0:
        print("ffmpeg не смог создать 60 FPS MP4.")
        return False
    return path.exists()


def make_pptx(path: Path) -> bool:
    """Тестовая презентация: фон, картинки, фигуры, таблица, текст."""
    try:
        from pptx import Presentation  # noqa: PLC0415
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.enum.shapes import MSO_SHAPE  # noqa: PLC0415
        from pptx.enum.text import PP_ALIGN  # noqa: PLC0415
        from pptx.util import Inches, Pt  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"python-pptx не установлен — PPTX не создан ({exc}).")
        return False

    def background(slide, color) -> None:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(*color)

    def text(slide, left, top, width, height, value, size, color=(255, 255, 255),
             bold=False, align=PP_ALIGN.LEFT, font="Segoe UI"):
        box = slide.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.word_wrap = True
        paragraph = frame.paragraphs[0]
        paragraph.alignment = align
        run = paragraph.add_run()
        run.text = value
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.name = font
        run.font.color.rgb = RGBColor(*color)

    def bullets(slide, left, top, width, height, lines, size=22) -> None:
        box = slide.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.word_wrap = True
        for index, line in enumerate(lines):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            run = paragraph.add_run()
            run.text = f"• {line}"
            run.font.size = Pt(size)
            run.font.name = "Segoe UI"
            run.font.color.rgb = RGBColor(0xE6, 0xE8, 0xEB)

    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]

    slide = presentation.slides.add_slide(blank)
    background(slide, (0x10, 0x13, 0x17))
    text(slide, Inches(0.9), Inches(2.2), Inches(11.5), Inches(1.4),
         "POWERPOINT DECK", 66, bold=True)
    text(slide, Inches(0.9), Inches(3.7), Inches(11.5), Inches(0.9),
         "PPTX → PDF → PyVideoMixer", 30, color=(0x9A, 0xA4, 0xB2))
    text(slide, Inches(0.9), Inches(6.2), Inches(11.5), Inches(0.6),
         "слайд 1 из 5", 18, color=(0x6B, 0x74, 0x80))

    slide = presentation.slides.add_slide(blank)
    background(slide, (0x0F, 0x2A, 0x4A))
    text(slide, Inches(0.8), Inches(0.5), Inches(11.8), Inches(0.9),
         "Изображения сохраняются", 40, bold=True)
    bars = OUT_DIR / "color_bars.png"
    if bars.exists():
        slide.shapes.add_picture(str(bars), Inches(0.9), Inches(1.7), width=Inches(8.2))
    text(slide, Inches(9.4), Inches(2.2), Inches(3.2), Inches(2.5),
         "Картинка\nвставлена\nкак есть", 24, color=(0xBB, 0xD7, 0xF0))

    slide = presentation.slides.add_slide(blank)
    background(slide, (0x14, 0x1E, 0x30))
    text(slide, Inches(0.8), Inches(0.5), Inches(11.8), Inches(0.9),
         "Три уровня конвертации", 40, bold=True)
    boxes = (
        ("1", "PowerPoint\nCOM / SaveAs PDF", (0x25, 0x63, 0xEB)),
        ("2", "LibreOffice\n--headless", (0x0F, 0x76, 0x6E)),
        ("3", "python-pptx\n+ PyMuPDF", (0x7C, 0x3A, 0xED)),
    )
    for index, (number, caption, color) in enumerate(boxes):
        shape = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(0.9 + index * 4.0), Inches(1.9),
            Inches(3.5), Inches(3.6),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(*color)
        shape.line.fill.background()
        frame = shape.text_frame
        frame.word_wrap = True
        frame.text = f"{number}\n{caption}"
        for paragraph in frame.paragraphs:
            paragraph.alignment = PP_ALIGN.CENTER
            for run in paragraph.runs:
                run.font.size = Pt(26)
                run.font.bold = True
                run.font.name = "Segoe UI"
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    bullets(slide, Inches(0.9), Inches(5.8), Inches(11.5), Inches(1.4),
            ["если нет PowerPoint — пробуем LibreOffice", "если нет и его — собираем PDF сами"],
            size=20)

    slide = presentation.slides.add_slide(blank)
    background(slide, (0x1B, 0x1B, 0x1F))
    text(slide, Inches(0.8), Inches(0.4), Inches(11.8), Inches(0.9),
         "Таблицы и текст", 40, bold=True)
    rows, columns = 4, 3
    table_shape = slide.shapes.add_table(
        rows, columns, Inches(0.9), Inches(1.7), Inches(11.5), Inches(4.6)
    )
    table = table_shape.table
    data = (
        ("Уровень", "Движок", "Когда используется"),
        ("1", "Microsoft PowerPoint", "установлен Office"),
        ("2", "LibreOffice", "есть soffice"),
        ("3", "python-pptx + PyMuPDF", "автономный фолбэк"),
    )
    for row_index, row in enumerate(data):
        for column_index, value in enumerate(row):
            cell = table.cell(row_index, column_index)
            cell.text = value
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(20 if row_index else 22)
                    run.font.bold = row_index == 0
                    run.font.name = "Segoe UI"

    slide = presentation.slides.add_slide(blank)
    background(slide, (0x22, 0x14, 0x0A))
    ocean = OUT_DIR / "gradient_ocean.jpg"
    if ocean.exists():
        slide.shapes.add_picture(str(ocean), Inches(0), Inches(0), width=Inches(13.333))
    text(slide, Inches(0.9), Inches(2.3), Inches(11.5), Inches(1.6),
         "Слайд 5", 72, bold=True, align=PP_ALIGN.CENTER)
    text(slide, Inches(0.9), Inches(4.1), Inches(11.5), Inches(0.8),
         "панель слайд-шоу управляет и PPTX", 26,
         color=(0xFF, 0xE0, 0x8A), align=PP_ALIGN.CENTER)

    presentation.save(str(path))
    return path.exists()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_app()

    make_color_bars(OUT_DIR / "color_bars.png")
    make_gradient(OUT_DIR / "gradient_sunset.webp", "SUNSET", "#ff512f", "#dd2476")
    make_gradient(OUT_DIR / "gradient_ocean.jpg", "OCEAN", "#141e30", "#243b55")
    make_pdf(OUT_DIR / "presentation.pdf")
    make_pptx(OUT_DIR / "presentation.pptx")
    make_video(OUT_DIR / "test_video.mp4")
    make_60fps_video(OUT_DIR / "test_60fps.mp4")

    print(f"Тестовые материалы готовы: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
