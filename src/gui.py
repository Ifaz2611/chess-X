import os
import sys
import platform
import atexit
import signal
import logging
import tempfile
import multiprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkFont
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common import WebDriverException
from overlay import run
from stockfish_bot import StockfishBot
from utilities import get_logger, is_wayland, check_linux_input_permissions, get_keyboard_handler

logger = get_logger("gui")

# Keyboard fallback
_kb, _kb_backend = get_keyboard_handler()
if _kb is None:
    try:
        import keyboard as _kb
        _kb_backend = "keyboard"
    except Exception:
        _kb = None
        _kb_backend = None

if is_wayland():
    logger.warning("Wayland detected – PyAutoGUI and overlay may not work. Use X11/Xorg or QT_QPA_PLATFORM=xcb")

_ok, _msg = check_linux_input_permissions()
if not _ok:
    logger.warning(_msg)

# ─────────────────────────────────────────────────────────────
#  DESIGN TOKENS — modern dark chess theme
# ─────────────────────────────────────────────────────────────
BG_BASE       = "#0B0F14"   # page
BG_CARD       = "#151B23"   # card
BG_CARD_HI    = "#1C242F"   # card hover / elevated
BG_ELEVATED   = "#232E3C"   # inputs / trough
BG_INPUT      = "#1E2A3A"
BORDER        = "#1F2A38"
BORDER_LIGHT  = "#2A3A4E"
TEXT_PRIMARY  = "#F1F5F9"
TEXT_SECONDARY= "#94A3B8"
TEXT_MUTED    = "#64748B"
ACCENT        = "#8B5CF6"   # violet
ACCENT_HI     = "#7C3AED"
ACCENT_CYAN   = "#06B6D4"
ACCENT_EMERALD= "#10B981"
SUCCESS       = "#10B981"
SUCCESS_BG    = "#052E1C"
DANGER        = "#EF4444"
DANGER_BG     = "#2E0D12"
WARNING       = "#F59E0B"
WARNING_BG    = "#2E1F0A"


def _hex_to_rgb(h):
    h=h.lstrip("#")
    return tuple(int(h[i:i+2],16) for i in (0,2,4))


