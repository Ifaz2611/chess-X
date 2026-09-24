from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from utilities import get_logger

logger = get_logger("input_backend")

try:
    from platform_info import is_wayland, is_macos, get_device_pixel_ratio  # type: ignore
except Exception:
    def is_wayland():  # type: ignore
        return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    def is_macos():  # type: ignore
        return platform.system() == "Darwin"
    def get_device_pixel_ratio():  # type: ignore
        return 1.0


# ------------------------------------------------------------------
# Abstract interface
# ------------------------------------------------------------------

class InputBackend(ABC):
    """Unified mouse control interface."""

    name: str = "abstract"

    @abstractmethod
    def moveTo(self, x: float, y: float, duration: float = 0.0):  # noqa: N802
        ...

    @abstractmethod
    def dragTo(self, x: float, y: float, duration: float = 0.0, button: str = "left"):  # noqa: N802
        ...

    @abstractmethod
    def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left"):
        ...

    def position(self) -> Tuple[int, int]:
        return (0, 0)

    def is_available(self) -> bool:
        return True


# ------------------------------------------------------------------
# PyAutoGUI backend (default)
# ------------------------------------------------------------------

class PyAutoGUIBackend(InputBackend):
    name = "pyautogui"

    def __init__(self):
        self._pg = None
        try:
            import pyautogui  # type: ignore
            self._pg = pyautogui
            # Disable failsafe for automated runs but keep pause minimal
            try:
                self._pg.FAILSAFE = False
                self._pg.PAUSE = 0.01
            except Exception:
                pass
        except Exception as e:
            logger.debug("PyAutoGUI import failed: %s", e)
            self._pg = None

    def is_available(self) -> bool:
        if self._pg is None:
            return False
        if is_wayland():
            # PyAutoGUI uses X11 (Xlib) – fails on pure Wayland
            # Still report unavailable so factory picks pynput
            logger.warning("PyAutoGUI unavailable on Wayland – use pynput/xdotool/ydotool")
            return False
        return True

    def moveTo(self, x: float, y: float, duration: float = 0.0):  # noqa: N802
        assert self._pg is not None
        # macOS retina: pyautogui expects logical points; our coords are already logical
        # No extra scaling needed – Qt overlay handles DPR separately
        return self._pg.moveTo(x, y, duration=duration)

    def dragTo(self, x: float, y: float, duration: float = 0.0, button: str = "left"):  # noqa: N802
        assert self._pg is not None
        return self._pg.dragTo(x, y, duration=duration, button=button)

    def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left"):
        assert self._pg is not None
        if x is not None and y is not None:
            return self._pg.click(x=x, y=y, button=button)
        return self._pg.click(button=button)

    def position(self) -> Tuple[int, int]:
        if self._pg is None:
            return (0, 0)
        try:
            p = self._pg.position()
            return (int(p.x), int(p.y))
        except Exception:
            return (0, 0)


# ------------------------------------------------------------------
# pynput backend (Wayland + Linux fallback, macOS fallback)
# ------------------------------------------------------------------

class PynputBackend(InputBackend):
    name = "pynput"

    def __init__(self):
        self._ctrl = None
        self._btn = None
        try:
            from pynput.mouse import Controller, Button  # type: ignore
            self._ctrl = Controller()
            self._btn = Button
            # Test that controller works
            try:
                _ = self._ctrl.position
            except Exception as e:
                logger.debug("pynput controller test failed: %s", e)
                self._ctrl = None
        except Exception as e:
            logger.debug("pynput import failed: %s", e)
            self._ctrl = None

    def is_available(self) -> bool:
        return self._ctrl is not None

    def moveTo(self, x: float, y: float, duration: float = 0.0):  # noqa: N802
        assert self._ctrl is not None
        # pynput has no duration – interpolate if requested
        if duration and duration > 0:
            try:
                sx, sy = self._ctrl.position
                steps = max(1, int(duration * 60))
                for i in range(1, steps + 1):
                    t = i / steps
                    nx = sx + (x - sx) * t
                    ny = sy + (y - sy) * t
                    self._ctrl.position = (int(nx), int(ny))
                    time.sleep(duration / steps)
                return
            except Exception:
                pass
        self._ctrl.position = (int(x), int(y))
        if duration:
            time.sleep(duration)

    def dragTo(self, x: float, y: float, duration: float = 0.0, button: str = "left"):  # noqa: N802
        assert self._ctrl is not None and self._btn is not None
        btn = self._btn.left if button == "left" else self._btn.right if button == "right" else self._btn.left
        try:
            self._ctrl.press(btn)
            self.moveTo(x, y, duration=duration)
            self._ctrl.release(btn)
        except Exception as e:
            logger.debug("pynput dragTo failed: %s", e)
            # fallback: move then click
            try:
                self._ctrl.position = (int(x), int(y))
            except Exception:
                pass

    def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left"):
        assert self._ctrl is not None and self._btn is not None
        btn = self._btn.left if button == "left" else self._btn.right if button == "right" else self._btn.left
        if x is not None and y is not None:
            self._ctrl.position = (int(x), int(y))
        self._ctrl.click(btn, 1)

    def position(self) -> Tuple[int, int]:
        if self._ctrl is None:
            return (0, 0)
        try:
            return tuple(int(v) for v in self._ctrl.position)  # type: ignore
        except Exception:
            return (0, 0)


# ------------------------------------------------------------------
# xdotool backend (X11/Wayland via XWayland)
# ------------------------------------------------------------------

