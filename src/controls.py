"""Controls / layout helpers extracted from gui.py (no behavior change).

Contains styling constants and helper builders for the left control pane
(platform radios, buttons, checkboxes, scales). GUI keeps the same public
API but delegates building to these helpers.
"""
from __future__ import annotations

import tkinter as tk

# Re-export color constants for other modules (single source is gui.py)
try:
    from gui import BG_BASE, BG_CARD, BG_INPUT, BG_ELEVATED, BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, ACCENT, ACCENT_HI, CYAN, SUCCESS, SUCCESS_BG, DANGER, DANGER_BG, WARNING
except Exception:
    BG_BASE = "#F4F1EA"; BG_CARD = "#FFFFFF"; BG_INPUT = "#FAF8F4"; BG_ELEVATED = "#ECE7DE"; BORDER = "#D7D0C4"; TEXT_PRIMARY = "#000000"; TEXT_SECONDARY="#000000"; TEXT_MUTED="#000000"; ACCENT="#B85C35"; ACCENT_HI="#974923"; CYAN="#3F7180"; SUCCESS="#2E7D5B"; SUCCESS_BG="#E6F2EC"; DANGER="#B54747"; DANGER_BG="#F8E9E7"; WARNING="#B7832F"


def update_platform_radios(gui):
    """Sync radio bg/fg so selected platform is clearly highlighted."""
    try:
        sel = gui.website.get() if hasattr(gui, "website") else "chesscom"
        is_chess = sel == "chesscom"
        gui.chesscom_radio_button.configure(
            bg=ACCENT if is_chess else BG_ELEVATED,
            fg="#FFFFFF" if is_chess else "#000000",
            activebackground=ACCENT_HI if is_chess else BG_ELEVATED,
            selectcolor=ACCENT,
        )
        gui.lichess_radio_button.configure(
            bg=ACCENT if not is_chess else BG_ELEVATED,
            fg="#FFFFFF" if not is_chess else "#000000",
            activebackground=ACCENT_HI if not is_chess else BG_ELEVATED,
            selectcolor=ACCENT,
        )
    except Exception:
        pass


def clamp_variable(variable, minimum, maximum):
    try:
        variable.set(max(minimum, min(maximum, int(variable.get()))))
    except Exception:
        variable.set(minimum)
