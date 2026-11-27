"""Композитор кадров: Preview/Program, переходы Cut/Fade, Freeze и Black (FTB).

OutputManager живёт в GUI-потоке и по умолчанию целится в 60 FPS, забирая
последние кадры у источников. Сами источники декодируют данные в своих
потоках, поэтому UI не блокируется.

Приоритет обработки финального кадра: BLACK > FREEZE > FADE/Program.
"""

from __future__ import annotations

import gc
from typing import Optional

from PySide6.QtCore import QElapsedTimer, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter

from sources.base import BaseSource

DEFAULT_FRAME_SIZE = QSize(1920, 1080)


def _black_frame(size: Optional[QSize] = None) -> QImage:
    frame = QImage(size or DEFAULT_FRAME_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    frame.fill(Qt.GlobalColor.black)
    return frame


class OutputManager(QObject):
    """Собирает Preview/Program/Output кадры и реализует спасательные функции."""

    preview_frame = Signal(QImage)
    program_frame = Signal(QImage)
    output_frame = Signal(QImage)
    state_changed = Signal()
    transition_finished = Signal(object, object)
    fps_updated = Signal(float, float)
    black_alpha_changed = Signal(float)

    def __init__(
        self,
        parent: Optional[QObject] = None,
        fps: int = 60,
        transition_ms: int = 800,
    ) -> None:
        super().__init__(parent)
        self._fps = max(1, int(fps))
        self._idle_fps = 60
        self._default_transition_ms = max(40, int(transition_ms))

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(max(1, int(1000 / self._fps)))
        self._timer.timeout.connect(self._tick)
        self._render_backpressure = False
        self._output_frame_pending = False

        self._fps_elapsed = QElapsedTimer()
        self._fps_elapsed.start()
        self._fps_frames = 0
        self._fps_window = []
        self.current_fps = 0.0
        self.average_fps = 0.0

        self._preview: Optional[BaseSource] = None
        self._program: Optional[BaseSource] = None
        self.video_output_mode = "qvideowidget"

        # Freeze
        self._freeze = False
        self._freeze_frame: Optional[QImage] = None

        # Black / FTB
        self._black = False
        self._black_alpha = 0.0
        self._black_from = 0.0
        self._black_to = 0.0
        self._black_duration_ms = 800
        self._black_anim_active = False
        self._black_timer = QElapsedTimer()
        self._black_snapshot: Optional[QImage] = None

        # Fade
        self._fade_active = False
        self._fade_from: Optional[QImage] = None
        self._fade_to: Optional[QImage] = None
        self._fade_outgoing: Optional[BaseSource] = None
        self._fade_duration_ms = self._default_transition_ms
        self._fade_timer = QElapsedTimer()

        # Кэш последних кадров
        self._last_program: Optional[QImage] = None
        self._last_content: Optional[QImage] = None
        self._last_output: Optional[QImage] = None
        self._had_output = False
        self._had_program = False
        self._black_cache: Optional[QImage] = None

    # ------------------------------------------------------------------
    # Свойства
    # ------------------------------------------------------------------
    @property
    def preview_source(self) -> Optional[BaseSource]:
        return self._preview

    @property
    def program_source(self) -> Optional[BaseSource]:
        return self._program

    @property
    def freeze_active(self) -> bool:
        return self._freeze

    @property
    def black_active(self) -> bool:
        return self._black

    @property
    def fade_active(self) -> bool:
        return self._fade_active

    def set_idle_fps(self, fps: int) -> None:
        self._idle_fps = max(1, min(60, int(fps)))

    def set_low_cpu_idle(self, enabled: bool) -> None:
        target_ms = (
            max(1, int(1000 / self._idle_fps))
            if enabled
            else max(1, int(1000 / self._fps))
        )
        if self._timer.interval() != target_ms:
            self._timer.setInterval(target_ms)

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def enable_render_backpressure(self, enabled: bool = True) -> None:
        self._render_backpressure = bool(enabled)
        self._output_frame_pending = False

    def notify_frame_rendered(self) -> None:
        self._output_frame_pending = False

    # ------------------------------------------------------------------
    # Управление источниками и переходами
    # ------------------------------------------------------------------
    def set_preview(self, source: Optional[BaseSource]) -> None:
        if self._preview is source:
            return
        self._preview = source
        self.state_changed.emit()

    def set_video_output_mode(self, mode: str) -> None:
        self.video_output_mode = mode if mode in ("qvideowidget", "external") else "qvideowidget"
        self.state_changed.emit()

    def native_video_active(self) -> bool:
        return bool(
            self.video_output_mode == "qvideowidget"
            and getattr(self._program, "kind", "") == "video"
            and not self._freeze
            and not self._black
            and not self._black_anim_active
        )

    def set_program(self, source: Optional[BaseSource]) -> None:
        if self._program is source:
            return
        self._program = source
        self._last_emitted_output = None
        self._last_content = None
        self._last_program = None
        self._had_program = False
        self._fade_active = False
        self._fade_from = None
        self._fade_to = None
        self._fade_outgoing = None
        self.state_changed.emit()

    def cut(self) -> None:
        """Мгновенно отправляет источник из Preview в Program."""
        if self._preview is None:
            return
        outgoing = self._fade_outgoing if self._fade_active else self._program
        self._last_emitted_output = None
        self._program = self._preview
        self._fade_active = False
        self._fade_from = None
        self._fade_to = None
        self._fade_outgoing = None
        self.state_changed.emit()
        self.transition_finished.emit(outgoing, self._program)

    def fade(self, duration_ms: Optional[int] = None) -> None:
        """Плавный переход Program <- Preview с кроссфейдом."""
        self.set_low_cpu_idle(False)
        if self._preview is None or self._preview is self._program:
            return

        duration = self._default_transition_ms if duration_ms is None else int(duration_ms)
        outgoing = self._last_content if self._last_content is not None else self._last_output
        self._last_emitted_output = None
        self._fade_from = outgoing.copy() if outgoing is not None else None
        self._fade_outgoing = self._program
        self._fade_to = None

        self._program = self._preview
        self._fade_duration_ms = max(40, duration)
        self._fade_timer.restart()
        self._fade_active = True
        self.state_changed.emit()

    # ------------------------------------------------------------------
    # Freeze / Black
    # ------------------------------------------------------------------
    def set_freeze(self, enabled: bool, snapshot: Optional[QImage] = None) -> None:
        self.set_low_cpu_idle(False)
        enabled = bool(enabled)
        if enabled == self._freeze:
            return
        self._last_emitted_output = None
        self._freeze = enabled
        if enabled:
            # Фиксируем последний контентный кадр (без Black-оверлея).
            # Если кадра еще нет, _tick захватит первый пришедший кадр.
            frame = snapshot if snapshot is not None else (
                self._last_content if self._last_content is not None else self._last_output
            )
            self._freeze_frame = frame.copy() if frame is not None else None
        else:
            self._freeze_frame = None
            frame = self._get_source_frame(self._program)
            if frame is None:
                frame = self._last_content if self._last_content is not None else self._last_program
            if frame is not None:
                self._last_content = frame
                if self._black_alpha > 0.0:
                    frame = self._apply_black(frame, self._black_alpha)
            self._emit_output(frame)
        self.state_changed.emit()

    def toggle_freeze(self) -> None:
        self.set_freeze(not self._freeze)

    def set_black_duration(self, duration_ms: int) -> None:
        self._black_duration_ms = max(100, min(5000, int(duration_ms)))

    def set_black(
        self, enabled: bool, smooth: bool = False, duration_ms: Optional[int] = None
    ) -> None:
        self.set_low_cpu_idle(False)
        enabled = bool(enabled)
        self._last_emitted_output = None
        self._black = enabled
        target = 1.0 if enabled else 0.0

        if smooth:
            grab_full_frame = getattr(self._program, "grab_full_frame", None)
            snap = grab_full_frame() if callable(grab_full_frame) else self._last_content
            self._black_snapshot = (
                snap if snap is not None and not snap.isNull() else None
            )
            self._black_from = self._black_alpha
            self._black_to = target
            if duration_ms is not None:
                self._black_duration_ms = max(100, min(5000, int(duration_ms)))
            self._black_timer.restart()
            self._black_anim_active = True
        else:
            self._black_snapshot = None
            self._black_alpha = target
            self._black_anim_active = False
            self.black_alpha_changed.emit(self._black_alpha)
            if not enabled:
                frame = self._get_source_frame(self._program)
                if frame is None:
                    frame = self._last_content or self._last_program
                self._emit_output(frame)

        self.state_changed.emit()

    def toggle_black(self, smooth: bool = False) -> None:
        self.set_black(not self._black, smooth=smooth)

    # ------------------------------------------------------------------
    # Тик композитора
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        is_idle = (
            self.native_video_active()
            and not self._fade_active
            and not self._freeze
            and not self._black
            and not self._black_anim_active
        )
        self.set_low_cpu_idle(is_idle)
        self._advance_fps()
        self._advance_black()

        if self._program is None:
            preview_img = self._get_source_frame(self._preview)
            self._last_program = None
            self._last_content = None
            self._emit_preview(preview_img)
            self._emit_program(None)
            self._emit_output(None)
            return

        if self._preview is not None and self._preview is self._program:
            program_img = self._get_source_frame(self._preview)
            get_next_preview = getattr(self._preview, "get_next_preview", None)
            next_img = get_next_preview() if callable(get_next_preview) else None
            preview_img = next_img if next_img is not None else program_img
        else:
            preview_img = self._get_source_frame(self._preview)
            program_img = self._get_source_frame(self._program)
        if program_img is None and (self._black or self._black_anim_active):
            grab_full_frame = getattr(self._program, "grab_full_frame", None)
            if callable(grab_full_frame):
                program_img = grab_full_frame()
            if program_img is None:
                program_img = self._last_content

        if program_img is not None:
            self._last_program = program_img
            self._last_content = program_img

        if self._fade_active:
            program_img = self._apply_fade(program_img)
            if program_img is not None:
                self._last_content = program_img

        content = program_img
        if self._freeze:
            if self._freeze_frame is None and content is not None:
                self._freeze_frame = content.copy()
            output_img = self._freeze_frame
        else:
            output_img = content

        if self._black_anim_active and self._black_snapshot is not None:
            output_img = self._black_snapshot
        if output_img is not None and self._black_alpha > 0.0:
            output_img = self._apply_black(output_img, self._black_alpha)

        self._emit_preview(preview_img)
        self._emit_program(program_img)
        self._emit_output(output_img)

    @staticmethod
    def _source_is_playing(source: Optional[BaseSource]) -> bool:
        return bool(source is not None and getattr(source, "is_playing", False))

    @staticmethod
    def _get_source_frame(source: Optional[BaseSource]) -> Optional[QImage]:
        if source is None:
            return None
        return source.get_latest()


    def _advance_fps(self) -> None:
        self._fps_frames += 1
        elapsed = self._fps_elapsed.elapsed()
        if elapsed < 500:
            return
        rate = self._fps_frames * 1000.0 / max(1, elapsed)
        self.current_fps = rate
        self._fps_window.append((elapsed, self._fps_frames))
        while len(self._fps_window) > 1 and sum(item[0] for item in self._fps_window) > 5000:
            self._fps_window.pop(0)
        total_ms = sum(item[0] for item in self._fps_window)
        total_frames = sum(item[1] for item in self._fps_window)
        self.average_fps = total_frames * 1000.0 / max(1, total_ms)
        self.fps_updated.emit(self.current_fps, self.average_fps)
        self._fps_frames = 0
        self._fps_elapsed.restart()

    def _advance_black(self) -> None:
        if not self._black_anim_active:
            return
        progress = min(1.0, self._black_timer.elapsed() / max(1, self._black_duration_ms))
        self._black_alpha = self._black_from + (self._black_to - self._black_from) * progress
        self.black_alpha_changed.emit(self._black_alpha)
        if progress >= 1.0:
            self._black_alpha = self._black_to
            self._black_anim_active = False
            self._black_snapshot = None
            self.state_changed.emit()

    def _apply_fade(self, program_img: Optional[QImage]) -> Optional[QImage]:
        progress = min(1.0, self._fade_timer.elapsed() / max(1, self._fade_duration_ms))
        eased = progress * progress * (3.0 - 2.0 * progress)

        target = program_img if program_img is not None else self._fade_from
        base = self._fade_from
        if base is None and target is not None:
            base = self._black_frame_for(target.size())

        if base is not None and target is not None:
            if progress >= 1.0:
                result = target
            else:
                result = self._blend(base, target, eased)
        else:
            result = target

        if progress >= 1.0:
            outgoing = self._fade_outgoing
            incoming = self._program
            self._fade_active = False
            self._fade_from = None
            self._fade_to = None
            self._fade_outgoing = None
            self.state_changed.emit()
            self.transition_finished.emit(outgoing, incoming)
        return result

    def _blend(self, base: QImage, target: QImage, opacity: float) -> QImage:
        canvas = QImage(
            base.size(), QImage.Format.Format_ARGB32_Premultiplied
        )
        painter = QPainter(canvas)
        painter.drawImage(canvas.rect(), base)
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.drawImage(canvas.rect(), target)
        painter.end()
        return canvas

    def _apply_black(self, image: QImage, alpha: float) -> QImage:
        if alpha >= 0.999:
            return self._black_frame_for(image.size())
        canvas = QImage(
            image.size(), QImage.Format.Format_ARGB32_Premultiplied
        )
        painter = QPainter(canvas)
        painter.drawImage(canvas.rect(), image)
        painter.setOpacity(max(0.0, min(1.0, alpha)))
        painter.fillRect(canvas.rect(), Qt.GlobalColor.black)
        painter.end()
        return canvas

    # ------------------------------------------------------------------
    # Эмиссия кадров
    # ------------------------------------------------------------------
    def _emit_preview(self, image: Optional[QImage]) -> None:
        if image is not None:
            self.preview_frame.emit(image)

    def _emit_program(self, image: Optional[QImage]) -> None:
        if image is not None:
            self._had_program = True
            self.program_frame.emit(image)
        elif self._had_program:
            self._had_program = False
            self.program_frame.emit(QImage())

    def _emit_output(self, image: Optional[QImage]) -> None:
        if (
            image is not None
            and image is getattr(self, "_last_emitted_output", None)
            and not self._fade_active
            and not self._black_anim_active
        ):
            return
        self._last_emitted_output = image
        if image is not None:
            self._had_output = True
            self._last_output = image
            if self._render_backpressure:
                self._output_frame_pending = True
            self.output_frame.emit(image)
        else:
            self._output_frame_pending = False
            if self._had_output:
                self._had_output = False
                self._last_output = None
                if self._render_backpressure:
                    self._output_frame_pending = True
                self.output_frame.emit(QImage())

    def release_cached_frames(self) -> None:
        self._last_program = None
        self._last_content = None
        self._last_output = None
        self._last_emitted_output = None
        self._freeze_frame = None
        self._black_snapshot = None
        self._black_cache = None
        gc.collect()

    # ------------------------------------------------------------------
    # Вспомогательное
    # ------------------------------------------------------------------
    def _frame_size(self) -> QSize:
        for image in (self._last_content, self._last_program, self._last_output):
            if image is not None and not image.isNull():
                return image.size()
        return DEFAULT_FRAME_SIZE

    def _black_frame_for(self, size: QSize) -> QImage:
        if self._black_cache is None or self._black_cache.size() != size:
            self._black_cache = _black_frame(size)
        return self._black_cache