class XdotoolBackend(InputBackend):
    name = "xdotool"

    def __init__(self):
        self._bin = shutil.which("xdotool")
        # xdotool only works under X11/XWayland; check DISPLAY
        if self._bin and is_wayland():
            # Under pure Wayland without XWayland, xdotool fails – check if we have XWayland
            # Still allow it; it will fail gracefully at runtime and we log
            pass

    def is_available(self) -> bool:
        return self._bin is not None and not is_macos()

    def _run(self, *args: str):
        try:
            subprocess.run([self._bin] + list(args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception as e:
            logger.debug("xdotool %s failed: %s", args, e)
            raise

    def moveTo(self, x: float, y: float, duration: float = 0.0):  # noqa: N802
        self._run("mousemove", str(int(x)), str(int(y)))
        if duration:
            time.sleep(duration)

    def dragTo(self, x: float, y: float, duration: float = 0.0, button: str = "left"):  # noqa: N802
        btn = "1" if button == "left" else "3"
        self._run("mousedown", btn)
        self._run("mousemove", str(int(x)), str(int(y)))
        if duration:
            time.sleep(duration)
        self._run("mouseup", btn)

    def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left"):
        if x is not None and y is not None:
            self._run("mousemove", str(int(x)), str(int(y)))
        btn = "1" if button == "left" else "3"
        self._run("click", btn)


# ------------------------------------------------------------------
# ydotool backend (Wayland native, needs ydotoold daemon)
# ------------------------------------------------------------------

class YdotoolBackend(InputBackend):
    name = "ydotool"

    def __init__(self):
        self._bin = shutil.which("ydotool")

    def is_available(self) -> bool:
        return self._bin is not None and is_wayland()

    def _run(self, *args: str):
        try:
            subprocess.run([self._bin] + list(args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception as e:
            logger.debug("ydotool %s failed: %s", args, e)
            raise

    def moveTo(self, x: float, y: float, duration: float = 0.0):  # noqa: N802
        self._run("mousemove", "--absolute", str(int(x)), str(int(y)))
        if duration:
            time.sleep(duration)

    def dragTo(self, x: float, y: float, duration: float = 0.0, button: str = "left"):  # noqa: N802
        # ydotool drag = mousedown, move, mouseup
        btn_code = "0x110" if button == "left" else "0x111"  # BTN_LEFT / BTN_RIGHT
        # ydotool click syntax varies; use mousemove + click
        self._run("mousemove", "--absolute", str(int(x)), str(int(y)))
        # Note: ydotool's click handling is limited; we emulate with mousedown/up
        try:
            subprocess.run([self._bin, "click", btn_code], check=True, timeout=2)
        except Exception:
            pass

    def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left"):
        if x is not None and y is not None:
            self._run("mousemove", "--absolute", str(int(x)), str(int(y)))
        btn_code = "0x110" if button == "left" else "0x111"
        try:
            subprocess.run([self._bin, "click", btn_code], check=True, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.debug("ydotool click failed: %s", e)
            raise


# ------------------------------------------------------------------
# Dummy backend (for tests / headless)
# ------------------------------------------------------------------

class DummyBackend(InputBackend):
    name = "dummy"
    def __init__(self):
        self.moves: list[tuple] = []
    def moveTo(self, x: float, y: float, duration: float = 0.0):  # noqa: N802
        self.moves.append(("moveTo", x, y))
    def dragTo(self, x: float, y: float, duration: float = 0.0, button: str = "left"):  # noqa: N802
        self.moves.append(("dragTo", x, y, button))
    def click(self, x: Optional[float] = None, y: Optional[float] = None, button: str = "left"):
        self.moves.append(("click", x, y, button))


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------

def get_input_backend(preferred: Optional[str] = None) -> InputBackend:
    """Return best available InputBackend for this platform.

    Priority:
      - If preferred is given and available, use it.
      - Wayland: pynput -> ydotool -> xdotool -> pyautogui -> dummy
      - macOS: pyautogui -> pynput -> dummy (pyautogui handles retina)
      - X11/Windows: pyautogui -> pynput -> xdotool -> dummy
    Env CHESSX_INPUT_BACKEND can force a backend name.
    """
    forced = preferred or os.environ.get("CHESSX_INPUT_BACKEND")
    if forced:
        forced = forced.lower()
        candidates = _all_backends()
        for b in candidates:
            if b.name == forced and b.is_available():
                logger.info("Using forced input backend: %s", b.name)
                return b
        logger.warning("Forced backend %s not available, falling back to auto", forced)

    wayland = is_wayland()
    macos = is_macos()

    # Build priority list
    if wayland:
        order = [PynputBackend, YdotoolBackend, XdotoolBackend, PyAutoGUIBackend, DummyBackend]
    elif macos:
        order = [PyAutoGUIBackend, PynputBackend, DummyBackend]
    else:
        order = [PyAutoGUIBackend, PynputBackend, XdotoolBackend, DummyBackend]

    for cls in order:
        try:
            inst = cls()
            if inst.is_available():
                logger.info("Selected input backend: %s (Wayland=%s macOS=%s)", inst.name, wayland, macos)
                return inst
        except Exception as e:
            logger.debug("Backend %s init failed: %s", cls.__name__, e)
            continue

    logger.warning("No input backend available, using DummyBackend (moves will be no-ops)")
    return DummyBackend()


def _all_backends() -> list[InputBackend]:
    out: list[InputBackend] = []
    for cls in [PyAutoGUIBackend, PynputBackend, XdotoolBackend, YdotoolBackend, DummyBackend]:
        try:
            out.append(cls())
        except Exception:
            continue
    return out


def list_available_backends() -> list[str]:
    """Return names of available backends on this host (for diagnostics)."""
    names: list[str] = []
    for cls in [PyAutoGUIBackend, PynputBackend, XdotoolBackend, YdotoolBackend]:
        try:
            inst = cls()
            if inst.is_available():
                names.append(inst.name)
        except Exception:
            continue
    if not names:
        names.append("dummy")
    return names
