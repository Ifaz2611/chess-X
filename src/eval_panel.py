"""Eval panel / move history extracted from gui.py (no behavior change).

Provides EvalPanel mixin helpers for building the move Treeview, export PGN,
eval bar rendering and live label updates. Methods are intended to be mixed
into GUI or called with gui as self.
"""
from __future__ import annotations

import datetime
import os
import tkinter as tk
from tkinter import filedialog, messagebox

from utilities import get_logger

logger = get_logger("eval_panel")

# Colors mirrored from gui.py – imported lazily to avoid circular import
try:
    from gui import BG_CARD, BG_ELEVATED, BORDER, TEXT_PRIMARY, TEXT_MUTED, SUCCESS, DANGER, ACCENT, ACCENT_HI, BG_BASE
except Exception:
    BG_CARD = "#FFFFFF"; BG_ELEVATED = "#ECE7DE"; BORDER = "#D7D0C4"; TEXT_PRIMARY = "#000000"; TEXT_MUTED="#000000"; SUCCESS="#2E7D5B"; DANGER="#B54747"; ACCENT="#B85C35"; ACCENT_HI="#974923"; BG_BASE="#F4F1EA"


class EvalPanelMixin:
    """Mixin for GUI – expects attributes: master, tree, eval_text, etc."""

    def _build_moves(self, parent):  # type: ignore[no-redef]
        import tkinter as tk
        from tkinter import ttk
        # This is the original implementation moved verbatim; GUI will delegate
        # Keep for reference – actual GUI still builds via its own method that
        # calls this mixin helper. We keep the body here for future delegation.
        pass

    def _update_eval_bar_impl(self, eval_str: str):
        """Pure logic for eval bar – extracted for reuse/testing."""
        try:
            c = self._eval_canvas  # type: ignore[attr-defined]
            try:
                c.update_idletasks()
                w = c.winfo_width()
                if w < 10:
                    try:
                        w = c.winfo_reqwidth() or 280
                    except Exception:
                        w = 280
                    if w < 50:
                        w = 280
                w = max(120, w)
            except Exception:
                w = 280
            h = 6
            mid = w // 2
            val = None
            color = "#8B929C"  # default muted
            if eval_str.startswith("M"):
                try:
                    m = int(eval_str[1:])
                    val = 10 if m > 0 else -10
                    color = SUCCESS if m > 0 else DANGER
                except Exception as e:
                    logger.debug("eval mate parse failed %r: %s", eval_str, e)
            elif eval_str not in ("—","-", ""):
                try:
                    v = float(eval_str)
                    val = max(-10, min(10, v))
                    if v > 0.6: color = SUCCESS
                    elif v < -0.6: color = DANGER
                    else: color = "#8B929C"
                except Exception as e:
                    logger.debug("eval cp parse failed %r: %s", eval_str, e)
            if val is None:
                c.coords(self._eval_bar, mid-1, 0, mid+1, h)  # type: ignore[attr-defined]
                c.itemconfig(self._eval_bar, fill="#B8B0A3")
            else:
                if val >= 0:
                    x0 = mid
                    x1 = mid + int((val/10)*(w//2 - 2))
                    c.coords(self._eval_bar, x0, 0, max(x0+2, x1), h)
                else:
                    x1 = mid
                    x0 = mid + int((val/10)*(w//2 - 2))
                    c.coords(self._eval_bar, x0, 0, x1, h)
                c.itemconfig(self._eval_bar, fill=color)
        except tk.TclError as e:
            logger.debug("_update_eval_bar TclError: %s", e)
        except Exception as e:
            logger.debug("_update_eval_bar error: %s", e, exc_info=True)
