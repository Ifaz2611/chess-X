from __future__ import annotations

import os
import platform
import sys
import subprocess
import shutil
from typing import Tuple, Optional

from utilities import get_logger

logger = get_logger("platform_info")

# ------------------------------------------------------------------
# Basic OS / display server predicates
# ------------------------------------------------------------------

def is_macos() -> bool:
    return platform.system() == "Darwin"

def is_windows() -> bool:
    return platform.system() == "Windows"

def is_linux() -> bool:
    return platform.system() == "Linux"

def is_wayland() -> bool:
    """Detect Wayland session (same logic as utilities.is_wayland, canonical here)."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))

def is_x11() -> bool:
    if is_wayland():
        return False
    # If DISPLAY is set and not wayland, assume X11 on Linux
    return is_linux() and bool(os.environ.get("DISPLAY"))

def is_retina() -> bool:
    """True if running on macOS with retina scaling >1. Best-effort."""
    if not is_macos():
        return False
    try:
        # Use PyQt6 devicePixelRatio if available
        from PyQt6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            for screen in QGuiApplication.screens():
                try:
                    if screen.devicePixelRatio() > 1.0:
                        return True
                except Exception:
                    continue
        # Fallback: macOS system_profiler check (expensive – cache)
    except Exception:
        pass
    # Fallback env heuristic: CGDisplay check via NSScreen scale is >1 on retina
    # We can't reliably detect without GUI toolkit, assume False here and let
    # display helpers do runtime correction.
    return False

# ------------------------------------------------------------------
# macOS permission helpers
# ------------------------------------------------------------------

def check_macos_permissions() -> Tuple[bool, str]:
    """Check macOS accessibility / screen-recording permissions.

    Returns (ok, message). On non-macOS, returns (True, "not macos").
    This is best-effort: we cannot programmatically check TCC db without
    private APIs, so we probe known failure modes.

    Guidance mirrors Apple docs: System Settings → Privacy & Security →
    Accessibility + Screen Recording.
    """
    if not is_macos():
        return True, "not macos"
    # Check if process has accessibility access via optional AXIsProcessTrusted
    # Use AppleScript fallback; most reliable is to try pynput/pyautogui and
    # catch permission error.
    messages = []
    # 1) Try to import Quartz and check AXIsProcessTrusted if available
    try:
        # PyObjC's ApplicationServices flag
        import ApplicationServices  # type: ignore
        trusted = ApplicationServices.AXIsProcessTrusted()
        if not trusted:
            messages.append(
                "Accessibility permission not granted. "
                "Enable: System Settings → Privacy & Security → Accessibility → Chess-X / Terminal / Python."
            )
        else:
            messages.append("Accessibility: ok")
    except ImportError:
        messages.append("PyObjC not installed – cannot verify Accessibility; if clicks fail, grant Accessibility to Terminal/Python.")
    except Exception as e:
        logger.debug("macOS AX check failed: %s", e)

    # 2) Screen recording hint – no reliable API, just guidance
    # CGWindowListCreateImage will fail silently if not permitted
    try:
        # Try lightweight screencapture test (if available)
        # We just advise; actual overlay uses Qt which needs Screen Recording
        messages.append(
            "Screen Recording may be required for overlay/screenshots. "
            "Enable: System Settings → Privacy & Security → Screen Recording → Chess-X / Terminal."
        )
    except Exception:
        pass

    ok = True
    # If PyObjC said not trusted, ok = False
    if any("not granted" in m for m in messages):
        ok = False
    return ok, " | ".join(messages)


def check_macos_chromedriver() -> Tuple[bool, str]:
    """Hint for ChromeDriver on macOS (quarantine / Rosetta)."""
    if not is_macos():
        return True, "not macos"
    # ChromeDriver downloaded via webdriver-manager may be quarantined
    # Common fix: xattr -d com.apple.quarantine <path>
    return True, (
        "On macOS, if ChromeDriver fails with 'cannot be opened' or permission error, run: "
        "xattr -d com.apple.quarantine $(find ~/.wdm -name 'chromedriver' -type f)  "
        "and ensure Chrome is installed (Arm vs Intel). webdriver-manager handles Arch automatically."
    )

# ------------------------------------------------------------------
# DPI / scaling helpers
# ------------------------------------------------------------------

def get_device_pixel_ratio() -> float:
    """Return current screen devicePixelRatio (1.0 on non-retina)."""
    try:
        from PyQt6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            screens = QGuiApplication.screens()
            if screens:
                # Prefer primary screen
                try:
                    return float(screens[0].devicePixelRatio())
                except Exception:
                    pass
        # Try creating a temporary QGuiApplication offscreen probe is too heavy;
        # fall back to 1.0 or env override.
    except Exception:
        pass
    # Env override for testing
    try:
        env = os.environ.get("CHESSX_SCALE_FACTOR")
        if env:
            return float(env)
    except Exception:
        pass
    # macOS retina default is 2.0 if we detect retina via system
    if is_macos():
        # Ask system_profiler for Retina display? lightweight fallback: assume 2.0 on Mac with large res
        # We conservatively return 1.0 and let caller adjust via Qt when available.
        return 1.0
    return 1.0


def get_scaling_factor() -> float:
    """Unified scaling factor for square→screen mapping.

    On macOS retina: devicePixelRatio (e.g., 2.0). On Windows: DPI scale from
    GetDpiForWindow / Tk scaling. On Linux X11: GDK_SCALE / QT_SCALE_FACTOR env.
    """
    # Check env overrides first
    try:
        for key in ("CHESSX_SCALE_FACTOR", "GDK_SCALE", "QT_SCALE_FACTOR"):
            v = os.environ.get(key)
            if v:
                try:
                    f = float(v)
                    if 0.5 <= f <= 4.0:
                        return f
                except ValueError:
                    continue
    except Exception:
        pass

    # Qt devicePixelRatio is most accurate across platforms
    try:
        dpr = get_device_pixel_ratio()
        if dpr != 1.0:
            return dpr
    except Exception:
        pass

    # Windows DPI awareness via ctypes
    if is_windows():
        try:
            import ctypes
            # Try GetDpiForWindow/GetDpiForSystem (Windows 10+)
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor v2
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
            try:
                hdc = ctypes.windll.user32.GetDC(0)
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
                if dpi and dpi != 96:
                    return dpi / 96.0
            except Exception:
                pass
        except Exception:
            pass

    # Tk scaling fallback (works without Qt)
    try:
        import tkinter as tk
        # Only attempt if we can create a hidden root quickly
        # Avoid creating Tk if already have Qt
        from PyQt6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            root = tk.Tk()
            root.withdraw()
            # tk scaling is pixels per point; default 1.0
            try:
                scaling = float(root.tk.call("tk", "scaling"))
                # On Windows, tk scaling maps to DPI/72; normalize so 1.0 = 96 DPI
                # Simpler: return scaling only if clearly >1.2
                if scaling > 1.2:
                    # Rough: DPI 144 => scaling ~2.0 in tk? keep as is for offset correction
                    root.destroy()
                    return scaling
            except Exception:
                pass
            try:
                root.destroy()
            except Exception:
                pass
    except Exception:
        pass

    return 1.0


def apply_retina_correction(x: float, y: float, dpr: Optional[float] = None) -> Tuple[float, float]:
    """Apply retina correction for screen coords if needed. No-op on non-retina."""
    if dpr is None:
        dpr = get_device_pixel_ratio()
    if dpr == 1.0:
        return x, y
    # On macOS, PyAutoGUI coords are in logical points, but Qt overlay uses device pixels.
    # To keep move_executor in sync with overlay, scale logical -> device if needed.
    # Currently we return unscaled (callers decide). This helper documents intent.
    # For backwards compat we return logical coords; overlay code scales itself.
    return x, y


def get_chrome_args_for_platform(browser: str = "chrome") -> list:
    """Return extra Chrome/Chromium args needed for this platform."""
    args: list[str] = []
    if is_linux():
        # Needed for containers & headless-like envs
        args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
        if is_wayland():
            # Force XWayland via xcb until native Wayland Chrome is stable for automation
            # User can override with QT_QPA_PLATFORM=xcb already
            args.append("--ozone-platform-hint=x11")
    if is_macos():
        # macOS specific: disable automation infobar mismatch on retina, allow
        args.extend([
            "--disable-features=VizDisplayCompositor",
        ])
        # On Apple Silicon, ensure Rosetta not needed – webdriver-manager handles it
    return args

# ------------------------------------------------------------------
# Executable helpers
# ------------------------------------------------------------------

def get_stockfish_candidates() -> list[str]:
    """Return platform-specific stockfish binary candidates for packaging."""
    base = ["stockfish", "stockfish.exe" if is_windows() else "stockfish"]
    if is_macos():
        # macOS bundles often use Stockfish.app/Contents/MacOS/stockfish
        base.extend([
            "stockfish-macos",
            "stockfish_apple_silicon",
            "stockfish_x86_64",
        ])
    return base


def is_frozen() -> bool:
    """True if running as PyInstaller / briefcase frozen exe."""
    return getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")

def get_resource_path(relative: str) -> str:
    """Resolve resource path for both dev and frozen (PyInstaller) mode."""
    try:
        if is_frozen():
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            return os.path.join(base, relative)
        return os.path.abspath(relative)
    except Exception:
        return relative
