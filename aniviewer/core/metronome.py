"""
Simple metronome helper that follows the viewer's BPM setting.
"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from time import perf_counter

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QUrl
from PyQt6.QtWidgets import QApplication

try:
    from PyQt6.QtMultimedia import QSoundEffect
except ImportError:  # pragma: no cover - PyQt6 without multimedia extras
    QSoundEffect = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - missing PortAudio or backend issues
    sd = None

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows platforms
    winsound = None


_TICK_WAV_B64 = (
    "UklGRuwNAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YcgNAAAAAPoTdyf8ORZLWFpjZ+Zxnnla"
    "fv5/f37meVBy7Gf9WtJLzDpVKOAU6QDt7GfZ1MaotU6mJ5mFjqyGzIEDgF6B1IVIjYyXYKRys2XEztY66i7+"
    "LBK6JVs4mkkLWU1mDnEJeQ1++X/DfnF6H3P6aENcSE1pPA8qrBa8ArvuJtt3yCa3nac/mmCPRIcdggyAHoFM"
    "hXyMgZYco/6xysIV1W7oW/xeEPsjtjYaSLlXMmUwcG54uH3ufwB/9nrocwJqhF26TgM+xit3GI4EivDm3B3K"
    "qLjxqF2bQZDih3SCG4DkgMqEtot7ld2hjrAxwV7TpOaI+o4OOiIONZZGY1YSZE1vzXddfdt/N390e6t0BWvA"
    "XilQmj98LUEaYQZa8qfexsstukqqgJwnkYaI0oIwgLCAT4T2inuUo6Air5y/qtHb5Lb4vgx3IGQzD0UIVexi"
    "Y24ld/x8wn9nf+x7aHUDbPhfklEtQS8vCRwzCCr0a+Byzba7p6upnRSSMYk3g02Ag4DagzyKgZNvn7qtCr74"
    "zxPj5PbtCrMetzGEQ6lTwWF0bXh2lHyjf5F/XnwfdvpsKmH4Ur1C4DDQHQUK+/Uw4iDPQ70IrdaeBpPhiaKD"
    "b4BdgGyDiImMkj+eV6x8vEnOTeET9RwJ7RwIMPZBRlKRYH9sxHUmfH1/s3/JfM927G1XYllUSkSOMpUf1gvN"
    "9/fj0dDTvm6uCKD9k5iKFISZgD6ABIPbiJ2RFJ34qvG6nMyJ30LzSgclG1YuZEDeUF1fhWsKdbF7UH/Qfy59"
    "enfZboBjtlXTRTo0WSGmDZ/5v+WE0mbA169AofuUVYuMhMmAJYCjgjOIs5Dum52parnyysbdcvF4BVwZoizP"
    "PnJPI16Fakp0Nnscf+V/jH0eeL9vo2QPV1hH4zUaI3YPcvuJ5zrU/cFGsXyi/pUYjAqFAIESgEiCkofQj86a"
    "R6jmt0rJBdyi76UDkhfrKjY9Ak7kXH9phHO0euJ+9H/jfbx4oHDBZWNY2kiJN9okRRFE/VTp8dWXw7iyvaMG"
    "l+GMj4U9gQeA84H3hvKOs5n1pma2pcdG2tTt0gHGFTIpmzuOTKBbdGi4cix6on79fzR+VHl7cdlmsllYSiw5"
    "mSYTExf/IOur1zTFLrQDpRSYsI0ahoGBAoCmgWKGGo6dmKil6rQExonYBuwAAPoTdyf8ORZLWFpjZ+Zxnnla"
    "fv5/f37meVBy7Gf9WtJLzDpVKOAU6QDt7GfZ1MaotU6mJ5mFjqyGzIEDgF6B1IVIjYyXYKRys2XEztY66i7+"
    "LBK6JVs4mkkLWU1mDnEJeQ1++X/DfnF6H3P6aENcSE1pPA8qrBa8ArvuJtt3yCa3nac/mmCPRIcdggyAHoFM"
    "hXyMgZYco/6xysIV1W7oW/xeEPsjtjYaSLlXMmUwcG54uH3ufwB/9nrocwJqhF26TgM+xit3GI4EivDm3B3K"
    "qLjxqF2bQZDih3SCG4DkgMqEtot7ld2hjrAxwV7TpOaI+o4OOiIONZZGY1YSZE1vzXddfdt/N390e6t0BWvA"
    "XilQmj98LUEaYQZa8qfexsstukqqgJwnkYaI0oIwgLCAT4T2inuUo6Air5y/qtHb5Lb4vgx3IGQzD0UIVexi"
    "Y24ld/x8wn9nf+x7aHUDbPhfklEtQS8vCRwzCCr0a+Byzba7p6upnRSSMYk3g02Ag4DagzyKgZNvn7qtCr74"
    "zxPj5PbtCrMetzGEQ6lTwWF0bXh2lHyjf5F/XnwfdvpsKmH4Ur1C4DDQHQUK+/Uw4iDPQ70IrdaeBpPhiaKD"
    "b4BdgGyDiImMkj+eV6x8vEnOTeET9RwJ7RwIMPZBRlKRYH9sxHUmfH1/s3/JfM927G1XYllUSkSOMpUf1gvN"
    "9/fj0dDTvm6uCKD9k5iKFISZgD6ABIPbiJ2RFJ34qvG6nMyJ30LzSgclG1YuZEDeUF1fhWsKdbF7UH/Qfy59"
    "enfZboBjtlXTRTo0WSGmDZ/5v+WE0mbA169AofuUVYuMhMmAJYCjgjOIs5Dum52parnyysbdcvF4BVwZoizP"
    "PnJPI16Fakp0Nnscf+V/jH0eeL9vo2QPV1hH4zUaI3YPcvuJ5zrU/cFGsXyi/pUYjAqFAIESgEiCkofQj86a"
    "R6jmt0rJBdyi76UDkhfrKjY9Ak7kXH9phHO0euJ+9H/jfbx4oHDBZWNY2kiJN9okRRFE/VTp8dWXw7iyvaMG"
    "l+GMj4U9gQeA84H3hvKOs5n1pma2pcdG2tTt0gHGFTIpmzuOTKBbdGi4cix6on79fzR+VHl7cdlmsllYSiw5"
    "mSYTExf/IOur1zTFLrQDpRSYsI0ahoGBAoCmgWKGGo6dmKil6rQExonYBuwAAPoTdyf8ORZLWFpjZ+Zxnnla"
    "fv5/f37meVBy7Gf9WtJLzDpVKOAU6QDt7GfZ1MaotU6mJ5mFjqyGzIEDgF6B1IVIjYyXYKRys2XEztY66i7+"
    "LBK6JVs4mkkLWU1mDnEJeQ1++X/DfnF6H3P6aENcSE1pPA8qrBa8ArvuJtt3yCa3nac/mmCPRIcdggyAHoFM"
    "hXyMgZYco/6xysIV1W7oW/xeEPsjtjYaSLlXMmUwcG54uH3ufwB/9nrocwJqhF26TgM+xit3GI4EivDm3B3K"
    "qLjxqF2bQZDih3SCG4DkgMqEtot7ld2hjrAxwV7TpOaI+o4OOiIONZZGY1YSZE1vzXddfdt/N390e6t0BWvA"
    "XilQmj98LUEaYQZa8qfexsstukqqgJwnkYaI0oIwgLCAT4T2inuUo6Air5y/qtHb5Lb4vgx3IGQzD0UIVexi"
    "Y24ld/x8wn9nf+x7aHUDbPhfklEtQS8vCRwzCCr0a+Byzba7p6upnRSSMYk3g02Ag4DagzyKgZNvn7qtCr74"
    "zxPj5PbtCrMetzGEQ6lTwWF0bXh2lHyjf5F/XnwfdvpsKmH4Ur1C4DDQHQUK+/Uw4iDPQ70IrdaeBpPhiaKD"
    "b4BdgGyDiImMkj+eV6x8vEnOTeET9RwJ7RwIMPZBRlKRYH9sxHUmfH1/s3/JfM927G1XYllUSkSOMpUf1gvN"
    "9/fj0dDTvm6uCKD9k5iKFISZgD6ABIPbiJ2RFJ34qvG6nMyJ30LzSgclG1YuZEDeUF1fhWsKdbF7UH/Qfy59"
    "enfZboBjtlXTRTo0WSGmDZ/5v+WE0mbA169AofuUVYuMhMmAJYCjgjOIs5Dum52parnyysbdcvF4BVwZoizP"
    "PnJPI16Fakp0Nnscf+V/jH0eeL9vo2QPV1hH4zUaI3YPcvuJ5zrU/cFGsXyi/pUYjAqFAIESgEiCkofQj86a"
    "R6jmt0rJBdyi76UDkhfrKjY9Ak7kXH9phHO0euJ+9H/jfbx4oHDBZWNY2kiJN9okRRFE/VTp8dWXw7iyvaMG"
    "l+GMj4U9gQeA84H3hvKOs5n1pma2pcdG2tTt0gHGFTIpmzuOTKBbdGi4cix6on79fzR+VHl7cdlmsllYSiw5"
    "mSYTExf/IOur1zTFLrQDpRSYsI0ahoGBAoCmgWKGGo6dmKil6rQExonYBuw="
)
_TICK_WAV_BYTES = base64.b64decode(_TICK_WAV_B64.encode("ascii"))
_TICK_WAV_PATH: str | None = None


def _ensure_tick_wav_path() -> str:
    """Write the embedded click to a temp WAV file if needed."""
    global _TICK_WAV_PATH
    if _TICK_WAV_PATH and os.path.exists(_TICK_WAV_PATH):
        return _TICK_WAV_PATH

    out_dir = Path(tempfile.gettempdir()) / "msm_animation_viewer"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        out_dir = Path(tempfile.gettempdir())

    candidate = out_dir / "metronome_tick.wav"
    try:
        if not candidate.exists() or candidate.stat().st_size != len(_TICK_WAV_BYTES):
            candidate.write_bytes(_TICK_WAV_BYTES)
        _TICK_WAV_PATH = str(candidate)
        return _TICK_WAV_PATH
    except OSError:
        # Fall back to an in-memory play via winsound
        _TICK_WAV_PATH = ""
        return _TICK_WAV_PATH


class Metronome(QObject):
    """Utility that emits ticks (and optional beeps) at the current BPM."""

    tick = pyqtSignal(bool)  # True when a downbeat (first beat of the bar)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bpm: float = 120.0
        self._audible: bool = True
        self._beats_per_measure: int = 4
        self._beat_note_value: int = 4
        self._beat_index: int = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._sound_effect = self._init_sound_effect()
        (
            self._sd_waveform_normal,
            self._sd_waveform_accent,
            self._sd_samplerate,
        ) = self._build_sd_waveforms()
        self._sd_stream = self._init_sd_stream()
        self.destroyed.connect(self._cleanup_audio)
        self._period_sec: float = 0.5
        self._next_tick_time: float | None = None
        self.set_time_signature(4, 4, resync=False)
        self._apply_interval()

    def _init_sound_effect(self):
        if QSoundEffect is None:
            return None
        try:
            click_path = _ensure_tick_wav_path()
            if not click_path:
                return None
            effect = QSoundEffect(self)
            effect.setLoopCount(1)
            effect.setVolume(0.9)
            effect.setSource(QUrl.fromLocalFile(click_path))
            if effect.status() == QSoundEffect.Status.Error:
                return None
            effect.setMuted(True)
            effect.play()
            effect.stop()
            effect.setMuted(False)
            return effect
        except Exception:
            return None

    def _build_sd_waveforms(self):
        if sd is None or np is None:
            return None, None, None
        sample_rate = 44100
        duration = 0.045
        frames = max(1, int(sample_rate * duration))
        t = np.linspace(0.0, duration, frames, endpoint=False, dtype=np.float32)
        freq = 1100.0
        envelope = np.exp(-60.0 * t)
        base = (0.6 * envelope * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        base = base.reshape(-1, 1)
        accent = np.clip(base * 1.3, -1.0, 1.0)
        return base, accent, sample_rate

    def _init_sd_stream(self):
        if sd is None or self._sd_waveform_normal is None:
            return None
        try:
            stream = sd.OutputStream(
                samplerate=self._sd_samplerate,
                channels=1,
                dtype='float32',
                blocksize=self._sd_waveform_normal.shape[0],
                latency='low',
            )
            stream.start()
            return stream
        except Exception:
            return None

    def _cleanup_audio(self, *_):
        if self._sd_stream is not None:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None

    def set_bpm(self, bpm: float) -> None:
        """Update the tempo while keeping the timer running."""
        clamped = max(20.0, min(300.0, float(bpm)))
        if abs(clamped - self._bpm) < 1e-4:
            return
        self._bpm = clamped
        was_active = self._timer.isActive()
        self._apply_interval()
        if was_active:
            self._resync_timer()

    def set_enabled(self, enabled: bool) -> None:
        """Start or stop ticking."""
        if enabled:
            if not self._timer.isActive():
                self._beat_index = 0
                self._next_tick_time = perf_counter() + self._period_sec
                self._schedule_timer()
        else:
            self._timer.stop()
            self._next_tick_time = None
            self._beat_index = 0

    def is_enabled(self) -> bool:
        return self._timer.isActive()

    def set_audible(self, audible: bool) -> None:
        """Toggle whether an audible click should play each beat."""
        self._audible = bool(audible)

    def set_time_signature(self, numerator: int, denominator: int, *, resync: bool = True) -> None:
        """Define the current time signature for accenting."""
        numerator = max(1, int(numerator))
        allowed = {1, 2, 4, 8, 16, 32}
        denominator = int(denominator)
        if denominator not in allowed:
            denominator = 4
        changed = (
            numerator != self._beats_per_measure
            or denominator != self._beat_note_value
        )
        self._beats_per_measure = numerator
        self._beat_note_value = denominator
        self._beat_index = 0
        if changed:
            self._apply_interval()
            if resync and self._timer.isActive():
                self._resync_timer()

    def _apply_interval(self) -> None:
        note_scale = 4.0 / float(max(1, self._beat_note_value))
        self._period_sec = (60.0 / max(1e-3, self._bpm)) * note_scale

    def _schedule_timer(self) -> None:
        if self._next_tick_time is None:
            self._next_tick_time = perf_counter() + self._period_sec
        now = perf_counter()
        delay = max(0.0, self._next_tick_time - now)
        self._timer.start(max(1, int(delay * 1000)))

    def _resync_timer(self) -> None:
        self._next_tick_time = perf_counter() + self._period_sec
        self._schedule_timer()

    def _play_audible_tick(self, downbeat: bool) -> None:
        if not self._audible:
            return

        if self._sd_stream is not None and self._sd_waveform_normal is not None:
            try:
                waveform = (
                    self._sd_waveform_accent if downbeat else self._sd_waveform_normal
                )
                self._sd_stream.write(waveform)
                return
            except Exception:
                pass

        if winsound is not None:
            try:
                freq = 1480 if downbeat else 1220
                dur = 80 if downbeat else 55
                winsound.Beep(freq, dur)
                return
            except RuntimeError:
                pass
            wav_path = _ensure_tick_wav_path()
            if wav_path:
                try:
                    winsound.PlaySound(
                        wav_path,
                        winsound.SND_FILENAME | winsound.SND_NODEFAULT | winsound.SND_ASYNC,
                    )
                    return
                except RuntimeError:
                    pass

        if self._sound_effect is not None:
            self._sound_effect.stop()
            if downbeat:
                self._sound_effect.setVolume(1.0)
            else:
                self._sound_effect.setVolume(0.7)
            self._sound_effect.play()
            return

        QApplication.beep()

    def _on_timeout(self) -> None:
        downbeat = (self._beat_index == 0)
        self._play_audible_tick(downbeat)
        self.tick.emit(downbeat)
        if self._beats_per_measure > 0:
            self._beat_index = (self._beat_index + 1) % self._beats_per_measure
        now = perf_counter()
        if self._next_tick_time is None:
            target = now
        else:
            target = self._next_tick_time

        # Advance exactly one beat ahead of the target time. If the timer fired
        # very late (multiple beats behind), jump forward so the next click
        # occurs roughly one beat from "now" instead of spamming catch-up ticks.
        next_target = target + self._period_sec
        if next_target <= now:
            missed = int((now - target) // self._period_sec) + 1
            next_target = target + missed * self._period_sec

        self._next_tick_time = next_target
        self._schedule_timer()
