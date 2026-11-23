"""Сборка автономного PyVideoMixer.exe (Windows, onedir + windowed).

Запуск:
    python build_exe.py                # чистая сборка + автопроверка exe
    python build_exe.py --no-selftest  # только собрать
    python build_exe.py --no-clean     # переиспользовать build/ (быстрее)

Результат: dist/PyVideoMixer/PyVideoMixer.exe
Папку dist/PyVideoMixer целиком можно копировать на флешку: Python на целевой
машине не нужен. В сборку не попадают test_media и tools.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "build.spec"
DIST_ROOT = ROOT / "dist"
BUILD_ROOT = ROOT / "build"
APP_DIR = DIST_ROOT / "PyVideoMixer"
EXE = APP_DIR / "PyVideoMixer.exe"
VERSIONS_ROOT = Path(r"F:\versions PVM")
GUIDE_SOURCE = ROOT / "docs" / "КАК_ПОЛЬЗОВАТЬСЯ.txt"
GUIDE_TARGET = APP_DIR / "КАК_ПОЛЬЗОВАТЬСЯ.txt"
SELFTEST_REPORT = BUILD_ROOT / "selftest.json"
TEST_VIDEO = ROOT / "test_media" / "test_video.mp4"


def _run(command: list[str], timeout: int | None = None) -> int:
    print(">", " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, timeout=timeout, check=False).returncode


def _human_size(path: Path) -> str:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return f"{total / (1024 * 1024):.1f} МиБ"


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401,PLC0415
    except ImportError:
        print("PyInstaller не установлен. Ставлю...", flush=True)
        code = _run([sys.executable, "-m", "pip", "install", "pyinstaller>=6.10"])
        if code != 0:
            raise SystemExit("Не удалось установить PyInstaller")
    import PyInstaller  # noqa: PLC0415

    print(f"PyInstaller {PyInstaller.__version__}", flush=True)


def _next_version() -> Path:
    VERSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    version_pattern = re.compile(r"1\.(\d+)")
    numbers = [
        int(match.group(1))
        for child in VERSIONS_ROOT.iterdir()
        if child.is_dir() and (match := version_pattern.fullmatch(child.name))
    ]

    if not numbers:
        previous_build = APP_DIR.exists() and EXE.exists()
        version = VERSIONS_ROOT / "1.1" if previous_build else VERSIONS_ROOT / "1.0"
        if previous_build:
            shutil.move(str(APP_DIR), str(VERSIONS_ROOT / "1.0"))
            print(f"Сохранена предыдущая сборка: {VERSIONS_ROOT / '1.0'}", flush=True)
        return version

    return VERSIONS_ROOT / f"1.{max(numbers) + 1}"


def _clean() -> None:
    if BUILD_ROOT.exists():
        print(f"Удаляю {BUILD_ROOT}", flush=True)
        shutil.rmtree(BUILD_ROOT, ignore_errors=True)


def _build(clean: bool) -> None:
    if APP_DIR.exists():
        print(f"Удаляю временную сборку {APP_DIR}", flush=True)
        shutil.rmtree(APP_DIR)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC),
        "--noconfirm",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT),
    ]
    if clean:
        command.append("--clean")
    started = time.monotonic()
    code = _run(command, timeout=1800)
    if code != 0:
        raise SystemExit(f"PyInstaller завершился с кодом {code}")
    if not EXE.exists():
        raise SystemExit(f"EXE не найден: {EXE}")
    print(f"Сборка заняла {time.monotonic() - started:.0f} c", flush=True)


def _copy_guide() -> None:
    """Кладет подробное руководство рядом с exe."""
    if not GUIDE_SOURCE.exists():
        print(f"Нет файла руководства: {GUIDE_SOURCE}", flush=True)
        return
    shutil.copyfile(GUIDE_SOURCE, GUIDE_TARGET)
    print(f"Руководство: {GUIDE_TARGET.name}", flush=True)


def _verify_layout() -> None:
    """Проверяет, что в бандл попали критичные DLL/плагины и нет test_media."""
    checks = {
        "Qt multimedia plugin": APP_DIR / "_internal" / "PySide6" / "plugins" / "multimedia",
        "PyMuPDF": APP_DIR / "_internal" / "pymupdf",
        "python-pptx": APP_DIR / "_internal" / "pptx",
        "dxcam": APP_DIR / "_internal" / "dxcam",
        "Qt6Core": APP_DIR / "_internal" / "PySide6" / "Qt6Core.dll",
        "Qt6Multimedia": APP_DIR / "_internal" / "PySide6" / "Qt6Multimedia.dll",
        "Qt6MultimediaWidgets": APP_DIR / "_internal" / "PySide6" / "Qt6MultimediaWidgets.dll",
        "Руководство": GUIDE_TARGET,
    }
    failed = []
    for label, path in checks.items():
        ok = path.exists() and (not path.is_dir() or any(path.iterdir()))
        print(f"  {'OK ' if ok else 'НЕТ'}  {label}: {path.relative_to(APP_DIR)}", flush=True)
        if not ok:
            failed.append(label)

    multimedia = checks["Qt multimedia plugin"]
    if multimedia.exists():
        plugins = [item.name for item in multimedia.glob("*.dll")]
        print(f"  плагины multimedia: {', '.join(plugins) or 'пусто'}", flush=True)
        if not plugins:
            failed.append("multimedia plugins")

    for forbidden in ("test_media", "tools"):
        if (APP_DIR / "_internal" / forbidden).exists() or (APP_DIR / forbidden).exists():
            failed.append(f"лишняя папка {forbidden}")

    if failed:
        raise SystemExit("Проблемы со сборкой: " + ", ".join(failed))


def _selftest() -> bool:
    if not TEST_VIDEO.exists():
        print(f"Нет тестового видео {TEST_VIDEO} — проверка QMediaPlayer пропущена")
        return True

    if SELFTEST_REPORT.exists():
        SELFTEST_REPORT.unlink()

    print("Запускаю самотест собранного exe...", flush=True)
    code = _run(
        [str(EXE), "--selftest", str(SELFTEST_REPORT), str(TEST_VIDEO)],
        timeout=180,
    )
    if not SELFTEST_REPORT.exists():
        print("Отчет самотеста не создан", flush=True)
        return False

    report = json.loads(SELFTEST_REPORT.read_text(encoding="utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return code == 0 and bool(report.get("ok"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка PyVideoMixer.exe")
    parser.add_argument("--no-clean", action="store_true", help="не удалять build/ и dist/")
    parser.add_argument("--no-selftest", action="store_true", help="не запускать exe после сборки")
    args = parser.parse_args()

    _ensure_pyinstaller()
    version_dir = _next_version()
    if not args.no_clean:
        _clean()
    _build(clean=not args.no_clean)
    _copy_guide()

    print("\nПроверка содержимого бандла:", flush=True)
    _verify_layout()
    print(f"Размер папки: {_human_size(APP_DIR)}")

    if not args.no_selftest:
        if not _selftest():
            print("\nСАМОТЕСТ НЕ ПРОЙДЕН")
            return 1
        print("\nСАМОТЕСТ ПРОЙДЕН")

    shutil.move(str(APP_DIR), str(version_dir))
    final_exe = version_dir / "PyVideoMixer.exe"
    print(f"\nГотово: {final_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
