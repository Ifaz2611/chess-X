import atexit
import os
import platform
import signal
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkFont, messagebox, ttk

import multiprocess
from selenium import webdriver
from selenium.common import WebDriverException
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

from overlay import run
from stockfish_bot import StockfishBot
from utilities import check_linux_input_permissions, get_keyboard_handler, get_logger, is_wayland

logger = get_logger("gui")
_kb, _kb_backend = get_keyboard_handler()
if _kb is None:
    try:
        import keyboard as _kb
        _kb_backend = "keyboard"
    except Exception:
        _kb = None
        _kb_backend = None

if is_wayland():
    logger.warning("Wayland detected - overlay may not work reliably")
_ok, _msg = check_linux_input_permissions()
if not _ok:
    logger.warning(_msg)

BG_BASE = "#0B0F14"
BG_CARD = "#151B23"
BG_INPUT = "#1E2A3A"
BG_ELEVATED = "#232E3C"
BORDER = "#263447"
TEXT_PRIMARY = "#F1F5F9"
TEXT_SECONDARY = "#A6B3C5"
TEXT_MUTED = "#708096"
ACCENT = "#8B5CF6"
ACCENT_HI = "#7C3AED"
CYAN = "#06B6D4"
SUCCESS = "#10B981"
SUCCESS_BG = "#052E1C"
DANGER = "#EF4444"
DANGER_BG = "#2E0D12"
WARNING = "#F59E0B"


