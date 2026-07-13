"""Optional Acer SpatialLabs / Leia Simulated Reality SDK bridge."""

from __future__ import annotations

import ctypes
import os
import platform
import time
from dataclasses import dataclass
from typing import Optional, Tuple


_CREATE_GL_WEAVER = "?CreateGLWeaver@SR@@YA?AW4WeaverErrorCode@@AEAVSRContext@1@PEAUHWND__@@PEAPEAVIGLWeaver1@1@@Z"
_CREATE_GL_WEAVER_CONCRETE = "?create@GLWeaver@SR@@SAPEAV12@AEAVSRContext@2@II@Z"
_GET_FRAMEBUFFER = "?getFrameBuffer@GLWeaverBase@SR@@QEAAIXZ"
_CAN_WEAVE = "?canWeave@GLWeaverBase@SR@@QEAA_NXZ"
_CAN_WEAVE_SIZE = "?canWeave@GLWeaverBase@SR@@QEAA_NII@Z"
_WEAVE_SIZE = "?weave@GLWeaverBase@SR@@QEAAXII@Z"
_SET_INPUT_FRAMEBUFFER = "?setInputFrameBuffer@GLWeaverBase@SR@@QEAAXII@Z"


@dataclass
class SpatialLabsScreenInfo:
    resolution_width: int = 0
    resolution_height: int = 0
    physical_resolution_width: int = 0
    physical_resolution_height: int = 0
    recommended_views_width: int = 0
    recommended_views_height: int = 0
    dot_pitch: float = 0.0


class SpatialLabsSdkError(RuntimeError):
    """Raised when the optional SpatialLabs SDK cannot be used."""


class SpatialLabsGLWeaver:
    """Thin wrapper around the SDK OpenGL weaver object."""

    def __init__(self, sdk: "SpatialLabsSdk", pointer: int, create_error: int = 0):
        self.sdk = sdk
        self.pointer = int(pointer or 0)
        self.create_error = int(create_error)
        opengl = sdk.opengl
        if opengl is None or not self.pointer:
            raise SpatialLabsSdkError("OpenGL weaver is unavailable")

        self._get_framebuffer = getattr(opengl, _GET_FRAMEBUFFER)
        self._get_framebuffer.restype = ctypes.c_uint
        self._get_framebuffer.argtypes = [ctypes.c_void_p]

        self._can_weave = getattr(opengl, _CAN_WEAVE, None)
        if self._can_weave is not None:
            self._can_weave.restype = ctypes.c_bool
            self._can_weave.argtypes = [ctypes.c_void_p]

        self._can_weave_size = getattr(opengl, _CAN_WEAVE_SIZE, None)
        if self._can_weave_size is not None:
            self._can_weave_size.restype = ctypes.c_bool
            self._can_weave_size.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]

        self._weave_size = getattr(opengl, _WEAVE_SIZE)
        self._weave_size.restype = None
        self._weave_size.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]

        self._set_input_framebuffer = getattr(opengl, _SET_INPUT_FRAMEBUFFER, None)
        if self._set_input_framebuffer is not None:
            self._set_input_framebuffer.restype = None
            self._set_input_framebuffer.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_uint,
            ]

    def framebuffer(self) -> int:
        return int(self._get_framebuffer(ctypes.c_void_p(self.pointer)))

    def can_weave(self, width: Optional[int] = None, height: Optional[int] = None) -> bool:
        if width is None or height is None:
            if self._can_weave is None:
                return True
            return bool(self._can_weave(ctypes.c_void_p(self.pointer)))
        if self._can_weave_size is None:
            return True
        return bool(
            self._can_weave_size(
                ctypes.c_void_p(self.pointer),
                ctypes.c_uint(max(1, int(width))),
                ctypes.c_uint(max(1, int(height))),
            )
        )

    def set_input_framebuffer(self, framebuffer: int, texture_or_view_count: int = 0) -> None:
        if self._set_input_framebuffer is None:
            raise SpatialLabsSdkError("SDK setInputFrameBuffer export is unavailable")
        self._set_input_framebuffer(
            ctypes.c_void_p(self.pointer),
            ctypes.c_uint(max(0, int(framebuffer))),
            ctypes.c_uint(max(0, int(texture_or_view_count))),
        )

    def weave(self, width: int, height: int) -> None:
        self._weave_size(
            ctypes.c_void_p(self.pointer),
            ctypes.c_uint(max(1, int(width))),
            ctypes.c_uint(max(1, int(height))),
        )