class GUI:
    def __init__(self, master):
        self.master = master
        self.exit = False
        self.chrome = None
        self.chrome_url = None
        self.chrome_session_id = None
        self.stockfish_bot_pipe = None
        self.overlay_screen_pipe = None
        self.stockfish_bot_process = None
        self.overlay_screen_process = None
        self.restart_after_stopping = False
        self.match_moves = []
        self._overlay_queue = None

        master.title("CHESS-X  —  Stockfish Bot")
        master.geometry("895x785")
        master.minsize(895, 785)
        try:
            # keep reference so image doesn't get GC'd
            self._pawn_img = tk.PhotoImage(file="src/assets/pawn_32x32.png")
            # upscale a bit with zoom if tiny, else keep
            master.iconphoto(True, self._pawn_img)
        except Exception:
            self._pawn_img = None
        master.resizable(False, False)
        master.configure(bg=BG_BASE)
        master.attributes("-topmost", True)
        master.protocol("WM_DELETE_WINDOW", self.on_close_listener)

        # Graceful shutdown handlers
        atexit.register(self._cleanup)
        try:
            signal.signal(signal.SIGINT, lambda s, f: self.on_close_listener())
            signal.signal(signal.SIGTERM, lambda s, f: self.on_close_listener())
        except Exception:
            pass

        # ── Fonts ─────────────────────────────────────
        self.F_TITLE      = tkFont.Font(family="Segoe UI", size=14, weight="bold")
        self.F_SUB        = tkFont.Font(family="Segoe UI", size=8)
        self.F_CARD_TITLE = tkFont.Font(family="Segoe UI", size=7, weight="bold")
        self.F_LABEL      = tkFont.Font(family="Segoe UI", size=8)
        self.F_LABEL_B    = tkFont.Font(family="Segoe UI", size=8, weight="bold")
        self.F_VALUE      = tkFont.Font(family="Consolas", size=9, weight="bold")
        self.F_BTN        = tkFont.Font(family="Segoe UI", size=9, weight="bold")
        self.F_BTN_SM     = tkFont.Font(family="Segoe UI", size=8, weight="bold")
        self.F_TREE_HEAD  = tkFont.Font(family="Segoe UI", size=8, weight="bold")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Treeview dark style
        style.configure("Treeview",
                        background=BG_CARD, foreground=TEXT_PRIMARY,
                        fieldbackground=BG_CARD, rowheight=23,
                        borderwidth=0, relief="flat", font=("Segoe UI", 8))
        style.configure("Treeview.Heading",
                        background="#1E293B", foreground=TEXT_SECONDARY,
                        relief="flat", font=("Segoe UI", 8, "bold"), padding=6)
        style.map("Treeview.Heading", background=[("active", "#25344A")])
        style.configure("Vertical.TScrollbar",
                        background=BG_ELEVATED, troughcolor=BG_CARD,
                        borderwidth=0, arrowsize=0)
        style.map("Vertical.TScrollbar", background=[("active", BORDER_LIGHT)])
        style.configure("Horizontal.TScale",
                        background=BG_CARD, troughcolor=BG_ELEVATED,
                        sliderlength=16, borderwidth=0)

        # ═══════════════════════════════════════════════════
        #  HEADER
        # ═══════════════════════════════════════════════════
        header = tk.Frame(master, bg=BG_CARD, height=62)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        # subtle inner highlight line at top
        tk.Frame(header, bg="#1F2E44", height=1).pack(fill="x", side="top")
        header_inner = tk.Frame(header, bg=BG_CARD)
        header_inner.pack(fill="both", expand=True, padx=16, pady=9)

        h_left = tk.Frame(header_inner, bg=BG_CARD)
        h_left.pack(side="left", anchor="w")
        # pawn badge
        badge = tk.Frame(h_left, bg="#1E293B", highlightbackground=BORDER, highlightthickness=1)
        badge.pack(side="left", padx=(0, 12))
        badge_inner = tk.Frame(badge, bg="#1E293B")
        badge_inner.pack(padx=7, pady=6)
        if self._pawn_img is not None:
            # pawn image is light; place on dark badge with no extra bg
            tk.Label(badge_inner, image=self._pawn_img, bg="#1E293B").pack()
        else:
            tk.Label(badge_inner, text="♞", font=("Segoe UI", 18), fg=ACCENT, bg="#1E293B").pack()

        title_stack = tk.Frame(h_left, bg=BG_CARD)
        title_stack.pack(side="left", anchor="w")
        row1 = tk.Frame(title_stack, bg=BG_CARD)
        row1.pack(anchor="w")
        tk.Label(row1, text="CHESS", font=self.F_TITLE, fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left")
        tk.Label(row1, text="-X", font=self.F_TITLE, fg=ACCENT, bg=BG_CARD).pack(side="left")
        tk.Label(row1, text="  v2.1", font=("Segoe UI", 7), fg=TEXT_MUTED, bg=BG_CARD).pack(side="left", padx=(6,0), anchor="s", pady=(6,0))
        tk.Label(title_stack, text="STOCKFISH  •  CHESS.COM  •  LICHESS", font=self.F_SUB, fg=TEXT_MUTED, bg=BG_CARD).pack(anchor="w")

        h_right = tk.Frame(header_inner, bg=BG_CARD)
        h_right.pack(side="right", anchor="e")
        # live indicator dot + status pill
        self._status_pill = tk.Frame(h_right, bg=DANGER_BG, highlightbackground="#3A1A1E", highlightthickness=1)
        self._status_pill.pack(side="top", anchor="e")
        pill_inner = tk.Frame(self._status_pill, bg=DANGER_BG)
        pill_inner.pack(padx=10, pady=5)
        self._status_dot = tk.Canvas(pill_inner, width=8, height=8, bg=DANGER_BG, highlightthickness=0)
        self._status_dot.pack(side="left", padx=(0,6))
        self._dot_oval = self._status_dot.create_oval(0,0,8,8, fill=DANGER, outline="")
        # status_text lives inside pill so pipe handlers still work via fg/bg
        self.status_text = tk.Label(pill_inner, text="INACTIVE", font=("Segoe UI", 7, "bold"), fg=DANGER, bg=DANGER_BG)
        self.status_text.pack(side="left")
        tk.Label(h_right, text="Press  1 ▶ Start   2 ■ Stop   3 ↻ Move", font=("Segoe UI", 7), fg=TEXT_MUTED, bg=BG_CARD).pack(anchor="e", pady=(6,0))

        # hairline under header
        tk.Frame(master, bg=BORDER, height=1).pack(fill="x", side="top")

        # ═══════════════════════════════════════════════════
        #  BODY
        # ═══════════════════════════════════════════════════
        body = tk.Frame(master, bg=BG_BASE)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        # Scrollable left pane — fixes clipping when content exceeds window height
        left_container = tk.Frame(body, bg=BG_BASE, width=330)
        left_container.pack(side="left", fill="y", padx=(0, 10))
        left_container.pack_propagate(False)

        left_canvas = tk.Canvas(left_container, bg=BG_BASE, highlightthickness=0, width=330)
        left_vsb = ttk.Scrollbar(left_container, orient="vertical", command=left_canvas.yview)
        # keep scrollbar subtle on dark bg
        left_canvas.configure(yscrollcommand=lambda f,l: left_vsb.set(f,l))
        left_vsb.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left_frame = tk.Frame(left_canvas, bg=BG_BASE)
        left_win = left_canvas.create_window((0, 0), window=left_frame, anchor="nw")
        def _left_configure(event):
            try:
                bbox = left_canvas.bbox("all")
                if bbox:
                    left_canvas.configure(scrollregion=bbox)
                # match width
                left_canvas.itemconfig(left_win, width=event.width if event.width>0 else 310)
            except tk.TclError as e:
                logger.debug("left_configure failed: %s", e)
        left_frame.bind("<Configure>", _left_configure)
        # inner resize
        def _canvas_configure(event):
            try:
                left_canvas.itemconfig(left_win, width=event.width)
            except tk.TclError as e:
                logger.debug("canvas_configure failed: %s", e)
        left_canvas.bind("<Configure>", _canvas_configure)
        # keep scrollbar subtle — direct set, no lambda wrapper
        left_canvas.configure(yscrollcommand=left_vsb.set)
        # ── mouse wheel — robust, works over any child widget (Windows/macOS/Linux) ──
        def _on_mousewheel(event):
            try:
                # Windows / macOS: event.delta is multiple of 120 (Win) or small values (macOS)
                if getattr(event, "delta", 0):
                    delta = event.delta
                    # On Windows delta is 120/-120 per notch; macOS may be 1/-1
                    if abs(delta) >= 120:
                        steps = int(-1 * (delta / 120))
                    else:
                        steps = -1 if delta > 0 else 1
                    left_canvas.yview_scroll(steps, "units")
                    return "break"
                # Linux: Button-4 (up) / Button-5 (down)
                if getattr(event, "num", None) == 4:
                    left_canvas.yview_scroll(-3, "units")
                    return "break"
                if getattr(event, "num", None) == 5:
                    left_canvas.yview_scroll(3, "units")
                    return "break"
            except tk.TclError as e:
                logger.debug("mousewheel TclError: %s", e)
            except Exception as e:
                logger.debug("mousewheel error: %s", e)
            return "break"

        def _is_over_left():
            try:
                px, py = left_container.winfo_pointerxy()
                rx, ry = left_container.winfo_rootx(), left_container.winfo_rooty()
                rw, rh = left_container.winfo_width(), left_container.winfo_height()
                return rx <= px <= rx + rw and ry <= py <= ry + rh
            except tk.TclError:
                return False
            except Exception:
                return False

        def _global_wheel(event):
            if _is_over_left():
                return _on_mousewheel(event)
            return None

        # Global bindings — check hover before scrolling so right pane doesn't steal
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                left_canvas.bind_all(seq, _global_wheel, add="+")
            except tk.TclError as e:
                logger.debug("bind_all %s failed: %s", seq, e)
        # Direct bindings for cases where bind_all is blocked (ensure break propagation)
        for widget in (left_canvas, left_frame, left_container):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                try:
                    widget.bind(seq, _on_mousewheel, add="+")
                except tk.TclError:
                    pass
        # Keep references so GC doesn't drop callbacks
        self._on_mousewheel = _on_mousewheel
        self._global_wheel = _global_wheel
        self._left_canvas = left_canvas
        self._left_container = left_container

        right_frame = tk.Frame(body, bg=BG_BASE)
        right_frame.pack(side="left", fill="both", expand=True)

        # ── helpers ─────────────────────────────────────
        def card(parent, title, icon="◆"):
            outer = tk.Frame(parent, bg=BORDER, highlightthickness=0)
            outer.pack(fill="x", pady=(0, 10))
            inner = tk.Frame(outer, bg=BG_CARD)
            inner.pack(fill="x", padx=1, pady=1)
            head = tk.Frame(inner, bg=BG_CARD)
            head.pack(fill="x", padx=12, pady=(10, 2))
            tk.Label(head, text=icon, font=("Segoe UI", 7), fg=ACCENT, bg=BG_CARD).pack(side="left", padx=(0,6))
            tk.Label(head, text=title.upper(), font=self.F_CARD_TITLE, fg=TEXT_MUTED, bg=BG_CARD).pack(side="left")
            tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(6, 8))
            content = tk.Frame(inner, bg=BG_CARD)
            content.pack(fill="x", padx=12, pady=(0, 10))
            return inner, content

        def styled_button(parent, text, bg, bg_hover, fg="#FFFFFF", cmd=None, height=1, padx=10):
            btn = tk.Button(parent, text=text, command=cmd,
                            font=self.F_BTN, fg=fg, bg=bg, activebackground=bg_hover,
                            activeforeground=fg, relief="flat", bd=0, padx=padx, pady=6,
                            cursor="hand2", highlightthickness=0)
            # hover
            def on_enter(e): btn.configure(bg=bg_hover, activebackground=bg_hover)
            def on_leave(e): btn.configure(bg=bg, activebackground=bg_hover)
            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            # fake rounded corners via outer frame
            wrapper = tk.Frame(parent, bg=bg, highlightthickness=0)
            # pack wrapper? caller packs btn directly so we just return btn with bindings
            # need to keep wrapper for border radius illusion — just return btn
            wrapper._btn = btn
            return btn

        def make_scale(parent, var, frm, to, label_text):
            row = tk.Frame(parent, bg=BG_CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label_text, font=self.F_LABEL, fg=TEXT_SECONDARY, bg=BG_CARD, width=14, anchor="w").pack(side="left")
            val_lbl = tk.Label(row, textvariable=None, font=("Consolas", 8, "bold"), fg=ACCENT, bg=BG_CARD, width=4, anchor="e")
            # update val_lbl when var changes
            def _upd(*a):
                try: val_lbl.configure(text=str(var.get()))
                except: pass
            try: var.trace_add("write", _upd)
            except: var.trace("w", lambda *a: _upd())
            _upd()
            val_lbl.pack(side="right")
            sc = tk.Scale(row, from_=frm, to=to, orient="horizontal",
                          variable=var, bg=BG_CARD, fg=TEXT_MUTED,
                          troughcolor=BG_ELEVATED, highlightthickness=0,
                          activebackground=ACCENT, sliderlength=14,
                          relief="flat", bd=0, length=150,
                          font=("Segoe UI", 7))
            sc.pack(fill="x", padx=(0,4), pady=(0,0))
            return sc

        def add_int_validation(entry, var, min_v, max_v):
            """Clamp int entries, reject non-numeric input, show red border on error."""
            def _on_focus_out(event):
                try:
                    v = int(var.get())
                except Exception:
                    # try parse entry text directly
                    try:
                        v = int(entry.get().strip())
                        var.set(v)
                    except Exception:
                        entry.configure(highlightbackground=DANGER, highlightcolor=DANGER)
                        return
                # clamp
                orig = v
                v = max(min_v, min(max_v, v))
                if v != orig:
                    var.set(v)
                    try:
                        entry.configure(highlightbackground=WARNING, highlightcolor=WARNING)
                        entry.after(800, lambda: entry.configure(highlightbackground=BORDER, highlightcolor=BORDER))
                    except: pass
                else:
                    entry.configure(highlightbackground=BORDER, highlightcolor=BORDER)
            def _validate(key):
                # allow empty during typing, digits only
                if key == "":
                    return True
                # allow minus? no, only positive
                return key.isdigit()
            try:
                vcmd = (entry.register(_validate), "%P")
                entry.configure(validate="key", validatecommand=vcmd)
            except Exception:
                pass
            entry.bind("<FocusOut>", _on_focus_out)
            entry.bind("<Return>", _on_focus_out)
            return

        # ── CARD: ENGINE READOUT ────────────────────────
        eval_card, eval_content = card(left_frame, "Engine evaluation", "⬢")

        # eval headline + bar
        self.eval_frame = tk.Frame(eval_content, bg=BG_CARD)
        self.eval_frame.pack(fill="x")

        # Eval row with bar
        eval_row = tk.Frame(eval_content, bg=BG_CARD)
        eval_row.pack(fill="x", pady=(0,6))
        tk.Label(eval_row, text="EVAL", font=self.F_LABEL_B, fg=TEXT_MUTED, bg=BG_CARD).pack(side="left")
        self.eval_text = tk.Label(eval_row, text="—", font=("Consolas", 11, "bold"), fg=TEXT_PRIMARY, bg=BG_CARD)
        self.eval_text.pack(side="right")
        # eval bar canvas (small advantage meter)
        self._eval_canvas = tk.Canvas(eval_content, height=6, bg=BG_ELEVATED, highlightthickness=0, bd=0)
        self._eval_canvas.pack(fill="x", pady=(0,10))
        self._eval_bar = self._eval_canvas.create_rectangle(0,0,0,6, fill=TEXT_MUTED, outline="")

        def _eval_grid(parent, icon, label, var_name):
            r = tk.Frame(parent, bg=BG_CARD)
            r.pack(fill="x", pady=3)
            left = tk.Frame(r, bg=BG_CARD)
            left.pack(side="left")
            tk.Label(left, text=icon, font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_CARD, width=2, anchor="center").pack(side="left")
            tk.Label(left, text=label, font=self.F_LABEL, fg=TEXT_SECONDARY, bg=BG_CARD).pack(side="left", padx=(4,0))
            val = tk.Label(r, font=self.F_VALUE, bg=BG_CARD, fg=TEXT_PRIMARY)
            val.pack(side="right", anchor="e")
            return val

        self.wdl_text      = _eval_grid(eval_content, "♟", "WDL", "wdl")
        self.material_text = _eval_grid(eval_content, "◈", "MATERIAL", "mat")
        self.white_acc_text= _eval_grid(eval_content, "⬣", "BOT ACC", "bacc")
        self.black_acc_text= _eval_grid(eval_content, "⬡", "OPPONENT ACC", "oacc")
        # default texts
        for w in (self.wdl_text, self.material_text, self.white_acc_text, self.black_acc_text):
            w.configure(text="—", fg=TEXT_MUTED)

        # wayland / permission warnings inside card as banners
        banner_parent = eval_content
        if is_wayland():
            b = tk.Frame(banner_parent, bg=WARNING_BG, highlightbackground="#3B2A0A", highlightthickness=1)
            b.pack(fill="x", pady=(8,0))
            tk.Label(b, text="⚠  Wayland detected — overlay may fail (use X11)", font=("Segoe UI", 7), fg=WARNING, bg=WARNING_BG, wraplength=270, justify="left").pack(padx=8, pady=5, anchor="w")
        if not _ok and platform.system() == "Linux":
            b = tk.Frame(banner_parent, bg=WARNING_BG, highlightbackground="#3B2A0A", highlightthickness=1)
            b.pack(fill="x", pady=(4,0))
            tk.Label(b, text="⚠  keyboard may need sudo / input group", font=("Segoe UI", 7), fg=WARNING, bg=WARNING_BG, wraplength=270, justify="left").pack(padx=8, pady=5, anchor="w")

        # ── CARD: PLATFORM ──────────────────────────────
        plat_card, plat_content = card(left_frame, "Platform", "◉")
        self.website = tk.StringVar(value="chesscom")

        # pill radio buttons
        pill = tk.Frame(plat_content, bg=BG_ELEVATED, highlightthickness=0)
        pill.pack(fill="x", pady=2)
        pill_inner = tk.Frame(pill, bg=BG_ELEVATED)
        pill_inner.pack(padx=3, pady=3, fill="x")

        def pill_radio(parent, text, value):
            rb = tk.Radiobutton(parent, text=text, variable=self.website, value=value,
                                indicatoron=0, selectcolor=ACCENT, bg=BG_ELEVATED,
                                fg=TEXT_SECONDARY, activebackground=BG_ELEVATED,
                                activeforeground=TEXT_PRIMARY, relief="flat", bd=0,
                                font=("Segoe UI", 8, "bold"), padx=12, pady=6,
                                cursor="hand2", highlightthickness=0, offrelief="flat", overrelief="flat")
            # dynamic colors on select
            def _refresh(*a):
                if self.website.get()==value:
                    rb.configure(bg=ACCENT, fg="#FFFFFF", activebackground=ACCENT_HI)
                else:
                    rb.configure(bg=BG_ELEVATED, fg=TEXT_SECONDARY, activebackground=BG_CARD_HI)
            try: self.website.trace_add("write", _refresh)
            except: self.website.trace("w", lambda *a: _refresh())
            _refresh()
            rb.pack(side="left", fill="x", expand=True, padx=2)
            return rb
        self.chesscom_radio_button = pill_radio(pill_inner, "♚  Chess.com", "chesscom")
        self.lichess_radio_button  = pill_radio(pill_inner, "♞  Lichess.org", "lichess")

        # ── CARD: CONTROLS ──────────────────────────────
        ctrl_card, ctrl_content = card(left_frame, "Controls", "▶")

        self.opening_browser = False
        self.opened_browser = False
        # Browser button — cyan / outline style
        self.open_browser_button = tk.Button(ctrl_content, text="↗   OPEN BROWSER", command=self.on_open_browser_button_listener,
                                             font=self.F_BTN, fg="#FFFFFF", bg="#0E7490", activebackground="#155E75",
                                             activeforeground="#FFFFFF", relief="flat", bd=0, padx=10, pady=9, cursor="hand2", highlightthickness=0)
        self.open_browser_button.pack(fill="x", pady=(0,8))
        def _hover_browser(e, enter):
            if self.opened_browser: return
            self.open_browser_button.configure(bg="#155E75" if enter else "#0E7490")
        self.open_browser_button.bind("<Enter>", lambda e: _hover_browser(e, True))
        self.open_browser_button.bind("<Leave>", lambda e: _hover_browser(e, False))

        self.running = False
        self.start_button = tk.Button(ctrl_content, text="▶   START ENGINE", command=self.on_start_button_listener,
                                      font=self.F_BTN, fg="#FFFFFF", bg=ACCENT, activebackground=ACCENT_HI,
                                      activeforeground="#FFFFFF", relief="flat", bd=0, padx=10, pady=10, cursor="hand2", highlightthickness=0,
                                      state="disabled", disabledforeground="#FFFFFF")
        self.start_button.pack(fill="x")
        # keep hover for start (only when enabled)
        def _hover_start(e, enter):
            if str(self.start_button["state"])=="disabled": return
            # detect STOP via substring (covers "■   STOP" etc.)
            txt = str(self.start_button["text"]).lower()
            is_stop = "stop" in txt
            if is_stop:
                self.start_button.configure(bg="#B91C1C" if enter else "#DC2626")
            else:
                self.start_button.configure(bg=ACCENT_HI if enter else ACCENT)
        self.start_button.bind("<Enter>", lambda e: _hover_start(e, True))
        self.start_button.bind("<Leave>", lambda e: _hover_start(e, False))

        # ── CARD: MODES ─────────────────────────────────
        modes_card, modes_content = card(left_frame, "Modes", "⚙")

        def styled_check(parent, text, var):
            cb = tk.Checkbutton(parent, text=text, variable=var,
                                bg=BG_CARD, fg=TEXT_SECONDARY, selectcolor=BG_ELEVATED,
                                activebackground=BG_CARD, activeforeground=TEXT_PRIMARY,
                                font=("Segoe UI", 8), anchor="w", padx=0, pady=2,
                                highlightthickness=0, bd=0, cursor="hand2")
            cb.pack(fill="x", anchor="w")
            return cb

        self.enable_manual_mode = tk.BooleanVar(value=False)
        self.manual_mode_checkbox = styled_check(modes_content, "  Manual Mode  (play with 3)", self.enable_manual_mode)
        try: self.manual_mode_checkbox.configure(command=self.on_manual_mode_checkbox_listener)
        except: pass
        self.manual_mode_frame = tk.Frame(modes_content, bg=BG_CARD)
        # banner shown when manual on
        inner_banner = tk.Frame(self.manual_mode_frame, bg="#1A2332", highlightbackground="#23344A", highlightthickness=1)
        inner_banner.pack(fill="x", pady=(4,2))
        self.manual_mode_label = tk.Label(inner_banner, text="●  Press  3  to make a move", font=("Segoe UI", 7, "bold"), fg=ACCENT_CYAN, bg="#1A2332")
        self.manual_mode_label.pack(padx=8, pady=5, anchor="w")

        self.enable_mouseless_mode = tk.BooleanVar(value=False)
        self.mouseless_mode_checkbox = styled_check(modes_content, "  Mouseless Mode  (Lichess only)", self.enable_mouseless_mode)

        self.enable_non_stop_puzzles = tk.IntVar(value=0)
        self.non_stop_puzzles_check_button = styled_check(modes_content, "  Non-stop puzzles", self.enable_non_stop_puzzles)

        self.enable_non_stop_matches = tk.IntVar(value=0)
        self.non_stop_matches_check_button = styled_check(modes_content, "  Non-stop online matches", self.enable_non_stop_matches)

        self.enable_bongcloud = tk.IntVar()
        self.bongcloud_check_button = styled_check(modes_content, "  Bongcloud  😎", self.enable_bongcloud)

        # mouse latency row (compact)
        lat_row = tk.Frame(modes_content, bg=BG_CARD)
        lat_row.pack(fill="x", pady=(8,0))
        tk.Label(lat_row, text="Mouse latency", font=self.F_LABEL, fg=TEXT_MUTED, bg=BG_CARD).pack(side="left")
        self.mouse_latency = tk.DoubleVar(value=0.0)
        tk.Label(lat_row, textvariable=self.mouse_latency, font=("Consolas", 8), fg=TEXT_SECONDARY, bg=BG_CARD, width=5, anchor="e").pack(side="right")
        self.mouse_latency_scale = tk.Scale(modes_content, from_=0.0, to=15, resolution=0.2, orient="horizontal", variable=self.mouse_latency,
                                            bg=BG_CARD, fg=TEXT_MUTED, troughcolor=BG_ELEVATED, highlightthickness=0,
                                            activebackground=ACCENT, sliderlength=14, relief="flat", bd=0, length=200)
        self.mouse_latency_scale.pack(fill="x")

        # ── CARD: STOCKFISH ─────────────────────────────
        sf_card, sf_content = card(left_frame, "Stockfish parameters", "⬢")

        # slow mover
        slow_row = tk.Frame(sf_content, bg=BG_CARD)
        slow_row.pack(fill="x", pady=2)
        self.slow_mover_label = tk.Label(slow_row, text="Slow Mover", font=self.F_LABEL, fg=TEXT_SECONDARY, bg=BG_CARD, width=14, anchor="w")
        self.slow_mover_label.pack(side="left")
        self.slow_mover = tk.IntVar(value=100)
        self.slow_mover_entry = tk.Entry(slow_row, textvariable=self.slow_mover, justify="center", width=8,
                                         font=("Consolas", 9), fg=TEXT_PRIMARY, bg=BG_INPUT, relief="flat",
                                         highlightbackground=BORDER, highlightthickness=1, insertbackground=TEXT_PRIMARY)
        self.slow_mover_entry.pack(side="right", ipady=3)
        add_int_validation(self.slow_mover_entry, self.slow_mover, 10, 1000)

        self.skill_level = tk.IntVar(value=20)
        self.skill_level_scale = make_scale(sf_content, self.skill_level, 0, 20, "Skill Level")
        self.stockfish_depth = tk.IntVar(value=15)
        self.stockfish_depth_scale = make_scale(sf_content, self.stockfish_depth, 1, 20, "Depth")

        mem_row = tk.Frame(sf_content, bg=BG_CARD)
        mem_row.pack(fill="x", pady=(6,2))
        tk.Label(mem_row, text="Memory", font=self.F_LABEL, fg=TEXT_SECONDARY, bg=BG_CARD, width=14, anchor="w").pack(side="left")
        self.memory = tk.IntVar(value=512)
        self.memory_entry = tk.Entry(mem_row, textvariable=self.memory, justify="center", width=7,
                                     font=("Consolas", 9), fg=TEXT_PRIMARY, bg=BG_INPUT, relief="flat",
                                     highlightbackground=BORDER, highlightthickness=1, insertbackground=TEXT_PRIMARY)
        self.memory_entry.pack(side="left")
        tk.Label(mem_row, text="MB", font=self.F_LABEL, fg=TEXT_MUTED, bg=BG_CARD).pack(side="left", padx=(6,0))
        add_int_validation(self.memory_entry, self.memory, 16, 8192)

        thr_row = tk.Frame(sf_content, bg=BG_CARD)
        thr_row.pack(fill="x", pady=2)
        tk.Label(thr_row, text="CPU Threads", font=self.F_LABEL, fg=TEXT_SECONDARY, bg=BG_CARD, width=14, anchor="w").pack(side="left")
        self.cpu_threads = tk.IntVar(value=1)
        self.cpu_threads_entry = tk.Entry(thr_row, textvariable=self.cpu_threads, justify="center", width=7,
                                          font=("Consolas", 9), fg=TEXT_PRIMARY, bg=BG_INPUT, relief="flat",
                                          highlightbackground=BORDER, highlightthickness=1, insertbackground=TEXT_PRIMARY)
        self.cpu_threads_entry.pack(side="left")
        add_int_validation(self.cpu_threads_entry, self.cpu_threads, 1, 32)

        # ── CARD: MISC ──────────────────────────────────
        misc_card, misc_content = card(left_frame, "Misc", "—")

        self.enable_topmost = tk.IntVar(value=1)
        self.topmost_check_button = styled_check(misc_content, "  Window stays on top", self.enable_topmost)
        try: self.topmost_check_button.configure(command=self.on_topmost_check_button_listener)
        except: pass

        self.stockfish_path = self._load_stockfish_path()
        self.select_stockfish_button = tk.Button(misc_content, text="  SELECT STOCKFISH BINARY  ", command=self.on_select_stockfish_button_listener,
                                                 font=self.F_BTN_SM, fg=TEXT_SECONDARY, bg=BG_ELEVATED, activebackground=BG_CARD_HI,
                                                 activeforeground=TEXT_PRIMARY, relief="flat", bd=0, padx=8, pady=7, cursor="hand2",
                                                 highlightbackground=BORDER, highlightthickness=1)
        self.select_stockfish_button.pack(fill="x", pady=(6,0))
        def _hover_sel(e, enter):
            self.select_stockfish_button.configure(bg=BG_CARD_HI if enter else BG_ELEVATED)
        self.select_stockfish_button.bind("<Enter>", lambda e: _hover_sel(e, True))
        self.select_stockfish_button.bind("<Leave>", lambda e: _hover_sel(e, False))

        initial_text = self.stockfish_path if self.stockfish_path else "No stockfish selected"
        if self.stockfish_path:
            pcol = "#10B981"
            pbg = "#0B1E16"
            pfg = "#6EE7B7"
        else:
            pcol = DANGER
            pbg = DANGER_BG
            pfg = "#FCA5A5"
        path_wrap = tk.Frame(misc_content, bg=pbg, highlightbackground="#2A1E1E" if not self.stockfish_path else "#0F2A1E", highlightthickness=1)
        path_wrap.pack(fill="x", pady=(6,0))
        self._path_wrap = path_wrap
        self.stockfish_path_text = tk.Label(path_wrap, text=initial_text, wraplength=260, font=("Consolas", 7), fg=pfg, bg=pbg, justify="left", anchor="w")
        self.stockfish_path_text.pack(fill="x", padx=8, pady=6)

        if not self.stockfish_path:
            auto = self._auto_find_stockfish()
            if auto:
                self.stockfish_path = auto
                self.stockfish_path_text.configure(text=auto, fg="#6EE7B7", bg="#0B1E16")
                self._path_wrap.configure(bg="#0B1E16", highlightbackground="#0F2A1E")
                self.stockfish_path_text.configure(bg="#0B1E16")
                logger.info("Auto-detected stockfish at %s", auto)
                self._save_stockfish_path(auto)

        # ═══════════════════════════════════════════════════
        #  RIGHT — MOVES
        # ═══════════════════════════════════════════════════
        hist_outer = tk.Frame(right_frame, bg=BORDER, highlightthickness=0)
        hist_outer.pack(fill="both", expand=True)
        hist_inner = tk.Frame(hist_outer, bg=BG_CARD)
        hist_inner.pack(fill="both", expand=True, padx=1, pady=1)

        hist_head = tk.Frame(hist_inner, bg=BG_CARD)
        hist_head.pack(fill="x", padx=14, pady=(12,8))
        h = tk.Frame(hist_head, bg=BG_CARD)
        h.pack(side="left")
        tk.Label(h, text="◆", font=("Segoe UI", 7), fg=ACCENT, bg=BG_CARD).pack(side="left", padx=(0,6))
        tk.Label(h, text="MOVE HISTORY", font=self.F_CARD_TITLE, fg=TEXT_MUTED, bg=BG_CARD).pack(side="left")
        # move count badge
        self._move_count = tk.Label(hist_head, text="0 moves", font=("Segoe UI", 7, "bold"), fg=TEXT_MUTED, bg=BG_ELEVATED, padx=8, pady=2)
        self._move_count.pack(side="right")

        tk.Frame(hist_inner, bg=BORDER, height=1).pack(fill="x", padx=14, pady=(0,0))

        treeview_frame = tk.Frame(hist_inner, bg=BG_CARD)
        treeview_frame.pack(fill="both", expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(treeview_frame, columns=("move_no", "white", "black"), show="headings", height=22, selectmode="browse", style="Treeview")
        self.tree.pack(side="left", fill="both", expand=True)
        self.vsb = ttk.Scrollbar(treeview_frame, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar")
        self.vsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=self.vsb.set)
        self.tree.column("move_no", anchor="center", width=44, stretch=False)
        self.tree.heading("move_no", text="#")
        self.tree.column("white", anchor="center", width=120)
        self.tree.heading("white", text="White")
        self.tree.column("black", anchor="center", width=120)
        self.tree.heading("black", text="Black")
        # zebra striping tag
        self.tree.tag_configure("odd", background="#161C25")
        self.tree.tag_configure("even", background=BG_CARD)

        self.export_pgn_button = tk.Button(hist_inner, text="⬇   EXPORT PGN", command=self.on_export_pgn_button_listener,
                                           font=self.F_BTN_SM, fg=TEXT_SECONDARY, bg=BG_ELEVATED, activebackground=BG_CARD_HI,
                                           activeforeground=TEXT_PRIMARY, relief="flat", bd=0, padx=10, pady=8, cursor="hand2",
                                           highlightbackground=BORDER, highlightthickness=1)
        self.export_pgn_button.pack(fill="x", padx=12, pady=(0,12))
        def _hover_pgn(e, enter):
            self.export_pgn_button.configure(bg=BG_CARD_HI if enter else BG_ELEVATED, fg=TEXT_PRIMARY if enter else TEXT_SECONDARY)
        self.export_pgn_button.bind("<Enter>", lambda e: _hover_pgn(e, True))
        self.export_pgn_button.bind("<Leave>", lambda e: _hover_pgn(e, False))

        # footer
        footer = tk.Frame(hist_inner, bg=BG_CARD)
        footer.pack(fill="x", padx=12, pady=(0,10))
        tk.Label(footer, text="Tip: keep this window on top while playing  •  Dark theme", font=("Segoe UI", 7), fg=TEXT_MUTED, bg=BG_CARD, anchor="w").pack(side="left")

        # initial eval bar update — defer until geometry is computed
        self._update_eval_bar("—")
        try:
            self.master.after(100, lambda: self._update_eval_bar("—"))
            self.master.after(500, lambda: self._update_eval_bar(self.eval_text.cget("text") or "—"))
        except Exception:
            pass

        # Threads daemon so they don't block exit
        threading.Thread(target=self.process_checker_thread, daemon=True).start()
        threading.Thread(target=self.browser_checker_thread, daemon=True).start()
        threading.Thread(target=self.process_communicator_thread, daemon=True).start()
        threading.Thread(target=self.keypress_listener_thread, daemon=True).start()

    # ── UI helpers ─────────────────────────────────
    def _set_status(self, text, color, bg):
        try:
            self.status_text.configure(text=text.upper(), fg=color, bg=bg)
            self._status_pill.configure(bg=bg, highlightbackground=bg)
            for child in self._status_pill.winfo_children():
                try: child.configure(bg=bg)
                except: pass
                for gc in child.winfo_children():
                    try: gc.configure(bg=bg)
                    except: pass
            self._status_dot.configure(bg=bg)
            self._status_dot.itemconfig(self._dot_oval, fill=color)
        except Exception:
            try:
                self.status_text["text"] = text
                self.status_text["fg"] = color
            except: pass

    def _update_eval_bar(self, eval_str):
        try:
            c = self._eval_canvas
            # Use fixed width if canvas not yet mapped (race on startup)
            # winfo_width can be 1 during __init__ before pack geometry is computed.
            try:
                c.update_idletasks()
                w = c.winfo_width()
                if w < 10:
                    # canvas not yet laid out – use inner eval_content width or fallback 280
                    try:
                        w = c.winfo_reqwidth() or 280
                    except:
                        w = 280
                    if w < 50:
                        w = 280
                # ensure minimum
                w = max(120, w)
            except Exception:
                w = 280
            h = 6
            # default center
            mid = w // 2
            # map eval to offset: clamp +-10 pawns => full bar
            val = None
            color = TEXT_MUTED
            if eval_str.startswith("M"):
                try:
                    m = int(eval_str[1:])
                    val = 10 if m>0 else -10
                    color = SUCCESS if m>0 else DANGER
                except (ValueError, TypeError) as e:
                    logger.debug("eval mate parse failed %r: %s", eval_str, e)
            elif eval_str not in ("—","-", ""):
                try:
                    v = float(eval_str)
                    val = max(-10, min(10, v))
                    if v>0.6: color = SUCCESS
                    elif v<-0.6: color = DANGER
                    else: color = "#94A3B8"
                except (ValueError, TypeError) as e:
                    logger.debug("eval cp parse failed %r: %s", eval_str, e)
            if val is None:
                # idle center tick
                c.coords(self._eval_bar, mid-1, 0, mid+1, h)
                c.itemconfig(self._eval_bar, fill="#334155")
            else:
                # bar extends from center outward
                if val >=0:
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

    # --- Graceful shutdown ---
    def _cleanup(self):
        if getattr(self, "_cleaned", False):
            return
        self._cleaned = True
        logger.info("GUI cleanup – killing children, closing pipes, quitting chrome")
        self.exit = True
        # Stop bot + overlay
        try:
            if self.stockfish_bot_process is not None:
                if self.stockfish_bot_process.is_alive():
                    self.stockfish_bot_process.terminate()
                    self.stockfish_bot_process.join(timeout=2)
                    if self.stockfish_bot_process.is_alive():
                        self.stockfish_bot_process.kill()
                self.stockfish_bot_process = None
        except Exception as e:
            logger.debug("cleanup bot process error: %s", e)
        try:
            if self.overlay_screen_process is not None:
                if self.overlay_screen_process.is_alive():
                    self.overlay_screen_process.terminate()
                    self.overlay_screen_process.join(timeout=1.5)
                    if self.overlay_screen_process.is_alive():
                        self.overlay_screen_process.kill()
                self.overlay_screen_process = None
        except Exception as e:
            logger.debug("cleanup overlay error: %s", e)
        try:
            if self.stockfish_bot_pipe is not None:
                try:
                    self.stockfish_bot_pipe.close()
                except Exception:
                    pass
                self.stockfish_bot_pipe = None
        except Exception:
            pass
        try:
            if self._overlay_queue is not None:
                try:
                    self._overlay_queue.close()
                    self._overlay_queue.cancel_join_thread()
                except Exception:
                    pass
                self._overlay_queue = None
        except Exception:
            pass
        try:
            if self.chrome is not None:
                try:
                    self.chrome.quit()
                except Exception as e:
                    logger.debug("chrome.quit error: %s", e)
                self.chrome = None
        except Exception:
            pass

    def on_close_listener(self):
        self._cleanup()
        try:
            self.master.destroy()
        except tk.TclError as e:
            logger.debug("master destroy failed: %s", e)
        except Exception as e:
            logger.debug("destroy unexpected: %s", e, exc_info=True)
        # Avoid os._exit which bypasses cleanup; let mainloop exit naturally.
        # As last resort, after 1s force exit if still alive (e.g., daemon threads stuck).
        def _force_exit():
            if not self.exit:
                return
            try:
                # check if main window still exists
                self.master.winfo_exists()
            except tk.TclError:
                # window gone, we are done
                return
            except Exception:
                pass
            logger.warning("Forcing exit after destroy")
            try:
                sys.exit(0)
            except SystemExit:
                raise
            except Exception as e:
                logger.debug("force exit failed: %s", e)
                try:
                    os._exit(0)
                except Exception:
                    pass
        try:
            self.master.after(1000, _force_exit)
        except tk.TclError:
            pass
        except Exception as e:
            logger.debug("schedule force exit failed: %s", e)

    def process_checker_thread(self):
        while not self.exit:
            if self.running and self.stockfish_bot_process is not None and not self.stockfish_bot_process.is_alive():
                try:
                    self.master.after(0, self.on_stop_button_listener)
                except tk.TclError as e:
                    logger.debug("after on_stop failed: %s", e)
                    self.on_stop_button_listener()
                except Exception as e:
                    logger.debug("process_checker after error: %s", e, exc_info=True)
                if self.restart_after_stopping:
                    self.restart_after_stopping = False
                    try:
                        self.master.after(100, self.on_start_button_listener)
                    except tk.TclError as e:
                        logger.debug("after on_start failed: %s", e)
                        self.on_start_button_listener()
                    except Exception as e:
                        logger.debug("restart after error: %s", e, exc_info=True)
            time.sleep(0.2)

    def _on_browser_closed_ui(self):
        """Thread-safe UI reset when browser is closed externally."""
        try:
            self.open_browser_button.configure(text="↗   OPEN BROWSER", state="normal", bg="#0E7490")
        except tk.TclError as e:
            logger.debug("browser closed UI reset failed: %s", e)

    def browser_checker_thread(self):
        while not self.exit:
            try:
                if self.opened_browser and self.chrome is not None:
                    try:
                        logs = self.chrome.get_log("driver")
                        if logs and "target window already closed" in logs[-1].get("message", ""):
                            self.opened_browser = False
                            try:
                                self.master.after(0, self._on_browser_closed_ui)
                            except Exception as e:
                                logger.debug("after _on_browser_closed_ui failed: %s", e)
                            try:
                                self.master.after(0, self.on_stop_button_listener)
                            except tk.TclError:
                                self.on_stop_button_listener()
                            self.chrome = None
                    except (WebDriverException, AttributeError) as e:
                        # get_log not supported or driver gone – fallback below
                        logger.debug("get_log check failed, falling through to current_url check: %s", e)
                        try:
                            _ = self.chrome.current_url
                        except WebDriverException as ce:
                            if "invalid session" in str(ce).lower() or "disconnected" in str(ce).lower() or "no such window" in str(ce).lower():
                                self.opened_browser = False
                                try:
                                    self.master.after(0, self._on_browser_closed_ui)
                                except Exception as ae:
                                    logger.debug("after _on_browser_closed_ui fallback failed: %s", ae)
                                self.chrome = None
                        except Exception as ue:
                            logger.debug("current_url unexpected error: %s", ue)
                    except Exception as e:
                        logger.debug("browser_checker get_log unexpected: %s", e)
                        # Fallback: check if window handles empty
                        try:
                            _ = self.chrome.current_url
                        except WebDriverException as ce:
                            if "invalid session" in str(ce).lower() or "disconnected" in str(ce).lower() or "no such window" in str(ce).lower():
                                self.opened_browser = False
                                try:
                                    self.master.after(0, self._on_browser_closed_ui)
                                except Exception as ae:
                                    logger.debug("after fallback failed: %s", ae)
                                self.chrome = None
            except IndexError as e:
                logger.debug("browser_checker IndexError: %s", e)
            except Exception as e:
                logger.debug("browser_checker error: %s", e, exc_info=True)
            time.sleep(0.5)

    def process_communicator_thread(self):
        while not self.exit:
            try:
                if self.stockfish_bot_pipe is not None and self.stockfish_bot_pipe.poll():
                    data = self.stockfish_bot_pipe.recv()
                    # Use after for thread-safe UI
                    try:
                        self.master.after(0, lambda d=data: self._handle_pipe_message(d))
                    except Exception:
                        self._handle_pipe_message(data)
            except (BrokenPipeError, OSError, EOFError):
                self.stockfish_bot_pipe = None
            except Exception as e:
                logger.debug("process_communicator error: %s", e)
            time.sleep(0.05)

    def _handle_pipe_message(self, data):
        try:
            if not isinstance(data, str):
                logger.debug("Unknown pipe message type: %s", data)
                return
            if data == "START":
                self.clear_tree()
                self.match_moves = []
                self._set_status("RUNNING", SUCCESS, SUCCESS_BG)
                try:
                    self.start_button["text"] = "■   STOP"
                    self.start_button["state"] = "normal"
                    self.start_button.configure(bg="#DC2626", activebackground="#B91C1C", disabledforeground="#FCA5A5")
                    self.start_button["command"] = self.on_stop_button_listener
                    self.start_button.update()
                except Exception:
                    pass
                logger.info("Bot START received")
            elif data.startswith("RESTART"):
                self.restart_after_stopping = True
                try:
                    self.stockfish_bot_pipe.send("DELETE")
                except Exception:
                    pass
            elif data.startswith("S_MOVE"):
                move = data[6:]
                self.match_moves.append(move)
                self.insert_move(move)
                self.tree.yview_moveto(1)
                try: self._move_count.configure(text=f"{len(self.match_moves)} moves")
                except: pass
            elif data.startswith("M_MOVE"):
                moves = data[6:].split(",") if len(data) > 6 else []
                moves = [m for m in moves if m]
                self.match_moves += moves
                self.set_moves(moves)
                self.tree.yview_moveto(1)
                try: self._move_count.configure(text=f"{len(self.match_moves)} moves")
                except: pass
            elif data.startswith("EVAL|"):
                parts = data.split("|")
                if len(parts) >= 6:
                    eval_str, wdl_str, material_str, bot_accuracy_str, opponent_accuracy_str = parts[1:6]
                    self.update_evaluation_display(eval_str, wdl_str, material_str, bot_accuracy_str, opponent_accuracy_str)
            elif data.startswith("ERR_EXE"):
                messagebox.showerror("Error", "Stockfish path provided is not valid!")
                logger.error("ERR_EXE: invalid stockfish path")
                self.on_stop_button_listener()
            elif data.startswith("ERR_PERM"):
                messagebox.showerror("Error", "Stockfish path provided is not executable! (chmod +x on Linux)")
                logger.error("ERR_PERM")
                self.on_stop_button_listener()
            elif data.startswith("ERR_BOARD"):
                messagebox.showerror("Error", "Can't find board! DOM may have changed – try updating selectors or reloading page.")
                logger.error("ERR_BOARD")
                self.on_stop_button_listener()
            elif data.startswith("ERR_COLOR"):
                messagebox.showerror("Error", "Can't find player color!")
                logger.error("ERR_COLOR")
                self.on_stop_button_listener()
            elif data.startswith("ERR_MOVES"):
                messagebox.showerror("Error", "Can't find moves list! Ensure you're on a live game. Check logs.")
                logger.error("ERR_MOVES")
                self.on_stop_button_listener()
            elif data.startswith("ERR_GAMEOVER"):
                messagebox.showerror("Error", "Game has already finished!")
                logger.error("ERR_GAMEOVER")
            elif data.startswith("ERR_TIMEOUT"):
                messagebox.showerror("Error", "Engine timeout – Stockfish did not respond in time. Check CPU load / depth.")
                logger.error("ERR_TIMEOUT")
            elif data.startswith("ERR_DISCONNECT"):
                messagebox.showerror("Error", "Browser disconnected (Chrome closed or Selenium lost). Re-open browser.")
                logger.error("ERR_DISCONNECT")
                self.on_stop_button_listener()
                self.opened_browser = False
                try:
                    self.open_browser_button["text"] = "↗   OPEN BROWSER"
                    self.open_browser_button["state"] = "normal"
                except Exception:
                    pass
            elif data.startswith("ERR_STALE"):
                messagebox.showwarning("Warning", "Stale DOM – page reloaded or board detached. Retrying…")
                logger.warning("ERR_STALE")
            elif data.startswith("ERR_ENGINE"):
                detail = data.split("|", 1)[1] if "|" in data else ""
                messagebox.showerror("Engine Error", f"Stockfish error: {detail[:400]}")
                logger.error("ERR_ENGINE: %s", detail)
                # log full traceback to file
            else:
                logger.debug("Unhandled pipe message: %s", data[:200])
        except Exception as e:
            logger.error("Error handling pipe message %r: %s", data[:200] if isinstance(data, str) else data, e, exc_info=True)

    def keypress_listener_thread(self):
        while not self.exit:
            time.sleep(0.1)
            if not self.opened_browser:
                continue
            if _kb is None:
                continue
            try:
                if _kb.is_pressed("1"):
                    try:
                        self.master.after(0, self.on_start_button_listener)
                    except Exception:
                        self.on_start_button_listener()
                    time.sleep(0.5)
                elif _kb.is_pressed("2"):
                    try:
                        self.master.after(0, self.on_stop_button_listener)
                    except Exception:
                        self.on_stop_button_listener()
                    time.sleep(0.5)
            except Exception as e:
                logger.debug("keypress poll error: %s", e)
                time.sleep(0.3)

    def on_open_browser_button_listener(self):
        # Non-blocking entry: update UI on main thread, then delegate heavy work to background thread
        if self.opening_browser or self.opened_browser:
            return
        self.opening_browser = True
        try:
            self.open_browser_button.configure(text="◷  OPENING...", state="disabled")
        except tk.TclError as e:
            logger.debug("open_browser button update failed: %s", e)
        threading.Thread(target=self._open_browser_worker, daemon=True).start()

    def _open_browser_worker(self):
        """Background worker for Chrome startup – never touches Tk directly."""
        def _ui(fn):
            try:
                self.master.after(0, fn)
            except tk.TclError as e:
                logger.debug("after schedule failed: %s", e)
                try:
                    fn()
                except Exception:
                    pass

        def _fail_reset(msg_title, msg_body):
            self.opening_browser = False
            def _reset():
                try:
                    self.open_browser_button.configure(text="↗   OPEN BROWSER", state="normal", bg="#0E7490")
                except tk.TclError as e:
                    logger.debug("reset button failed: %s", e)
                messagebox.showerror(msg_title, msg_body)
            _ui(_reset)

        options = webdriver.ChromeOptions()
        options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('useAutomationExtension', False)
        if platform.system() == "Linux":
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
        chromedriver_path = None
        service = None
        try:
            raw_path = ChromeDriverManager().install()
            chromedriver_path = raw_path
            if chromedriver_path:
                norm = os.path.normpath(chromedriver_path)
                if norm.lower().endswith(("third_party_notices.chromedriver", "license.chromedriver")) or not norm.lower().endswith(("chromedriver.exe", "chromedriver")):
                    sibling = os.path.join(os.path.dirname(norm), "chromedriver.exe")
                    if os.path.exists(sibling):
                        logger.info("Fixing webdriver-manager path %s -> %s", chromedriver_path, sibling)
                        chromedriver_path = sibling
                    else:
                        search_root = os.path.dirname(norm)
                        for _ in range(3):
                            if not search_root or search_root == os.path.dirname(search_root):
                                break
                            for root, _, files in os.walk(search_root):
                                if "chromedriver.exe" in files:
                                    found = os.path.join(root, "chromedriver.exe")
                                    logger.info("Found chromedriver via walk: %s", found)
                                    chromedriver_path = found
                                    search_root = ""
                                    break
                            if chromedriver_path != raw_path and os.path.exists(chromedriver_path):
                                break
                            search_root = os.path.dirname(search_root)
                if not os.path.exists(chromedriver_path):
                    logger.warning("webdriver-manager path does not exist: %s", chromedriver_path)
                    chromedriver_path = raw_path
                logger.info("Using chromedriver: %s", chromedriver_path)
                service = ChromeService(executable_path=chromedriver_path)
                self.chrome = webdriver.Chrome(service=service, options=options)
            else:
                raise RuntimeError("ChromeDriverManager returned empty path")
        except Exception as wdm_error:
            logger.warning("webdriver-manager failed (%s), trying Selenium Manager fallback", wdm_error)
            try:
                if service is not None:
                    try:
                        service.stop()
                    except Exception as e:
                        logger.debug("service stop failed: %s", e)
                self.chrome = webdriver.Chrome(options=options)
                logger.info("Selenium Manager fallback succeeded")
            except Exception as sm_error:
                import traceback
                diag = traceback.format_exc()
                logger.error("Selenium Manager also failed: %s\n%s", sm_error, diag)
                chrome_hint = ""
                if platform.system() == "Windows":
                    chrome_hint = "Ensure Google Chrome is installed (check chrome://version). Try deleting %USERPROFILE%\\.wdm folder and retry, or download chromedriver manually matching your Chrome version from https://chromedriver.chromium.org/"
                else:
                    chrome_hint = "Ensure google-chrome or chromium is installed and in PATH. On Linux try: google-chrome --version"
                _fail_reset("ChromeDriver error",
                    f"Failed to start Chrome.\n\nwebdriver-manager error:\n{wdm_error}\n\nSelenium Manager error:\n{sm_error}\n\n{chrome_hint}\n\nDetails logged to logs/chess-x.log")
                return
        if self.chrome is None:
            _fail_reset("Error", "Failed to create Chrome driver (unknown error)")
            logger.error("Chrome driver is None after creation attempts")
            return
        try:
            target = "https://www.chess.com" if self.website.get() == "chesscom" else "https://www.lichess.org"
            self.chrome.get(target)
            try:
                if getattr(self.chrome, "service", None) and getattr(self.chrome.service, "service_url", None):
                    self.chrome_url = self.chrome.service.service_url
                else:
                    self.chrome_url = getattr(self.chrome.command_executor, "_url", "")
                    if "/session" in self.chrome_url:
                        self.chrome_url = self.chrome_url.split("/session")[0]
                    if not self.chrome_url.startswith("http"):
                        self.chrome_url = self.chrome.command_executor._url
                if not self.chrome_url:
                    self.chrome_url = getattr(self.chrome.command_executor, "_url", "")
            except Exception as e:
                logger.debug("chrome_url fallback error: %s", e)
                try:
                    self.chrome_url = self.chrome.command_executor._url
                except Exception:
                    self.chrome_url = ""
            self.chrome_session_id = getattr(self.chrome, "session_id", None)
            self.opening_browser = False
            self.opened_browser = True
            def _success():
                try:
                    self.open_browser_button.configure(text="✓  BROWSER OPEN", state="disabled", bg="#1E3A2E", fg="#6EE7B7")
                    self.start_button.configure(state="normal")
                except tk.TclError as e:
                    logger.debug("success UI update failed: %s", e)
            _ui(_success)
            logger.info("Browser opened: %s", self.chrome_url)
        except Exception as e:
            logger.error("Error navigating: %s", e, exc_info=True)
            def _nav_fail():
                messagebox.showerror("Error", f"Failed to navigate: {e}")
                try:
                    self.open_browser_button.configure(text="↗   OPEN BROWSER", state="normal")
                except tk.TclError:
                    pass
                self.opening_browser = False
            _ui(_nav_fail)

    def on_start_button_listener(self):
        if self.running:
            return
        try:
            slow_mover = self.slow_mover.get()
        except Exception:
            messagebox.showerror("Error", "Slow Mover must be an integer")
            return
        if slow_mover < 10 or slow_mover > 1000:
            messagebox.showerror("Error", "Slow Mover must be between 10 and 1000")
            return
        # Auto-try to resolve empty stockfish path before failing
        if not self.stockfish_path or not self.stockfish_path.strip():
            auto = self._auto_find_stockfish() or self._load_stockfish_path()
            if auto and os.path.exists(auto):
                self.stockfish_path = auto
                try:
                    self.stockfish_path_text["text"] = auto
                    self.stockfish_path_text["fg"] = "#6EE7B7"
                    self.stockfish_path_text["bg"] = "#0B1E16"
                    self.stockfish_path_text.update()
                    self._path_wrap.configure(bg="#0B1E16", highlightbackground="#0F2A1E")
                except Exception:
                    pass
                logger.info("Auto-resolved stockfish path on Start: %s", auto)
            else:
                msg = (
                    "Stockfish path is empty.\n\n"
                    "1) Click 'Select Stockfish' and choose the engine binary.\n"
                    "2) Download Stockfish if you don't have it: https://stockfishchess.org/download/\n"
                    "   (Windows: stockfish-windows-x86-64-avx2.exe)\n"
                    "3) On Linux: sudo apt install stockfish or chmod +x stockfish\n\n"
                    "Would you like to select the file now?\n"
                    "(No = try auto-download on Windows)"
                )
                if messagebox.askyesno("Stockfish not configured", msg):
                    self.on_select_stockfish_button_listener()
                    if not self.stockfish_path or not self.stockfish_path.strip():
                        return
                else:
                    # Offer auto-download on Windows
                    if platform.system() == "Windows" and messagebox.askyesno("Auto-download", "Download Stockfish automatically to ./stockfish.exe?"):
                        dl = self._download_stockfish(dest_dir=".")
                        if dl and os.path.exists(dl):
                            self.stockfish_path = dl
                            self._save_stockfish_path(dl)
                            try:
                                self.stockfish_path_text["text"] = dl
                                self.stockfish_path_text["fg"] = "#6EE7B7"
                                self.stockfish_path_text.update()
                            except Exception:
                                pass
                            messagebox.showinfo("Downloaded", f"Stockfish downloaded to:\n{dl}")
                        else:
                            messagebox.showerror("Download failed", "Auto-download failed. Please download manually from https://stockfishchess.org/download/")
                            return
                    else:
                        return
        if not os.path.exists(self.stockfish_path):
            msg = (
                f"Stockfish not found at:\n{self.stockfish_path}\n\n"
                "The file was moved or deleted. Please re-select the binary.\n"
                "Download from https://stockfishchess.org/download/"
            )
            # Offer both select and download
            choice = messagebox.askyesnocancel("Stockfish not found", msg + "\n\nYes = Select file\nNo = Auto-download (Windows)\nCancel = Abort")
            # ask* returns True/False/None; map: True->select, False->download, None->cancel
            if choice is True:
                self.on_select_stockfish_button_listener()
                if not self.stockfish_path or not os.path.exists(self.stockfish_path):
                    return
            elif choice is False:
                dl = self._download_stockfish(dest_dir=".")
                if dl and os.path.exists(dl):
                    self.stockfish_path = dl
                    self._save_stockfish_path(dl)
                    try:
                        self.stockfish_path_text["text"] = dl
                        self.stockfish_path_text["fg"] = "#6EE7B7"
                        self.stockfish_path_text.update()
                    except Exception:
                        pass
                    messagebox.showinfo("Downloaded", f"Stockfish downloaded to:\n{dl}")
                else:
                    messagebox.showerror("Download failed", "Auto-download failed. Please select manually.")
                    return
            else:
                return
        if self.enable_mouseless_mode.get() == 1 and self.website.get() == "chesscom":
            messagebox.showerror("Error", "Mouseless mode is only supported on lichess.org")
            return
        # Validate threads vs cpu_count
        try:
            threads = int(self.cpu_threads.get())
            cpu = os.cpu_count() or 1
            if threads < 1 or threads > cpu * 2:
                messagebox.showwarning("Warning", f"CPU Threads ({threads}) unusual for {cpu} cores – clamping to {cpu}")
                self.cpu_threads.set(min(max(1, threads), cpu))
        except Exception:
            pass
        try:
            memory = int(self.memory.get())
            if memory < 16 or memory > 8192:
                messagebox.showwarning("Warning", "Memory recommended 16–8192 MB")
        except Exception:
            pass

        parent_conn, child_conn = multiprocess.Pipe()
        self.stockfish_bot_pipe = parent_conn
        st_ov_queue = multiprocess.Queue()
        self._overlay_queue = st_ov_queue

        self.stockfish_bot_process = StockfishBot(
            self.chrome_url, self.chrome_session_id, self.website.get(), child_conn, st_ov_queue,
            self.stockfish_path, self.enable_manual_mode.get() == 1, self.enable_mouseless_mode.get() == 1,
            self.enable_non_stop_puzzles.get() == 1, self.enable_non_stop_matches.get() == 1,
            self.mouse_latency.get(), self.enable_bongcloud.get() == 1, self.slow_mover.get(),
            self.skill_level.get(), self.stockfish_depth.get(), self.memory.get(), self.cpu_threads.get(),
        )
        self.stockfish_bot_process.start()
        self.overlay_screen_process = multiprocess.Process(target=run, args=(st_ov_queue,))
        self.overlay_screen_process.start()
        self.running = True
        try:
            self.start_button["text"] = "◷  STARTING..."
            self.start_button["state"] = "disabled"
            self.start_button.update()
        except Exception:
            pass
        logger.info("Started StockfishBot + overlay")

    def on_stop_button_listener(self):
        if self.stockfish_bot_process is not None:
            try:
                if self.overlay_screen_process is not None:
                    try:
                        if self.overlay_screen_process.is_alive():
                            self.overlay_screen_process.terminate()
                            self.overlay_screen_process.join(timeout=1.5)
                            if self.overlay_screen_process.is_alive():
                                self.overlay_screen_process.kill()
                    except Exception as e:
                        logger.debug("overlay kill error: %s", e)
                    self.overlay_screen_process = None
                if self.stockfish_bot_process.is_alive():
                    self.stockfish_bot_process.terminate()
                    self.stockfish_bot_process.join(timeout=2)
                    if self.stockfish_bot_process.is_alive():
                        self.stockfish_bot_process.kill()
            except Exception as e:
                logger.debug("bot kill error: %s", e)
            self.stockfish_bot_process = None
        if self.stockfish_bot_pipe is not None:
            try:
                self.stockfish_bot_pipe.close()
            except Exception:
                pass
            self.stockfish_bot_pipe = None
        if self._overlay_queue is not None:
            try:
                self._overlay_queue.close()
                self._overlay_queue.cancel_join_thread()
            except Exception:
                pass
            self._overlay_queue = None
        self.running = False
        try:
            self._set_status("INACTIVE", DANGER, DANGER_BG)
            self.eval_text["text"] = "—"
            self.eval_text["fg"] = TEXT_PRIMARY
            self._update_eval_bar("—")
            self.wdl_text["text"] = "—"
            self.wdl_text["fg"] = TEXT_MUTED
            self.material_text["text"] = "—"
            self.material_text["fg"] = TEXT_MUTED
            self.white_acc_text["text"] = "—"
            self.white_acc_text["fg"] = TEXT_MUTED
            self.black_acc_text["text"] = "—"
            self.black_acc_text["fg"] = TEXT_MUTED
            self.eval_text.update()
            self.wdl_text.update()
            self.material_text.update()
            self.white_acc_text.update()
            self.black_acc_text.update()
        except Exception:
            pass
        try:
            if not self.restart_after_stopping:
                self.start_button["text"] = "▶   START ENGINE"
                self.start_button["state"] = "normal"
                self.start_button["command"] = self.on_start_button_listener
                self.start_button.configure(bg=ACCENT, activebackground=ACCENT_HI, fg="#FFFFFF", disabledforeground="#7A6AA0")
            else:
                self.restart_after_stopping = False
                self.on_start_button_listener()
            self.start_button.update()
        except Exception:
            pass
        logger.info("Stopped bot")

    def on_topmost_check_button_listener(self):
        try:
            if self.enable_topmost.get() == 1:
                self.master.attributes("-topmost", True)
            else:
                self.master.attributes("-topmost", False)
        except Exception:
            pass

    def on_export_pgn_button_listener(self):
        # Disable topmost so dialog is visible (fixes hidden dialog bug)
        was_topmost = bool(self.enable_topmost.get())
        try:
            self.master.attributes("-topmost", False)
        except Exception:
            pass
        try:
            f = filedialog.asksaveasfile(parent=self.master, initialfile="match.pgn", defaultextension=".pgn", filetypes=[("Portable Game Notation", "*.pgn"), ("All Files", "*.*")])
        finally:
            try:
                if was_topmost:
                    self.master.attributes("-topmost", True)
            except Exception:
                pass
        if f is None:
            return
        data = ""
        for i in range(len(self.match_moves) // 2 + 1):
            if len(self.match_moves) % 2 == 0 and i == len(self.match_moves) // 2:
                continue
            data += str(i + 1) + ". "
            data += self.match_moves[i * 2] + " "
            if (i * 2) + 1 < len(self.match_moves):
                data += self.match_moves[i * 2 + 1] + " "
        try:
            f.write(data)
            f.close()
        except Exception as e:
            logger.error("PGN write failed: %s", e)
            messagebox.showerror("Error", f"Failed to write PGN: {e}")

    def _config_path(self):
        # Store next to src/config.json and also in project root
        for cand in [os.path.join("src", "config.json"), "config.json", os.path.join(os.path.expanduser("~"), ".chess-x.json")]:
            # prefer src/config.json
            if cand == os.path.join("src", "config.json"):
                return cand
        return os.path.join("src", "config.json")

    def _is_valid_stockfish(self, path, quick_check=True):
        """Validate that path points to a real Stockfish binary (not a .py etc)."""
        try:
            if not path or not isinstance(path, str):
                return False
            if not os.path.exists(path) or not os.path.isfile(path):
                return False
            # reject obvious non-binaries
            low = path.lower()
            if low.endswith((".py", ".txt", ".json", ".log", ".md", ".png")):
                return False
            # size sanity
            try:
                if os.path.getsize(path) < 20000:  # stockfish is > 1MB
                    return False
            except Exception:
                pass
            if not quick_check:
                import subprocess
                r = subprocess.run([path, "uci"], input="quit\n", capture_output=True, text=True, timeout=3)
                if "uciok" not in (r.stdout or ""):
                    return False
            else:
                # quick: check filename contains stockfish
                base = os.path.basename(low)
                # allow stockfish.exe, stockfish, stockfish-* .exe
                if base not in ("stockfish", "stockfish.exe") and not base.startswith("stockfish"):
                    # still allow if path was explicitly selected earlier? But warn
                    # We check for valid exe via extension + size already; allow non-standard name only if uci check passes
                    # Do strict uci check for non-standard names
                    import subprocess
                    r = subprocess.run([path, "uci"], input="quit\n", capture_output=True, text=True, timeout=3)
                    if "uciok" not in (r.stdout or ""):
                        logger.warning("File %s does not look like Stockfish (uciok missing)", path)
                        return False
            return True
        except Exception as e:
            logger.debug("stockfish validation failed for %s: %s", path, e)
            return False

    def _load_stockfish_path(self):
        import json as _json
        for p in [os.path.join("src", "config.json"), "config.json", os.path.join(os.path.expanduser("~"), ".chess-x.json")]:
            try:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as fh:
                        data = _json.load(fh)
                        s = data.get("stockfish_path") or data.get("stockfish") or ""
                        if s and os.path.exists(s):
                            if not self._is_valid_stockfish(s, quick_check=True):
                                logger.warning("Saved stockfish path invalid (not a binary): %s – ignoring", s)
                                continue
                            logger.info("Loaded stockfish path from %s: %s", p, s)
                            return s
                        elif s:
                            logger.warning("Saved stockfish path not found on disk: %s", s)
            except Exception as e:
                logger.debug("load config %s failed: %s", p, e)
        return ""

    def _save_stockfish_path(self, path):
        # Do not persist invalid stockfish paths
        if path and not self._is_valid_stockfish(path, quick_check=True):
            # Allow saving only if deeper check passes (non-standard names)
            if not self._is_valid_stockfish(path, quick_check=False):
                logger.warning("Refusing to save invalid Stockfish path: %s", path)
                return
        import json as _json
        cfg = self._config_path()
        try:
            os.makedirs(os.path.dirname(cfg) if os.path.dirname(cfg) else ".", exist_ok=True)
            data = {}
            if os.path.exists(cfg):
                try:
                    with open(cfg, "r", encoding="utf-8") as fh:
                        data = _json.load(fh)
                except Exception:
                    data = {}
            data["stockfish_path"] = path
            with open(cfg, "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2)
            logger.info("Saved stockfish path to %s", cfg)
        except Exception as e:
            logger.debug("save config failed: %s", e)

    def _auto_find_stockfish(self):
        import shutil
        candidates = []
        # 1. In PATH
        for name in ["stockfish", "stockfish.exe"]:
            w = shutil.which(name)
            if w:
                candidates.append(w)
        # 2. Common project locations
        for rel in [
            "stockfish.exe", "stockfish",
            os.path.join("src", "stockfish.exe"), os.path.join("src", "stockfish"),
            os.path.join(".", "stockfish.exe"),
            os.path.join("C:\\", "stockfish", "stockfish.exe"),
            os.path.join(os.path.expanduser("~"), "stockfish.exe"),
        ]:
            if os.path.exists(rel) and os.path.isfile(rel):
                candidates.append(os.path.abspath(rel))
        # 3. Search recursively in project (max depth 2) - only exact stockfish binaries
        try:
            for root, _, files in os.walk("."):
                if root.count(os.sep) > 4:
                    continue
                # skip venv and hidden dirs
                if any(skip in root for skip in [".venv", ".git", "__pycache__", ".wdm"]):
                    continue
                for fn in files:
                    low = fn.lower()
                    # Only real binaries: stockfish.exe or stockfish (no extension), not stockfish_bot.py etc
                    if low in ("stockfish", "stockfish.exe") or (low.startswith("stockfish-") and low.endswith(".exe")):
                        full = os.path.join(root, fn)
                        if os.path.isfile(full) and full not in candidates:
                            candidates.append(os.path.abspath(full))
                if len(candidates) >= 3:
                    break
        except Exception:
            pass
        # Return first candidate that passes real validation
        for c in candidates:
            if self._is_valid_stockfish(c, quick_check=True):
                return c
        # fallback: any with size check
        for c in candidates:
            try:
                if os.path.getsize(c) > 1000:
                    return c
            except Exception:
                continue
        return candidates[0] if candidates else None

    def _download_stockfish(self, dest_dir="."):
        """Attempt to download Stockfish binary for current platform. Returns path or None."""
        import urllib.request
        import zipfile
        import shutil as _shutil
        try:
            system = platform.system()
            if system == "Windows":
                # Official Stockfish GitHub releases – AVX2 build is widely compatible
                urls = [
                    "https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-windows-x86-64-avx2.zip",
                    "https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish_15.1_win_x64_avx2.zip",
                ]
                dest_zip = os.path.join(tempfile.gettempdir(), "stockfish.zip")
                success = False
                for url in urls:
                    try:
                        logger.info("Downloading Stockfish from %s", url)
                        urllib.request.urlretrieve(url, dest_zip)
                        success = True
                        break
                    except Exception as e:
                        logger.debug("Download %s failed: %s", url, e)
                        continue
                if not success:
                    return None
                # Extract
                with zipfile.ZipFile(dest_zip, "r") as z:
                    for member in z.namelist():
                        if member.lower().endswith("stockfish.exe") or member.lower().endswith("stockfish-windows-x86-64-avx2.exe"):
                            out = os.path.join(dest_dir, "stockfish.exe")
                            with z.open(member) as src, open(out, "wb") as dst:
                                _shutil.copyfileobj(src, dst)
                            logger.info("Stockfish extracted to %s", out)
                            return os.path.abspath(out)
                    # Fallback: extract first .exe
                    for member in z.namelist():
                        if member.lower().endswith(".exe"):
                            out = os.path.join(dest_dir, os.path.basename(member))
                            with z.open(member) as src, open(out, "wb") as dst:
                                _shutil.copyfileobj(src, dst)
                            return os.path.abspath(out)
            else:
                # Linux/macOS – try apt hint or download
                logger.info("Auto download not implemented for %s – please install via package manager", system)
        except Exception as e:
            logger.warning("Stockfish download failed: %s", e)
        return None

    def on_select_stockfish_button_listener(self):
        # Temporarily disable topmost so dialog is visible
        was_topmost = bool(self.enable_topmost.get())
        try:
            self.master.attributes("-topmost", False)
        except Exception:
            pass
        try:
            f = filedialog.askopenfilename(
                parent=self.master,
                title="Select Stockfish binary",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            )
        finally:
            try:
                if was_topmost:
                    self.master.attributes("-topmost", True)
            except Exception:
                pass
        if not f or not f.strip():
            logger.debug("Stockfish selection cancelled")
            return
        f = f.strip()
        # Validate immediately before saving — prevent saving gui.py etc.
        if not self._is_valid_stockfish(f, quick_check=False):
            # Try quick check first to give detailed error
            low = f.lower()
            if low.endswith(".py"):
                messagebox.showerror("Invalid Stockfish", f"Selected file is a Python script, not a Stockfish binary:\n{f}\n\nPlease select the Stockfish executable (stockfish.exe on Windows).")
                logger.warning("User selected non-binary as Stockfish: %s", f)
                return
            # attempt uci to confirm
            import subprocess
            try:
                r = subprocess.run([f, "uci"], input="quit\n", capture_output=True, text=True, timeout=3)
                if "uciok" not in (r.stdout or ""):
                    messagebox.showerror("Invalid Stockfish", f"Selected file does not appear to be Stockfish:\n{f}\n\nOutput: {(r.stdout or '')[:200]}\n\nDownload Stockfish from https://stockfishchess.org/download/")
                    logger.warning("Stockfish validation failed for %s: stdout=%r", f, r.stdout[:200])
                    return
            except Exception as e:
                messagebox.showerror("Invalid Stockfish", f"Selected file cannot be executed as Stockfish:\n{f}\n{e}\n\nDownload Stockfish from https://stockfishchess.org/download/")
                logger.warning("Stockfish validation exception for %s: %s", f, e)
                return
        self.stockfish_path = f
        self._save_stockfish_path(self.stockfish_path)
        try:
            self.stockfish_path_text["text"] = self.stockfish_path
            self.stockfish_path_text["fg"] = "#6EE7B7"
            self.stockfish_path_text["bg"] = "#0B1E16"
            self._path_wrap.configure(bg="#0B1E16", highlightbackground="#0F2A1E")
            self.stockfish_path_text.update()
        except Exception:
            pass
        logger.info("Stockfish path set to %s", f)
        # Quick validation success already done
        try:
            import subprocess
            r = subprocess.run([self.stockfish_path, "uci"], input="quit\n", capture_output=True, text=True, timeout=3)
            if "uciok" in (r.stdout or ""):
                logger.info("Stockfish validation success: %s", f)
                self.stockfish_path_text["fg"] = "#6EE7B7"
            else:
                logger.warning("Stockfish validation warning stdout=%r stderr=%r", r.stdout[:200], r.stderr[:200])
        except Exception as e:
            logger.warning("Stockfish validation failed for %s: %s", f, e)
            messagebox.showwarning("Warning", f"Selected file may not be valid Stockfish:\n{e}\n\nYou can download Stockfish from https://stockfishchess.org/download/")

    def clear_tree(self):
        try:
            self.tree.delete(*self.tree.get_children())
            try:
                self._move_count.configure(text="0 moves")
            except tk.TclError as e:
                logger.debug("move_count clear failed: %s", e)
            try:
                self.tree.update_idletasks()
            except tk.TclError as e:
                logger.debug("tree update failed: %s", e)
        except tk.TclError as e:
            logger.debug("clear_tree TclError: %s", e)
        except Exception as e:
            logger.debug("clear_tree error: %s", e, exc_info=True)

    def insert_move(self, move):
        try:
            # Use match_moves length which already includes this move (caller appends before call).
            # move_index is 1-based count of moves.
            move_index = len(self.match_moves)
            if move_index == 0:
                logger.warning("insert_move called with empty match_moves for move %r", move)
                return
            if move_index % 2 == 1:
                # White move -> new row
                row_number = (move_index + 1) // 2
                tag = "even" if (row_number - 1) % 2 == 0 else "odd"
                self.tree.insert("", "end", values=(row_number, move, ""), tags=(tag,))
            else:
                # Black move -> fill last row's black column
                children = self.tree.get_children()
                if not children:
                    # Recovery: no row exists (e.g., after clear) – create it with empty white
                    row_number = move_index // 2
                    tag = "even" if (row_number - 1) % 2 == 0 else "odd"
                    logger.debug("insert_move recovery: no row for black move %r", move)
                    self.tree.insert("", "end", values=(row_number, "", move), tags=(tag,))
                else:
                    row = children[-1]
                    self.tree.set(row, "black", move)
            try:
                self.tree.update_idletasks()
                self.tree.yview_moveto(1.0)
            except tk.TclError as e:
                logger.debug("insert_move scroll failed: %s", e)
            try:
                self._move_count.configure(text=f"{len(self.match_moves)} moves")
            except tk.TclError as e:
                logger.debug("move_count update failed: %s", e)
        except tk.TclError as e:
            logger.debug("insert_move TclError: %s", e)
        except Exception as e:
            logger.debug("insert_move error: %s", e, exc_info=True)

    def set_moves(self, moves):
        try:
            self.clear_tree()
            # Build rows of 2 moves (white, black)
            for i in range(0, len(moves), 2):
                row_number = i // 2 + 1
                white = moves[i] if i < len(moves) else ""
                black = moves[i + 1] if i + 1 < len(moves) else ""
                tag = "even" if (row_number - 1) % 2 == 0 else "odd"
                self.tree.insert("", "end", values=(row_number, white, black), tags=(tag,))
            try:
                self.tree.update_idletasks()
                self.tree.yview_moveto(1.0)
            except tk.TclError as e:
                logger.debug("set_moves scroll failed: %s", e)
            try:
                self._move_count.configure(text=f"{len(moves)} moves")
            except tk.TclError as e:
                logger.debug("move_count update failed: %s", e)
        except tk.TclError as e:
            logger.debug("set_moves TclError: %s", e)
        except Exception as e:
            logger.debug("set_moves error: %s", e, exc_info=True)

    def on_manual_mode_checkbox_listener(self):
        try:
            if self.enable_manual_mode.get() == 1:
                self.manual_mode_frame.pack(fill="x", after=self.manual_mode_checkbox, pady=(0,2))
                self.manual_mode_frame.update()
            else:
                self.manual_mode_frame.pack_forget()
                self.manual_mode_checkbox.update()
        except Exception:
            pass

    def update_evaluation_display(self, eval_str, wdl_str, material_str, bot_acc, opponent_acc):
        try:
            self.eval_text["text"] = eval_str
            try:
                if eval_str.startswith("M"):
                    mate_value = int(eval_str[1:])
                    self.eval_text["fg"] = SUCCESS if mate_value > 0 else DANGER
                else:
                    eval_value = float(eval_str)
                    self.eval_text["fg"] = SUCCESS if eval_value > 0.3 else (DANGER if eval_value < -0.3 else TEXT_PRIMARY)
            except ValueError:
                self.eval_text["fg"] = TEXT_MUTED
            self._update_eval_bar(eval_str)
            self.wdl_text["text"] = wdl_str
            self.wdl_text["fg"] = TEXT_PRIMARY
            self.material_text["text"] = material_str
            try:
                if material_str.startswith("+"):
                    self.material_text["fg"] = SUCCESS
                elif material_str.startswith("-"):
                    self.material_text["fg"] = DANGER
                else:
                    self.material_text["fg"] = TEXT_MUTED
            except Exception:
                self.material_text["fg"] = TEXT_MUTED
            self.white_acc_text["text"] = bot_acc
            self.white_acc_text["fg"] = TEXT_PRIMARY
            self.black_acc_text["text"] = opponent_acc
            self.black_acc_text["fg"] = TEXT_PRIMARY
            self.eval_text.update()
            self.wdl_text.update()
            self.material_text.update()
            self.white_acc_text.update()
            self.black_acc_text.update()
        except Exception as e:
            logger.debug("update_evaluation_display error: %s", e)


if __name__ == "__main__":
    window = tk.Tk()
    my_gui = GUI(window)
    window.mainloop()
