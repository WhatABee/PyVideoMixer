"""Управление внешним видеоплеером для резервного режима вывода."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QGuiApplication


class ExternalPlayer:
    """Запускает mpv/VLC на указанном экране и завершает старый процесс."""

    def __init__(self, executable: str = "", screen: int = 1) -> None:
        self.executable = executable
        self.screen = max(0, int(screen))
        self.process: Optional[subprocess.Popen] = None

    @staticmethod
    def discover() -> str:
        for name in ("mpv", "vlc", "mpv.exe", "vlc.exe"):
            found = shutil.which(name)
            if found:
                return found
        return ""

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def play(self, path: str, screen: Optional[object] = None) -> bool:
        self.stop()
        if not Path(path).is_file():
            return False
        executable = self.executable or self.discover()
        if not executable:
            if os.name == "nt":
                try:
                    os.startfile(path)
                    return True
                except OSError:
                    pass
            return False

        if screen is None:
            monitor = self.screen
        elif isinstance(screen, int):
            monitor = max(0, screen)
        else:
            screens = QGuiApplication.screens()
            try:
                monitor = screens.index(screen)
            except ValueError:
                try:
                    monitor = next(
                        index for index, candidate in enumerate(screens)
                        if candidate.name() == screen.name()
                    )
                except (AttributeError, StopIteration):
                    monitor = self.screen
            monitor = max(0, int(monitor))

        name = Path(executable).name.lower()
        if "vlc" in name:
            args = [
                executable,
                "--fullscreen",
                "--no-video-title-show",
                f"--qt-fullscreen-screennumber={monitor}",
                path,
            ]
        else:
            args = [
                executable,
                "--fs",
                "--no-border",
                "--ontop",
                f"--screen={monitor}",
                f"--geometry=100%:100%",
                path,
            ]
        try:
            self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.process = None
            return False
        return True

    def __del__(self) -> None:
        self.stop()