class SpatialLabsSdk:
    """Loads the installed SpatialLabs runtime and exposes safe Python helpers."""

    DEFAULT_BIN_DIRS = (
        r"C:\Program Files\Acer\SpatialLabs\Platform\bin",
        r"C:\Program Files (x86)\Simulated Reality\Platform\bin",
    )

    def __init__(self) -> None:
        self.bin_dir: str = ""
        self.platform_version: str = ""
        self.screen = SpatialLabsScreenInfo()
        self.available: bool = False
        self.status: str = "SpatialLabs SDK not initialized"
        self._dll_directory_handle = None
        self.platform = None
        self.core = None
        self.displays = None
        self.opengl = None
        self.context: Optional[int] = None
        self.screen_pointer: Optional[int] = None
        self.lens_hint_pointer: Optional[int] = None
        self._lens_hint_enabled: Optional[bool] = None

    @classmethod
    def find_bin_dir(cls) -> Optional[str]:
        env_path = os.environ.get("ANIVIEWER_SPATIALLABS_SDK_BIN", "").strip()
        candidates = (env_path,) + cls.DEFAULT_BIN_DIRS if env_path else cls.DEFAULT_BIN_DIRS
        for candidate in candidates:
            if not candidate:
                continue
            if os.path.exists(os.path.join(candidate, "SimulatedRealityCore.dll")):
                return candidate
        return None

    def initialize(self) -> bool:
        if self.available:
            return True
        if platform.system().lower() != "windows":
            self.status = "SpatialLabs SDK is only available on Windows"
            return False

        bin_dir = self.find_bin_dir()
        if not bin_dir:
            self.status = "SpatialLabs SDK runtime was not found"
            return False
        self.bin_dir = bin_dir

        try:
            if hasattr(os, "add_dll_directory"):
                self._dll_directory_handle = os.add_dll_directory(bin_dir)
            self.platform = ctypes.CDLL(os.path.join(bin_dir, "simulatedreality.dll"))
            self.core = ctypes.CDLL(os.path.join(bin_dir, "SimulatedRealityCore.dll"))
            self.displays = ctypes.CDLL(os.path.join(bin_dir, "SimulatedRealityDisplays.dll"))

            self._bind_core_exports()
            self._bind_display_exports()

            self.platform.getSRPlatformVersion.restype = ctypes.c_char_p
            self.platform.getSRPlatformVersion.argtypes = []
            version = self.platform.getSRPlatformVersion()
            self.platform_version = (
                version.decode("utf-8", "replace")
                if isinstance(version, (bytes, bytearray))
                else str(version or "")
            )

            for attempt in range(3):
                ctx = self.core.newSRContext()
                if not ctx:
                    if attempt >= 2:
                        raise SpatialLabsSdkError("newSRContext returned null")
                    time.sleep(0.15)
                    continue
                self.context = int(ctx)
                if bool(self.core.initializeSRContext(ctypes.c_void_p(self.context))):
                    break
                if attempt >= 2:
                    raise SpatialLabsSdkError("initializeSRContext returned false")
                self.context = None
                time.sleep(0.15)
            if not self.context:
                raise SpatialLabsSdkError("initializeSRContext returned false")

            screen = self.displays.createScreen(ctypes.c_void_p(self.context))
            if not screen:
                raise SpatialLabsSdkError("createScreen returned null")
            self.screen_pointer = int(screen)
            self._read_screen_info()

            try:
                lens_hint = self.displays.createSwitchableLensHint(ctypes.c_void_p(self.context))
                if lens_hint:
                    self.lens_hint_pointer = int(lens_hint)
            except Exception:
                self.lens_hint_pointer = None

            self.available = True
            self.status = self.describe()
            return True
        except Exception as exc:
            self.status = f"SpatialLabs SDK unavailable: {exc}"
            self.shutdown()
            return False

    def _bind_core_exports(self) -> None:
        if self.core is None:
            raise SpatialLabsSdkError("Core DLL is not loaded")
        self.core.newSRContext.restype = ctypes.c_void_p
        self.core.newSRContext.argtypes = []
        self.core.initializeSRContext.restype = ctypes.c_bool
        self.core.initializeSRContext.argtypes = [ctypes.c_void_p]
        self.core.deleteSRContext.restype = None
        self.core.deleteSRContext.argtypes = [ctypes.c_void_p]

    def _bind_display_exports(self) -> None:
        if self.displays is None:
            raise SpatialLabsSdkError("Displays DLL is not loaded")
        self.displays.createScreen.restype = ctypes.c_void_p
        self.displays.createScreen.argtypes = [ctypes.c_void_p]
        self.displays.createSwitchableLensHint.restype = ctypes.c_void_p
        self.displays.createSwitchableLensHint.argtypes = [ctypes.c_void_p]
        self.displays.deleteSwitchableLensHint.restype = None
        self.displays.deleteSwitchableLensHint.argtypes = [ctypes.c_void_p]

        for name in (
            "getResolutionWidth",
            "getResolutionHeight",
            "getPhysicalResolutionWidth",
            "getPhysicalResolutionHeight",
            "getRecommendedViewsTextureWidth",
            "getRecommendedViewsTextureHeight",
        ):
            fn = getattr(self.displays, name)
            fn.restype = ctypes.c_int
            fn.argtypes = [ctypes.c_void_p]

        self.displays.getDotPitch.restype = ctypes.c_float
        self.displays.getDotPitch.argtypes = [ctypes.c_void_p]
        self.displays.lensEnableHint.restype = None
        self.displays.lensEnableHint.argtypes = [ctypes.c_void_p]
        self.displays.lensDisableHint.restype = None
        self.displays.lensDisableHint.argtypes = [ctypes.c_void_p]
        self.displays.isLensEnabled.restype = ctypes.c_bool
        self.displays.isLensEnabled.argtypes = [ctypes.c_void_p]
        self.displays.isLensEnabledByPreference.restype = ctypes.c_bool
        self.displays.isLensEnabledByPreference.argtypes = [ctypes.c_void_p]

    def _read_screen_info(self) -> None:
        if not self.screen_pointer or self.displays is None:
            return
        ptr = ctypes.c_void_p(self.screen_pointer)
        self.screen = SpatialLabsScreenInfo(
            resolution_width=int(self.displays.getResolutionWidth(ptr)),
            resolution_height=int(self.displays.getResolutionHeight(ptr)),
            physical_resolution_width=int(self.displays.getPhysicalResolutionWidth(ptr)),
            physical_resolution_height=int(self.displays.getPhysicalResolutionHeight(ptr)),
            recommended_views_width=int(self.displays.getRecommendedViewsTextureWidth(ptr)),
            recommended_views_height=int(self.displays.getRecommendedViewsTextureHeight(ptr)),
            dot_pitch=float(self.displays.getDotPitch(ptr)),
        )

    def ensure_opengl_loaded(self) -> bool:
        if self.opengl is not None:
            return True
        if not self.bin_dir:
            return False
        try:
            self.opengl = ctypes.CDLL(os.path.join(self.bin_dir, "SimulatedRealityOpenGL.dll"))
            return True
        except Exception as exc:
            self.status = f"SpatialLabs OpenGL SDK unavailable: {exc}"
            self.opengl = None
            return False

    def create_gl_weaver(
        self,
        hwnd: int = 0,
        view_size: Optional[Tuple[int, int]] = None,
    ) -> SpatialLabsGLWeaver:
        if not self.initialize() or not self.context:
            raise SpatialLabsSdkError(self.status)
        if not self.ensure_opengl_loaded() or self.opengl is None:
            raise SpatialLabsSdkError(self.status)

        if view_size is None:
            width, height = self.recommended_view_size((1, 1))
        else:
            width = max(1, int(view_size[0]))
            height = max(1, int(view_size[1]))
        create = getattr(self.opengl, _CREATE_GL_WEAVER_CONCRETE)
        create.restype = ctypes.c_void_p
        create.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        weaver_ptr = create(
            ctypes.c_void_p(self.context),
            ctypes.c_uint(max(1, int(width))),
            ctypes.c_uint(max(1, int(height))),
        )
        if not weaver_ptr:
            raise SpatialLabsSdkError(
                f"GLWeaver::create returned null for {width}x{height}"
            )
        return SpatialLabsGLWeaver(self, int(weaver_ptr), create_error=0)

    def set_lens_hint(self, enabled: bool) -> None:
        if not self.available or not self.displays or not self.lens_hint_pointer:
            return
        normalized = bool(enabled)
        if self._lens_hint_enabled is normalized:
            return
        ptr = ctypes.c_void_p(self.lens_hint_pointer)
        if normalized:
            self.displays.lensEnableHint(ptr)
        else:
            self.displays.lensDisableHint(ptr)
        self._lens_hint_enabled = normalized

    def lens_state(self) -> Tuple[Optional[bool], Optional[bool]]:
        if not self.available or not self.displays or not self.lens_hint_pointer:
            return None, None
        ptr = ctypes.c_void_p(self.lens_hint_pointer)
        try:
            return (
                bool(self.displays.isLensEnabled(ptr)),
                bool(self.displays.isLensEnabledByPreference(ptr)),
            )
        except Exception:
            return None, None

    def recommended_view_size(self, fallback: Tuple[int, int]) -> Tuple[int, int]:
        width = int(self.screen.recommended_views_width or 0)
        height = int(self.screen.recommended_views_height or 0)
        if width <= 0 or height <= 0:
            return max(1, int(fallback[0])), max(1, int(fallback[1]))
        return max(1, width), max(1, height)

    def describe(self) -> str:
        version = self.platform_version or "unknown"
        sw = self.screen.resolution_width
        sh = self.screen.resolution_height
        vw = self.screen.recommended_views_width
        vh = self.screen.recommended_views_height
        if sw > 0 and sh > 0 and vw > 0 and vh > 0:
            return f"SpatialLabs SDK {version} ({sw}x{sh}, views {vw}x{vh})"
        return f"SpatialLabs SDK {version}"

    def shutdown(self) -> None:
        try:
            if self.displays is not None and self.lens_hint_pointer:
                try:
                    self.displays.lensDisableHint(ctypes.c_void_p(self.lens_hint_pointer))
                except Exception:
                    pass
                try:
                    self.displays.deleteSwitchableLensHint(ctypes.c_void_p(self.lens_hint_pointer))
                except Exception:
                    pass
        finally:
            self.lens_hint_pointer = None
            self._lens_hint_enabled = None

        try:
            if self.core is not None and self.context:
                self.core.deleteSRContext(ctypes.c_void_p(self.context))
        except Exception:
            pass
        self.context = None
        self.screen_pointer = None
        self.available = False
