"""
Lightweight opt-in performance profiling helpers for Aniviewer.

The profiler is intentionally dormant by default. Instrumented code can keep
small top-level scopes in place without changing behavior or collecting data
unless the user enables profiling from settings.
"""

from __future__ import annotations

import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


LogFn = Callable[[str, str], None]


@dataclass
class ProfileStat:
    count: int = 0
    total_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float = 0.0
    slow_count: int = 0

    def add(self, elapsed_ms: float, slow_threshold_ms: float) -> None:
        value = max(0.0, float(elapsed_ms))
        self.count += 1
        self.total_ms += value
        self.last_ms = value
        if self.count == 1:
            self.min_ms = value
            self.max_ms = value
        else:
            self.min_ms = min(self.min_ms, value)
            self.max_ms = max(self.max_ms, value)
        if slow_threshold_ms > 0.0 and value >= slow_threshold_ms:
            self.slow_count += 1

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0


class _NoopScope(AbstractContextManager):
    def __enter__(self) -> "_NoopScope":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _ProfileScope(AbstractContextManager):
    def __init__(self, profiler: "PerformanceProfiler", name: str):
        self._profiler = profiler
        self._name = name
        self._start = 0.0

    def __enter__(self) -> "_ProfileScope":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        self._profiler.record(self._name, elapsed_ms)
        return False


class PerformanceProfiler:
    """Aggregates coarse performance timings and emits throttled summaries."""

    def __init__(self) -> None:
        self.enabled = False
        self.slow_threshold_ms = 50.0
        self.report_interval_sec = 8.0
        self.max_sections = 512
        self._lock = threading.RLock()
        self._stats: Dict[str, ProfileStat] = {}
        self._last_report_at = time.perf_counter()
        self._log_fn: Optional[LogFn] = None

    def configure(
        self,
        *,
        enabled: bool,
        slow_threshold_ms: float = 50.0,
        report_interval_sec: float = 8.0,
        log_fn: Optional[LogFn] = None,
        reset: bool = False,
    ) -> None:
        with self._lock:
            was_enabled = self.enabled
            self.enabled = bool(enabled)
            self.slow_threshold_ms = max(0.0, float(slow_threshold_ms))
            self.report_interval_sec = max(1.0, float(report_interval_sec))
            self._log_fn = log_fn
            if reset or (self.enabled and not was_enabled):
                self._stats.clear()
                self._last_report_at = time.perf_counter()

    def scope(self, name: str) -> AbstractContextManager:
        if not self.enabled:
            return _NOOP_SCOPE
        return _ProfileScope(self, str(name))

    def record(self, name: str, elapsed_ms: float) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        report_lines: List[str] = []
        with self._lock:
            if name not in self._stats:
                if len(self._stats) >= self.max_sections:
                    return
                self._stats[name] = ProfileStat()
            self._stats[name].add(elapsed_ms, self.slow_threshold_ms)
            if (now - self._last_report_at) >= self.report_interval_sec:
                report_lines = self.summary_lines(top_n=8, reset=False)
                self._last_report_at = now

        if (
            report_lines
            and self._log_fn is not None
            and threading.current_thread() is threading.main_thread()
        ):
            for line in report_lines:
                self._log_fn(line, "INFO")

    def summary_lines(self, *, top_n: int = 16, reset: bool = False) -> List[str]:
        with self._lock:
            items = sorted(
                self._stats.items(),
                key=lambda item: (item[1].total_ms, item[1].max_ms),
                reverse=True,
            )
            if not items:
                return ["[Perf] No samples collected yet."]
            lines = [
                "[Perf] Top sections by total time:",
            ]
            for name, stat in items[: max(1, int(top_n))]:
                lines.append(
                    "[Perf] "
                    f"{name}: count={stat.count}, "
                    f"total={stat.total_ms:.1f} ms, "
                    f"avg={stat.avg_ms:.2f} ms, "
                    f"max={stat.max_ms:.2f} ms, "
                    f"slow={stat.slow_count}"
                )
            if reset:
                self._stats.clear()
                self._last_report_at = time.perf_counter()
            return lines


_NOOP_SCOPE = _NoopScope()
_PROFILER = PerformanceProfiler()


def get_profiler() -> PerformanceProfiler:
    return _PROFILER


def profile_scope(name: str) -> AbstractContextManager:
    return _PROFILER.scope(name)
