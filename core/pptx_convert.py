"""Конвертация презентаций PowerPoint (.pptx/.ppt) во временный PDF.

Три уровня, от лучшего качества к автономному:

1. Microsoft PowerPoint через COM (win32com) — точный рендер слайдов.
2. LibreOffice в headless-режиме (`soffice --convert-to pdf`).
3.Автономный фолбэк: python-pptx разбирает слайды, PyMuPDF собирает PDF
   из фонов, картинок, фигур, таблиц и текста.

Вся работа идет в отдельном QThread: UI не блокируется, наружу уходят только
сигналы о стадии, готовом PDF или ошибке.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal, Slot


class PptxConvertWorker(QObject):
    """Конвертирует одну презентацию в PDF, перебирая доступные движки."""

    converted = Signal(str)  # путь к готовому PDF
    failed = Signal(str)
    stage_changed = Signal(str)

    def __init__(self, source_path: str, out_dir: str) -> None:
        super().__init__()
        self._source = str(Path(source_path).resolve())
        self._out_dir = str(Path(out_dir).resolve())

    # ------------------------------------------------------------------
    @Slot()
    def convert(self) -> None:
        out_pdf = str(Path(self._out_dir) / "slides.pdf")
        errors = []
        stages = (
            ("PowerPoint", self._via_powerpoint),
            ("LibreOffice", self._via_libreoffice),
            ("python-pptx", self._via_python_pptx),
        )
        for label, converter in stages:
            self.stage_changed.emit(label)
            try:
                # Не принимаем PDF от предыдущего запуска за результат текущего.
                cached_pdf = Path(out_pdf)
                if cached_pdf.exists():
                    cached_pdf.unlink()
                if converter(out_pdf) and self._is_ready(out_pdf):
                    self.converted.emit(out_pdf)
                    return
                errors.append(f"{label}: PDF не создан")
            except Exception as exc:  # noqa: BLE001 - показываем оператору
                errors.append(f"{label}: {exc}")

        message = "Не удалось конвертировать презентацию. " + "; ".join(errors)
        if Path(self._source).suffix.lower() == ".ppt":
            message += (
                " Старый формат .ppt требует установленного PowerPoint "
                "или LibreOffice."
            )
        self.failed.emit(message)

    @staticmethod
    def _is_ready(path: str) -> bool:
        try:
            return Path(path).exists() and Path(path).stat().st_size > 0
        except OSError:
            return False

    # ==================================================================
    # Уровень 1: Microsoft PowerPoint (COM)
    # ==================================================================
    def _via_powerpoint(self, out_pdf: str) -> bool:
        try:
            import pythoncom  # noqa: PLC0415 - Windows COM
            import win32com.client  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"pywin32 недоступен ({exc})") from exc

        pythoncom.CoInitialize()
        app = None
        deck = None
        try:
            try:
                app = win32com.client.DispatchEx("PowerPoint.Application")
            except Exception as exc:  # noqa: BLE001 - класс не зарегистрирован
                raise RuntimeError("PowerPoint не установлен") from exc

            deck = app.Presentations.Open(
                self._source,
                ReadOnly=True,
                Untitled=False,
                WithWindow=False,
            )
            deck.SaveAs(os.path.abspath(out_pdf), 32)  # ppSaveAsPDF
            return True
        finally:
            if deck is not None:
                try:
                    deck.Close()
                except Exception:  # noqa: BLE001
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:  # noqa: BLE001
                    pass
            pythoncom.CoUninitialize()

    # ==================================================================
    # Уровень 2: LibreOffice (headless)
    # ==================================================================
    def _via_libreoffice(self, out_pdf: str) -> bool:
        soffice = _find_soffice()
        if not soffice:
            raise RuntimeError("LibreOffice не найден")

        work = Path(self._out_dir) / "libreoffice"
        work.mkdir(parents=True, exist_ok=True)
        profile = (Path(self._out_dir) / "lo_profile").as_uri()
        command = [
            soffice,
            "--headless",
            "--norestore",
            "--nodefault",
            "--nolockcheck",
            f"-env:UserInstallation={profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work),
            self._source,
        ]
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            subprocess.run(
                command,
                capture_output=True,
                timeout=240,
                check=False,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("LibreOffice не ответил за 4 минуты") from exc

        produced = work / (Path(self._source).stem + ".pdf")
        if produced.exists() and produced.stat().st_size > 0:
            shutil.move(str(produced), out_pdf)
            return True
        raise RuntimeError("LibreOffice не создал PDF")

    # ==================================================================
    # Уровень 3: python-pptx + PyMuPDF
    # ==================================================================
    def _via_python_pptx(self, out_pdf: str) -> bool:
        try:
            import pptx  # noqa: F401,PLC0415 - проверка наличия
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"python-pptx не установлен ({exc})") from exc

        if Path(self._source).suffix.lower() == ".ppt":
            raise RuntimeError("python-pptx не читает формат .ppt")

        renderer = _PptxRenderer(self._source, out_pdf)
        pages = renderer.render()
        if pages <= 0:
            raise RuntimeError("в презентации нет слайдов")
        return True


def _find_soffice() -> Optional[str]:
    """Ищет исполняемый файл LibreOffice."""
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    candidates = (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


# ======================================================================
# Автономный рендерер: python-pptx -> PyMuPDF
# ======================================================================
class _PptxRenderer:
    """Собирает PDF из слайдов, которые умеет читать python-pptx.

    Это фолбэк: точные шрифты, темы и анимации не воспроизводятся, но
    композиция (фон, картинки, фигуры, таблицы, текст) сохраняется.
    """

    EMU_PER_PT = 914400.0 / 72.0

    def __init__(self, source_path: str, out_pdf: str) -> None:
        self._source = source_path
        self._out = out_pdf
        self._fitz = None  # type: ignore[assignment]
        self._background_color = (1.0, 1.0, 1.0)
        self._font_regular, self._font_bold = _find_fonts()

    # ------------------------------------------------------------------
    def render(self) -> int:
        from pptx import Presentation  # noqa: PLC0415

        import pymupdf as fitz  # noqa: PLC0415

        self._fitz = fitz
        presentation = Presentation(self._source)
        width = self._pt(presentation.slide_width) or 960.0
        height = self._pt(presentation.slide_height) or 540.0

        document = fitz.open()
        try:
            for slide in presentation.slides:
                self._background_color = self._slide_background_color(slide)
                page = document.new_page(width=width, height=height)
                self._draw_background(page, slide)
                shapes = list(slide.shapes)
                for shape in shapes:
                    if self._is_full_slide_background(shape, width, height):
                        self._draw_shape(page, shape, self._identity)
                for shape in shapes:
                    if not self._is_full_slide_background(shape, width, height):
                        self._draw_shape(page, shape, self._identity, draw_text=False)
                for shape in shapes:
                    self._draw_shape(page, shape, self._identity, draw_text=True, text_only=True)
            document.save(self._out, deflate=True, garbage=3)
            return document.page_count
        finally:
            document.close()

    # ------------------------------------------------------------------
    # Геометрия
    # ------------------------------------------------------------------
    def _pt(self, emu) -> float:
        if emu is None:
            return 0.0
        try:
            return float(emu) / self.EMU_PER_PT
        except (TypeError, ValueError):
            return 0.0

    def _identity(self, x: float, y: float, w: float, h: float):
        return self._fitz.Rect(x, y, x + w, y + h)

    def _rect_for(self, shape, transform):
        return transform(
            self._pt(getattr(shape, "left", 0)),
            self._pt(getattr(shape, "top", 0)),
            self._pt(getattr(shape, "width", 0)),
            self._pt(getattr(shape, "height", 0)),
        )

    def _group_transform(self, shape, parent):
        """Учитывает координаты группы (chOff/chExt) при обходе детей."""
        try:
            xfrm = shape._element.grpSpPr.xfrm
            off, ext = xfrm.off, xfrm.ext
            ch_off, ch_ext = xfrm.chOff, xfrm.chExt
            if off is None or ext is None or ch_off is None or ch_ext is None:
                return parent
            ox, oy = self._pt(off.x), self._pt(off.y)
            ew, eh = self._pt(ext.cx), self._pt(ext.cy)
            cx, cy = self._pt(ch_off.x), self._pt(ch_off.y)
            cw, ch = self._pt(ch_ext.cx), self._pt(ch_ext.cy)
            sx = ew / cw if cw else 1.0
            sy = eh / ch if ch else 1.0
        except Exception:  # noqa: BLE001 - группа без xfrm
            return parent

        def transform(x: float, y: float, w: float, h: float):
            return parent(ox + (x - cx) * sx, oy + (y - cy) * sy, w * sx, h * sy)

        return transform

    # ------------------------------------------------------------------
    # Отрисовка
    # ------------------------------------------------------------------
    def _slide_background_color(self, slide) -> Tuple[float, float, float]:
        try:
            from pptx.enum.dml import MSO_FILL  # noqa: PLC0415

            fill = slide.background.fill
            if fill.type == MSO_FILL.SOLID:
                return _rgb(fill.fore_color.rgb)
        except Exception:  # noqa: BLE001 - фон по умолчанию белый
            pass
        return (1.0, 1.0, 1.0)

    def _draw_background(self, page, slide) -> None:
        self._background_color = self._slide_background_color(slide)
        page.draw_rect(page.rect, color=None, fill=self._background_color)

    def _is_full_slide_background(self, shape, width: float, height: float) -> bool:
        try:
            from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: PLC0415

            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                return False
            tolerance = self.EMU_PER_PT
            return (
                self._pt(shape.left) <= tolerance
                and self._pt(shape.top) <= tolerance
                and abs(self._pt(shape.width) - width) <= tolerance
                and abs(self._pt(shape.height) - height) <= tolerance
            )
        except Exception:  # noqa: BLE001
            return False

    def _draw_shape(self, page, shape, transform, draw_text: bool = True, text_only: bool = False) -> None:
        try:
            from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            MSO_SHAPE_TYPE = None

        shape_type = getattr(shape, "shape_type", None)

        if MSO_SHAPE_TYPE is not None and shape_type == MSO_SHAPE_TYPE.GROUP:
            child_transform = self._group_transform(shape, transform)
            for child in shape.shapes:
                self._draw_shape(page, child, child_transform, draw_text, text_only)
            return

        has_text = getattr(shape, "has_text_frame", False)
        has_table = getattr(shape, "has_table", False)
        if text_only:
            # Таблицы содержат текстовые ячейки, но не всегда имеют text_frame.
            if has_table:
                self._draw_table(page, shape, self._rect_for(shape, transform))
            elif has_text:
                self._draw_text(page, shape, self._rect_for(shape, transform))
            return
        if has_table:
            self._draw_table(page, shape, self._rect_for(shape, transform))
            return
        if draw_text and has_text:
            self._draw_text(page, shape, self._rect_for(shape, transform))

        rect = self._rect_for(shape, transform)
        if rect.is_empty:
            return

        if MSO_SHAPE_TYPE is not None and shape_type == MSO_SHAPE_TYPE.PICTURE:
            if not text_only:
                self._draw_picture(page, shape, rect)
            return

        if not text_only:
            fill = self._fill_color(shape)
            if fill is not None:
                page.draw_rect(rect, color=None, fill=fill)

    def _draw_picture(self, page, shape, rect) -> None:
        try:
            blob = shape.image.blob
        except Exception:  # noqa: BLE001 - SVG/EMF/битая картинка
            return
        try:
            page.insert_image(rect, stream=blob)
        except Exception:  # noqa: BLE001 - формат не поддержан PyMuPDF
            pass

    def _fill_color(self, shape) -> Optional[Tuple[float, float, float]]:
        try:
            from pptx.enum.dml import MSO_FILL  # noqa: PLC0415

            fill = shape.fill
            if fill.type == MSO_FILL.SOLID:
                return _rgb(fill.fore_color.rgb)
        except Exception:  # noqa: BLE001 - градиент/картинка/тема
            return None
        return None

    # ------------------------------------------------------------------
    # Текст
    # ------------------------------------------------------------------
    def _draw_text(self, page, shape, rect) -> None:
        try:
            frame = shape.text_frame
        except Exception:  # noqa: BLE001
            return

        paragraphs = [p for p in frame.paragraphs if p.text.strip()]
        if not paragraphs:
            return

        left = rect.x0 + self._pt(getattr(frame, "margin_left", 0))
        right = rect.x1 - self._pt(getattr(frame, "margin_right", 0))
        top = rect.y0 + self._pt(getattr(frame, "margin_top", 0))
        bottom = rect.y1 - self._pt(getattr(frame, "margin_bottom", 0))
        if right - left < 4 or bottom - top < 4:
            left, right, top, bottom = rect.x0, rect.x1, rect.y0, rect.y1

        sizes = [self._paragraph_size(paragraph) for paragraph in paragraphs]
        total = sum(size * 1.22 for size in sizes)

        anchor = getattr(frame, "vertical_anchor", None)
        anchor_value = getattr(anchor, "value", anchor)
        if anchor_value == 3:  # MIDDLE
            top += max(0.0, (bottom - top - total) / 2)
        elif anchor_value == 4:  # BOTTOM
            top += max(0.0, bottom - top - total)

        y = top
        for paragraph, size in zip(paragraphs, sizes):
            text = self._paragraph_text(paragraph)
            if not text:
                continue
            fontname, fontfile = self._font(size, self._paragraph_bold(paragraph))
            box = self._fitz.Rect(left, y, max(left + 4.0, right), bottom)
            truncated = False
            while True:
                spare = page.insert_textbox(
                    box,
                    text,
                    fontsize=size,
                    fontname=fontname,
                    fontfile=fontfile,
                    color=self._paragraph_color(paragraph),
                    align=self._paragraph_align(paragraph),
                )
                if spare >= 0:
                    y += box.height - spare
                    break
                if size <= 4.0:
                    if len(text) <= 1:
                        break
                    if not truncated:
                        print("[PPTX] text truncated: shape=%s size=%.1fpt" % (getattr(shape, "name", "?"), size))
                        truncated = True
                    text = text[:max(1, int(len(text) * 0.9))]
                    continue
                size = max(4.0, size * 0.9)

    def _paragraph_text(self, paragraph) -> str:
        if paragraph.runs:
            text = "".join(run.text for run in paragraph.runs)
        else:
            text = paragraph.text
        text = text.replace("\x0b", "\n").replace("\r", "\n").strip()
        if text and self._has_bullet(paragraph):
            prefix = "• " * (1 + max(0, paragraph.level))
            text = prefix + text
        return text

    @staticmethod
    def _has_bullet(paragraph) -> bool:
        try:
            p_pr = paragraph._p.pPr
            if p_pr is None:
                return False
            tags = (
                "{http://schemas.openxmlformats.org/drawingml/2006/main}buChar",
                "{http://schemas.openxmlformats.org/drawingml/2006/main}buAutoNum",
            )
            return any(p_pr.find(tag) is not None for tag in tags)
        except Exception:  # noqa: BLE001
            return False

    def _paragraph_size(self, paragraph) -> float:
        size = None
        try:
            size = paragraph.font.size
        except Exception:  # noqa: BLE001
            size = None
        if size is None:
            for run in paragraph.runs:
                try:
                    if run.font.size is not None:
                        size = run.font.size
                        break
                except Exception:  # noqa: BLE001
                    continue
        return self._pt(size) if size is not None else 18.0

    @staticmethod
    def _paragraph_bold(paragraph) -> bool:
        try:
            if paragraph.font.bold is not None:
                return bool(paragraph.font.bold)
        except Exception:  # noqa: BLE001
            pass
        for run in paragraph.runs:
            try:
                if run.font.bold:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _paragraph_color(self, paragraph) -> Tuple[float, float, float]:
        colors = []
        for source in (paragraph.font, *[run.font for run in paragraph.runs]):
            try:
                color = source.color
                rgb = color.rgb
                if rgb is not None:
                    colors.append(_rgb(rgb))
                    continue
                # Цвет темы (SCHEME) и явно не заданный цвет требуют fallback.
                if getattr(color, "type", None) is None or getattr(color, "theme_color", None) is not None:
                    continue
            except Exception:  # noqa: BLE001 - цвет темы/не задан
                continue
        color = colors[0] if colors else (0.07, 0.07, 0.07)
        # Белый или близкий к фону текст на светлом фоне невидим.
        if _color_distance(color, self._background_color) < 0.22:
            return (0.07, 0.07, 0.07)
        return color

    @staticmethod
    def _paragraph_align(paragraph) -> int:
        value = getattr(getattr(paragraph, "alignment", None), "value", None)
        return {2: 1, 3: 2, 4: 3}.get(value, 0)  # left / center / right

    def _font(self, size: float, bold: bool):
        if bold and self._font_bold:
            return "Arial-Bold", self._font_bold
        if self._font_regular:
            return "Arial", self._font_regular
        return ("hebo" if bold else "helv"), None

    # ------------------------------------------------------------------
    # Таблицы
    # ------------------------------------------------------------------
    def _draw_table(self, page, shape, rect) -> None:
        try:
            table = shape.table
        except Exception:  # noqa: BLE001
            return

        column_widths = [self._pt(column.width) for column in table.columns]
        row_heights = [self._pt(row.height) for row in table.rows]
        if not column_widths or not row_heights:
            return

        scale = rect.width / sum(column_widths) if sum(column_widths) else 1.0
        y = rect.y0
        for row_index, row_height in enumerate(row_heights):
            if y > rect.y1:
                break
            x = rect.x0
            for column_index, column_width in enumerate(column_widths):
                width = column_width * scale
                cell_rect = self._fitz.Rect(x, y, x + width, y + row_height)
                page.draw_rect(
                    cell_rect,
                    color=(0.65, 0.68, 0.72),
                    width=0.6,
                    fill=(1.0, 1.0, 1.0),
                )
                try:
                    text = table.cell(row_index, column_index).text.strip()
                except Exception:  # noqa: BLE001
                    text = ""
                if text:
                    fontname, fontfile = self._font(14.0, False)
                    size = min(14.0, max(7.0, row_height * 0.34))
                    line_count = max(1, text.count("\n") + 1)
                    block = size * 1.25 * line_count
                    top = cell_rect.y0 + max(2.0, (cell_rect.height - block) / 2)
                    text_rect = self._fitz.Rect(
                        cell_rect.x0 + 4.0,
                        top,
                        cell_rect.x1 - 4.0,
                        cell_rect.y1 - 2.0,
                    )
                    try:
                        page.insert_textbox(
                            text_rect,
                            text,
                            fontsize=size,
                            fontname=fontname,
                            fontfile=fontfile,
                            color=(0.08, 0.09, 0.11),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                x += width
            y += row_height


def _color_distance(first: Tuple[float, float, float], second: Tuple[float, float, float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second)) ** 0.5


def _rgb(color) -> Tuple[float, float, float]:
    """RGBColor (tuple-подобный) -> кортеж 0..1 для PyMuPDF."""
    try:
        red, green, blue = tuple(color)[:3]
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 0.0)
    return (red / 255.0, green / 255.0, blue / 255.0)


def _find_fonts() -> Tuple[Optional[str], Optional[str]]:
    """Ищет TTF с кириллицей: Arial на Windows, DejaVu на Unix."""
    candidates = (
        (
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf",
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ),
    )
    for regular, bold in candidates:
        if regular.exists():
            return str(regular), str(bold) if bold.exists() else None
    return None, None
