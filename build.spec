# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: автономная onedir/windowed сборка PyVideoMixer.

Сборка:  python build_exe.py
Вручную: python -m PyInstaller build.spec --noconfirm --clean

Результат: dist/PyVideoMixer/PyVideoMixer.exe (папку целиком можно копировать
на флешку). test_media и tools в бандл не попадают. Руководство
docs/КАК_ПОЛЬЗОВАТЬСЯ.txt рядом с exe копирует build_exe.py.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PROJECT_ROOT = Path(SPECPATH)  # noqa: F821 - задает PyInstaller

datas = []
binaries = []
hiddenimports = []

# У PyInstaller 6.19 нет штатных хуков для PyMuPDF (pymupdf/fitz), python-pptx
# (нужен шаблон pptx/templates/default.pptx), dxcam и mss — забираем их целиком.
# collect_all кладет и python-модули, и бинарники (.pyd/.dll), и data-файлы.
for package in ("pymupdf", "fitz", "pptx", "dxcam", "mss"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception as exc:  # noqa: BLE001 - сборка не должна падать из-за одного пакета
        print(f"[build.spec] collect_all({package}): {exc}")
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# COM/pywin32 и comtypes подгружаются лениво (внутри функций), поэтому
# статический анализ PyInstaller их не видит.
hiddenimports += [
    "PIL",
    "pythoncom",
    "pywintypes",
    "win32api",
    "win32con",
    "win32gui",
    "win32com",
    "win32com.client",
    "win32com.client.dynamic",
    "win32com.client.gencache",
    "comtypes",
    "comtypes.client",
    # PySide6.QtMultimedia тянет плагины multimedia (ffmpeg/windows) через
    # hook-PySide6.QtMultimedia.py; модули перечислены явно для надежности.
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtNetwork",
]

# Не тащим в бандл тестовые материалы, генераторы и лишние тяжелые пакеты.
excludes = [
    "test_media",
    "tools",
    "tkinter",
    "pytest",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "notebook",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtQuick",
    "PySide6.QtQml",
]

a = Analysis(  # noqa: F821
    ["main.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PyVideoMixer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # --windowed: без черного окна консоли
    disable_windowed_traceback=False,  # при падении покажет traceback в диалоге
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PyVideoMixer",
)
