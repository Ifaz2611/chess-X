"""Hotkey handling extracted from gui.py (no behavior change).

Provides HotkeyManager which polls keyboard (keyboard / pynput fallback) and
triggers GUI actions (start/stop/kill/manual move). Also handles Linux
permission warnings.
"""
from __future__ import annotations

import time
import threading
import tkinter as tk

from utilities import get_keyboard_handler, get_logger, check_linux_input_permissions, is_wayland

logger = get_logger("hotkeys")


class HotkeyManager:
    """Polls keyboard in a daemon thread and dispatches to GUI callbacks."""

    def __init__(self, gui):
        self.gui = gui
        self._kb, self._backend = get_keyboard_handler()
        if self._kb is None:
            try:
                import keyboard as _kb
                self._kb = _kb
                self._backend = "keyboard"
            except Exception:
                self._kb = None
                self._backend = None
        if is_wayland():
            logger.warning("Wayland detected - hotkeys may not work reliably")
        ok, msg = check_linux_input_permissions()
        if not ok:
            logger.warning(msg)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.debug("HotkeyManager started (backend=%s)", self._backend)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self.gui.exit and not self._stop.is_set():
            time.sleep(0.1)
            if self._kb is None:
                continue
            try:
                # Emergency kill - works even without browser
                try:
                    if self._kb.is_pressed("esc") or self._kb.is_pressed("f12"):
                        try:
                            self.gui.master.after(0, self.gui.emergency_kill)
                        except Exception:
                            self.gui.emergency_kill()
                        time.sleep(0.5)
                        continue
                except Exception:
                    pass
                if not self.gui.opened_browser:
                    continue
                if self._kb.is_pressed("1"):
                    try:
                        self.gui.master.after(0, self.gui.on_start_button_listener)
                    except Exception:
                        self.gui.on_start_button_listener()
                    time.sleep(0.5)
                elif self._kb.is_pressed("2"):
                    try:
                        self.gui.master.after(0, self.gui.on_stop_button_listener)
                    except Exception:
                        self.gui.on_stop_button_listener()
                    time.sleep(0.5)
            except Exception as e:
                logger.debug("keypress poll error: %s", e)
                time.sleep(0.3)