class GUI:
    """Compact responsive controller for the Stockfish bot.

    The process, browser, persistence, and protocol methods below are kept
    compatible with the original application; only the layout is simplified.
    """

    def __init__(self, master):
        self.master = master
        self.exit = False
        self._cleaned = False
        self.chrome = None
        self.chrome_url = None
        self.chrome_session_id = None
        self.stockfish_bot_pipe = None
        self.overlay_screen_pipe = None
        self.stockfish_bot_process = None
        self.overlay_screen_process = None
        self._overlay_queue = None
        self.restart_after_stopping = False
        self.match_moves = []
        self.running = False
        self.opened_browser = False
        self.opening_browser = False
        self._save_after_id = None
        self._export_counter = 0
        self._last_export_dir = None

        master.title("CHESS-X · Stockfish Bot")
        master.geometry("980x700")
        master.minsize(760, 560)
        master.configure(bg=BG_BASE)
        master.protocol("WM_DELETE_WINDOW", self.on_close_listener)
        try:
            master.iconphoto(True, tk.PhotoImage(file="src/assets/pawn_32x32.png"))
        except Exception:
            pass
        try:
            master.attributes("-topmost", True)
        except Exception:
            pass
        atexit.register(self._cleanup)
        try:
            signal.signal(signal.SIGINT, lambda *_: self.on_close_listener())
            signal.signal(signal.SIGTERM, lambda *_: self.on_close_listener())
        except Exception:
            pass

        self.F_TITLE = tkFont.Font(family="Segoe UI", size=15, weight="bold")
        self.F_LABEL = tkFont.Font(family="Segoe UI", size=9)
        self.F_VALUE = tkFont.Font(family="Consolas", size=10, weight="bold")
        self.F_BUTTON = tkFont.Font(family="Segoe UI", size=9, weight="bold")
        self.F_SMALL = tkFont.Font(family="Segoe UI", size=8)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=BG_CARD, fieldbackground=BG_CARD,
                        foreground=TEXT_PRIMARY, rowheight=28, borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=BG_ELEVATED,
                        foreground=TEXT_SECONDARY, relief="flat",
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#3B2A68")])
        style.configure("Dark.Vertical.TScrollbar", background=BG_ELEVATED,
                        troughcolor=BG_BASE, borderwidth=0, arrowsize=12)

        self._build_header()
        self._build_body()
        self._build_footer()
        self._load_state()
        self._start_background_workers()

    def _build_header(self):
        header = tk.Frame(self.master, bg=BG_CARD, height=70)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=ACCENT, height=2).pack(fill="x")
        inner = tk.Frame(header, bg=BG_CARD)
        inner.pack(fill="both", expand=True, padx=20, pady=10)
        title = tk.Frame(inner, bg=BG_CARD)
        title.pack(side="left", fill="y")
        tk.Label(title, text="CHESS", font=self.F_TITLE, fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left")
        tk.Label(title, text="-X", font=self.F_TITLE, fg=ACCENT, bg=BG_CARD).pack(side="left")
        tk.Label(title, text="  STOCKFISH BOT", font=self.F_SMALL, fg=TEXT_MUTED, bg=BG_CARD).pack(side="left", padx=(8, 0), pady=(8, 0))
        tk.Label(inner, text="1 Start   ·   2 Stop   ·   Esc Kill", font=self.F_SMALL,
                 fg=TEXT_MUTED, bg=BG_CARD).pack(side="right", anchor="s", pady=(0, 3))

        status = tk.Frame(inner, bg=DANGER_BG, highlightbackground="#4A151A", highlightthickness=1)
        status.pack(side="right", padx=(0, 18))
        self._status_pill = status
        self._status_dot = tk.Canvas(status, width=9, height=9, bg=DANGER_BG, highlightthickness=0)
        self._status_dot.pack(side="left", padx=(9, 5), pady=7)
        self._dot_oval = self._status_dot.create_oval(0, 0, 9, 9, fill=DANGER, outline="")
        self.status_text = tk.Label(status, text="INACTIVE", font=("Segoe UI", 8, "bold"), fg=DANGER, bg=DANGER_BG)
        self.status_text.pack(side="left", padx=(0, 10), pady=5)

    def _build_body(self):
        body = tk.Frame(self.master, bg=BG_BASE)
        body.pack(fill="both", expand=True, padx=16, pady=14)
        pane = tk.PanedWindow(body, orient="horizontal", sashwidth=8, bg=BG_BASE, bd=0, relief="flat")
        pane.pack(fill="both", expand=True)
        left = tk.Frame(pane, bg=BG_BASE, width=350)
        right = tk.Frame(pane, bg=BG_CARD)
        pane.add(left, minsize=300, width=350, stretch="never")
        pane.add(right, minsize=400, stretch="always")

        canvas = tk.Canvas(left, bg=BG_BASE, highlightthickness=0)
        scroll = ttk.Scrollbar(left, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
        content = tk.Frame(canvas, bg=BG_BASE)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

        self._build_controls(content)
        self._build_moves(right)

    def _card(self, parent, title):
        box = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="x", pady=(0, 10))
        tk.Label(box, text=title.upper(), font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=BG_CARD).pack(anchor="w", padx=14, pady=(11, 7))
        tk.Frame(box, bg=BORDER, height=1).pack(fill="x", padx=14)
        content = tk.Frame(box, bg=BG_CARD)
        content.pack(fill="x", padx=14, pady=12)
        return content

    def _button(self, parent, text, command, color=ACCENT):
        button = tk.Button(parent, text=text, command=command, font=self.F_BUTTON,
                           fg="#FFFFFF", bg=color, activebackground=ACCENT_HI,
                           activeforeground="#FFFFFF", relief="flat", bd=0,
                           cursor="hand2", padx=10, pady=8)
        button.pack(fill="x", pady=(0, 8))
        return button

    def _check(self, parent, text, variable, command=None):
        return tk.Checkbutton(parent, text=text, variable=variable, command=command,
                              bg=BG_CARD, fg=TEXT_SECONDARY, activebackground=BG_CARD,
                              activeforeground=TEXT_PRIMARY, selectcolor=BG_ELEVATED,
                              anchor="w", font=self.F_LABEL, bd=0, highlightthickness=0)

    def _build_controls(self, parent):
        self.website = tk.StringVar(value="chesscom")
        self.enable_manual_mode = tk.BooleanVar(value=False)
        self.enable_mouseless_mode = tk.BooleanVar(value=False)
        self.enable_non_stop_puzzles = tk.IntVar(value=0)
        self.enable_non_stop_matches = tk.IntVar(value=0)
        self.enable_bongcloud = tk.IntVar(value=0)
        self.mouse_latency = tk.DoubleVar(value=0.0)
        self.slow_mover = tk.IntVar(value=100)
        self.skill_level = tk.IntVar(value=20)
        self.stockfish_depth = tk.IntVar(value=15)
        self.memory = tk.IntVar(value=512)
        self.cpu_threads = tk.IntVar(value=1)
        self.enable_topmost = tk.IntVar(value=1)

        site = self._card(parent, "Platform")
        row = tk.Frame(site, bg=BG_ELEVATED)
        row.pack(fill="x")
        self.chesscom_radio_button = tk.Radiobutton(row, text="Chess.com", variable=self.website, value="chesscom", indicatoron=0, bg=ACCENT, fg="white", selectcolor=ACCENT, activebackground=ACCENT_HI, relief="flat", bd=0, pady=7)
        self.chesscom_radio_button.pack(side="left", fill="x", expand=True)
        self.lichess_radio_button = tk.Radiobutton(row, text="Lichess.org", variable=self.website, value="lichess", indicatoron=0, bg=BG_ELEVATED, fg=TEXT_SECONDARY, selectcolor=ACCENT, activebackground=BG_ELEVATED, relief="flat", bd=0, pady=7)
        self.lichess_radio_button.pack(side="left", fill="x", expand=True)

        controls = self._card(parent, "Controls")
        self.open_browser_button = self._button(controls, "OPEN BROWSER", self.on_open_browser_button_listener, CYAN)
        self.start_button = self._button(controls, "START ENGINE", self.on_start_button_listener)
        self.start_button.configure(state="disabled", disabledforeground="#FFFFFF")

        modes = self._card(parent, "Modes")
        self.manual_mode_checkbox = self._check(modes, "Manual mode  (press 3)", self.enable_manual_mode, self.on_manual_mode_checkbox_listener)
        self.manual_mode_checkbox.pack(fill="x")
        self.manual_mode_frame = tk.Frame(modes, bg="#1A2332", highlightbackground="#23344A", highlightthickness=1)
        self.manual_mode_label = tk.Label(self.manual_mode_frame, text="Press 3 to make a move", font=("Segoe UI", 8, "bold"), fg=CYAN, bg="#1A2332")
        self.manual_mode_label.pack(anchor="w", padx=9, pady=7)
        self._check(modes, "Mouseless mode  (Lichess only)", self.enable_mouseless_mode).pack(fill="x")
        self._check(modes, "Non-stop puzzles", self.enable_non_stop_puzzles).pack(fill="x")
        self._check(modes, "Non-stop online matches", self.enable_non_stop_matches).pack(fill="x")
        self._check(modes, "Bongcloud", self.enable_bongcloud).pack(fill="x")
        tk.Label(modes, text="Mouse latency", fg=TEXT_MUTED, bg=BG_CARD, font=self.F_LABEL).pack(anchor="w", pady=(8, 0))
        self.mouse_latency_scale = tk.Scale(modes, from_=0, to=15, resolution=.2, variable=self.mouse_latency, orient="horizontal", bg=BG_CARD, fg=TEXT_MUTED, troughcolor=BG_ELEVATED, activebackground=ACCENT, highlightthickness=0, bd=0, showvalue=True)
        self.mouse_latency_scale.pack(fill="x")

        engine = self._card(parent, "Stockfish")
        self._entry_row(engine, "Slow mover", self.slow_mover, "slow_mover_entry", 10, 1000)
        self._scale_row(engine, "Skill level", self.skill_level, 0, 20, "skill_level_scale")
        self._scale_row(engine, "Depth", self.stockfish_depth, 1, 20, "stockfish_depth_scale")
        self._entry_row(engine, "Memory (MB)", self.memory, "memory_entry", 16, 8192)
        self._entry_row(engine, "CPU threads", self.cpu_threads, "cpu_threads_entry", 1, 32)

        misc = self._card(parent, "Engine binary")
        self.stockfish_path = ""
        self.select_stockfish_button = self._button(misc, "SELECT STOCKFISH", self.on_select_stockfish_button_listener, BG_ELEVATED)
        self.stockfish_path_text = tk.Label(misc, text="No Stockfish selected", fg="#FCA5A5", bg=DANGER_BG, justify="left", anchor="w", wraplength=300, font=("Consolas", 8))
        self.stockfish_path_text.pack(fill="x", pady=(2, 0), padx=2)
        self._path_wrap = misc
        self.topmost_check_button = self._check(misc, "Keep window on top", self.enable_topmost, self.on_topmost_check_button_listener)
        self.topmost_check_button.pack(fill="x", pady=(10, 0))

    def _entry_row(self, parent, label, variable, attr, minimum, maximum):
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label, fg=TEXT_SECONDARY, bg=BG_CARD, font=self.F_LABEL).pack(side="left")
        entry = tk.Entry(row, textvariable=variable, width=8, justify="center", font=("Consolas", 9), fg=TEXT_PRIMARY, bg=BG_INPUT, insertbackground=TEXT_PRIMARY, relief="flat", highlightthickness=1, highlightbackground=BORDER)
        entry.pack(side="right", ipady=4)
        setattr(self, attr, entry)
        def validate(value):
            return value == "" or value.isdigit()
        entry.configure(validate="key", validatecommand=(entry.register(validate), "%P"))
        entry.bind("<FocusOut>", lambda _e: self._clamp(variable, minimum, maximum))

    def _scale_row(self, parent, label, variable, minimum, maximum, attr):
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, fg=TEXT_SECONDARY, bg=BG_CARD, font=self.F_LABEL).pack(anchor="w")
        scale = tk.Scale(row, from_=minimum, to=maximum, variable=variable, orient="horizontal", bg=BG_CARD, fg=TEXT_MUTED, troughcolor=BG_ELEVATED, activebackground=ACCENT, highlightthickness=0, bd=0, showvalue=True)
        scale.pack(fill="x")
        setattr(self, attr, scale)

    def _clamp(self, variable, minimum, maximum):
        try:
            variable.set(max(minimum, min(maximum, int(variable.get()))))
        except (TypeError, ValueError):
            variable.set(minimum)

    def _build_moves(self, parent):
        top = tk.Frame(parent, bg=BG_CARD)
        top.pack(fill="x", padx=16, pady=(15, 10))
        tk.Label(top, text="MOVE HISTORY", font=("Segoe UI", 10, "bold"), fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left")
        self._move_count = tk.Label(top, text="0 moves", font=self.F_SMALL, fg=TEXT_MUTED, bg=BG_ELEVATED, padx=8, pady=3)
        self._move_count.pack(side="right")
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=16)
        table = tk.Frame(parent, bg=BG_CARD)
        table.pack(fill="both", expand=True, padx=12, pady=12)
        self.tree = ttk.Treeview(table, columns=("move_no", "white", "black"), show="headings", selectmode="browse")
        self.tree.heading("move_no", text="#")
        self.tree.heading("white", text="White")
        self.tree.heading("black", text="Black")
        self.tree.column("move_no", width=48, stretch=False, anchor="center")
        self.tree.column("white", width=150, anchor="center")
        self.tree.column("black", width=150, anchor="center")
        self.tree.tag_configure("odd", background="#161C25")
        self.tree.tag_configure("even", background=BG_CARD)
        self.vsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=self.vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.export_pgn_button = self._button(parent, "EXPORT PGN", self.on_export_pgn_button_listener, BG_ELEVATED)
        self.export_pgn_button.pack(fill="x", padx=16, pady=(0, 12))

        eval_box = tk.Frame(parent, bg="#10161F", highlightbackground=BORDER, highlightthickness=1)
        eval_box.pack(fill="x", padx=16, pady=(0, 16))
        tk.Label(eval_box, text="ENGINE EVALUATION", font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg="#10161F").pack(anchor="w", padx=12, pady=(10, 5))
        self.eval_text = tk.Label(eval_box, text="—", font=("Consolas", 18, "bold"), fg=TEXT_PRIMARY, bg="#10161F")
        self.eval_text.pack(anchor="w", padx=12)
        self._eval_canvas = tk.Canvas(eval_box, height=8, bg=BG_ELEVATED, highlightthickness=0)
        self._eval_canvas.pack(fill="x", padx=12, pady=8)
        self._eval_bar = self._eval_canvas.create_rectangle(0, 0, 0, 8, fill=TEXT_MUTED, outline="")
        stats = tk.Frame(eval_box, bg="#10161F")
        stats.pack(fill="x", padx=12, pady=(0, 10))
        self.wdl_text = self._stat(stats, "WDL")
        self.material_text = self._stat(stats, "MATERIAL")
        self.white_acc_text = self._stat(stats, "BOT ACC")
        self.black_acc_text = self._stat(stats, "OPP ACC")

    def _stat(self, parent, name):
        box = tk.Frame(parent, bg="#10161F")
        box.pack(side="left", fill="x", expand=True)
        tk.Label(box, text=name, font=("Segoe UI", 7), fg=TEXT_MUTED, bg="#10161F").pack(anchor="w")
        value = tk.Label(box, text="—", font=self.F_VALUE, fg=TEXT_PRIMARY, bg="#10161F")
        value.pack(anchor="w")
        return value

    def _build_footer(self):
        bar = tk.Frame(self.master, bg=DANGER_BG, highlightbackground="#4A151A", highlightthickness=1)
        bar.pack(fill="x", padx=16, pady=(0, 12))
        tk.Label(bar, text="Emergency stop", fg="#FCA5A5", bg=DANGER_BG, font=self.F_SMALL).pack(side="left", padx=10, pady=7)
        self.emergency_kill_button = tk.Button(bar, text="KILL BOT + BROWSER", command=self.emergency_kill, font=self.F_BUTTON, fg="white", bg=DANGER, activebackground="#B91C1C", relief="flat", bd=0, padx=12, pady=5, cursor="hand2")
        self.emergency_kill_button.pack(side="right", padx=7, pady=5)
        for key in ("<Escape>", "<F12>", "<Control-q>", "<Control-Q>"):
            self.master.bind(key, lambda _e: self.emergency_kill())

    def _load_state(self):
        self._config = self._load_config()
        self.stockfish_path = self._config.get("stockfish_path") or self._load_stockfish_path()
        self._apply_config_values()
        self._refresh_stockfish_label()
        self._setup_config_autosave()
        self._setup_validation_traces()
        self.on_manual_mode_checkbox_listener()
        self._validate_inputs()
        self._update_eval_bar("—")
        self.master.after(150, lambda: self._update_eval_bar("—"))

    def _refresh_stockfish_label(self):
        path = self.stockfish_path or "No Stockfish selected"
        valid = bool(self.stockfish_path and os.path.exists(self.stockfish_path))
        self.stockfish_path_text.configure(text=path, fg="#6EE7B7" if valid else "#FCA5A5", bg="#0B1E16" if valid else DANGER_BG)

    def _start_background_workers(self):
        threading.Thread(target=self.process_checker_thread, daemon=True).start()
        threading.Thread(target=self.browser_checker_thread, daemon=True).start()
        threading.Thread(target=self.process_communicator_thread, daemon=True).start()
        threading.Thread(target=self.keypress_listener_thread, daemon=True).start()

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
        # Persist config on exit (P1)
        try:
            self._save_config()
        except Exception:
            pass
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

    def emergency_kill(self):
        """Emergency hard-kill: immediately terminate bot, overlay, browser and exit process."""
        logger.critical("EMERGENCY KILL triggered by user")
        self.exit = True
        # visual feedback
        try:
            self.emergency_kill_button.configure(text="☠ KILLING...", state="disabled", bg="#7F1D1D")
            self.emergency_kill_button.update_idletasks()
        except Exception:
            pass
        try:
            self._set_status("KILLED", "#FFFFFF", "#7F1D1D")
        except Exception:
            pass
        # hard-kill child processes without graceful join
        for proc_attr in ("stockfish_bot_process", "overlay_screen_process"):
            try:
                proc = getattr(self, proc_attr, None)
                if proc is not None:
                    try:
                        if hasattr(proc, "is_alive") and proc.is_alive():
                            try:
                                proc.kill()
                            except Exception:
                                try:
                                    proc.terminate()
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    setattr(self, proc_attr, None)
            except Exception as e:
                logger.debug("emergency kill %s error: %s", proc_attr, e)
        # close pipes / queues immediately
        try:
            if getattr(self, "stockfish_bot_pipe", None) is not None:
                try:
                    self.stockfish_bot_pipe.close()
                except Exception:
                    pass
                self.stockfish_bot_pipe = None
        except Exception:
            pass
        try:
            if getattr(self, "_overlay_queue", None) is not None:
                try:
                    self._overlay_queue.close()
                    self._overlay_queue.cancel_join_thread()
                except Exception:
                    pass
                self._overlay_queue = None
        except Exception:
            pass
        # quit chrome hard
        try:
            if getattr(self, "chrome", None) is not None:
                try:
                    self.chrome.quit()
                except Exception:
                    pass
                self.chrome = None
        except Exception:
            pass
        self.running = False
        self.opened_browser = False
        self.opening_browser = False
        # destroy window
        try:
            self.master.destroy()
        except Exception:
            pass
        # force exit bypassing atexit delays - ensures no zombie
        try:
            os._exit(1)
        except Exception:
            pass
        try:
            sys.exit(1)
        except SystemExit:
            raise
        except Exception:
            try:
                os._exit(1)
            except Exception:
                pass

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
            if _kb is None:
                continue
            try:
                # Emergency kill - works even without browser open
                try:
                    if _kb.is_pressed("esc") or _kb.is_pressed("f12"):
                        try:
                            self.master.after(0, self.emergency_kill)
                        except Exception:
                            self.emergency_kill()
                        time.sleep(0.5)
                        continue
                except Exception:
                    pass
                if not self.opened_browser:
                    continue
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
                    # P1: re-validate inputs now that browser is ready
                    try: self._validate_inputs()
                    except Exception: pass
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

    def _unique_pgn_initialfile(self):
        """Generate a unique PGN filename for every export click (timestamp + counter)."""
        try:
            self._export_counter += 1
        except Exception:
            self._export_counter = 1
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # use milliseconds to avoid collision within same second rapid clicks
        ms = datetime.datetime.now().strftime("%f")[:3]
        return f"match_{ts}_{ms}_{self._export_counter:02d}.pgn"

    def on_export_pgn_button_listener(self):
        # Disable topmost so dialog is visible (fixes hidden dialog bug)
        was_topmost = bool(self.enable_topmost.get())
        try:
            self.master.attributes("-topmost", False)
        except Exception:
            pass
        # unique filename every click - fixes bug where export.pgn always overwrote same file
        initial = self._unique_pgn_initialfile()
        initial_dir = self._last_export_dir if self._last_export_dir and os.path.isdir(self._last_export_dir) else None
        try:
            # use asksaveasfilename so we can enforce unique name if file exists
            path = filedialog.asksaveasfilename(parent=self.master, initialfile=initial, initialdir=initial_dir,
                                                defaultextension=".pgn", filetypes=[("Portable Game Notation", "*.pgn"), ("All Files", "*.*")])
        finally:
            try:
                if was_topmost:
                    self.master.attributes("-topmost", True)
            except Exception:
                pass
        if not path:
            return
        # remember dir for next time
        try:
            self._last_export_dir = os.path.dirname(os.path.abspath(path)) or self._last_export_dir
        except Exception:
            pass
        # ensure no overwrite: if file exists, append _1, _2 ...
        if os.path.exists(path):
            base, ext = os.path.splitext(path)
            # if extension missing, assume .pgn
            if not ext:
                ext = ".pgn"
                path = base + ext
                base, ext = os.path.splitext(path)
            n = 1
            new_path = f"{base}_{n}{ext}"
            while os.path.exists(new_path):
                n += 1
                new_path = f"{base}_{n}{ext}"
            path = new_path
            logger.info("Export file exists, using unique fallback: %s", path)
        data = ""
        for i in range(len(self.match_moves) // 2 + 1):
            if len(self.match_moves) % 2 == 0 and i == len(self.match_moves) // 2:
                continue
            data += str(i + 1) + ". "
            data += self.match_moves[i * 2] + " "
            if (i * 2) + 1 < len(self.match_moves):
                data += self.match_moves[i * 2 + 1] + " "
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            logger.info("PGN exported to %s", path)
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

    # ── Full config persistence (P1: Persist config) ─────────────────
    def _load_config(self):
        """Load full GUI config from JSON. Returns dict with defaults merged."""
        import json as _json
        defaults = {
            "stockfish_path": "",
            "website": "chesscom",
            "enable_manual_mode": False,
            "enable_mouseless_mode": False,
            "enable_non_stop_puzzles": 0,
            "enable_non_stop_matches": 0,
            "enable_bongcloud": 0,
            "mouse_latency": 0.0,
            "slow_mover": 100,
            "skill_level": 20,
            "stockfish_depth": 15,
            "memory": 512,
            "cpu_threads": 1,
            "enable_topmost": 1,
        }
        cfg_path = self._config_path()
        candidates = [cfg_path, "config.json", os.path.join(os.path.expanduser("~"), ".chess-x.json"), os.path.join("src", "config.json")]
        # deduplicate preserving order
        seen = set()
        ordered = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                ordered.append(c)
        for p in ordered:
            try:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as fh:
                        data = _json.load(fh)
                        if not isinstance(data, dict):
                            continue
                        # merge
                        merged = dict(defaults)
                        for k in defaults:
                            if k in data:
                                merged[k] = data[k]
                        # stockfish_path backward compat
                        if "stockfish" in data and not merged.get("stockfish_path"):
                            merged["stockfish_path"] = data.get("stockfish") or ""
                        # validate stockfish path exists else clear
                        sp = merged.get("stockfish_path")
                        if sp and not os.path.exists(sp):
                            logger.warning("Saved stockfish path not found on disk: %s", sp)
                            merged["stockfish_path"] = ""
                        elif sp and not self._is_valid_stockfish(sp, quick_check=True):
                            logger.warning("Saved stockfish path invalid: %s", sp)
                            # keep but warn; don't clear – user may have custom name
                        # clamp numeric
                        try:
                            merged["mouse_latency"] = max(0.0, min(15.0, float(merged["mouse_latency"])))
                            merged["slow_mover"] = max(10, min(1000, int(merged["slow_mover"])))
                            merged["skill_level"] = max(0, min(20, int(merged["skill_level"])))
                            merged["stockfish_depth"] = max(1, min(20, int(merged["stockfish_depth"])))
                            merged["memory"] = max(16, min(8192, int(merged["memory"])))
                            merged["cpu_threads"] = max(1, min(32, int(merged["cpu_threads"])))
                            merged["enable_topmost"] = 1 if int(merged["enable_topmost"]) else 0
                            merged["website"] = "lichess" if str(merged["website"]).lower() in ("lichess", "lichess.org") else "chesscom"
                        except Exception:
                            pass
                        logger.info("Loaded config from %s", p)
                        return merged
            except Exception as e:
                logger.debug("load config %s failed: %s", p, e)
        return defaults

    def _save_config(self):
        """Persist current GUI values to JSON (debounced caller should use _schedule_save)."""
        import json as _json
        cfg = self._config_path()
        try:
            os.makedirs(os.path.dirname(cfg) if os.path.dirname(cfg) else ".", exist_ok=True)
            data = {}
            if os.path.exists(cfg):
                try:
                    with open(cfg, "r", encoding="utf-8") as fh:
                        data = _json.load(fh)
                        if not isinstance(data, dict):
                            data = {}
                except Exception:
                    data = {}
            # collect current values
            try:
                data["stockfish_path"] = getattr(self, "stockfish_path", "") or ""
                data["website"] = self.website.get() if hasattr(self, "website") else "chesscom"
                data["enable_manual_mode"] = bool(self.enable_manual_mode.get()) if hasattr(self, "enable_manual_mode") else False
                data["enable_mouseless_mode"] = bool(self.enable_mouseless_mode.get()) if hasattr(self, "enable_mouseless_mode") else False
                data["enable_non_stop_puzzles"] = int(self.enable_non_stop_puzzles.get()) if hasattr(self, "enable_non_stop_puzzles") else 0
                data["enable_non_stop_matches"] = int(self.enable_non_stop_matches.get()) if hasattr(self, "enable_non_stop_matches") else 0
                data["enable_bongcloud"] = int(self.enable_bongcloud.get()) if hasattr(self, "enable_bongcloud") else 0
                data["mouse_latency"] = float(self.mouse_latency.get()) if hasattr(self, "mouse_latency") else 0.0
                data["slow_mover"] = int(self.slow_mover.get()) if hasattr(self, "slow_mover") else 100
                data["skill_level"] = int(self.skill_level.get()) if hasattr(self, "skill_level") else 20
                data["stockfish_depth"] = int(self.stockfish_depth.get()) if hasattr(self, "stockfish_depth") else 15
                data["memory"] = int(self.memory.get()) if hasattr(self, "memory") else 512
                data["cpu_threads"] = int(self.cpu_threads.get()) if hasattr(self, "cpu_threads") else 1
                data["enable_topmost"] = int(self.enable_topmost.get()) if hasattr(self, "enable_topmost") else 1
            except Exception as e:
                logger.debug("_save_config collect error: %s", e)
            with open(cfg, "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2)
            logger.debug("Saved config to %s", cfg)
        except Exception as e:
            logger.debug("save config failed: %s", e)

    def _apply_config_values(self):
        """Apply loaded config dict to tkinter variables (call after vars created)."""
        cfg = getattr(self, "_config", {}) or {}
        try:
            if "website" in cfg:
                try: self.website.set(cfg["website"])
                except Exception: pass
            if "enable_manual_mode" in cfg:
                try: self.enable_manual_mode.set(bool(cfg["enable_manual_mode"]))
                except Exception: pass
            if "enable_mouseless_mode" in cfg:
                try: self.enable_mouseless_mode.set(bool(cfg["enable_mouseless_mode"]))
                except Exception: pass
            if "enable_non_stop_puzzles" in cfg:
                try: self.enable_non_stop_puzzles.set(int(cfg["enable_non_stop_puzzles"]))
                except Exception: pass
            if "enable_non_stop_matches" in cfg:
                try: self.enable_non_stop_matches.set(int(cfg["enable_non_stop_matches"]))
                except Exception: pass
            if "enable_bongcloud" in cfg:
                try: self.enable_bongcloud.set(int(cfg["enable_bongcloud"]))
                except Exception: pass
            if "mouse_latency" in cfg:
                try: self.mouse_latency.set(float(cfg["mouse_latency"]))
                except Exception: pass
            if "slow_mover" in cfg:
                try: self.slow_mover.set(int(cfg["slow_mover"]))
                except Exception: pass
            if "skill_level" in cfg:
                try: self.skill_level.set(int(cfg["skill_level"]))
                except Exception: pass
            if "stockfish_depth" in cfg:
                try: self.stockfish_depth.set(int(cfg["stockfish_depth"]))
                except Exception: pass
            if "memory" in cfg:
                try: self.memory.set(int(cfg["memory"]))
                except Exception: pass
            if "cpu_threads" in cfg:
                try: self.cpu_threads.set(int(cfg["cpu_threads"]))
                except Exception: pass
            if "enable_topmost" in cfg:
                try:
                    self.enable_topmost.set(int(cfg["enable_topmost"]))
                    # apply topmost immediately
                    try:
                        self.master.attributes("-topmost", bool(int(cfg["enable_topmost"])))
                    except Exception: pass
                except Exception: pass
            # need to refresh manual mode visibility
            try: self.on_manual_mode_checkbox_listener()
            except Exception: pass
        except Exception as e:
            logger.debug("_apply_config_values error: %s", e)

    def _schedule_save(self, *a):
        """Debounce save: coalesce rapid var changes into single disk write after 400ms."""
        try:
            if hasattr(self, "_save_after_id") and self._save_after_id:
                try: self.master.after_cancel(self._save_after_id)
                except Exception: pass
            self._save_after_id = self.master.after(400, self._save_config)
        except Exception:
            try: self._save_config()
            except Exception: pass

    def _setup_config_autosave(self):
        """Wire traces on all persisted vars so changes auto-save."""
        try:
            for var in [self.website, self.enable_manual_mode, self.enable_mouseless_mode,
                        self.enable_non_stop_puzzles, self.enable_non_stop_matches,
                        self.enable_bongcloud, self.mouse_latency, self.slow_mover,
                        self.skill_level, self.stockfish_depth, self.memory, self.cpu_threads,
                        self.enable_topmost]:
                try: var.trace_add("write", lambda *a: self._schedule_save())
                except Exception:
                    try: var.trace("w", lambda *a: self._schedule_save())
                    except Exception: pass
        except Exception as e:
            logger.debug("autosave setup failed: %s", e)

    # ── P1: Input validation – live error hints, clamp, disable Start until valid ─
    def _validate_inputs(self, *a):
        """Validate all numeric entries; show red border on error and toggle Start button."""
        valid = True
        cpu = os.cpu_count() or 4
        # Stockfish path must exist if Start is to be enabled (Browser can still open without)
        try:
            has_sf = bool(self.stockfish_path and os.path.exists(self.stockfish_path))
        except Exception:
            has_sf = False
        # Check Slow Mover 10-1000
        try:
            sm = int(self.slow_mover.get())
            if sm < 10 or sm > 1000:
                valid = False
                try: self.slow_mover_entry.configure(highlightbackground=DANGER, highlightcolor=DANGER)
                except Exception: pass
            else:
                try: self.slow_mover_entry.configure(highlightbackground=BORDER, highlightcolor=BORDER)
                except Exception: pass
        except Exception:
            valid = False
            try: self.slow_mover_entry.configure(highlightbackground=DANGER, highlightcolor=DANGER)
            except Exception: pass
        # Memory 16-8192
        try:
            mem = int(self.memory.get())
            if mem < 16 or mem > 8192:
                valid = False
                try: self.memory_entry.configure(highlightbackground=DANGER, highlightcolor=DANGER)
                except Exception: pass
            else:
                try: self.memory_entry.configure(highlightbackground=BORDER, highlightcolor=BORDER)
                except Exception: pass
        except Exception:
            valid = False
        # Threads 1 - cpu*2, warn if > cpu_count
        try:
            thr = int(self.cpu_threads.get())
            if thr < 1 or thr > 32:
                valid = False
                try: self.cpu_threads_entry.configure(highlightbackground=DANGER, highlightcolor=DANGER)
                except Exception: pass
            elif thr > cpu:
                try: self.cpu_threads_entry.configure(highlightbackground=WARNING, highlightcolor=WARNING)
                except Exception: pass
            else:
                try: self.cpu_threads_entry.configure(highlightbackground=BORDER, highlightcolor=BORDER)
                except Exception: pass
            # Prevent Threads > cpu*2 silently
            if thr > cpu*2:
                valid = False
        except Exception:
            valid = False
        # Skill/Depth already via scales (0-20,1-20) so always valid

        # If browser not opened, Start remains disabled regardless
        can_start = valid and has_sf and getattr(self, "opened_browser", False) and not getattr(self, "running", False)
        # But allow Start to be enabled once browser opened; disable if invalid
        try:
            if not self.opened_browser:
                # keep Start disabled until browser opened
                self.start_button.configure(state="disabled", disabledforeground="#7A6AA0")
            elif not valid or not has_sf:
                self.start_button.configure(state="disabled", disabledforeground="#FCA5A5")
            else:
                if not self.running:
                    self.start_button.configure(state="normal")
        except Exception as e:
            logger.debug("_validate_inputs button toggle failed: %s", e)
        return valid

    def _setup_validation_traces(self):
        """Call after widgets exist to wire live validation + Start toggle."""
        try:
            for var in [self.slow_mover, self.memory, self.cpu_threads, self.skill_level, self.stockfish_depth, self.mouse_latency]:
                try: var.trace_add("write", self._validate_inputs)
                except Exception:
                    try: var.trace("w", lambda *a: self._validate_inputs())
                    except Exception: pass
            # Also validate when stockfish path changes (via _save_config)
            # Poll stockfish_path via after
            def _poll_sf():
                try: self._validate_inputs()
                except Exception: pass
                try: self.master.after(1000, _poll_sf)
                except Exception: pass
            try: self.master.after(800, _poll_sf)
            except Exception: pass
        except Exception as e:
            logger.debug("validation traces setup failed: %s", e)

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
        # Do not persist invalid stockfish paths – delegate to full config save
        if path and not self._is_valid_stockfish(path, quick_check=True):
            if not self._is_valid_stockfish(path, quick_check=False):
                logger.warning("Refusing to save invalid Stockfish path: %s", path)
                return
        # Update instance and persist full config
        self.stockfish_path = path
        try:
            # also update _config cache
            if hasattr(self, "_config") and isinstance(self._config, dict):
                self._config["stockfish_path"] = path
        except Exception:
            pass
        self._save_config()
        logger.info("Saved stockfish path to %s", self._config_path())

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
        # P1: live validation refresh
        try: self._validate_inputs()
        except Exception: pass

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
