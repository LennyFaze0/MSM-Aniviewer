"""Diagnostics for live Qt and Windows window activity."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple


_PROPERTY_NAME = "aniviewer_conversion_diagnostics_active"
_LOG_PATH = Path.cwd() / "live_window_diagnostics.log"
_LOCK = threading.RLock()
_ACTIVE_COUNT = 0
_PERSISTENT_ACTIVE = False
_WINDOW_LOGGER_ENABLED = True
_STOP_EVENT: Optional[threading.Event] = None
_WATCH_THREAD: Optional[threading.Thread] = None
_PROCESS_IMAGE_CACHE: Dict[int, str] = {}
_QMESSAGEBOX_PATCHED = False


def compact_diagnostic_text(value: object, *, limit: int = 700) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def emit_conversion_diagnostic(message: str) -> None:
    line = f"[Aniviewer live window logger] {message}"
    print(line, flush=True)
    try:
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {line}\n")
    except Exception:
        pass


def set_conversion_window_diagnostics_enabled(enabled: bool) -> None:
    """Enable/disable live window diagnostics globally."""
    global _ACTIVE_COUNT, _PERSISTENT_ACTIVE, _WINDOW_LOGGER_ENABLED
    with _LOCK:
        _WINDOW_LOGGER_ENABLED = bool(enabled)
        if _WINDOW_LOGGER_ENABLED:
            return
        _ACTIVE_COUNT = 0
        _PERSISTENT_ACTIVE = False
        _set_qt_diagnostic_property(False)
        if _STOP_EVENT is not None:
            _STOP_EVENT.set()


def conversion_window_diagnostics_enabled() -> bool:
    with _LOCK:
        return bool(_WINDOW_LOGGER_ENABLED)


def conversion_diagnostics_active() -> bool:
    with _LOCK:
        if not _WINDOW_LOGGER_ENABLED:
            return False
        if _ACTIVE_COUNT > 0 or _PERSISTENT_ACTIVE:
            return True
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        return bool(app and app.property(_PROPERTY_NAME))
    except Exception:
        return False


def _set_qt_diagnostic_property(active: bool) -> None:
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.setProperty(_PROPERTY_NAME, bool(active))
    except Exception:
        pass


def install_qmessagebox_diagnostics(qmessagebox_cls: Any) -> None:
    """Print QMessageBox static calls while live window diagnostics are active."""
    global _QMESSAGEBOX_PATCHED
    with _LOCK:
        if _QMESSAGEBOX_PATCHED:
            return
        _QMESSAGEBOX_PATCHED = True

    for method_name in ("warning", "critical", "information", "question"):
        original = getattr(qmessagebox_cls, method_name, None)
        if original is None:
            continue

        def _make_wrapper(name: str, wrapped: Any):
            def _wrapper(*args: Any, **kwargs: Any):
                if conversion_diagnostics_active():
                    title = args[1] if len(args) > 1 else kwargs.get("title", "")
                    text = args[2] if len(args) > 2 else kwargs.get("text", "")
                    emit_conversion_diagnostic(
                        f"QMessageBox.{name} called: "
                        f"title={compact_diagnostic_text(title)}; "
                        f"text={compact_diagnostic_text(text, limit=1200)}"
                    )
                return wrapped(*args, **kwargs)

            return _wrapper

        try:
            setattr(qmessagebox_cls, method_name, staticmethod(_make_wrapper(method_name, original)))
        except Exception as exc:
            emit_conversion_diagnostic(f"Could not patch QMessageBox.{method_name}: {exc}")


def start_conversion_window_diagnostics(label: str = "conversion") -> None:
    """Start live window diagnostics and the Windows top-level window watcher."""
    global _ACTIVE_COUNT
    with _LOCK:
        if not _WINDOW_LOGGER_ENABLED:
            _set_qt_diagnostic_property(False)
            return
        _ACTIVE_COUNT += 1
        _set_qt_diagnostic_property(True)
        _ensure_windows_watcher_locked(label)


def start_persistent_window_diagnostics(label: str = "application-wide monitor") -> None:
    """Start diagnostics that remain active for the life of the app."""
    global _PERSISTENT_ACTIVE
    with _LOCK:
        if not _WINDOW_LOGGER_ENABLED:
            _set_qt_diagnostic_property(False)
            return
        if _PERSISTENT_ACTIVE:
            return
        _PERSISTENT_ACTIVE = True
        _set_qt_diagnostic_property(True)
        _ensure_windows_watcher_locked(label)


def stop_conversion_window_diagnostics(label: str = "conversion") -> None:
    """Stop live window diagnostics when no diagnostic scopes remain."""
    global _ACTIVE_COUNT
    with _LOCK:
        if _ACTIVE_COUNT > 0:
            _ACTIVE_COUNT -= 1
        still_active = _ACTIVE_COUNT > 0 or _PERSISTENT_ACTIVE
        _set_qt_diagnostic_property(still_active)
        if still_active:
            return
        if _STOP_EVENT is not None:
            _STOP_EVENT.set()


def diagnostic_log_path() -> Path:
    return _LOG_PATH


def _ensure_windows_watcher_locked(label: str) -> None:
    global _STOP_EVENT, _WATCH_THREAD
    if os.name != "nt":
        return
    if (
        _WATCH_THREAD
        and _WATCH_THREAD.is_alive()
        and _STOP_EVENT is not None
        and not _STOP_EVENT.is_set()
    ):
        return
    _STOP_EVENT = threading.Event()
    _WATCH_THREAD = threading.Thread(
        target=_watch_windows,
        args=(_STOP_EVENT, label),
        name="AniviewerConversionWindowDiagnostics",
        daemon=True,
    )
    _WATCH_THREAD.start()


def _watch_windows(stop_event: threading.Event, label: str) -> None:
    if os.name != "nt":
        return
    try:
        visible_hwnds = set(_enumerate_visible_top_level_windows().keys())
        event_counts: Dict[Tuple[str, str, str, str], int] = {}
        emit_conversion_diagnostic(
            f"Windows top-level window watcher active for {label}; log={_LOG_PATH}"
        )
        _run_windows_watch_loop(stop_event, visible_hwnds, event_counts)
    except Exception as exc:
        emit_conversion_diagnostic(f"Windows top-level window watcher failed: {exc}")


if os.name == "nt":
    _USER32 = ctypes.windll.user32
    _KERNEL32 = ctypes.windll.kernel32
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _WINEVENTPROC = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )
else:
    _USER32 = None
    _KERNEL32 = None
    _WNDENUMPROC = None
    _WINEVENTPROC = None


if os.name == "nt":
    try:
        _USER32.SetWinEventHook.restype = wintypes.HANDLE
        _USER32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
            _WINEVENTPROC,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        _USER32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        _USER32.GetAncestor.restype = wintypes.HWND
        _USER32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        _USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _USER32.IsWindowVisible.argtypes = [wintypes.HWND]
        _USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        _USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        _KERNEL32.OpenProcess.restype = wintypes.HANDLE
        _KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _KERNEL32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    except Exception:
        pass


def _run_windows_watch_loop(
    stop_event: threading.Event,
    visible_hwnds: Set[int],
    event_counts: Dict[Tuple[str, str, str, str], int],
) -> None:
    if _USER32 is None or _WINEVENTPROC is None:
        return

    EVENT_SYSTEM_FOREGROUND = 0x0003
    EVENT_OBJECT_CREATE = 0x8000
    EVENT_OBJECT_DESTROY = 0x8001
    EVENT_OBJECT_SHOW = 0x8002
    EVENT_OBJECT_FOCUS = 0x8005
    WINEVENT_OUTOFCONTEXT = 0x0000
    GA_ROOT = 2
    PM_REMOVE = 0x0001
    event_names = {
        EVENT_SYSTEM_FOREGROUND: "foreground-event",
        EVENT_OBJECT_CREATE: "create-event",
        EVENT_OBJECT_DESTROY: "destroy-event",
        EVENT_OBJECT_SHOW: "show-event",
        EVENT_OBJECT_FOCUS: "focus-event",
    }

    def _log_hwnd(hwnd: Any, reason: str, *, force: bool = False) -> None:
        root = _USER32.GetAncestor(hwnd, GA_ROOT) or hwnd
        root_int = _hwnd_int(root)
        if not root_int:
            return
        info = _window_info(root)
        if not info or (not force and not info.get("visible")):
            return
        fingerprint = (
            reason,
            str(info.get("class") or ""),
            str(info.get("title") or ""),
            str(info.get("exe_name") or ""),
        )
        count = event_counts.get(fingerprint, 0) + 1
        event_counts[fingerprint] = count
        if count > 8 and count not in (10, 25, 50, 100, 250, 500):
            return
        repeat = f"; repeat={count}" if count > 1 else ""
        emit_conversion_diagnostic(
            "Windows top-level window "
            f"{reason}: hwnd=0x{root_int:x}; "
            f"title={compact_diagnostic_text(info.get('title'))}; "
            f"class={compact_diagnostic_text(info.get('class'))}; "
            f"pid={info.get('pid')}; "
            f"exe={compact_diagnostic_text(info.get('exe'))}; "
            f"rect={info.get('rect')}{repeat}"
        )

    def _on_event(
        _hook: Any,
        event: int,
        hwnd: Any,
        _object_id: int,
        _child_id: int,
        _event_thread: int,
        _event_time: int,
    ) -> None:
        if hwnd:
            _log_hwnd(hwnd, event_names.get(int(event), f"event-0x{int(event):x}"), force=True)

    callback = _WINEVENTPROC(_on_event)
    object_hook = _USER32.SetWinEventHook(
        EVENT_OBJECT_CREATE,
        EVENT_OBJECT_SHOW,
        0,
        callback,
        0,
        0,
        WINEVENT_OUTOFCONTEXT,
    )
    foreground_hook = _USER32.SetWinEventHook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_FOREGROUND,
        0,
        callback,
        0,
        0,
        WINEVENT_OUTOFCONTEXT,
    )
    focus_hook = _USER32.SetWinEventHook(
        EVENT_OBJECT_FOCUS,
        EVENT_OBJECT_FOCUS,
        0,
        callback,
        0,
        0,
        WINEVENT_OUTOFCONTEXT,
    )
    if not object_hook and not foreground_hook and not focus_hook:
        emit_conversion_diagnostic("Windows watcher could not install WinEvent hooks; polling only")

    try:
        msg = wintypes.MSG()
        last_heartbeat = time.monotonic()
        while not stop_event.is_set():
            current_visible = set(_enumerate_visible_top_level_windows().keys())
            for hwnd in current_visible - visible_hwnds:
                _log_hwnd(hwnd, "poll-visible")
            visible_hwnds.clear()
            visible_hwnds.update(current_visible)
            now = time.monotonic()
            if now - last_heartbeat >= 10.0:
                emit_conversion_diagnostic(
                    f"Windows top-level window watcher heartbeat: visible_windows={len(current_visible)}"
                )
                last_heartbeat = now
            end_time = now + 0.01
            while time.monotonic() < end_time and not stop_event.is_set():
                while _USER32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
                    _USER32.TranslateMessage(ctypes.byref(msg))
                    _USER32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.001)
    finally:
        for hook in (object_hook, foreground_hook, focus_hook):
            if hook:
                _USER32.UnhookWinEvent(hook)
        emit_conversion_diagnostic("Windows top-level window watcher stopped")


def _enumerate_visible_top_level_windows() -> Dict[int, Dict[str, Any]]:
    if _USER32 is None or _WNDENUMPROC is None:
        return {}
    windows: Dict[int, Dict[str, Any]] = {}

    def _callback(hwnd: Any, _lparam: Any) -> int:
        info = _window_info(hwnd)
        if info and info.get("visible"):
            windows[int(info["hwnd"])] = info
        return 1

    callback = _WNDENUMPROC(_callback)
    _USER32.EnumWindows(callback, 0)
    return windows


def _window_info(hwnd: Any) -> Optional[Dict[str, Any]]:
    if _USER32 is None:
        return None
    hwnd_int = _hwnd_int(hwnd)
    if not hwnd_int:
        return None
    visible = bool(_USER32.IsWindowVisible(hwnd))
    title = _window_title(hwnd)
    class_name = _window_class(hwnd)
    pid = wintypes.DWORD(0)
    _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _process_image_name(int(pid.value))
    rect = wintypes.RECT()
    rect_tuple = None
    if _USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
        rect_tuple = (rect.left, rect.top, rect.right, rect.bottom)
    return {
        "hwnd": hwnd_int,
        "visible": visible,
        "title": title,
        "class": class_name,
        "pid": int(pid.value),
        "exe": exe,
        "exe_name": os.path.basename(exe).lower(),
        "rect": rect_tuple,
    }


def _window_title(hwnd: Any) -> str:
    if _USER32 is None:
        return ""
    try:
        length = int(_USER32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        _USER32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value
    except Exception:
        return ""


def _window_class(hwnd: Any) -> str:
    if _USER32 is None:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(256)
        _USER32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value
    except Exception:
        return ""


def _process_image_name(pid: int) -> str:
    if pid <= 0 or _KERNEL32 is None:
        return ""
    cached = _PROCESS_IMAGE_CACHE.get(pid)
    if cached is not None:
        return cached
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = _KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        _PROCESS_IMAGE_CACHE[pid] = ""
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _KERNEL32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            value = buffer.value
        else:
            value = ""
    except Exception:
        value = ""
    finally:
        _KERNEL32.CloseHandle(handle)
    _PROCESS_IMAGE_CACHE[pid] = value
    return value


def _hwnd_int(hwnd: Any) -> int:
    if hasattr(hwnd, "value"):
        return int(hwnd.value or 0)
    return int(hwnd or 0)
