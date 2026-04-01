"""
Audio Manager
Low-latency audio playback layer with continuous streaming.

We decode the game's vocal clips with SoundFile and feed them through a
sounddevice OutputStream so that playback starts instantly and restarts without
hiccups whenever the animation loops.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional, Tuple, Dict


_SOUNDDEVICE_IMPORT_ERROR: Optional[str] = None
_QTMULTIMEDIA_IMPORT_ERROR: Optional[str] = None

import numpy as np
import soundfile as sf
from PyQt6.QtCore import QObject, QCoreApplication, QThread, pyqtSignal, QUrl

try:
    import sounddevice as sd
except Exception as exc:  # pragma: no cover - missing PortAudio or backend issues
    sd = None
    _SOUNDDEVICE_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"


try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
except Exception as exc:  # pragma: no cover - multimedia plugin not available
    QMediaPlayer = None
    QAudioOutput = None
    _QTMULTIMEDIA_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"


class _TimeStretchWorker(QObject):
    finished = pyqtSignal(int, object, float, bool)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, audio: np.ndarray, speed: float,
                 ratio: float, resume: bool):
        super().__init__()
        self.request_id = request_id
        self.audio = audio
        self.speed = speed
        self.ratio = ratio
        self.resume = resume

    def run(self):
        result = AudioManager._time_stretch_audio_static(self.audio, self.speed)
        self.finished.emit(self.request_id, result, self.ratio, self.resume)


class AudioManager(QObject):
    """Manages monster audio playback with manual loop control."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._backend_kind: str = "sounddevice" if sd is not None else "none"
        self._backend_available: bool = sd is not None
        self._backend_error: Optional[str] = _SOUNDDEVICE_IMPORT_ERROR
        self._enabled: bool = self._backend_available
        self._current_file: Optional[str] = None

        self._output_device: Optional[int] = None
        self._forced_output_selector: str = str(os.getenv("MSM_AUDIO_DEVICE", "") or "").strip()
        self._qt_player: Optional[QMediaPlayer] = None
        self._qt_audio_output: Optional[QAudioOutput] = None

        self._audio_data: Optional[np.ndarray] = None  # Processed audio buffer
        self._source_audio_data: Optional[np.ndarray] = None  # Original audio buffer
        self._sample_rate: int = 44100
        self._channels: int = 1
        self._duration: float = 0.0
        self._active_duration: float = 0.0

        self._current_position: float = 0.0  # seconds
        self._playback_cursor: int = 0       # frame index
        self._play_active: bool = False

        self._volume: float = 0.8
        self._playback_speed: float = 1.0
        self._pitch_mode: str = "time_stretch"  # time_stretch, pitch_shift, chipmunk, pitch_down_locked
        self._pending_pitch_config: Optional[Tuple[float, str]] = None
        self._stretch_thread: Optional[QThread] = None
        self._stretch_worker: Optional[_TimeStretchWorker] = None
        self._stretch_request_id: int = 0

        self._stream: Optional[sd.OutputStream] = None
        self._stream_lock = threading.RLock()
        self._time_stretch_ratio: float = 1.0

        if self._backend_available:
            forced_device = self._resolve_forced_output_device(
                self._forced_output_selector,
                sample_rate=self._sample_rate,
                channels=self._channels,
            )
            if forced_device is not None:
                self._output_device = forced_device
            else:
                self._output_device = self._resolve_compatible_output_device(
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                )
            if self._output_device is None:
                self._backend_error = "No compatible audio output device detected for sounddevice backend"
                self._backend_available = False
                self._enabled = False
                self._backend_kind = "none"

        if not self._backend_available:
            self._init_qt_backend()

    # ------------------------------------------------------------------ #
    # Properties                                                         #
    # ------------------------------------------------------------------ #
    @property
    def backend_available(self) -> bool:
        return bool(self._backend_available and self._backend_kind in ("sounddevice", "qt"))

    @property
    def backend_error(self) -> str:
        return str(self._backend_error or "")

    @property
    def backend_kind(self) -> str:
        return str(self._backend_kind or "none")

    @property
    def backend_summary(self) -> str:
        parts = [
            f"kind={self._backend_kind}",
            f"available={self._backend_available}",
            f"enabled={self._enabled}",
        ]
        if self._backend_kind == "sounddevice" and sd is not None:
            try:
                parts.append(f"default_device={sd.default.device}")
            except Exception:
                pass
            if self._forced_output_selector:
                parts.append(f"forced_selector={self._forced_output_selector}")
            if self._output_device is not None:
                parts.append(f"selected_device={self._output_device}")
                name = self._describe_output_device(self._output_device)
                if name:
                    parts.append(f"selected_name={name}")
        elif self._backend_kind == "qt":
            parts.append("selected_device=QtMultimedia")
        if self._backend_error:
            parts.append(f"error={self._backend_error}")
        return ", ".join(parts)

    @property
    def current_file(self) -> Optional[str]:
        return self._current_file

    @property
    def is_ready(self) -> bool:
        if self._audio_data is not None:
            return True
        return bool(self._backend_kind == "qt" and self._current_file)

    @property
    def duration(self) -> float:
        """Length of the currently loaded clip in seconds."""
        return self._duration

    @property
    def current_position(self) -> float:
        """Current playback position in seconds."""
        with self._stream_lock:
            return float(self._current_position or 0.0)

    @property
    def active_duration(self) -> float:
        """Length of the non-silent portion of the clip in seconds."""
        if self._active_duration > 0.0:
            return self._active_duration
        return self._duration

    # ------------------------------------------------------------------ #
    # Public controls                                                    #
    # ------------------------------------------------------------------ #
    def set_enabled(self, enabled: bool):
        if enabled and not self.backend_available:
            if not self._init_qt_backend():
                enabled = False
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if not enabled:
            self._stop_playback()
            self._close_stream()

    def load_file(self, file_path: str) -> bool:
        """
        Load an audio clip and prepare it for playback.
        """
        if not file_path or not os.path.exists(file_path):
            self._reset_state()
            return False

        decoded_audio: Optional[np.ndarray] = None
        sample_rate = 0
        decode_error: Optional[str] = None
        try:
            data, sample_rate = sf.read(file_path, always_2d=True, dtype="float32")
            decoded_audio = np.ascontiguousarray(data, dtype=np.float32)
        except Exception as exc:
            decode_error = f"Audio decode failed: {exc.__class__.__name__}: {exc}"

        reuse_stream = (
            self._backend_kind == "sounddevice"
            and self._stream is not None
            and decoded_audio is not None
            and self._sample_rate == sample_rate
            and self._channels == decoded_audio.shape[1]
        )
        self._stop_playback()
        if self._backend_kind == "sounddevice" and not reuse_stream:
            self._close_stream()

        if decoded_audio is not None and sample_rate > 0:
            self._audio_data = decoded_audio
            # Keep a shared reference to avoid heavy copies on clip switches.
            # We never mutate the source buffer in-place, so this is safe.
            self._source_audio_data = self._audio_data
            self._sample_rate = int(sample_rate)
            self._channels = decoded_audio.shape[1]
            self._duration = len(decoded_audio) / sample_rate if sample_rate > 0 else 0.0
            self._active_duration = self._detect_active_duration(self._audio_data, sample_rate)
        else:
            self._audio_data = None
            self._source_audio_data = None
            self._duration = 0.0
            self._active_duration = 0.0

        self._current_file = file_path
        self._time_stretch_ratio = 1.0
        self._pitch_mode = "time_stretch"
        self._playback_speed = 1.0

        qt_loaded = False
        if self._backend_kind == "qt" or (not self._backend_available and self._init_qt_backend()):
            if self._qt_player is not None:
                try:
                    self._qt_player.stop()
                    self._qt_player.setSource(QUrl.fromLocalFile(os.path.abspath(file_path)))
                    if self._qt_audio_output is not None:
                        self._qt_audio_output.setVolume(self._volume)
                    self._backend_error = None
                    qt_loaded = True
                except Exception as exc:
                    self._backend_error = f"Qt audio failed to load source: {exc.__class__.__name__}: {exc}"
                    qt_loaded = False

        self._set_position(0.0)

        if decoded_audio is None and not qt_loaded:
            if decode_error:
                self._backend_error = decode_error
            return False

        if decode_error and qt_loaded:
            # Keep decode failure details for exports while allowing playback via Qt.
            self._backend_error = decode_error

        return True

    @staticmethod
    def build_cache_entry(file_path: str) -> Optional[Tuple[np.ndarray, int, float]]:
        """Load audio data from disk for reuse without re-decoding."""
        if not file_path or not os.path.exists(file_path):
            return None
        try:
            data, sample_rate = sf.read(file_path, always_2d=True, dtype="float32")
        except Exception:
            return None
        audio = np.ascontiguousarray(data, dtype=np.float32)
        active_duration = AudioManager._detect_active_duration(audio, sample_rate)
        return (audio, int(sample_rate), float(active_duration))

    def load_cache_entry(self, entry: Tuple[np.ndarray, int, float], *, file_path: Optional[str] = None) -> bool:
        """Load audio from a pre-decoded cache entry."""
        if not entry:
            self._reset_state()
            return False
        audio, sample_rate, active_duration = entry
        if audio is None or sample_rate <= 0:
            self._reset_state()
            return False

        reuse_stream = (
            self._stream is not None
            and self._sample_rate == int(sample_rate)
            and self._channels == audio.shape[1]
        )
        self._stop_playback()
        if not reuse_stream:
            self._close_stream()

        self._audio_data = np.ascontiguousarray(audio, dtype=np.float32)
        # Keep a shared reference to avoid heavy copies on clip switches.
        self._source_audio_data = self._audio_data
        self._sample_rate = int(sample_rate)
        self._channels = audio.shape[1]
        self._duration = len(audio) / self._sample_rate if self._sample_rate > 0 else 0.0
        self._active_duration = float(active_duration or 0.0)
        self._current_file = file_path
        self._time_stretch_ratio = 1.0
        self._pitch_mode = "time_stretch"
        self._playback_speed = 1.0
        self._set_position(0.0)
        return True

    def play(self, start_time: Optional[float] = None):
        """Begin playback, optionally seeking first."""
        if not self._enabled or not self.is_ready:
            return

        if start_time is not None:
            self._set_position(start_time)

        if self._current_position >= self._duration:
            self._set_position(0.0)

        self._start_playback()

    def pause(self):
        """Pause playback but retain current position."""
        if not self.is_ready:
            return
        self._stop_playback()

    def stop(self):
        """Stop playback and rewind to start."""
        if not self.is_ready:
            return
        self._stop_playback()
        self._set_position(0.0)

    def restart(self):
        """Restart playback from the beginning."""
        if not self.is_ready:
            return
        self._stop_playback()
        self._set_position(0.0)
        if self._enabled:
            self._start_playback()

    def seek(self, seconds: float):
        """Seek to a new timestamp."""
        if not self.is_ready:
            return
        was_playing = self.is_playing()
        self._stop_playback()
        self._set_position(seconds)
        if was_playing and self._enabled:
            self._start_playback()

    def set_volume(self, volume_percent: int):
        """Adjust playback volume (0-100)."""
        clamped = max(0, min(100, volume_percent))
        with self._stream_lock:
            self._volume = clamped / 100.0
            stream = self._stream
            qt_output = self._qt_audio_output
        if stream:
            try:
                stream.write_available  # noop, ensures stream exists
            except Exception:
                pass
        if qt_output is not None:
            try:
                qt_output.setVolume(self._volume)
            except Exception:
                pass

    def is_playing(self) -> bool:
        if self._backend_kind == "qt" and self._qt_player is not None and QMediaPlayer is not None:
            try:
                return self._qt_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            except Exception:
                pass
        return self._play_active

    def configure_playback(self, speed: float, pitch_mode: str):
        """
        Apply tempo and pitch settings.

        Args:
            speed: Tempo multiplier relative to original audio.
            pitch_mode: 'time_stretch', 'pitch_shift', 'chipmunk', or 'pitch_down_locked'.
        """
        clamped_speed = max(0.01, float(speed))
        if pitch_mode not in ("time_stretch", "pitch_shift", "chipmunk", "pitch_down_locked"):
            pitch_mode = "time_stretch"

        if self._backend_kind == "qt" and self._qt_player is not None:
            with self._stream_lock:
                self._playback_speed = clamped_speed
                self._pitch_mode = pitch_mode
            try:
                # QtMultimedia cannot apply independent pitch shift while tempo remains fixed.
                # For pitch_down_locked, keep tempo normal as the best fallback.
                qt_rate = 1.0 if pitch_mode == "pitch_down_locked" else clamped_speed
                self._qt_player.setPlaybackRate(qt_rate)
                self._backend_error = None
            except Exception as exc:
                self._backend_error = f"Qt audio playback-rate change failed: {exc.__class__.__name__}: {exc}"
            self._pending_pitch_config = None
            return

        self._pending_pitch_config = (clamped_speed, pitch_mode)
        with self._stream_lock:
            source = self._source_audio_data
            current_time = self._current_position
            was_playing = self._play_active
            current_duration = self._duration
            if pitch_mode == "time_stretch" and self._pitch_mode == "time_stretch":
                effective_speed = self._time_stretch_ratio
            elif pitch_mode == self._pitch_mode:
                effective_speed = self._playback_speed
            else:
                effective_speed = None
            no_change = (
                source is not None
                and effective_speed is not None
                and abs(clamped_speed - effective_speed) < 1e-6
            )
        if source is None or no_change:
            self._pending_pitch_config = None
            return

        if pitch_mode == "time_stretch":
            self._start_time_stretch(source, clamped_speed, current_time, current_duration, was_playing)
        elif pitch_mode == "pitch_down_locked":
            self._apply_pitch_down_locked_mode(source, clamped_speed, current_time, was_playing)
        else:
            self._apply_pitch_mode(source, clamped_speed, pitch_mode, current_time, was_playing)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    def _reset_state(self):
        self._stop_playback()
        self._close_stream()
        self._cancel_stretch_worker()
        if self._qt_player is not None:
            try:
                self._qt_player.stop()
                self._qt_player.setSource(QUrl())
            except Exception:
                pass
        self._audio_data = None
        self._source_audio_data = None
        self._current_file = None
        self._duration = 0.0
        self._active_duration = 0.0
        self._current_position = 0.0
        self._playback_cursor = 0
        self._time_stretch_ratio = 1.0
        self._playback_speed = 1.0
        self._pitch_mode = "time_stretch"

    def _set_position(self, seconds: float):
        target = self._clamp_time(seconds)
        with self._stream_lock:
            self._current_position = target
            if self._audio_data is not None and self._sample_rate > 0:
                frame = min(int(target * self._sample_rate), len(self._audio_data))
            else:
                frame = 0
            self._playback_cursor = frame
        if self._backend_kind == "qt" and self._qt_player is not None:
            try:
                self._qt_player.setPosition(max(0, int(round(target * 1000.0))))
            except Exception:
                pass

    def _start_playback(self):
        if not self.backend_available:
            with self._stream_lock:
                self._play_active = False
            return

        if self._backend_kind == "qt":
            with self._stream_lock:
                self._play_active = True
            if self._qt_player is not None and self._enabled:
                try:
                    self._qt_player.play()
                    self._backend_error = None
                except Exception as exc:
                    with self._stream_lock:
                        self._play_active = False
                    self._backend_error = f"Qt audio playback failed: {exc.__class__.__name__}: {exc}"
            return

        with self._stream_lock:
            if self._audio_data is not None:
                self._playback_cursor = min(self._playback_cursor, len(self._audio_data))
                self._play_active = True
        self._ensure_stream()

    def _stop_playback(self):
        if self._backend_kind == "qt" and self._qt_player is not None:
            try:
                self._qt_player.pause()
            except Exception:
                pass
        with self._stream_lock:
            self._play_active = False

    def _ensure_stream(self):
        if self._backend_kind != "sounddevice" or not self._backend_available or sd is None:
            return

        if self._stream is None and self.is_ready:
            stream_kwargs = {
                "samplerate": self._sample_rate,
                "channels": self._channels,
                "dtype": "float32",
                "blocksize": 1024,
                "latency": "high",
                "callback": self._stream_callback,
            }
            if self._output_device is not None:
                stream_kwargs["device"] = self._output_device
            try:
                self._stream = sd.OutputStream(**stream_kwargs)
                self._stream.start()
                self._backend_error = None
                return
            except Exception as exc:
                self._close_stream()
                fallback_device = self._resolve_compatible_output_device(
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                )
                if fallback_device is not None:
                    try:
                        self._output_device = fallback_device
                        stream_kwargs["device"] = fallback_device
                        self._stream = sd.OutputStream(**stream_kwargs)
                        self._stream.start()
                        self._backend_error = None
                        return
                    except Exception as retry_exc:
                        reason = (
                            "Failed to open audio output stream on Linux: "
                            f"{retry_exc.__class__.__name__}: {retry_exc}"
                        )
                else:
                    reason = (
                        "Failed to open audio output stream on Linux: "
                        f"{exc.__class__.__name__}: {exc}"
                    )
                if self._promote_qt_fallback(reason):
                    if self._enabled and self._current_file:
                        self._start_playback()
                    return
                return

        if self._stream and not self._stream.active:
            try:
                self._stream.start()
                self._backend_error = None
            except Exception as exc:
                reason = (
                    "Audio stream stopped unexpectedly and could not restart: "
                    f"{exc.__class__.__name__}: {exc}"
                )
                if self._promote_qt_fallback(reason):
                    if self._enabled and self._current_file:
                        self._start_playback()
                    return
                self._close_stream()

    def _promote_qt_fallback(self, reason: str) -> bool:
        """Switch to Qt multimedia backend if sounddevice fails at runtime."""
        self._backend_error = str(reason or "sounddevice backend failed")
        self._backend_available = False
        self._backend_kind = "none"
        self._close_stream()
        if self._init_qt_backend():
            return True
        self._enabled = False
        return False

    def _resolve_compatible_output_device(self, sample_rate: int, channels: int) -> Optional[int]:
        """Find an output device that can play the requested format."""
        if sd is None:
            return None

        candidates = []

        def _add_candidate(raw_idx) -> None:
            try:
                idx = int(raw_idx)
            except Exception:
                return
            if idx < 0 or idx in candidates:
                return
            candidates.append(idx)

        try:
            device_list = sd.query_devices()
        except Exception as exc:
            self._backend_error = f"Unable to query output devices: {exc.__class__.__name__}: {exc}"
            return None

        # On Linux desktops, route through the mixer first to avoid silent HDMI/hw sinks.
        preferred_tokens = ("pipewire", "pulse", "default", "sysdefault")
        for token in preferred_tokens:
            for idx, dev in enumerate(device_list):
                try:
                    max_out = int(dev.get("max_output_channels", 0) or 0)
                except Exception:
                    max_out = 0
                if max_out <= 0:
                    continue
                name = str(dev.get("name", "") or "").lower()
                if token in name:
                    _add_candidate(idx)

        try:
            default_device = sd.default.device
            if isinstance(default_device, (list, tuple)) and len(default_device) >= 2:
                default_output = default_device[1]
            else:
                default_output = default_device
            _add_candidate(default_output)
        except Exception:
            pass

        for idx, dev in enumerate(device_list):
            try:
                max_out = int(dev.get("max_output_channels", 0) or 0)
            except Exception:
                max_out = 0
            if max_out > 0:
                _add_candidate(idx)

        req_channels = max(1, int(channels or 1))
        req_rate = int(sample_rate or 0)

        for idx in candidates:
            try:
                info = sd.query_devices(idx, "output")
            except Exception:
                continue
            max_out = int(info.get("max_output_channels", 0) or 0)
            if max_out <= 0:
                continue
            test_channels = min(req_channels, max_out)
            try:
                if req_rate > 0:
                    sd.check_output_settings(device=idx, channels=test_channels, samplerate=req_rate)
                else:
                    sd.check_output_settings(device=idx, channels=test_channels)
                return int(idx)
            except Exception:
                # Fallback to the device's preferred/default sample rate if needed.
                try:
                    sd.check_output_settings(device=idx, channels=test_channels)
                    return int(idx)
                except Exception:
                    continue

        return None

    def _describe_output_device(self, device_index: int) -> str:
        """Return a readable output device name for diagnostics logs."""
        if sd is None:
            return ""
        try:
            info = sd.query_devices(int(device_index), "output")
        except Exception:
            return ""
        name = str(info.get("name", "") or "").strip()
        return name

    def _resolve_forced_output_device(self, selector: str, sample_rate: int, channels: int) -> Optional[int]:
        """Resolve a forced output device by index or name token (MSM_AUDIO_DEVICE)."""
        if sd is None:
            return None
        token = str(selector or "").strip()
        if not token:
            return None

        try:
            devices = sd.query_devices()
        except Exception as exc:
            self._backend_error = f"Unable to query output devices: {exc.__class__.__name__}: {exc}"
            return None

        req_channels = max(1, int(channels or 1))
        req_rate = int(sample_rate or 0)

        def _supports(idx: int) -> bool:
            try:
                info = sd.query_devices(idx, "output")
            except Exception:
                return False
            max_out = int(info.get("max_output_channels", 0) or 0)
            if max_out <= 0:
                return False
            test_channels = min(req_channels, max_out)
            try:
                if req_rate > 0:
                    sd.check_output_settings(device=idx, channels=test_channels, samplerate=req_rate)
                else:
                    sd.check_output_settings(device=idx, channels=test_channels)
                return True
            except Exception:
                try:
                    sd.check_output_settings(device=idx, channels=test_channels)
                    return True
                except Exception:
                    return False

        try:
            idx = int(token)
        except Exception:
            idx = None
        if idx is not None:
            if _supports(idx):
                return idx
            self._backend_error = f"Forced output device index {idx} is not compatible."
            return None

        lowered = token.lower()
        for i, dev in enumerate(devices):
            try:
                max_out = int(dev.get("max_output_channels", 0) or 0)
            except Exception:
                max_out = 0
            if max_out <= 0:
                continue
            name = str(dev.get("name", "") or "").lower()
            if lowered in name and _supports(i):
                return i

        self._backend_error = f"Forced output device '{token}' did not match a compatible output sink."
        return None

    def _init_qt_backend(self) -> bool:
        """Initialize Qt multimedia as a fallback playback backend."""
        if self._qt_player is not None and self._qt_audio_output is not None and self._backend_kind == "qt":
            self._backend_available = True
            return True

        if QMediaPlayer is None or QAudioOutput is None:
            qt_msg = "QtMultimedia is not available in this PyQt build"
            if _QTMULTIMEDIA_IMPORT_ERROR:
                qt_msg = f"{qt_msg}: {_QTMULTIMEDIA_IMPORT_ERROR}"
            if self._backend_error:
                self._backend_error = f"{self._backend_error}; {qt_msg}"
            else:
                self._backend_error = qt_msg
            self._backend_kind = "none"
            self._backend_available = False
            return False

        try:
            audio_out = QAudioOutput(self)
            audio_out.setVolume(self._volume)
            player = QMediaPlayer(self)
            player.setAudioOutput(audio_out)
            player.positionChanged.connect(self._on_qt_position_changed)
            player.playbackStateChanged.connect(self._on_qt_playback_state_changed)
        except Exception as exc:
            self._qt_player = None
            self._qt_audio_output = None
            self._backend_kind = "none"
            self._backend_available = False
            self._backend_error = f"QtMultimedia backend initialization failed: {exc.__class__.__name__}: {exc}"
            return False

        self._qt_audio_output = audio_out
        self._qt_player = player
        self._backend_kind = "qt"
        self._backend_available = True
        self._backend_error = None

        if self._current_file:
            try:
                self._qt_player.setSource(QUrl.fromLocalFile(os.path.abspath(self._current_file)))
            except Exception:
                pass

        return True

    def _on_qt_position_changed(self, position_ms: int):
        seconds = max(0.0, float(position_ms) / 1000.0)
        with self._stream_lock:
            self._current_position = self._clamp_time(seconds)
            if self._audio_data is not None and self._sample_rate > 0:
                self._playback_cursor = min(int(self._current_position * self._sample_rate), len(self._audio_data))

    def _on_qt_playback_state_changed(self, state):
        if QMediaPlayer is None:
            return
        with self._stream_lock:
            self._play_active = state == QMediaPlayer.PlaybackState.PlayingState
        if self._qt_player is not None and self._duration <= 0.0:
            try:
                media_duration = float(self._qt_player.duration()) / 1000.0
            except Exception:
                media_duration = 0.0
            if media_duration > 0.0:
                self._duration = media_duration
                if self._active_duration <= 0.0:
                    self._active_duration = media_duration

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._backend_kind == "qt" and self._qt_player is not None:
            try:
                self._qt_player.stop()
            except Exception:
                pass

    def _stream_callback(self, outdata, frames, time_info, status):
        with self._stream_lock:
            audio = self._audio_data
            if (
                not self._enabled
                or not self._play_active
                or audio is None
                or self._playback_cursor >= len(audio)
            ):
                outdata.fill(0.0)
                if (
                    self._play_active
                    and audio is not None
                    and self._playback_cursor >= len(audio)
                ):
                    self._play_active = False
                    self._current_position = self._duration
                return

            start = self._playback_cursor
            mode = self._pitch_mode

            if mode in ("time_stretch", "pitch_down_locked"):
                end = min(start + frames, len(audio))
                chunk = audio[start:end]
                produced = chunk.shape[0]
                if produced > 0:
                    if self._volume != 1.0:
                        np.multiply(chunk, self._volume, out=outdata[:produced])
                    else:
                        outdata[:produced] = chunk
                    if produced < frames:
                        outdata[produced:frames] = 0.0
                else:
                    outdata.fill(0.0)
                self._playback_cursor = end
                if self._sample_rate > 0:
                    self._current_position = min(
                        self._playback_cursor / self._sample_rate,
                        self._duration,
                    )
                if self._playback_cursor >= len(audio):
                    self._play_active = False
                    self._current_position = self._duration
                return

            speed = max(0.01, self._playback_speed)
            source = self._source_audio_data if self._source_audio_data is not None else audio
            if mode == "pitch_shift":
                processed, consumed = self._render_pitch_shift_chunk(source, start, frames, speed)
            else:
                processed, consumed = self._render_chipmunk_chunk(source, start, frames, speed)

            produced = processed.shape[0]
            if produced < frames:
                outdata.fill(0.0)
            if produced > 0:
                length = min(frames, produced)
                if self._volume != 1.0:
                    np.multiply(processed[:length], self._volume, out=outdata[:length])
                else:
                    outdata[:length] = processed[:length]
                np.clip(outdata[:length], -1.0, 1.0, out=outdata[:length])
                if length < frames:
                    outdata[length:] = 0.0
            else:
                outdata.fill(0.0)

            if consumed <= 0 or start >= len(audio):
                self._play_active = False
                self._playback_cursor = len(audio)
                self._current_position = self._duration
                return

            self._playback_cursor = min(start + consumed, len(audio))
            if self._playback_cursor >= len(audio):
                self._play_active = False
                self._current_position = self._duration
            elif self._sample_rate > 0:
                self._current_position = min(
                    self._playback_cursor / self._sample_rate,
                    self._duration,
                )

    def _clamp_time(self, value: float) -> float:
        if self._duration <= 0.0:
            return 0.0
        return max(0.0, min(self._duration, value))

    def export_audio_segment(
        self,
        duration: float,
        *,
        speed: Optional[float] = None,
        pitch_mode: Optional[str] = None,
    ) -> Optional[Tuple[np.ndarray, int]]:
        """
        Return audio rendered for the requested duration using the current tempo/pitch settings.

        Args:
            duration: Length in seconds to render.
            speed: Optional override for playback speed multiplier.
            pitch_mode: Optional override for pitch handling
                ('time_stretch', 'pitch_shift', 'chipmunk', 'pitch_down_locked').
        """
        if not self.is_ready or duration <= 0.0:
            return None

        frames_needed = int(duration * self._sample_rate)
        if frames_needed <= 0:
            return None

        mode = pitch_mode or self._pitch_mode or "time_stretch"
        if mode not in ("time_stretch", "pitch_shift", "chipmunk", "pitch_down_locked"):
            mode = "time_stretch"

        if mode in ("time_stretch", "pitch_down_locked"):
            audio = self._audio_data
            if audio is None:
                return None
            if frames_needed <= len(audio):
                segment = audio[:frames_needed]
            else:
                pad_length = frames_needed - len(audio)
                pad = np.zeros((pad_length, self._channels), dtype=audio.dtype)
                segment = np.concatenate((audio, pad), axis=0)
            return segment.copy(), self._sample_rate

        source = self._source_audio_data if self._source_audio_data is not None else self._audio_data
        if source is None:
            return None
        effective_speed = max(0.01, float(speed if speed is not None else self._playback_speed))
        if mode == "pitch_shift":
            samples, _ = self._render_pitch_shift_chunk(source, 0, frames_needed, effective_speed)
        else:
            samples, _ = self._render_chipmunk_chunk(source, 0, frames_needed, effective_speed)
        return samples.copy(), self._sample_rate

    def _apply_pitch_mode(self, source: np.ndarray, speed: float, mode: str, seek_time: float, was_playing: bool):
        """Apply realtime resampling pitch modes."""
        if was_playing:
            self._stop_playback()
        with self._stream_lock:
            self._audio_data = source
            self._duration = len(source) / self._sample_rate if self._sample_rate > 0 else 0.0
            self._pitch_mode = mode
            self._playback_speed = speed
            if mode == "time_stretch":
                self._time_stretch_ratio = speed
            else:
                self._time_stretch_ratio = 1.0
            self._set_position(seek_time)
        if self._pending_pitch_config and self._pending_pitch_config[1] == mode:
            self._pending_pitch_config = None
        if was_playing and self._enabled:
            self._start_playback()

    def _apply_pitch_down_locked_mode(
        self,
        source: np.ndarray,
        pitch_factor: float,
        seek_time: float,
        was_playing: bool,
    ):
        """Apply a pitch-down effect while keeping playback tempo unchanged."""
        if was_playing:
            self._stop_playback()
        processed = self._pitch_shift_locked_audio_static(source, pitch_factor)
        with self._stream_lock:
            self._audio_data = processed
            self._duration = len(processed) / self._sample_rate if self._sample_rate > 0 else 0.0
            self._pitch_mode = "pitch_down_locked"
            self._playback_speed = max(0.01, float(pitch_factor))
            self._time_stretch_ratio = 1.0
            self._set_position(seek_time)
        if self._pending_pitch_config and self._pending_pitch_config[1] == "pitch_down_locked":
            self._pending_pitch_config = None
        if was_playing and self._enabled:
            self._start_playback()

    def _start_time_stretch(self, source: np.ndarray, speed: float, current_time: float,
                            current_duration: float, was_playing: bool):
        """Start asynchronous time-stretch processing."""
        if abs(speed - 1.0) < 1e-6:
            self._apply_pitch_mode(source, 1.0, "time_stretch", current_time, was_playing)
            return

        self._cancel_stretch_worker()
        ratio = current_time / current_duration if current_duration > 1e-6 else 0.0
        new_request_id = self._stretch_request_id + 1
        self._stretch_request_id = new_request_id
        self._time_stretch_ratio = speed
        self._playback_speed = speed

        worker = _TimeStretchWorker(new_request_id, source.copy(), speed, ratio, was_playing)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_stretch_finished)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(self._on_stretch_failed)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._stretch_worker = worker
        self._stretch_thread = thread
        thread.start()

    def _cancel_stretch_worker(self):
        thread = self._stretch_thread
        worker = self._stretch_worker
        self._stretch_thread = None
        self._stretch_worker = None
        if thread:
            thread.quit()
            thread.wait()
        if worker:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def _on_stretch_finished(self, request_id: int, data_obj, ratio: float, resume: bool):
        if request_id != self._stretch_request_id:
            return
        stretched = np.ascontiguousarray(data_obj, dtype=np.float32)
        if resume:
            self._stop_playback()
        with self._stream_lock:
            self._audio_data = stretched
            self._duration = len(stretched) / self._sample_rate if self._sample_rate > 0 else 0.0
            self._pitch_mode = "time_stretch"
            self._playback_speed = self._time_stretch_ratio
            new_time = ratio * self._duration
            self._set_position(new_time)
        self._pending_pitch_config = None
        if resume and self._enabled:
            self._start_playback()
        self._cancel_stretch_worker()

    def _on_stretch_failed(self, request_id: int, message: str):
        if request_id != self._stretch_request_id:
            return
        print(f"Time-stretch failed: {message}")
        self._pending_pitch_config = None
        self._time_stretch_ratio = 1.0
        self._cancel_stretch_worker()

    def _render_pitch_shift_chunk(
        self,
        audio: np.ndarray,
        start: int,
        frames: int,
        speed: float
    ) -> Tuple[np.ndarray, int]:
        """Produce audio samples when pitch should follow speed changes."""
        if frames <= 0 or start >= len(audio):
            return np.zeros((0, self._channels), dtype=np.float32), 0

        positions = start + np.arange(frames, dtype=np.float32) * speed
        lower = np.floor(positions).astype(int)
        upper = lower + 1
        valid_mask = lower < len(audio)

        lower = np.clip(lower, 0, len(audio) - 1)
        upper = np.clip(upper, 0, len(audio) - 1)
        fractions = (positions - lower).astype(np.float32)

        samples = (audio[lower] * (1.0 - fractions)[:, None]) + (audio[upper] * fractions[:, None])
        if not np.all(valid_mask):
            samples[~valid_mask] = 0.0

        consumed = int(np.ceil(frames * speed))
        consumed = max(1, min(consumed, len(audio) - start))
        return samples.astype(np.float32, copy=False), consumed

    def _render_chipmunk_chunk(
        self,
        audio: np.ndarray,
        start: int,
        frames: int,
        speed: float
    ) -> Tuple[np.ndarray, int]:
        """
        Generate an exaggerated pitch-shifted sound (helium/chipmunk effect) while
        keeping tempo synced to BPM. We apply a pitch shift and then re-map the
        playback cursor to the normal tempo progression.
        """
        if frames <= 0 or start >= len(audio):
            return np.zeros((0, self._channels), dtype=np.float32), 0

        extra = 1.35
        effective_speed = speed * extra if speed >= 1.0 else speed / extra
        samples, consumed_eff = self._render_pitch_shift_chunk(audio, start, frames, effective_speed)

        consumed = int(np.ceil(frames * speed))
        consumed = max(1, min(consumed, len(audio) - start))
        return samples, consumed

    @staticmethod
    def _detect_active_duration(audio: np.ndarray, sample_rate: int) -> float:
        """Return the trailing-trimmed duration based on a low amplitude threshold."""
        if audio is None or sample_rate <= 0 or len(audio) == 0:
            return 0.0
        threshold = 1e-3
        magnitude = np.max(np.abs(audio), axis=1)
        indices = np.where(magnitude > threshold)[0]
        if indices.size == 0:
            return len(audio) / sample_rate
        last_index = int(indices[-1])
        return (last_index + 1) / sample_rate

    @staticmethod
    def _time_stretch_audio_static(audio: np.ndarray, speed: float) -> np.ndarray:
        """Resize audio length using linear interpolation to preserve pitch."""
        if speed <= 0:
            speed = 0.01
        if abs(speed - 1.0) < 1e-6:
            return audio.copy()

        frame = 2048
        hop_a = frame // 2
        hop_s = max(1, int(round(hop_a / speed)))
        window = np.hanning(frame).astype(np.float32)
        channels = audio.shape[1]

        estimated_len = int(len(audio) / speed) + frame * 2
        output = np.zeros((estimated_len, channels), dtype=np.float32)
        window_norm = np.zeros(estimated_len, dtype=np.float32)

        pos = 0
        write = 0
        while pos < len(audio) and write < estimated_len - frame:
            chunk = audio[pos:pos + frame]
            if chunk.shape[0] < frame:
                pad = np.zeros((frame, channels), dtype=np.float32)
                pad[:chunk.shape[0]] = chunk
                chunk = pad
            windowed = chunk * window[:, None]
            output[write:write + frame] += windowed
            window_norm[write:write + frame] += window
            pos += hop_a
            write += hop_s

        nonzero = window_norm > 1e-3
        output[nonzero] /= window_norm[nonzero][:, None]
        max_index = np.where(window_norm > 0)[0]
        if max_index.size > 0:
            last_index = min(len(output), max_index[-1] + frame)
            output = output[:last_index]
        return output

    @staticmethod
    def _pitch_shift_locked_audio_static(audio: np.ndarray, pitch_factor: float) -> np.ndarray:
        """
        Lower/raise pitch while keeping overall playback duration close to original.

        This uses a two-stage process:
          1) resample by pitch_factor (changes pitch + tempo),
          2) time-stretch back by inverse factor (restores tempo, keeps shifted pitch).
        """
        if audio is None or len(audio) == 0:
            return np.zeros((0, 1), dtype=np.float32)
        try:
            factor = float(pitch_factor)
        except (TypeError, ValueError):
            factor = 1.0
        factor = max(0.25, min(4.0, factor))
        if abs(factor - 1.0) < 1e-6:
            return audio.copy()

        target_len = max(1, int(round(len(audio) / factor)))
        resampled = AudioManager._resample_chunk(audio, target_len)
        compensated = AudioManager._time_stretch_audio_static(resampled, 1.0 / factor)
        if compensated.ndim != 2:
            compensated = np.asarray(compensated, dtype=np.float32).reshape((-1, audio.shape[1]))
        if len(compensated) != len(audio):
            compensated = AudioManager._resample_chunk(compensated, len(audio))
        return np.ascontiguousarray(compensated, dtype=np.float32)

    @staticmethod
    def _resample_chunk(chunk: np.ndarray, new_length: int) -> np.ndarray:
        """Simple linear resampler to preserve pitch."""
        if new_length <= 0:
            return np.zeros((0, chunk.shape[1]), dtype=np.float32)
        if len(chunk) == 0:
            return np.zeros((new_length, chunk.shape[1]), dtype=np.float32)
        if len(chunk) == 1:
            return np.repeat(chunk, new_length, axis=0)
        positions = np.linspace(0, len(chunk) - 1, new_length)
        idx_lower = np.floor(positions).astype(int)
        idx_upper = np.clip(idx_lower + 1, 0, len(chunk) - 1)
        frac = (positions - idx_lower)[:, None]
        resampled = chunk[idx_lower] * (1.0 - frac) + chunk[idx_upper] * frac
        return resampled.astype(np.float32, copy=False)

    def clear(self):
        """Fully reset and release any loaded audio."""
        self._reset_state()


