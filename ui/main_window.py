"""Главное окно — пульт оператора PyVideoMixer."""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QImage, QKeySequence, QScreen, QShortcut
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.output_manager import OutputManager
from sources.base import BaseSource
from sources.image_source import ImageSource
from sources.pdf_source import PdfSource
from sources.folder_source import FolderSource
from sources.pptx_source import PptxSource
from sources.screen_source import ScreenSource
from sources.video_source import VideoSource
from ui.output_window import BlackOverlayWindow, OutputWindow, VideoOutputWindow
from ui.settings_dialog import SettingsDialog
from core.external_player import ExternalPlayer
from ui.preview_widget import ScreenWidget
from ui.style import set_active_property

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
PRESENTATION_EXTENSIONS = {".pptx", ".ppt"}
FILE_FILTER = (
    "Медиа (*.png *.jpg *.jpeg *.webp *.bmp *.pdf *.pptx *.ppt "
    "*.mp4 *.mkv *.mov *.avi *.m4v *.webm);;"
    "Презентации (*.pdf *.pptx *.ppt);;"
    "Все файлы (*)"
)

SOURCE_PREFIX = {
    "image": "IMG",
    "pdf": "PDF",
    "folder": "ALBUM",
    "pptx": "PPTX",
    "video": "VIDEO",
    "screen": "CAP",
}


def _list_windows() -> List[Tuple[int, str]]:
    """Список видимых окон Windows для диалога захвата."""
    windows: List[Tuple[int, str]] = []
    try:
        import win32gui  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - pywin32 может отсутствовать
        return windows

    def callback(hwnd, _extra) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if title.strip():
            windows.append((int(hwnd), title))
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:  # noqa: BLE001
        pass
    windows.sort(key=lambda item: item[1].lower())
    return windows


