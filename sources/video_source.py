"""Видеоисточник с воспроизведением в главном потоке и отдельным превью."""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink

from sources.base import BaseSource


class VideoSource(BaseSource):
    kind = "video"

    position_changed = Signal(int)
    duration_changed = Signal(int)
    playback_state_changed = Signal(bool)

    def __init__(self, name: str, path: str, loop: bool = True,
                 volume: float = 1.0, parent=None) -> None:
        super().__init__(name, path, parent)
        self.loop = bool(loop)
        self._duration_ms = 0
        self._is_playing = False
        self._user_paused = False
        self._high_res_transition = False
        self._volume = max(0.0, min(1.0, float(volume)))
        self._muted = False
        self._preview_interval = 1.0 / 12.0
        self._last_preview_time = 0.0
        self._full_frame: Optional[QImage] = None
        self._first_1080p_frame: Optional[QImage] = None
        self._last_video_frame: Optional[QVideoFrame] = None
        self._source_ready = False
        self._initializing_preview = True
        self._native_output_widget = None
        self._fade_audio_timer = QTimer(self)
        self._fade_audio_timer.setInterval(10)
        self._fade_audio_timer.timeout.connect(self._on_audio_fade_tick)
        self._current_audio_vol = 0.0
        self._target_audio_vol = 0.0
        self._audio_fade_step = 0.0
        self._pending_pause_after_fade = False
        self._pending_reset_after_fade = False

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.0)
        self._audio.setMuted(False)
        self._player.setAudioOutput(self._audio)
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_preview_frame)
        self._player.setVideoSink(self._sink)

        self._player.setLoops(QMediaPlayer.Loops.Infinite if self.loop else 1)
        self._audio.setVolume(0.0)
        self._audio.setMuted(False)
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.positionChanged.connect(self.position_changed.emit)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)

    @property
    def player(self) -> QMediaPlayer:
        return self._player

    def start(self) -> None:
        self._initializing_preview = True
        self._audio.setVolume(0.0)
        self._audio.setMuted(False)
        super().start()
        self._player.play()

    def stop(self) -> None:
        super().stop()
        self._player.stop()
        self._full_frame = None
        self._first_1080p_frame = None
        self._last_video_frame = None
        self._frame = None

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)
        loops = QMediaPlayer.Loops.Infinite if self.loop else 1
        self._player.setLoops(loops)

    def set_preview_fps(self, fps: int) -> None:
        self._preview_interval = 1.0 / max(1, int(fps))
        self._last_preview_time = 0.0

    def restore_video_sink(self) -> None:
        self.set_native_output(None)

    def set_native_output(self, widget) -> None:
        self._native_output_widget = widget

    def grab_full_frame(self) -> Optional[QImage]:
        if self._last_video_frame is not None and self._last_video_frame.isValid():
            image = self._last_video_frame.toImage()
            if not image.isNull():
                return image
        return self._full_frame.copy() if self._full_frame is not None else None

    def _start_audio_ramp(
        self, target_vol: float, duration_ms: int = 45,
        pause_when_done: bool = False,
    ) -> None:
        target_vol = max(0.0, min(1.0, float(target_vol)))
        ticks = max(1, duration_ms // 10)
        self._target_audio_vol = target_vol
        self._audio_fade_step = (target_vol - self._current_audio_vol) / ticks
        self._pending_pause_after_fade = pause_when_done
        self._fade_audio_timer.start()

    def _on_audio_fade_tick(self) -> None:
        delta = self._audio_fade_step
        remaining = self._target_audio_vol - self._current_audio_vol
        if abs(remaining) <= abs(delta) or abs(remaining) < 0.001:
            self._current_audio_vol = self._target_audio_vol
        else:
            self._current_audio_vol += delta
        self._audio.setVolume(self._current_audio_vol ** 2)
        if self._current_audio_vol == self._target_audio_vol:
            self._fade_audio_timer.stop()
            if self._target_audio_vol <= 0.001:
                self._audio.setVolume(0.0)
                self._audio.setMuted(False)
                if self._pending_pause_after_fade:
                    self._player.pause()
                    if self._pending_reset_after_fade:
                        self._player.setPosition(0)
                        self.position_changed.emit(0)
            self._pending_pause_after_fade = False
            self._pending_reset_after_fade = False

    def play(self) -> None:
        if (
            self._is_playing
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and not self._pending_pause_after_fade
        ):
            return
        self._user_paused = False
        self._is_playing = True
        self._pending_pause_after_fade = False
        self._pending_reset_after_fade = False
        self._fade_audio_timer.stop()
        if self._current_audio_vol != 0.0:
            self._current_audio_vol = 0.0
            self._audio.setVolume(0.0)
        self._audio.setMuted(False)
        self._player.play()
        if not self._muted:
            self._start_audio_ramp(self._volume, duration_ms=90)

    def pause(self, by_user: bool = False) -> None:
        if by_user:
            self._user_paused = True
        was_playing = self._is_playing
        self._is_playing = False
        if was_playing and not self._muted and self._current_audio_vol > 0.01:
            self._start_audio_ramp(0.0, duration_ms=40, pause_when_done=True)
            return
        self._fade_audio_timer.stop()
        self._player.pause()

    def toggle_play_pause(self) -> None:
        self.pause(by_user=True) if self._is_playing else self.play()

    def reset_to_start(self) -> None:
        was_user_paused = self._user_paused
        self._is_playing = False
        self._fade_audio_timer.stop()
        self._pending_pause_after_fade = False
        self._pending_reset_after_fade = False
        self._current_audio_vol = 0.0
        self._audio.setVolume(0.0)
        self._audio.setMuted(False)
        self._player.pause()
        self._player.setPosition(0)
        self._user_paused = was_user_paused
        self.position_changed.emit(0)

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def seek(self, position_ms: int) -> None:
        position_ms = max(0, int(position_ms))
        self._player.setPosition(position_ms)

    def set_high_res_transition(self, enabled: bool) -> None:
        self._high_res_transition = bool(enabled)
        if enabled:
            image = None
            if self._last_video_frame is not None and self._last_video_frame.isValid():
                image = self._last_video_frame.toImage()
            if image is None or image.isNull():
                image = self._first_1080p_frame
            if image is not None and not image.isNull():
                if image.width() > 1920 or image.height() > 1080:
                    scaled = image.scaled(
                        1920,
                        1080,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                else:
                    scaled = image
                self._full_frame = scaled
                self._publish(scaled)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if not self._fade_audio_timer.isActive():
            self._current_audio_vol = self._volume
            self._audio.setVolume(self._volume ** 2)

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._audio.setMuted(False)
        if self._muted:
            self._start_audio_ramp(0.0, duration_ms=45)
        elif self._is_playing:
            self._start_audio_ramp(self._volume, duration_ms=55)

    def info(self) -> str:
        suffix = " · loop" if self.loop else ""
        return f"{self.name}{suffix}"

    @Slot(QVideoFrame)
    def _on_preview_frame(self, frame: QVideoFrame) -> None:
        if self._native_output_widget is not None and frame.isValid():
            target_sink = self._native_output_widget.videoSink()
            if target_sink is not None:
                target_sink.setVideoFrame(frame)
        if getattr(self, "_closed", False) or (
            not self._active and not self._initializing_preview
        ):
            return
        if frame.isValid():
            self._last_video_frame = frame
        if frame.isValid() and self._initializing_preview:
            init_img = frame.toImage()
            if not init_img.isNull():
                first_frame = init_img
                if init_img.width() > 1920 or init_img.height() > 1080:
                    first_frame = init_img.scaled(
                        1920,
                        1080,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                self._first_1080p_frame = first_frame.copy()
                self._full_frame = first_frame
                self._player.pause()
                self._player.setPosition(0)
                self._initializing_preview = False
                self._publish(first_frame)
        now = time.monotonic()
        interval = (1.0 / 30.0) if self._high_res_transition else self._preview_interval
        if now - self._last_preview_time < interval:
            return
        self._last_preview_time = now
        image = frame.toImage()
        if image.isNull():
            return
        if self._high_res_transition:
            if image.width() > 1920 or image.height() > 1080:
                scaled = image.scaled(1920, 1080, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.FastTransformation)
                del image
            else:
                scaled = image
        else:
            scaled = image.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.FastTransformation)
            del image
        self._full_frame = scaled
        self._publish(scaled)

    @Slot(int)
    def _on_duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self._source_ready = True
        self.duration_changed.emit(self._duration_ms)

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state) -> None:
        self._is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.playback_state_changed.emit(self._is_playing)

    @Slot(QMediaPlayer.Error, str)
    def _on_player_error(self, _error, message: str) -> None:
        super()._on_error(message or "Ошибка воспроизведения видео")