@dataclass
class _MixVoice:
    audio: np.ndarray
    sample_rate: int
    channels: int
    cursor: int
    active: bool


class MultiTrackAudioMixer(QObject):
    """Mix multiple audio clips into a single output stream."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._backend_available: bool = sd is not None
        self._backend_error: Optional[str] = _SOUNDDEVICE_IMPORT_ERROR
        self._enabled: bool = self._backend_available
        self._volume: float = 0.8
        self._playing: bool = False
        self._voices: Dict[object, _MixVoice] = {}
        self._stream: Optional[sd.OutputStream] = None
        self._sample_rate: int = 44100
        self._channels: int = 1
        self._stream_lock = threading.RLock()
        self._output_device: Optional[int] = None

    def set_enabled(self, enabled: bool):
        if enabled and not self._backend_available:
            enabled = False
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if not enabled:
            self._playing = False
            self._close_stream()

    def set_volume(self, volume_percent: int):
        clamped = max(0, min(100, volume_percent))
        with self._stream_lock:
            self._volume = clamped / 100.0

    def set_playing(self, playing: bool):
        if playing and not self._backend_available:
            self._playing = False
            return
        with self._stream_lock:
            self._playing = bool(playing)
        if self._playing:
            self._ensure_stream()

    def update_voice(
        self,
        voice_id: object,
        audio: Optional[np.ndarray],
        sample_rate: int,
        position_seconds: float,
        playing: bool,
        *,
        force_seek: bool = False,
        resync_threshold: float = 0.05,
    ) -> bool:
        if audio is None or sample_rate <= 0:
            self.remove_voice(voice_id)
            return False
        channels = audio.shape[1] if audio.ndim > 1 else 1
        with self._stream_lock:
            if self._stream is not None:
                if self._sample_rate != int(sample_rate) or self._channels != channels:
                    # Cannot mix different formats; drop voice.
                    return False
            else:
                self._sample_rate = int(sample_rate)
                self._channels = channels
            voice = self._voices.get(voice_id)
            desired_cursor = max(0, min(int(position_seconds * sample_rate), len(audio)))
            if voice is None or voice.audio is not audio:
                voice = _MixVoice(
                    audio=audio,
                    sample_rate=int(sample_rate),
                    channels=channels,
                    cursor=desired_cursor,
                    active=bool(playing),
                )
                self._voices[voice_id] = voice
            else:
                if force_seek:
                    voice.cursor = desired_cursor
                else:
                    current_pos = voice.cursor / float(sample_rate)
                    if abs(current_pos - position_seconds) >= resync_threshold:
                        voice.cursor = desired_cursor
                voice.active = bool(playing)
            self._playing = bool(playing)
        if self._playing:
            self._ensure_stream()
        return True

    def remove_voice(self, voice_id: object):
        with self._stream_lock:
            if voice_id in self._voices:
                del self._voices[voice_id]

    def prune_voices(self, active_ids: set):
        with self._stream_lock:
            for key in list(self._voices.keys()):
                if key not in active_ids:
                    del self._voices[key]

    def clear(self):
        with self._stream_lock:
            self._voices.clear()
            self._playing = False
        self._close_stream()

    def _ensure_stream(self):
        if not self._backend_available or sd is None:
            return
        if self._stream is None and self._voices:
            stream_kwargs = {
                "samplerate": self._sample_rate,
                "channels": self._channels,
                "dtype": "float32",
                "callback": self._stream_callback,
            }
            if self._output_device is not None:
                stream_kwargs["device"] = self._output_device
            try:
                self._stream = sd.OutputStream(**stream_kwargs)
                self._stream.start()
                self._backend_error = None
                return
            except Exception:
                fallback = self._resolve_fallback_output_device()
                if fallback is None:
                    self._backend_available = False
                    self._enabled = False
                    return
                self._output_device = fallback
                try:
                    stream_kwargs["device"] = fallback
                    self._stream = sd.OutputStream(**stream_kwargs)
                    self._stream.start()
                    self._backend_error = None
                except Exception:
                    self._backend_available = False
                    self._enabled = False
                    self._close_stream()
                    return
        elif self._stream and not self._stream.active:
            try:
                self._stream.start()
            except Exception:
                self._backend_available = False
                self._enabled = False
                self._close_stream()

    def _resolve_fallback_output_device(self) -> Optional[int]:
        if sd is None:
            return None
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        for idx, dev in enumerate(devices):
            try:
                max_out = int(dev.get("max_output_channels", 0))
            except Exception:
                max_out = 0
            if max_out <= 0:
                continue
            try:
                sd.check_output_settings(device=idx, channels=min(self._channels, max_out))
                return idx
            except Exception:
                continue
        return None

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _stream_callback(self, outdata, frames, time_info, status):
        with self._stream_lock:
            if not self._enabled or not self._playing or not self._voices:
                outdata.fill(0.0)
                return
            outdata.fill(0.0)
            active_count = 0
            for voice in list(self._voices.values()):
                if not voice.active or voice.audio is None:
                    continue
                start = voice.cursor
                if start >= len(voice.audio):
                    voice.active = False
                    continue
                end = min(start + frames, len(voice.audio))
                chunk = voice.audio[start:end]
                produced = chunk.shape[0]
                if produced > 0:
                    outdata[:produced] += chunk
                    active_count += 1
                if produced < frames:
                    # Ended clip
                    voice.active = False
                voice.cursor = end
            if active_count > 1:
                # Prevent clipping when multiple voices overlap.
                outdata *= (1.0 / active_count)
            if active_count > 0:
                peak = float(np.max(np.abs(outdata)))
                if peak > 1.0:
                    outdata /= peak
            if self._volume != 1.0:
                np.multiply(outdata, self._volume, out=outdata)
            np.clip(outdata, -1.0, 1.0, out=outdata)