class CaptureDialog(QDialog):
    """Выбор монитора или окна Windows и частоты захвата."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Захват экрана / окна")
        self.setMinimumWidth(540)

        form = QFormLayout(self)

        self.monitor_combo = QComboBox()
        screens = QGuiApplication.screens()
        for index, screen in enumerate(screens):
            geo = screen.geometry()
            self.monitor_combo.addItem(
                f"{index + 1}: {screen.name()} · {geo.width()}×{geo.height()}",
                index,
            )
        if not screens:
            self.monitor_combo.addItem("Монитор не найден", 0)

        self.window_combo = QComboBox()
        self.refresh_windows_btn = QPushButton("Обновить список окон")
        self.refresh_windows_btn.clicked.connect(self._reload_windows)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 60)
        self.fps_spin.setValue(30)
        self.fps_spin.setSuffix(" FPS")

        form.addRow("Монитор:", self.monitor_combo)
        form.addRow("Окно Windows:", self.window_combo)
        form.addRow("", self.refresh_windows_btn)
        form.addRow("Частота захвата:", self.fps_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._reload_windows()

    def _reload_windows(self) -> None:
        self.window_combo.clear()
        self.window_combo.addItem("— весь выбранный монитор —", None)
        for hwnd, title in _list_windows():
            self.window_combo.addItem(title[:100], hwnd)

    def settings(self) -> Tuple[int, Optional[int], int, str]:
        monitor = self.monitor_combo.currentData()
        monitor = int(monitor) if monitor is not None else 0
        hwnd = self.window_combo.currentData()
        title = self.window_combo.currentText() if hwnd is not None else ""
        return monitor, hwnd, self.fps_spin.value(), title


class MainWindow(QMainWindow):
    """Операторский пульт: медиапул, Preview/Program и Master Controls."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyVideoMixer — пульт оператора")
        self.resize(1400, 800)
        self.setMinimumSize(1120, 660)

        self.output = OutputManager(self, fps=60, transition_ms=800)
        self.output_window = OutputWindow()
        self.video_output_window = VideoOutputWindow()
        self.black_overlay = BlackOverlayWindow()
        self._output_active = False
        self._fade_pause_outgoing = False
        self.settings = SettingsDialog.load_settings()
        self.output.set_video_output_mode(self.settings.get("video_output_mode", "qvideowidget"))
        self.external_player = ExternalPlayer(self.settings.get("external_player", ""))

        self.sources: List[BaseSource] = []
        self.preview_source: Optional[BaseSource] = None
        self.program_source: Optional[BaseSource] = None
        self.home_source: Optional[BaseSource] = None
        self._pdf_controls_source: Optional[PdfSource] = None
        self._pdf_page_label: Optional[QLabel] = None
        self._video_controls_source: Optional[VideoSource] = None
        self._video_play_btn: Optional[QPushButton] = None
        self._video_position_slider: Optional[QSlider] = None
        self._video_time_label: Optional[QLabel] = None
        self._video_mute_btn: Optional[QPushButton] = None
        self._video_volume_slider: Optional[QSlider] = None
        self._video_volume_label: Optional[QLabel] = None
        self._preview_play_btn: Optional[QPushButton] = None
        self._program_play_btn: Optional[QPushButton] = None
        self._preview_rendering = True
        self._program_rendering = True
        self._screens_initialized = False

        self._slide_source: Optional[PdfSource] = None
        self._slide_role = ""
        self._slide_timer = QTimer(self)
        self._slide_timer.timeout.connect(self._on_slide_timer)

        self._build_ui()
        self._connect_signals()
        self._install_shortcuts()
        self._keep_windows_awake()
        self._refresh_screens()
        self.output.start()
        self._sync_master_state()
        self._sync_live_controls_enabled()
        self._refresh_captions()
        self._apply_slide_interval()
        self._sync_slide_panel()

    # ==================================================================
    # Построение интерфейса
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        header_row = QHBoxLayout()
        header = QLabel("PyVideoMixer · пульт оператора")
        header.setObjectName("headerLabel")
        header_row.addWidget(header)
        header_row.addStretch(1)
        self.settings_btn = QPushButton("⚙ Настройки")
        self.settings_btn.setToolTip("Настройки вывода видео и предпросмотра")
        header_row.addWidget(self.settings_btn)
        root.addLayout(header_row)

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self._build_left_panel(), 0)
        body.addWidget(self._build_center_panel(), 1)
        body.addWidget(self._build_right_panel(), 0)
        root.addLayout(body, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage(
            "Готово · Space — Cut · Enter — Fade · F — Freeze · B — Black · "
            "←/→ — слайды активной презентации"
        )
        self.fps_label = QLabel("FPS: 0.0 | Avg: 0.0")
        self.statusBar().addPermanentWidget(self.fps_label)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.media_box = QGroupBox("МЕДИАПУЛ")
        media_box = self.media_box
        media_layout = QVBoxLayout(media_box)
        self.media_list = QListWidget()
        self.media_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.media_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.media_list.setWordWrap(True)
        media_layout.addWidget(self.media_list, 1)

        add_grid = QGridLayout()
        add_grid.setHorizontalSpacing(4)
        add_grid.setVerticalSpacing(4)
        add_grid.setColumnStretch(0, 1)
        add_grid.setColumnStretch(1, 1)
        self.add_files_btn = QPushButton("Добавить файлы…")
        self.add_folder_btn = QPushButton("Папка фото…")
        self.add_capture_btn = QPushButton("Захват экрана…")
        self.remove_btn = QPushButton("Удалить выбранный")
        for button in (
            self.add_files_btn,
            self.add_folder_btn,
            self.add_capture_btn,
            self.remove_btn,
        ):
            button.setObjectName("mediaPoolButton")
            button.setFixedHeight(30)
        add_grid.addWidget(self.add_files_btn, 0, 0)
        add_grid.addWidget(self.add_folder_btn, 0, 1)
        add_grid.addWidget(self.add_capture_btn, 1, 0)
        add_grid.addWidget(self.remove_btn, 1, 1)
        media_layout.addLayout(add_grid)

        self.source_box = QGroupBox("ВЫБРАННЫЙ ИСТОЧНИК")
        self.source_layout = QVBoxLayout(self.source_box)
        self.source_layout.addWidget(QLabel("Источник не выбран"))

        layout.addWidget(media_box, 3)
        layout.addWidget(self.source_box, 0)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        screens_row = QHBoxLayout()
        screens_row.setSpacing(8)

        preview_box = QGroupBox()
        preview_layout = QVBoxLayout(preview_box)
        preview_header = QHBoxLayout()
        preview_title = QLabel("PREVIEW")
        preview_title.setStyleSheet("font-weight: 700;")
        self._preview_play_btn = QPushButton("⏸ Пауза")
        self._preview_play_btn.setFont(QFont("Segoe UI Symbol"))
        self._preview_play_btn.setFixedHeight(23)
        self._preview_play_btn.setStyleSheet("padding: 0 6px;")
        self._preview_play_btn.setVisible(False)
        preview_header.addWidget(preview_title)
        preview_header.addStretch(1)
        preview_header.addWidget(self._preview_play_btn)
        preview_layout.addLayout(preview_header)
        self.preview_widget = ScreenWidget()
        self.preview_caption = QLabel("PREVIEW · —")
        self.preview_caption.setStyleSheet("color:#9aa4b2;")
        preview_layout.addWidget(self.preview_widget, 1)
        preview_layout.addWidget(self.preview_caption)

        program_box = QGroupBox()
        program_layout = QVBoxLayout(program_box)
        program_header = QHBoxLayout()
        program_title = QLabel("PROGRAM")
        program_title.setStyleSheet("font-weight: 700;")
        self._program_play_btn = QPushButton("⏸ Пауза")
        self._program_play_btn.setFont(QFont("Segoe UI Symbol"))
        self._program_play_btn.setFixedHeight(23)
        self._program_play_btn.setStyleSheet("padding: 0 6px;")
        self._program_play_btn.setVisible(False)
        program_header.addWidget(program_title)
        program_header.addStretch(1)
        program_header.addWidget(self._program_play_btn)
        program_layout.addLayout(program_header)
        self.program_widget = ScreenWidget()
        self.program_caption = QLabel("PROGRAM · —")
        self.program_caption.setStyleSheet("color:#9aa4b2;")
        program_layout.addWidget(self.program_widget, 1)
        program_layout.addWidget(self.program_caption)

        screens_row.addWidget(preview_box, 1)
        screens_row.addWidget(program_box, 1)
        layout.addLayout(screens_row, 3)
        self.media_box.installEventFilter(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Fade:"))
        self.fade_duration = QDoubleSpinBox()
        self.fade_duration.setRange(0.1, 5.0)
        self.fade_duration.setSingleStep(0.1)
        self.fade_duration.setDecimals(1)
        self.fade_duration.setValue(0.8)
        self.fade_duration.setSuffix(" с")
        controls.addWidget(self.fade_duration)
        controls.addSpacing(8)
        controls.addStretch(1)

        self.cut_btn = QPushButton("CUT")
        self.cut_btn.setObjectName("cutButton")
        self.cut_btn.setMinimumSize(160, 56)
        self.cut_btn.setToolTip("Мгновенно отправить Preview в эфир (Space)")

        self.fade_btn = QPushButton("FADE\n(без паузы)")
        self.fade_btn.setObjectName("fadeButton")
        self.fade_btn.setMinimumSize(160, 56)
        self.fade_btn.setToolTip("Плавный переход без остановки уходящего видео")

        self.fade_pause_btn = QPushButton("FADE\n(пауза)")
        self.fade_pause_btn.setObjectName("fadePauseButton")
        self.fade_pause_btn.setMinimumSize(160, 56)
        self.fade_pause_btn.setToolTip("Плавный переход с паузой уходящего видео")

        controls.addWidget(self.cut_btn)
        controls.addWidget(self.fade_btn)
        controls.addWidget(self.fade_pause_btn)
        layout.addLayout(controls)
        layout.addWidget(self._build_slide_panel())
        return panel

    def _build_slide_panel(self) -> QGroupBox:
        box = QGroupBox("СЛАЙД-ШОУ")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        self.slide_target_label = QLabel(
            "Презентация или альбом не выбраны · добавьте PDF, PPTX или папку фото"
        )
        self.slide_target_label.setStyleSheet("color:#9aa4b2;")
        self.slide_target_label.setWordWrap(True)
        layout.addWidget(self.slide_target_label)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(6)

        self.slide_prev_btn = QPushButton("◀")
        self.slide_prev_btn.setToolTip("Предыдущий слайд (←)")
        self.slide_prev_btn.setFixedSize(46, 34)

        self.slide_next_btn = QPushButton("▶")
        self.slide_next_btn.setToolTip("Следующий слайд (→)")
        self.slide_next_btn.setFixedSize(46, 34)

        self.slide_play_btn = QPushButton("⏯ Старт")
        self.slide_play_btn.setObjectName("slidePlayButton")
        self.slide_play_btn.setMinimumSize(118, 34)
        self.slide_play_btn.setToolTip(
            "Запустить / приостановить автоматическую смену слайдов"
        )

        self.slide_stop_btn = QPushButton("⏹ В начало")
        self.slide_stop_btn.setObjectName("slideStopButton")
        self.slide_stop_btn.setMinimumSize(112, 34)
        self.slide_stop_btn.setToolTip(
            "Остановить автопрокрутку и вернуться на первый слайд"
        )

        buttons_row.addWidget(self.slide_prev_btn)
        buttons_row.addWidget(self.slide_next_btn)
        buttons_row.addWidget(self.slide_play_btn)
        buttons_row.addWidget(self.slide_stop_btn)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        counter_row = QHBoxLayout()
        counter_row.setSpacing(6)
        counter_row.addWidget(QLabel("Интервал:"))
        self.slide_interval = QDoubleSpinBox()
        self.slide_interval.setRange(0.5, 600.0)
        self.slide_interval.setSingleStep(0.5)
        self.slide_interval.setDecimals(1)
        self.slide_interval.setValue(5.0)
        self.slide_interval.setSuffix(" с")
        self.slide_interval.setFixedWidth(90)
        self.slide_interval.setToolTip("Интервал автоматической смены слайдов")
        counter_row.addWidget(self.slide_interval)
        counter_row.addSpacing(12)

        counter_row.addWidget(QLabel("Слайд"))
        self.slide_number = QSpinBox()
        self.slide_number.setRange(1, 1)
        self.slide_number.setFixedWidth(72)
        self.slide_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slide_number.setKeyboardTracking(False)
        self.slide_number.setToolTip("Введите номер слайда и нажмите Enter")
        counter_row.addWidget(self.slide_number)
        self.slide_total_label = QLabel("/ —")
        self.slide_total_label.setStyleSheet("color:#9aa4b2;")
        counter_row.addWidget(self.slide_total_label)
        counter_row.addStretch(1)
        layout.addLayout(counter_row)
        return box

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        master_box = QGroupBox("MASTER CONTROLS")
        master_layout = QVBoxLayout(master_box)

        self.state_label = QLabel("ЭФИР")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        master_layout.addWidget(self.state_label)

        self.freeze_btn = QPushButton("FREEZE")
        self.freeze_btn.setObjectName("freezeButton")
        self.freeze_btn.setCheckable(True)
        self.freeze_btn.setMinimumHeight(62)
        self.freeze_btn.setToolTip("Заморозить последний кадр в Program Output (F)")
        master_layout.addWidget(self.freeze_btn)

        self.black_btn = QPushButton("BLACK / FTB")
        self.black_btn.setObjectName("blackButton")
        self.black_btn.setCheckable(True)
        self.black_btn.setMinimumHeight(62)
        self.black_btn.setToolTip("Fade to Black — погасить вывод (B)")
        master_layout.addWidget(self.black_btn)

        self.home_btn = QPushButton("HOME (H)")
        self.home_btn.setMinimumHeight(62)
        self.home_btn.setToolTip("Плавно вывести домашний фон (H)")
        master_layout.addWidget(self.home_btn)

        self.smooth_ftb = QCheckBox("Плавный FTB")
        self.ftb_duration = QDoubleSpinBox()
        self.ftb_duration.setRange(0.1, 5.0)
        self.ftb_duration.setSingleStep(0.1)
        self.ftb_duration.setDecimals(1)
        self.ftb_duration.setValue(0.8)
        self.ftb_duration.setSuffix(" с")
        ftb_row = QHBoxLayout()
        ftb_row.addWidget(self.smooth_ftb)
        ftb_row.addWidget(self.ftb_duration)
        master_layout.addLayout(ftb_row)

        layout.addWidget(master_box)

        output_box = QGroupBox("ВЫВОД НА ЭКРАН")
        output_layout = QVBoxLayout(output_box)
        form = QFormLayout()
        self.screen_combo = QComboBox()
        # Длинное имя монитора не должно распирать панель и обрезать подпись.
        self.screen_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.screen_combo.setMinimumContentsLength(10)
        self.screen_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Fit · поля", "fit")
        self.mode_combo.addItem("Fill · обрезка", "fill")
        form.addRow("Монитор:", self.screen_combo)
        form.addRow("Кадр:", self.mode_combo)
        output_layout.addLayout(form)

        self.fs_btn = QPushButton("На весь экран")
        self.hide_btn = QPushButton("Скрыть вывод")
        output_layout.addWidget(self.fs_btn)
        output_layout.addWidget(self.hide_btn)

        self.output_state_label = QLabel("ВЫВОД: скрыт")
        self.output_state_label.setWordWrap(True)
        self.output_state_label.setStyleSheet("color:#8b95a3;")
        output_layout.addWidget(self.output_state_label)
        layout.addWidget(output_box)

        hint = QLabel(
            "Горячие клавиши:\n"
            "Space — Cut\n"
            "Enter — Fade\n"
            "F — Freeze\n"
            "B — Black\n"
            "← / → — слайды активной презентации"
        )
        hint.setStyleSheet("color:#8b95a3;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

        self.freeze_btn.setMinimumWidth(260)
        return panel

    # ==================================================================
    # Сигналы
    # ==================================================================
    def _connect_signals(self) -> None:
        self.output.preview_frame.connect(self.preview_widget.set_image)
        self.output.program_frame.connect(self.program_widget.set_image)
        self.output.output_frame.connect(self.output_window.set_image)
        self.output.state_changed.connect(self._on_output_state_changed)
        self.output.transition_finished.connect(self._on_transition_finished)
        self.output.fps_updated.connect(self._on_fps_updated)
        self.settings_btn.clicked.connect(self._open_settings)

        self.media_list.currentItemChanged.connect(self._on_media_selected)
        self.media_list.model().rowsMoved.connect(self._on_media_rows_moved)
        self.add_files_btn.clicked.connect(self._add_files)
        self.add_folder_btn.clicked.connect(self._add_folder)
        self.add_capture_btn.clicked.connect(self._add_capture)
        self.remove_btn.clicked.connect(self._remove_selected)
        self.home_btn.clicked.connect(self._go_home)

        self.cut_btn.clicked.connect(self._cut)
        self.fade_btn.clicked.connect(lambda: self._fade(pause_outgoing=False))
        self.fade_pause_btn.clicked.connect(lambda: self._fade(pause_outgoing=True))
        self._preview_play_btn.clicked.connect(
            lambda: self._toggle_slot_rendering("preview")
        )
        self._program_play_btn.clicked.connect(
            lambda: self._toggle_slot_rendering("program")
        )
        self.output.black_alpha_changed.connect(self._on_black_alpha_changed)

        self.freeze_btn.toggled.connect(self._on_freeze_toggled)
        self.black_btn.toggled.connect(self._on_black_toggled)
        self.ftb_duration.valueChanged.connect(
            lambda value: self.output.set_black_duration(round(value * 1000))
        )

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.screen_combo.currentIndexChanged.connect(self._sync_native_output)
        self.fs_btn.clicked.connect(self._go_fullscreen)
        self.hide_btn.clicked.connect(self._hide_output)
        self.output_window.hidden_by_user.connect(self._hide_output)
        self.output_window.set_fill_mode(self.mode_combo.currentData() or "fit")
        settings = SettingsDialog.load_settings()
        preview_fps = max(1, int(settings.get("preview_fps", 12)))
        self.preview_widget.set_fps_limit(preview_fps)
        self.program_widget.set_fps_limit(preview_fps)

        self.slide_prev_btn.clicked.connect(self._slide_prev)
        self.slide_next_btn.clicked.connect(self._slide_next)
        self.slide_play_btn.clicked.connect(self._toggle_slideshow)
        self.slide_stop_btn.clicked.connect(self._stop_slideshow)
        self.slide_interval.valueChanged.connect(self._on_slide_interval_changed)
        self.slide_number.valueChanged.connect(self._on_slide_number_changed)

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._refresh_screens)
            app.screenRemoved.connect(self._refresh_screens)

    def _install_shortcuts(self) -> None:
        bindings = (
            ("Space", self._cut),
            ("Return", self._fade),
            ("F", self._toggle_freeze),
            ("B", self._toggle_black),
            ("H", self._go_home),
            ("Left", self._slide_prev),
            ("Right", self._slide_next),
        )
        self._shortcuts = []
        for key, handler in bindings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

        for index in range(1, 10):
            shortcut = QShortcut(QKeySequence(str(index)), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(
                lambda index=index: self._select_source_by_shortcut(index)
            )
            self._shortcuts.append(shortcut)

    # ==================================================================
    # Медиапул
    # ==================================================================
    def _create_source(self, path: str) -> BaseSource:
        extension = Path(path).suffix.lower()
        name = Path(path).name
        if extension == ".pdf":
            return PdfSource(name, path)
        if extension in PRESENTATION_EXTENSIONS:
            return PptxSource(name, path)
        if extension in VIDEO_EXTENSIONS:
            return VideoSource(name, path, loop=True)
        if extension in IMAGE_EXTENSIONS:
            return ImageSource(name, path)
        raise ValueError(f"Неподдерживаемый формат: {extension or 'без расширения'}")

    def _add_files(self) -> None:
        files, _selected = QFileDialog.getOpenFileNames(
            self,
            "Добавить медиа",
            "",
            FILE_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        for path in files:
            try:
                source = self._create_source(path)
                source.start()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "Ошибка источника",
                    f"Не удалось добавить файл:\n{path}\n\n{exc}",
                )
                continue
            self._register_source(source)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Добавить папку с фото",
            "",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path:
            return
        try:
            source = FolderSource(Path(path).name, path)
            source.start()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Ошибка источника",
                f"Не удалось добавить папку:\n{path}\n\n{exc}",
            )
            return
        self._register_source(source)

    def _add_capture(self) -> None:
        dialog = CaptureDialog(self)
        if not dialog.exec():
            return
        monitor, hwnd, fps, title = dialog.settings()
        if hwnd is not None:
            name = f"Захват: {title[:48]}"
        else:
            name = f"Захват: экран {monitor + 1}"
        try:
            source = ScreenSource(name=name, monitor_index=monitor, hwnd=hwnd, fps=fps)
            source.start()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Ошибка захвата", str(exc))
            return
        self._register_source(source)

    def _register_source(self, source: BaseSource) -> None:
        source.error.connect(self._on_source_error)
        if isinstance(source, PdfSource):
            source.page_changed.connect(
                lambda index, count, src=source: self._on_pdf_page_changed(src, index, count)
            )
        if isinstance(source, PptxSource):
            source.converted.connect(
                lambda _path, src=source: self.statusBar().showMessage(
                    f"{src.name}: презентация сконвертирована в PDF", 6000
                )
            )
        source.changed.connect(lambda src=source: self._update_item_text(src))

        self.sources.append(source)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, source)
        self.media_list.addItem(item)
        self._update_item_text(source)
        self.media_list.setCurrentItem(item)

    def _remove_selected(self) -> None:
        item = self.media_list.currentItem()
        if item is None:
            return
        source = item.data(Qt.ItemDataRole.UserRole)
        if source is self.program_source:
            QMessageBox.information(
                self,
                "Источник в эфире",
                "Источник находится в Program. Сначала переключите эфир "
                "на другой источник (CUT/FADE).",
            )
            return

        row = self.media_list.row(item)
        self.media_list.takeItem(row)
        if source in self.sources:
            self.sources.remove(source)
        if source is self.home_source:
            self.home_source = None
        QTimer.singleShot(20, lambda s=source: self._dispose_source(s))

    def _dispose_source(self, source: BaseSource) -> None:
        try:
            source.stop()
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"Остановка источника: {exc}", 5000)
        self.output.release_cached_frames()
        self.video_output_window._last_image = None
        source.deleteLater()

    def _on_media_selected(
        self,
        current: Optional[QListWidgetItem],
        _previous: Optional[QListWidgetItem],
    ) -> None:
        source = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._set_preview(source)

    def _set_preview(self, source: Optional[BaseSource]) -> None:
        self.preview_source = source
        if source is None:
            self.preview_widget.set_image(None)
        self.output.set_preview(source)
        self._sync_native_preview()
        self._refresh_captions()
        self._rebuild_source_controls()
        self._refresh_slide_target()
        self._sync_slot_play_buttons()

    def _sync_native_preview(self) -> None:
        # Превью видео приходит из VideoWorker через обычный QVideoSink.
        return

    def _select_source_by_shortcut(self, number: int) -> None:
        focus = QApplication.focusWidget()
        while focus is not None:
            if isinstance(focus, QAbstractSpinBox):
                return
            focus = focus.parentWidget()
        index = number - 1
        if index < 0 or index >= self.media_list.count():
            return
        self.media_list.setCurrentRow(index)

    def _on_media_rows_moved(self, *_args) -> None:
        self.sources = [
            self.media_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.media_list.count())
        ]
        for source in self.sources:
            self._update_item_text(source)

    def _update_item_text(self, source: BaseSource) -> None:
        item = self._find_item(source)
        if item is None:
            return
        index = self.media_list.row(item)
        shortcut_prefix = f"[{index + 1}] " if index < 9 else ""
        prefix = SOURCE_PREFIX.get(source.kind, "SRC")
        home_prefix = "★ " if source is self.home_source else ""
        suffix = ""
        if isinstance(source, PptxSource) and not source.page_count:
            stage = source.stage or "конвертация…"
            suffix = f"  [{stage} → PDF]"
        elif isinstance(source, PdfSource) and source.page_count:
            suffix = f"  [{source.page_index + 1}/{source.page_count}]"
        item.setText(f"{shortcut_prefix}{home_prefix}[{prefix}] {source.name}{suffix}")
        item.setToolTip(item.text())

    def _find_item(self, source: BaseSource) -> Optional[QListWidgetItem]:
        for index in range(self.media_list.count()):
            item = self.media_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) is source:
                return item
        return None

    # ==================================================================
    # Панель выбранного источника
    # ==================================================================
    @classmethod
    def _clear_layout(cls, layout) -> None:
        """Убирает виджеты из layout сразу, не дожидаясь deleteLater().

        takeAt() отвязывает элемент от layout, но виджет остается ребенком
        контейнера и продолжает рисоваться поверх новых элементов до тех пор,
        пока не выполнится deleteLater(). Поэтому старые виджеты сначала
        скрываем и отвязываем от родителя.
        """
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                continue
            child_layout = item.layout()
            if child_layout is not None:
                cls._clear_layout(child_layout)
                child_layout.deleteLater()

    def _rebuild_source_controls(self) -> None:
        previous_video_source = self._video_controls_source
        if previous_video_source is not None:
            for signal, slot in (
                (previous_video_source.position_changed, self._on_video_position_changed),
                (previous_video_source.duration_changed, self._on_video_duration_changed),
                (previous_video_source.playback_state_changed, self._on_video_playback_state_changed),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._clear_layout(self.source_layout)

        self._pdf_controls_source = None
        self._pdf_page_label = None
        self._video_controls_source = None
        self._video_play_btn = None
        self._video_position_slider = None
        self._video_time_label = None
        self._video_mute_btn = None
        self._video_volume_slider = None
        self._video_volume_label = None

        source = self.preview_source
        if source is None:
            self.source_layout.addWidget(QLabel("Источник не выбран"))
            return

        name = QLabel(source.name)
        name.setWordWrap(True)
        name.setStyleSheet("font-weight: 700; color: #ffffff;")
        self.source_layout.addWidget(name)

        home_btn = QPushButton("★ Домашний фон")
        home_btn.setCheckable(True)
        home_btn.setChecked(source is self.home_source)
        home_btn.setToolTip("Назначить или снять домашний фон")
        home_btn.toggled.connect(
            lambda checked, src=source: self._set_home_source(src, checked)
        )
        self.source_layout.addWidget(home_btn)

        kind = QLabel(f"Тип: {source.kind}")
        kind.setStyleSheet("color:#8b95a3;")
        self.source_layout.addWidget(kind)

        if isinstance(source, PdfSource):
            self._pdf_controls_source = source
            self._pdf_page_label = QLabel(
                f"{source.page_index + 1} / {source.page_count or '…'}"
            )
            self._pdf_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._pdf_page_label.setStyleSheet("font-size: 15px; font-weight: 700;")
            self.source_layout.addWidget(self._pdf_page_label)

            nav = QHBoxLayout()
            prev_btn = QPushButton("‹ Назад")
            next_btn = QPushButton("Вперёд ›")
            prev_btn.clicked.connect(
                lambda _checked=False, src=source: self._navigate_pdf(src, -1)
            )
            next_btn.clicked.connect(
                lambda _checked=False, src=source: self._navigate_pdf(src, 1)
            )
            nav.addWidget(prev_btn)
            nav.addWidget(next_btn)
            self.source_layout.addLayout(nav)
        elif isinstance(source, VideoSource):
            self._video_controls_source = source
            source.position_changed.connect(self._on_video_position_changed)
            source.duration_changed.connect(self._on_video_duration_changed)
            source.playback_state_changed.connect(self._on_video_playback_state_changed)

            video_buttons = QHBoxLayout()
            self._video_play_btn = QPushButton(
                "⏸ Пауза" if source._is_playing else "⏯ Старт"
            )
            self._video_play_btn.setFont(QFont("Segoe UI Symbol"))
            self._video_play_btn.clicked.connect(source.toggle_play_pause)
            reset_btn = QPushButton("⏹ В начало")
            reset_btn.setFont(QFont("Segoe UI Symbol"))
            reset_btn.clicked.connect(source.reset_to_start)
            video_buttons.addWidget(self._video_play_btn)
            video_buttons.addWidget(reset_btn)
            self.source_layout.addLayout(video_buttons)

            seek_row = QHBoxLayout()
            self._video_position_slider = QSlider(Qt.Orientation.Horizontal)
            self._video_position_slider.setRange(0, source._duration_ms)
            self._video_position_slider.sliderMoved.connect(source.seek)
            self._video_time_label = QLabel("00:00 / 00:00")
            self._video_time_label.setMinimumWidth(92)
            self._video_time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._video_time_label.setText(
                f"00:00 / {self._format_video_time(source._duration_ms)}"
            )
            seek_row.addWidget(self._video_position_slider, 1)
            seek_row.addWidget(self._video_time_label)
            self.source_layout.addLayout(seek_row)

            audio_row = QHBoxLayout()
            self._video_mute_btn = QPushButton("🔊")
            self._video_mute_btn.setFont(QFont("Segoe UI Symbol"))
            self._video_mute_btn.setCheckable(True)
            self._video_mute_btn.setToolTip("Включить или выключить звук")
            self._video_mute_btn.toggled.connect(self._on_video_mute_toggled)
            self._video_volume_slider = QSlider(Qt.Orientation.Horizontal)
            self._video_volume_slider.setRange(0, 100)
            self._video_volume_slider.setValue(100)
            self._video_volume_slider.valueChanged.connect(
                lambda value, src=source: src.set_volume(value / 100.0)
            )
            self._video_volume_slider.valueChanged.connect(self._on_video_volume_changed)
            self._video_volume_label = QLabel("100%")
            self._video_volume_label.setMinimumWidth(38)
            audio_row.addWidget(self._video_mute_btn)
            audio_row.addWidget(self._video_volume_slider, 1)
            audio_row.addWidget(self._video_volume_label)
            self.source_layout.addLayout(audio_row)

            loop = QCheckBox("Зациклить (Loop)")
            loop.setChecked(source.loop)
            loop.toggled.connect(source.set_loop)
            self.source_layout.addWidget(loop)
        else:
            info = QLabel(source.info())
            info.setWordWrap(True)
            info.setStyleSheet("color:#8b95a3;")
            self.source_layout.addWidget(info)

        self.source_layout.addStretch(1)

    @staticmethod
    def _format_video_time(position_ms: int) -> str:
        seconds = max(0, int(position_ms) // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _on_video_position_changed(self, position_ms: int) -> None:
        source = self._video_controls_source
        if source is None or self._video_position_slider is None:
            return
        with QSignalBlocker(self._video_position_slider):
            self._video_position_slider.setValue(position_ms)
        duration = source._duration_ms
        if self._video_time_label is not None:
            self._video_time_label.setText(
                f"{self._format_video_time(position_ms)} / "
                f"{self._format_video_time(duration)}"
            )

    def _on_video_duration_changed(self, duration_ms: int) -> None:
        source = self._video_controls_source
        if source is None or self._video_position_slider is None:
            return
        source._duration_ms = max(0, int(duration_ms))
        self._video_position_slider.setRange(0, source._duration_ms)
        self._on_video_position_changed(0)

    def _on_video_playback_state_changed(self, playing: bool) -> None:
        source = self._video_controls_source
        if self._video_play_btn is not None:
            self._video_play_btn.setText("⏸ Пауза" if playing else "▶ Старт")
        self._sync_slot_play_buttons()

    def _toggle_slot_rendering(self, role: str) -> None:
        if role == "preview":
            self._preview_rendering = not self._preview_rendering
            enabled = self._preview_rendering
            widget = self.preview_widget
            button = self._preview_play_btn
        else:
            self._program_rendering = not self._program_rendering
            enabled = self._program_rendering
            widget = self.program_widget
            button = self._program_play_btn
        widget.set_rendering_enabled(enabled)
        if button is not None:
            button.setText("⏸ Пауза" if enabled else "▶ Старт")

    def _sync_slot_play_buttons(self) -> None:
        for button, source, rendering in (
            (self._preview_play_btn, self.preview_source, self._preview_rendering),
            (self._program_play_btn, self.program_source, self._program_rendering),
        ):
            if button is None:
                continue
            is_video = isinstance(source, VideoSource)
            button.setVisible(is_video)
            if is_video:
                button.setText("⏸ Пауза" if rendering else "▶ Старт")

    def _on_video_mute_toggled(self, muted: bool) -> None:
        source = self._video_controls_source
        if source is not None:
            source.set_muted(muted)
        if self._video_mute_btn is not None:
            self._video_mute_btn.setText("🔇" if muted else "🔊")

    def _on_video_volume_changed(self, value: int) -> None:
        if self._video_volume_label is not None:
            self._video_volume_label.setText(f"{value}%")

    def _on_pdf_page_changed(self, source: PdfSource, index: int, count: int) -> None:
        self._update_item_text(source)
        if self._pdf_controls_source is source and self._pdf_page_label is not None:
            self._pdf_page_label.setText(f"{index + 1} / {count}")
        if self._slide_source is source:
            self._sync_slide_panel()
            if self._slide_timer.isActive() and source.is_last_page:
                # Автопрокрутка дошла до последнего слайда — дальше не идем.
                self._pause_slideshow()

    def _set_home_source(self, source: BaseSource, checked: bool) -> None:
        self.home_source = source if checked else None
        for item_index in range(self.media_list.count()):
            item_source = self.media_list.item(item_index).data(Qt.ItemDataRole.UserRole)
            if item_source is not None:
                self._update_item_text(item_source)
        self._rebuild_source_controls()

    def _go_home(self) -> None:
        if not self._output_active:
            return
        if self.home_source is None or self.home_source is self.program_source:
            return
        self.media_list.setCurrentItem(self._find_item(self.home_source))
        self._fade()

    def _sync_live_controls_enabled(self) -> None:
        enabled = bool(self._output_active)
        tooltip = "" if enabled else "Сначала включите вывод на экран («На весь экран»)"
        for button in (
            self.cut_btn,
            self.fade_btn,
            self.fade_pause_btn,
            self.freeze_btn,
            self.black_btn,
            self.home_btn,
        ):
            button.setEnabled(enabled)
            button.setToolTip(tooltip)

    def _keep_windows_awake(self) -> None:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except Exception:
            pass

    # ==================================================================
    # Переходы и Master Controls
    # ==================================================================
    def _cut(self) -> None:
        self._is_cut_transition = True
        if not self._output_active:
            return
        if self.preview_source is None:
            return
        previous = self.program_source
        target = self.preview_source
        self._fade_pause_outgoing = False
        self.program_source = target
        if isinstance(target, VideoSource):
            target.set_high_res_transition(True)
            target.set_muted(target._muted)
        self.output.cut()
        self._refresh_captions()
        self._refresh_slide_target()
        self._sync_slot_play_buttons()

    def _fade(self, pause_outgoing: bool = False) -> None:
        self._is_cut_transition = False
        self._fade_pause_outgoing = bool(pause_outgoing)
        if not self._output_active:
            return
        if self.preview_source is None or self.preview_source is self.program_source:
            return
        previous = self.program_source
        target = self.preview_source
        if isinstance(previous, VideoSource):
            previous.set_native_output(None)
            previous.set_high_res_transition(True)
        if isinstance(target, VideoSource):
            target.set_native_output(None)
            target.set_high_res_transition(True)
            target.set_muted(target._muted)
        live_frame = self._grab_live_program_frame()
        if live_frame is not None and not live_frame.isNull():
            self.output._last_content = live_frame
        self.program_source = target
        self.output.fade(int(self.fade_duration.value() * 1000))
        self._refresh_captions()
        self._refresh_slide_target()
        self._sync_slot_play_buttons()

    def _on_transition_finished(self, outgoing: object, incoming: object) -> None:
        if isinstance(outgoing, VideoSource) and outgoing is not incoming:
            outgoing.set_high_res_transition(False)
            outgoing.set_native_output(None)
            outgoing.reset_to_start()
        if not isinstance(incoming, VideoSource):
            self.video_output_window._last_image = None
        if isinstance(incoming, VideoSource) and incoming is self.program_source:
            incoming.set_high_res_transition(False)
            incoming.set_muted(incoming._muted)
            if self.output.freeze_active or self._fade_pause_outgoing:
                incoming.pause(by_user=self._fade_pause_outgoing)
            else:
                incoming._user_paused = False
                incoming.play()
        self._sync_slot_play_buttons()
        self._sync_native_output()

    def _apply_output_zorder(self, native_video: bool, black_state: bool) -> None:
        if black_state:
            self.black_overlay.raise_()
            if self.video_output_window.isVisible():
                self.video_output_window.lower()
        elif native_video:
            self.output_window.lower()
            self.video_output_window.raise_()
        else:
            self.video_output_window.lower()
            self.output_window.raise_()

    def _toggle_freeze(self) -> None:
        if not self._output_active:
            return
        self.freeze_btn.toggle()

    def _toggle_black(self) -> None:
        if not self._output_active:
            return
        self.black_btn.toggle()

    def _on_black_alpha_changed(self, alpha: float) -> None:
        screen = self.screen_combo.currentData()
        native_video = bool(
            self._output_active
            and self.settings.get("video_output_mode") != "external"
            and isinstance(self.program_source, VideoSource)
        )
        if native_video and alpha > 0.001:
            self.black_overlay.set_alpha(alpha, screen)
        else:
            self.black_overlay.hide()
            if alpha <= 0.001:
                black_state = self.output.black_active or self.output._black_anim_active
                self._apply_output_zorder(
                    native_video=bool(native_video and not black_state and not self.output.freeze_active and not self.output.fade_active),
                    black_state=black_state,
                )
            elif native_video:
                self.video_output_window.raise_()
            else:
                self.output_window.raise_()

    def _grab_live_program_frame(self) -> Optional[QImage]:
        source = self.program_source
        grab_full_frame = getattr(source, "grab_full_frame", None)
        if callable(grab_full_frame):
            image = grab_full_frame()
            if image is not None and not image.isNull():
                return image
        return self.output._last_content

    def _on_freeze_toggled(self, checked: bool) -> None:
        snapshot = self._grab_live_program_frame() if checked else None
        self.output.set_freeze(checked, snapshot)
        self._sync_native_output()

    def _on_black_toggled(self, checked: bool) -> None:
        smooth = self.smooth_ftb.isChecked()
        snapshot = self._grab_live_program_frame() if smooth else None
        self.output.set_black(
            checked,
            smooth=smooth,
            duration_ms=round(self.ftb_duration.value() * 1000),
        )
        if smooth and snapshot is not None and not snapshot.isNull():
            self.output._black_snapshot = snapshot
        self._sync_native_output()

    def _on_output_state_changed(self) -> None:
        self._sync_master_state()
        visual_state = (
            self._output_active,
            self.program_source,
            self.output.black_active,
            self.output._black_anim_active,
            self.output.freeze_active,
            self.output.fade_active,
            self.screen_combo.currentData(),
        )
        if visual_state != getattr(self, "_last_output_visual_state", None):
            self._last_output_visual_state = visual_state
            self._sync_native_output()

    def _on_compositor_output_frame(self, image: QImage) -> None:
        if not self._output_active:
            return
        if (
            isinstance(self.program_source, VideoSource)
            and not self.output.black_active
            and not self.output._black_anim_active
            and not self.output.freeze_active
            and not self.output.fade_active
        ):
            return
        self.video_output_window.set_image(image)

    def _sync_native_output(self) -> None:
        if not getattr(self, "_compositor_output_connected", False):
            self.output.output_frame.connect(self._on_compositor_output_frame)
            self.video_output_window.hidden_by_user.connect(self._hide_output)
            self._compositor_output_connected = True
        source = self.program_source
        fps = int(self.settings.get("preview_fps", 12))
        for video_source in self.sources:
            if isinstance(video_source, VideoSource):
                video_source.set_preview_fps(fps)
        external = self.settings.get("video_output_mode") == "external"
        screen = self.screen_combo.currentData()
        black_state = self.output.black_active or self.output._black_anim_active
        if external:
            if isinstance(source, VideoSource) and self._output_active:
                source.set_native_output(None)
                source.pause()
                self.external_player.play(source.path, screen)
            else:
                self.external_player.stop()
            self.output_window.hide()
            self.video_output_window.hide()
            self.black_overlay.hide()
        else:
            self.external_player.stop()
            self.output_window.hide()
            self.black_overlay.hide()
            if self._output_active:
                if screen is not None:
                    if (not self.video_output_window.isVisible()
                            or getattr(self, "_video_output_screen", None) != screen):
                        self.video_output_window.show_on_screen(screen, raise_window=False)
                        self._video_output_screen = screen
                elif not self.video_output_window.isVisible():
                    self.video_output_window.show()
                if isinstance(source, VideoSource):
                    if (not self.output.fade_active
                            and not self.output.freeze_active
                            and not black_state):
                        source.set_native_output(self.video_output_window)
                    else:
                        source.set_native_output(None)
                    source.set_high_res_transition(False)
                    if self.output.freeze_active:
                        source.pause()
                    elif (not self.output.fade_active
                          and not source.is_playing
                          and not getattr(source, "_user_paused", False)):
                        source.play()
                else:
                    frame = source.get_latest() if source is not None else None
                    self.video_output_window.set_image(frame)
                if black_state:
                    self.black_overlay.set_alpha(self.output._black_alpha, screen)
                    self.black_overlay.raise_()
                else:
                    self.black_overlay.hide()
            else:
                self.video_output_window.hide()
                self.black_overlay.hide()
                if isinstance(source, VideoSource):
                    source.set_native_output(None)
        self._sync_native_preview()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.settings()
            fps = int(self.settings.get("preview_fps", 12))
            for source in self.sources:
                if isinstance(source, VideoSource):
                    source.set_preview_fps(fps)
            self.output.set_video_output_mode(self.settings["video_output_mode"])
            self.external_player.executable = self.settings["external_player"]
            self._sync_native_output()

    def _sync_master_state(self) -> None:
        self._sync_master_button(self.freeze_btn, self.output.freeze_active)
        self._sync_master_button(self.black_btn, self.output.black_active)

        if self.output.black_active:
            text, color = "BLACK", "#ef4444"
        elif self.output.freeze_active:
            text, color = "FREEZE", "#f59e0b"
        else:
            text, color = "● ЭФИР", "#22c55e"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            f"color: {color}; font-weight: 800; font-size: 15px;"
        )

    @staticmethod
    def _sync_master_button(button: QPushButton, active: bool) -> None:
        button.blockSignals(True)
        button.setChecked(active)
        button.blockSignals(False)
        set_active_property(button, active)

    def _refresh_captions(self) -> None:
        from shiboken6 import isValid

        preview = self.preview_source.name if self.preview_source is not None else "—"
        program = self.program_source.name if self.program_source is not None else "—"
        if isValid(self.preview_caption):
            self.preview_caption.setText(f"PREVIEW · {preview}")
        if isValid(self.program_caption):
            self.program_caption.setText(f"PROGRAM · {program}")

    # ==================================================================
    # Слайд-шоу (PDF, PPTX и альбомы фото)
    # ==================================================================
    def _refresh_slide_target(self) -> None:
        """Цель панели: PDF/PPTX/альбом в Program, иначе в Preview."""
        program_pdf = (
            self.program_source if isinstance(self.program_source, PdfSource) else None
        )
        preview_pdf = (
            self.preview_source if isinstance(self.preview_source, PdfSource) else None
        )

        if program_pdf is not None:
            target, role = program_pdf, "PROGRAM"
        elif preview_pdf is not None:
            target, role = preview_pdf, "PREVIEW"
        else:
            target, role = None, ""

        if target is not self._slide_source:
            # Смена активной презентации всегда останавливает автопрокрутку.
            self._slide_timer.stop()
            self._slide_source = target
        self._slide_role = role
        self._sync_slide_panel()

    def _sync_slide_panel(self) -> None:
        target = self._slide_source
        active = target is not None and target.page_count > 0
        count = target.page_count if target is not None else 0
        index = target.page_index if active else 0
        playing = self._slide_timer.isActive()

        self.slide_prev_btn.setEnabled(active and index > 0)
        self.slide_next_btn.setEnabled(active and index < count - 1)
        self.slide_play_btn.setEnabled(active and count > 1)
        self.slide_stop_btn.setEnabled(active)
        self.slide_number.setEnabled(active)

        blocker = QSignalBlocker(self.slide_number)
        self.slide_number.setRange(1, max(1, count))
        self.slide_number.setValue(index + 1 if active else 1)
        blocker.unblock()

        self.slide_total_label.setText(f"/ {count}" if count else "/ —")

        if target is None:
            self.slide_target_label.setText(
                "Презентация или альбом не выбраны · добавьте PDF/PPTX "
                "или папку фото, либо выберите источник в Preview"
            )
        else:
            role = f" · {self._slide_role}" if self._slide_role else ""
            self.slide_target_label.setText(f"Слайд-шоу: {target.name}{role}")

        self.slide_play_btn.setText("⏸ Пауза" if playing else "⏯ Старт")
        set_active_property(self.slide_play_btn, playing)

    def _apply_slide_interval(self) -> None:
        self._slide_timer.setInterval(
            max(500, int(round(self.slide_interval.value() * 1000)))
        )

    def _on_slide_interval_changed(self, _value: float) -> None:
        self._apply_slide_interval()
        if self._slide_timer.isActive():
            self._slide_timer.start()  # перезапустить отсчет с новым интервалом

    def _toggle_slideshow(self) -> None:
        if self._slide_timer.isActive():
            self._pause_slideshow()
            return

        target = self._slide_source
        if target is None or target.page_count <= 1:
            self._sync_slide_panel()
            return

        if target.is_last_page:
            # На последнем слайде «Старт» начинает показ заново.
            target.first_page()

        self._apply_slide_interval()
        self._slide_timer.start()
        self._sync_slide_panel()

    def _pause_slideshow(self) -> None:
        if self._slide_timer.isActive():
            self._slide_timer.stop()
        self._sync_slide_panel()

    def _stop_slideshow(self) -> None:
        target = self._slide_source
        self._pause_slideshow()
        if target is not None and target.page_count > 0 and not target.is_first_page:
            if isinstance(target, PptxSource):
                target.restart()
            else:
                target.first_page()
        self._sync_slide_panel()

    def _on_slide_timer(self) -> None:
        target = self._slide_source
        if target is None or target.page_count <= 0:
            self._pause_slideshow()
            return
        target.next_page()

    def _slide_prev(self) -> None:
        target = self._slide_source
        if target is None or target.page_count <= 0:
            return
        self._pause_slideshow()
        target.prev_page()

    def _slide_next(self) -> None:
        target = self._slide_source
        if target is None or target.page_count <= 0:
            return
        self._pause_slideshow()
        target.next_page()

    def _on_slide_number_changed(self, value: int) -> None:
        target = self._slide_source
        if target is None or target.page_count <= 0:
            return
        index = int(value) - 1
        if index == target.page_index:
            return
        if isinstance(target, PptxSource):
            target.goto_slide(value)
        else:
            target.go_to_page(index)

    def _navigate_pdf(self, source: PdfSource, delta: int) -> None:
        """Ручная навигация из панели выбранного источника."""
        if source is self._slide_source:
            self._pause_slideshow()
        if delta < 0:
            source.prev_page()
        else:
            source.next_page()

    # ==================================================================
    # Вывод на монитор
    # ==================================================================
    def _refresh_screens(self, *_args) -> None:
        combo = self.screen_combo
        current = combo.currentData()
        primary = QGuiApplication.primaryScreen()
        control_screen = self.screen() or primary

        combo.blockSignals(True)
        combo.clear()
        screens = QGuiApplication.screens()
        for index, screen in enumerate(screens):
            if screen is control_screen:
                continue
            geo = screen.geometry()
            label = f"{index + 1}: {screen.name()} · {geo.width()}×{geo.height()}"
            if screen is primary:
                label += " · основной"
            combo.addItem(label, screen)

        if combo.count() == 0:
            combo.addItem("Нет второго монитора (пульт защищён)", None)
            combo.setEnabled(False)
            self.fs_btn.setEnabled(False)
        else:
            combo.setEnabled(True)
            self.fs_btn.setEnabled(True)

        if current is not None:
            for index in range(combo.count()):
                if combo.itemData(index) is current:
                    combo.setCurrentIndex(index)
                    break
        combo.blockSignals(False)

        if not self._screens_initialized and combo.count():
            self._screens_initialized = True
            # По умолчанию предлагаем не основной экран (проектор/второй монитор).
            for index in range(combo.count()):
                screen = combo.itemData(index)
                if screen is not None and screen is not primary:
                    combo.setCurrentIndex(index)
                    break

    def _go_fullscreen(self) -> None:
        screen = self.screen_combo.currentData()
        if screen is None or screen is (self.screen() or QGuiApplication.primaryScreen()):
            QMessageBox.information(
                self,
                "Нет монитора",
                "Подключенные мониторы не найдены.",
            )
            return
        self._output_active = True
        self._sync_live_controls_enabled()
        self.output_state_label.setText(
            f"ВЫВОД: {screen.name()} · режим {self.mode_combo.currentText()}"
        )
        self._sync_native_output()

    def _hide_output(self) -> None:
        self._output_active = False
        self._sync_live_controls_enabled()
        self.black_overlay.hide()
        self.output_window.hide()
        self.video_output_window.hide()
        self.output.release_cached_frames()
        self.video_output_window._last_image = None
        if isinstance(self.program_source, VideoSource):
            self.program_source.set_native_output(None)
        self.output_state_label.setText("ВЫВОД: скрыт")
        self._sync_native_output()

    def _on_mode_changed(self, _index: int) -> None:
        mode = self.mode_combo.currentData() or "fit"
        self.video_output_window.set_fill_mode(mode)
        if self.video_output_window.isVisible():
            screen = self.screen_combo.currentData()
            name = screen.name() if screen is not None else "—"
            self.output_state_label.setText(
                f"ВЫВОД: {name} · режим {self.mode_combo.currentText()}"
            )

    # ==================================================================
    # Прочее
    # ==================================================================
    def _on_fps_updated(self, current: float, average: float) -> None:
        self.fps_label.setText(f"FPS: {current:.1f} | Avg: {average:.1f}")

    def _on_source_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 10000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.program_source is not None:
            answer = QMessageBox.question(
                self,
                "Подтверждение закрытия",
                "В эфире сейчас активен источник. Вы действительно хотите закрыть PyVideoMixer?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
        self._slide_timer.stop()
        self._slide_source = None
        self.output.stop()
        self.black_overlay.hide()
        self.output_window.hide()
        for source in list(self.sources):
            try:
                source.stop()
            except Exception:  # noqa: BLE001
                pass
        self.sources.clear()
        self.output_window.close()
        self.video_output_window.close()
        self.black_overlay.close()
        super().closeEvent(event)
