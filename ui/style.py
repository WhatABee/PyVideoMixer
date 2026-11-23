"""Тёмная тема пульта и подсветка мастер-кнопок."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

STYLE_SHEET = """
QMainWindow {
    background-color: #16191d;
}
QWidget {
    color: #e6e8eb;
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #323842;
    border-radius: 8px;
    margin-top: 16px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    color: #9aa4b2;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}
QLabel#headerLabel {
    font-size: 18px;
    font-weight: 700;
    color: #f1f5f9;
    padding: 2px 4px;
}
QListWidget {
    background-color: #101317;
    border: 1px solid #2a3038;
    border-radius: 6px;
    outline: 0;
}
QListWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid #1d2229;
}
QListWidget::item:selected {
    background-color: #1f3a63;
    color: #ffffff;
}
QPushButton {
    background-color: #2a3038;
    border: 1px solid #3a424d;
    border-radius: 6px;
    padding: 6px 10px;
}
QPushButton#mediaPoolButton {
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#mediaPoolButton:hover {
    background-color: #343c46;
}
QPushButton:hover {
    background-color: #343c46;
}
QPushButton:pressed {
    background-color: #20252c;
}
QPushButton:disabled {
    color: #6b7280;
    background-color: #262b33;
    border-color: #323842;
}
QPushButton#cutButton:disabled,
QPushButton#fadeButton:disabled,
QPushButton#fadePauseButton:disabled,
QPushButton#freezeButton:disabled,
QPushButton#blackButton:disabled {
    color: #6b7280;
    background-color: #262b33;
    border-color: #323842;
}
QPushButton#cutButton {
    background-color: #2563eb;
    border-color: #3b82f6;
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 2px;
}
QPushButton#cutButton:hover {
    background-color: #3b82f6;
}
QPushButton#fadeButton,
QPushButton#fadePauseButton {
    background-color: #0f766e;
    border-color: #14b8a6;
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 2px;
}
QPushButton#fadeButton:hover,
QPushButton#fadePauseButton:hover {
    background-color: #14b8a6;
}
QPushButton#slidePlayButton, QPushButton#slideStopButton {
    /* Segoe UI Symbol рисует ⏯/⏹/⏸ монохромно, без синих emoji-квадратов.
       Одна семья, без списка: иначе Qt выбирает emoji-фолбэк. */
    font-family: "Segoe UI Symbol";
}
QPushButton#slidePlayButton[active="true"] {
    background-color: #15803d;
    border-color: #22c55e;
    color: #ffffff;
}
QPushButton#slidePlayButton[active="true"]:hover {
    background-color: #16a34a;
}
QPushButton#freezeButton {
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 2px;
}
QPushButton#freezeButton[active="true"] {
    background-color: #dc2626;
    border-color: #f87171;
    color: #ffffff;
}
QPushButton#blackButton {
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 2px;
}
QPushButton#blackButton[active="true"] {
    background-color: #000000;
    border-color: #ef4444;
    color: #ef4444;
}
QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #101317;
    border: 1px solid #2a3038;
    border-radius: 6px;
    padding: 4px 6px;
    min-height: 20px;
}
QComboBox QAbstractItemView {
    background-color: #1a1e24;
    selection-background-color: #1f3a63;
}
QCheckBox {
    spacing: 8px;
}
QStatusBar {
    background-color: #101317;
    color: #9aa4b2;
}
"""


def set_active_property(widget: QWidget, active: bool) -> None:
    """Переключает CSS-свойство active и перерисовывает виджет."""
    widget.setProperty("active", "true" if active else "false")
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()
