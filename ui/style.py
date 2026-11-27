"""Темы пульта и подсветка мастер-кнопок."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget


def _normalize_hex_color(value: str, fallback: str = "#2563eb") -> str:
    """Возвращает цвет в формате #rrggbb или безопасный fallback."""
    candidate = str(value or "").strip()
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", candidate)
    if match:
        return f"#{match.group(1).lower()}"
    match = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", candidate, re.IGNORECASE)
    if match:
        values = tuple(int(part) for part in match.groups())
        if all(0 <= value <= 255 for value in values):
            return "#" + "".join(f"{value:02x}" for value in values)
    match = re.fullmatch(r"(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", candidate)
    if match:
        values = tuple(int(part) for part in match.groups())
        if all(0 <= value <= 255 for value in values):
            return "#" + "".join(f"{value:02x}" for value in values)
    if fallback == value:
        return "#2563eb"
    return _normalize_hex_color(fallback, "#2563eb")


def build_stylesheet(
    theme: str = "dark",
    accent: str = "#2563eb",
    fade_color: str = "#0f766e",
    fade_pause_color: str = "#0d9488",
    cut_color: str = "#2563eb",
    cut_pause_color: str = "#1d4ed8",
) -> str:
    """Создаёт компактную светлую или тёмную тему с заданными цветами."""
    accent = _normalize_hex_color(accent)
    fade_color = _normalize_hex_color(fade_color, "#0f766e")
    fade_pause_color = _normalize_hex_color(fade_pause_color, "#0d9488")
    cut_color = _normalize_hex_color(cut_color)
    cut_pause_color = _normalize_hex_color(cut_pause_color, "#1d4ed8")
    if theme == "system":
        hints = QGuiApplication.styleHints()
        theme = "light" if hints.colorScheme() == Qt.ColorScheme.Light else "dark"
    light = theme == "light"
    bg = "#f8fafc" if light else "#16191d"
    field = "#ffffff" if light else "#101317"
    card = "#e2e8f0" if light else "#2a3038"
    border = "#cbd5e1" if light else "#2a3038"
    text = "#0f172a" if light else "#e6e8eb"
    muted = "#475569" if light else "#9aa4b2"
    header = "#0f172a" if light else "#f1f5f9"
    hover = "#cbd5e1" if light else "#343c46"
    selected = accent
    return f"""
QMainWindow, QDialog, QFileDialog {{ background-color: {bg}; color: {text}; }}
QWidget {{ color: {text}; font-family: "Segoe UI", "Arial", sans-serif; font-size: 13px; }}
QGroupBox {{ border: 1px solid {border}; border-radius: 8px; margin-top: 12px; padding: 7px 6px 6px 6px; font-weight: 600; color: {muted}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QLabel#headerLabel, QLabel#sourceTitleLabel {{ color: {header}; }}
QLabel#headerLabel {{ font-size: 18px; font-weight: 700; padding: 2px 4px; }}
QLabel#sourceTitleLabel {{ font-weight: 700; }}
QLabel#mutedLabel {{ color: {muted}; }}
QListWidget, QTreeView, QTableView, QListView, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background-color: {field}; color: {text}; border: 1px solid {border}; border-radius: 6px; }}
QListWidget {{ outline: 0; }}
QListWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {border}; }}
QListWidget::item:selected, QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {{ background-color: {selected}; color: #ffffff; }}
QTreeView, QTableView, QListView {{ alternate-background-color: {bg}; outline: 0; }}
QHeaderView::section {{ background-color: {card}; color: {text}; border: 1px solid {border}; padding: 4px 6px; }}
QPushButton, QToolButton {{ background-color: {card}; color: {text}; border: 1px solid {border}; border-radius: 6px; padding: 6px 10px; }}
QPushButton:hover, QToolButton:hover {{ background-color: {hover}; }}
QSlider::groove:horizontal {{ height: 6px; background: {card}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {accent}; border: 1px solid #ffffff; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }}
QPushButton:disabled {{ color: {muted}; background-color: {card}; }}
QPushButton#mediaPoolButton {{ padding: 4px 8px; font-size: 11px; font-weight: 500; }}
QPushButton#cutButton {{ background-color: {cut_color}; border-color: {cut_color}; color: #ffffff; font-size: 16px; font-weight: 800; }}
QPushButton#cutPauseButton {{ background-color: {cut_pause_color}; border-color: {cut_pause_color}; color: #ffffff; font-size: 16px; font-weight: 800; }}
QPushButton#cutButton:hover, QPushButton#cutPauseButton:hover {{ background-color: {hover}; }}
QPushButton#fadeButton {{ background-color: {fade_color}; border-color: {fade_color}; color: #ffffff; font-size: 16px; font-weight: 800; }}
QPushButton#fadePauseButton {{ background-color: {fade_pause_color}; border-color: {fade_pause_color}; color: #ffffff; font-size: 16px; font-weight: 800; }}
QPushButton#fadeButton:hover, QPushButton#fadePauseButton:hover {{ background-color: {hover}; }}
QPushButton#cutButton:disabled, QPushButton#cutPauseButton:disabled, QPushButton#fadeButton:disabled, QPushButton#fadePauseButton:disabled {{
    background-color: {card};
    border-color: {border};
    color: {muted};
}}
QPushButton#freezeButton[active="true"] {{ background-color: #dc2626; color: #ffffff; }}
QPushButton#blackButton[active="true"] {{ background-color: #000000; color: #ef4444; }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ padding: 4px 6px; min-height: 20px; }}
QComboBox QAbstractItemView {{ background-color: {field}; color: {text}; selection-background-color: {selected}; }}
QCheckBox {{ spacing: 8px; }}
QStatusBar {{ background-color: {field}; color: {muted}; }}
"""


STYLE_SHEET = build_stylesheet()


def set_active_property(widget: QWidget, active: bool) -> None:
    """Переключает CSS-свойство active и перерисовывает виджет."""
    widget.setProperty("active", "true" if active else "false")
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()
