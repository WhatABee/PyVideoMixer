"""Окна вывода Program на втором мониторе/проекторе."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QScreen
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget


class BlackOverlayWindow(QWidget):
    """Transparent black layer for native video FTB transitions."""

    def __init__(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet("background-color: #000000;")
        self.hide()

    def set_alpha(self, alpha: float, screen: Optional[QScreen] = None) -> None:
        alpha = max(0.0, min(1.0, float(alpha)))
        if alpha <= 0.001:
            alpha = 0.004
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.setWindowOpacity(alpha)
        if not self.isVisible():
            self.show()
        self.raise_()
        if alpha > 0.001:
            try:
                import win32con
                import win32gui
                win32gui.SetWindowPos(
                    int(self.winId()),
                    win32con.HWND_TOPMOST,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
                )
            except Exception:
                pass


class OutputWindow(QOpenGLWidget):
    """Простое нативное окно для слайдов, фото, FREEZE и BLACK."""

    hidden_by_user = Signal()
    frame_rendered = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("Program Output")
        self.setStyleSheet("background-color: #000000;")
        self._image: Optional[QImage] = None
        self._mode = "fit"

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        image = self._image
        if image is not None and not image.isNull():
            aspect = (
                Qt.AspectRatioMode.KeepAspectRatioByExpanding
                if self._mode == "fill"
                else Qt.AspectRatioMode.KeepAspectRatio
            )
            size = image.size().scaled(self.size(), aspect)
            target = QRect(
                (self.width() - size.width()) // 2,
                (self.height() - size.height()) // 2,
                size.width(),
                size.height(),
            )
            painter.drawImage(target, image)
        painter.end()
        self.frame_rendered.emit()

    def set_image(self, image: Optional[QImage]) -> None:
        if image is not None and image.isNull():
            image = None
        self._image = image
        if not self.isVisible():
            if image is None:
                self.frame_rendered.emit()
            return
        self.update()

    def set_fill_mode(self, mode: str) -> None:
        self._mode = "fill" if mode == "fill" else "fit"
        if self.isVisible():
            self.update()

    def update_overlay(self, alpha: float) -> None:
        """Оставлено для совместимости: BLACK приходит как кадр OutputManager."""
        del alpha

    def show_on_screen(self, screen: QScreen) -> None:
        self.setGeometry(screen.geometry())
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        self.show()
        self.raise_()
        self.update()

    def show_current_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.show_on_screen(screen)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.hidden_by_user.emit()
            return
        super().keyPressEvent(event)


class VideoOutputWindow(QVideoWidget):
    """Отдельное нативное окно для аппаратного видеовывода."""

    hidden_by_user = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("Program Output (Video)")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self._last_image: Optional[QImage] = None
        self._black_frame: Optional[QImage] = None
        self._mode = "fit"

    def set_fill_mode(self, mode: str) -> None:
        self._mode = "fill" if mode == "fill" else "fit"
        self.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatioByExpanding
            if self._mode == "fill"
            else Qt.AspectRatioMode.KeepAspectRatio
        )

    def set_image(self, image: Optional[QImage]) -> None:
        sink = self.videoSink()
        if sink is None:
            return
        if image is None or image.isNull():
            if self._black_frame is None:
                self._black_frame = QImage(
                    16, 16, QImage.Format.Format_ARGB32_Premultiplied
                )
                self._black_frame.fill(Qt.GlobalColor.black)
            self._last_image = None
            sink.setVideoFrame(QVideoFrame(self._black_frame))
            return
        if image is self._last_image:
            return
        self._last_image = image
        fmt_img = (
            image
            if image.format() == QImage.Format.Format_ARGB32_Premultiplied
            else image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        )
        sink.setVideoFrame(QVideoFrame(fmt_img))

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.hidden_by_user.emit()
            return
        super().keyPressEvent(event)

    def show_on_screen(self, screen: QScreen, raise_window: bool = True) -> None:
        self.setGeometry(screen.geometry())
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        self.show()
        if raise_window:
            self.raise_()
