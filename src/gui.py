from __future__ import annotations

import atexit
import datetime
import os
import platform
import signal
import sys
import threading
import time
from typing import Any

# Re-export color constants for backward compatibility (controls.py, eval_panel.py)
BG_BASE = "#F4F1EA"
BG_CARD = "#FFFFFF"
BG_INPUT = "#FAF8F4"
BG_ELEVATED = "#ECE7DE"
BORDER = "#D7D0C4"
TEXT_PRIMARY = "#000000"
TEXT_SECONDARY = "#000000"
TEXT_MUTED = "#666666"
ACCENT = "#B85C35"
ACCENT_HI = "#974923"
CYAN = "#3F7180"
SUCCESS = "#2E7D5B"
SUCCESS_BG = "#E6F2EC"
DANGER = "#B54747"
DANGER_BG = "#F8E9E7"
WARNING = "#B7832F"

# PyQt6 imports
import multiprocess
from PyQt6.QtCore import QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from selenium.common import WebDriverException

import protocol as proto
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


class UIProperty:
    """Reactive property with .get() and .set(val) matching Tkinter Var interface."""

    def __init__(self, value: Any, on_change=None):
        self._val = value
        self._callbacks = []
        if on_change:
            self._callbacks.append(on_change)

    def get(self) -> Any:
        return self._val

    def set(self, val: Any):
        if self._val != val:
            self._val = val
            for cb in list(self._callbacks):
                try:
                    cb(val)
                except TypeError:
                    try:
                        cb()
                    except Exception:
                        pass
                except Exception:
                    pass

    def trace_add(self, mode: str, callback):
        self._callbacks.append(callback)

    def trace(self, mode: str, callback):
        self._callbacks.append(callback)


class ThreadDispatcher(QObject):
    """Dispatches calls safely from background threads to Qt GUI main thread."""
    dispatch_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.dispatch_signal.connect(self._run)

    def _run(self, func):
        try:
            if callable(func):
                func()
        except Exception as e:
            logger.debug("Dispatched callback execution error: %s", e)

    def after(self, delay_ms: int, func):
        """Schedule callable on the Qt GUI thread (after delay_ms milliseconds)."""
        if delay_ms <= 0:
            self.dispatch_signal.emit(func)
        else:
            self.dispatch_signal.emit(lambda: QTimer.singleShot(delay_ms, func))


class EvalBarWidget(QWidget):
    """Custom horizontal evaluation bar widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self._eval_str = "—"
        self._val: float | None = None
        self._color = QColor("#8B929C")

    def set_eval(self, eval_str: str):
        self._eval_str = str(eval_str or "—").strip()
        self._val = None
        self._color = QColor("#8B929C")
        if self._eval_str.startswith("M"):
            try:
                m = int(self._eval_str[1:])
                self._val = 10.0 if m > 0 else -10.0
                self._color = QColor(SUCCESS) if m > 0 else QColor(DANGER)
            except Exception:
                pass
        elif self._eval_str not in ("—", "-", ""):
            try:
                v = float(self._eval_str)
                self._val = max(-10.0, min(10.0, v))
                if v > 0.5:
                    self._color = QColor(SUCCESS)
                elif v < -0.5:
                    self._color = QColor(DANGER)
                else:
                    self._color = QColor("#8B929C")
            except Exception:
                pass
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        mid = w // 2

        # Background trough
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#D1C8BB" if QGuiApplication.palette().window().color().lightness() > 128 else "#27272A")))
        painter.drawRoundedRect(0, 0, w, h, 4, 4)

        if self._val is None:
            # Zero / neutral tick
            painter.setBrush(QBrush(QColor("#8B929C")))
            painter.drawRect(mid - 1, 0, 2, h)
        else:
            painter.setBrush(QBrush(self._color))
            bar_len = int((abs(self._val) / 10.0) * (mid - 4))
            if self._val >= 0:
                painter.drawRoundedRect(mid, 1, max(2, bar_len), h - 2, 2, 2)
            else:
                painter.drawRoundedRect(mid - max(2, bar_len), 1, max(2, bar_len), h - 2, 2, 2)
        painter.end()


# ==============================================================================
# Modern QSS Stylesheets (Dark & Light)
# ==============================================================================

DARK_QSS = """
QMainWindow, QWidget#centralWidget {
    background-color: #121214;
    color: #F4F4F5;
    font-family: "Segoe UI", -apple-system, sans-serif;
}
QFrame.CardFrame {
    background-color: #1E1E22;
    border: 1px solid #2E2E34;
    border-radius: 8px;
}
QLabel.CardTitle {
    color: #A1A1AA;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
}
QFrame#HeaderFrame {
    background-color: #18181B;
    border-bottom: 2px solid #E06C38;
}
QLabel#AppTitleMain {
    color: #FFFFFF;
    font-size: 18px;
    font-weight: bold;
}
QLabel#AppTitleAccent {
    color: #E06C38;
    font-size: 18px;
    font-weight: bold;
}
QLabel#AppSubtitle {
    color: #71717A;
    font-size: 10px;
    font-weight: bold;
}
QPushButton {
    background-color: #27272A;
    color: #FFFFFF;
    border: 1px solid #3F3F46;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: bold;
    font-size: 11px;
}
QPushButton:hover {
    background-color: #3F3F46;
}
QPushButton:pressed {
    background-color: #18181B;
}
QPushButton:disabled {
    background-color: #18181B;
    color: #52525B;
    border-color: #27272A;
}
QPushButton#AccentButton {
    background-color: #E06C38;
    color: #FFFFFF;
    border: none;
}
QPushButton#AccentButton:hover {
    background-color: #C25625;
}
QPushButton#CyanButton {
    background-color: #0284C7;
    color: #FFFFFF;
    border: none;
}
QPushButton#CyanButton:hover {
    background-color: #0369A1;
}
QPushButton#DangerButton {
    background-color: #DC2626;
    color: #FFFFFF;
    border: none;
}
QPushButton#DangerButton:hover {
    background-color: #B91C1C;
}
QRadioButton, QCheckBox {
    color: #D4D4D8;
    font-size: 11px;
    spacing: 6px;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #52525B;
    border-radius: 3px;
    background-color: #27272A;
}
QRadioButton::indicator {
    border-radius: 7px;
}
QRadioButton::indicator:checked, QCheckBox::indicator:checked {
    background-color: #E06C38;
    border-color: #E06C38;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #27272A;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #E06C38;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #FFFFFF;
    border: 1px solid #71717A;
    width: 13px;
    margin-top: -4px;
    margin-bottom: -4px;
    border-radius: 6px;
}
QLineEdit, QSpinBox {
    background-color: #27272A;
    color: #FFFFFF;
    border: 1px solid #3F3F46;
    border-radius: 4px;
    padding: 3px 6px;
    font-family: "Consolas", monospace;
    font-size: 11px;
}
QTableWidget {
    background-color: #1E1E22;
    color: #F4F4F5;
    border: 1px solid #2E2E34;
    border-radius: 6px;
    gridline-color: #27272A;
    font-size: 11px;
}
QTableWidget::item:selected {
    background-color: #3F3F46;
    color: #FFFFFF;
}
QHeaderView::section {
    background-color: #27272A;
    color: #D4D4D8;
    padding: 5px;
    border: none;
    font-weight: bold;
    font-size: 11px;
}
QScrollBar:vertical {
    background: #18181B;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3F3F46;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""

LIGHT_QSS = """
QMainWindow, QWidget#centralWidget {
    background-color: #F4F1EA;
    color: #1A1A1A;
    font-family: "Segoe UI", -apple-system, sans-serif;
}
QFrame.CardFrame {
    background-color: #FFFFFF;
    border: 1px solid #D7D0C4;
    border-radius: 8px;
}
QLabel.CardTitle {
    color: #71695C;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
}
QFrame#HeaderFrame {
    background-color: #FFFFFF;
    border-bottom: 2px solid #B85C35;
}
QLabel#AppTitleMain {
    color: #1A1A1A;
    font-size: 18px;
    font-weight: bold;
}
QLabel#AppTitleAccent {
    color: #B85C35;
    font-size: 18px;
    font-weight: bold;
}
QLabel#AppSubtitle {
    color: #8C8273;
    font-size: 10px;
    font-weight: bold;
}
QPushButton {
    background-color: #ECE7DE;
    color: #1A1A1A;
    border: 1px solid #D7D0C4;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: bold;
    font-size: 11px;
}
QPushButton:hover {
    background-color: #DFD8CD;
}
QPushButton:pressed {
    background-color: #D1C8BA;
}
QPushButton:disabled {
    background-color: #ECE7DE;
    color: #9C9384;
    border-color: #E2DBD0;
}
QPushButton#AccentButton {
    background-color: #B85C35;
    color: #FFFFFF;
    border: none;
}
QPushButton#AccentButton:hover {
    background-color: #974923;
}
QPushButton#CyanButton {
    background-color: #3F7180;
    color: #FFFFFF;
    border: none;
}
QPushButton#CyanButton:hover {
    background-color: #2F5D6B;
}
QPushButton#DangerButton {
    background-color: #B54747;
    color: #FFFFFF;
    border: none;
}
QPushButton#DangerButton:hover {
    background-color: #943737;
}
QRadioButton, QCheckBox {
    color: #1A1A1A;
    font-size: 11px;
    spacing: 6px;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #BDB3A4;
    border-radius: 3px;
    background-color: #FAF8F4;
}
QRadioButton::indicator {
    border-radius: 7px;
}
QRadioButton::indicator:checked, QCheckBox::indicator:checked {
    background-color: #B85C35;
    border-color: #B85C35;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #ECE7DE;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #B85C35;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #FFFFFF;
    border: 1px solid #9C9384;
    width: 13px;
    margin-top: -4px;
    margin-bottom: -4px;
    border-radius: 6px;
}
QLineEdit, QSpinBox {
    background-color: #FAF8F4;
    color: #1A1A1A;
    border: 1px solid #D7D0C4;
    border-radius: 4px;
    padding: 3px 6px;
    font-family: "Consolas", monospace;
    font-size: 11px;
}
QTableWidget {
    background-color: #FFFFFF;
    color: #1A1A1A;
    border: 1px solid #D7D0C4;
    border-radius: 6px;
    gridline-color: #ECE7DE;
    font-size: 11px;
}
QTableWidget::item:selected {
    background-color: #E8D7CC;
    color: #1A1A1A;
}
QHeaderView::section {
    background-color: #E6E0D7;
    color: #1A1A1A;
    padding: 5px;
    border: none;
    font-weight: bold;
    font-size: 11px;
}
QScrollBar:vertical {
    background: #F4F1EA;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #D1C8BB;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""


class GUI(QMainWindow):
    """Modern single-window PyQt6 controller for Chess-X Stockfish bot."""

    def __init__(self, master=None):
        # Ensure a QApplication exists
        if QApplication.instance() is None:
            self._app = QApplication(sys.argv if sys.argv else ["chess-x"])
        else:
            self._app = QApplication.instance()

        super().__init__()
        self.master = self  # Backward compatibility adapter for self.master.after(...)
        self._dispatcher = ThreadDispatcher()

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
        self.stockfish_path = ""
        self.current_theme = "dark"

        # Browser session manager
        try:
            from browser_session import BrowserSessionManager
            self._browser_manager = BrowserSessionManager()
        except Exception:
            self._browser_manager = None

        self.setWindowTitle("CHESS-X · Stockfish Bot")
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)

        icon_path = os.path.join(os.path.dirname(__file__), "assets", "pawn_32x32.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # Setup exit handlers
        atexit.register(self._cleanup)
        try:
            signal.signal(signal.SIGINT, lambda *_: self.on_close_listener())
            signal.signal(signal.SIGTERM, lambda *_: self.on_close_listener())
        except Exception:
            pass

        # Build UI Structure
        self._build_properties()
        self._build_header()
        self._build_body()
        self._build_footer()
        self._setup_system_tray()
        self._setup_shortcuts()

        # Load persisted configuration and apply
        self._load_state()
        self._start_background_workers()

    # --- Backward compatibility adapter methods on master ---
    def after(self, delay_ms: int, func):
        self._dispatcher.after(delay_ms, func)

    def destroy(self):
        self.close()

    def title(self, text: str):
        self.setWindowTitle(text)

    def geometry(self, geom: str):
        try:
            parts = geom.lower().split("x")
            self.resize(int(parts[0]), int(parts[1]))
        except Exception:
            pass

    def minsize(self, w: int, h: int):
        self.setMinimumSize(w, h)

    def attributes(self, name: str, val: Any = None):
        if name == "-topmost":
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(val))
            self.show()

    def bind(self, key: str, func):
        pass  # Handled via Qt QShortcut

    # --- UI Construction ---
    def _build_properties(self):
        self.website = UIProperty("chesscom", on_change=self._update_platform_radios)
        self.browser = UIProperty("chrome", on_change=self._update_browser_radios)
        self.enable_manual_mode = UIProperty(False, on_change=self.on_manual_mode_checkbox_listener)
        self.enable_mouseless_mode = UIProperty(False)
        self.enable_non_stop_puzzles = UIProperty(0)
        self.enable_non_stop_matches = UIProperty(0)
        self.mouse_latency = UIProperty(0.0)
        self.slow_mover = UIProperty(60)
        self.skill_level = UIProperty(20)
        self.stockfish_depth = UIProperty(13)
        self.enable_topmost = UIProperty(1, on_change=self.on_topmost_check_button_listener)
        self.overlay_opacity = UIProperty(1.0, on_change=self.on_overlay_opacity_change)
        self.overlay_eval_side = UIProperty("left", on_change=self.on_overlay_side_toggle)
        self.theme = UIProperty("dark", on_change=lambda t: self.set_theme(t))

    def _build_header(self):
        header = QFrame(self)
        header.setObjectName("HeaderFrame")
        header.setFixedHeight(70)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)

        # Title branding
        title_box = QHBoxLayout()
        lbl_main = QLabel("CHESS")
        lbl_main.setObjectName("AppTitleMain")
        lbl_accent = QLabel("-X")
        lbl_accent.setObjectName("AppTitleAccent")
        lbl_sub = QLabel("  STOCKFISH BOT v2.1")
        lbl_sub.setObjectName("AppSubtitle")
        title_box.addWidget(lbl_main)
        title_box.addWidget(lbl_accent)
        title_box.addWidget(lbl_sub)
        title_box.addStretch()
        layout.addLayout(title_box)

        # Right side: Theme toggle, Calibration helper, Status badge
        right_box = QHBoxLayout()
        right_box.setSpacing(10)

        # Theme toggle button
        self.btn_theme = QPushButton("🌙 Dark")
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.clicked.connect(self.toggle_theme)
        right_box.addWidget(self.btn_theme)

        # Calibration button
        self.btn_calib = QPushButton("🎯 Calibrate")
        self.btn_calib.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_calib.clicked.connect(self.open_calibration_helper)
        right_box.addWidget(self.btn_calib)

        # Status badge pill
        self.status_pill = QFrame()
        self.status_pill.setStyleSheet("background-color: #F8E9E7; border-radius: 12px; padding: 4px 10px;")
        sp_layout = QHBoxLayout(self.status_pill)
        sp_layout.setContentsMargins(8, 2, 8, 2)
        sp_layout.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #B54747; font-size: 10px;")
        self.status_text = QLabel("INACTIVE")
        self.status_text.setStyleSheet("color: #B54747; font-weight: bold; font-size: 10px;")
        sp_layout.addWidget(self.status_dot)
        sp_layout.addWidget(self.status_text)
        right_box.addWidget(self.status_pill)

        layout.addLayout(right_box)
        self._header = header

    def _build_body(self):
        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._header)

        # Splitter between left controls and right telemetry/moves
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)

        # Left Scroll Area
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_content = QWidget()
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(18, 14, 14, 14)
        left_layout.setSpacing(12)

        self._build_controls(left_layout)
        left_scroll.setWidget(left_content)
        splitter.addWidget(left_scroll)

        # Right Panel
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 18, 14)
        right_layout.setSpacing(12)
        self._build_moves(right_layout)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        main_layout.addWidget(splitter, 1)

    def _card(self, parent_layout, title: str) -> QVBoxLayout:
        frame = QFrame()
        frame.setProperty("class", "CardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        lbl = QLabel(title.upper())
        lbl.setProperty("class", "CardTitle")
        layout.addWidget(lbl)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #3F3F46;")
        layout.addWidget(line)

        parent_layout.addWidget(frame)
        return layout

    def _build_controls(self, parent_layout):
        # 1. Platform & Browser Card
        site_card = self._card(parent_layout, "Platform & Browser")
        site_row = QHBoxLayout()
        self.site_group = QButtonGroup(self)
        self.chesscom_radio = QRadioButton("Chess.com")
        self.lichess_radio = QRadioButton("Lichess.org")
        self.site_group.addButton(self.chesscom_radio)
        self.site_group.addButton(self.lichess_radio)
        self.chesscom_radio.setChecked(True)
        self.chesscom_radio.toggled.connect(lambda c: self.website.set("chesscom" if c else "lichess"))
        site_row.addWidget(self.chesscom_radio)
        site_row.addWidget(self.lichess_radio)
        site_card.addLayout(site_row)

        brow_row = QHBoxLayout()
        brow_row.addWidget(QLabel("Browser:"))
        self.brow_group = QButtonGroup(self)
        self.browser_chrome_radio = QRadioButton("Chrome")
        self.browser_firefox_radio = QRadioButton("Firefox")
        self.browser_edge_radio = QRadioButton("Edge")
        self.brow_group.addButton(self.browser_chrome_radio)
        self.brow_group.addButton(self.browser_firefox_radio)
        self.brow_group.addButton(self.browser_edge_radio)
        self.browser_chrome_radio.setChecked(True)
        self.browser_chrome_radio.toggled.connect(lambda c: c and self.browser.set("chrome"))
        self.browser_firefox_radio.toggled.connect(lambda c: c and self.browser.set("firefox"))
        self.browser_edge_radio.toggled.connect(lambda c: c and self.browser.set("edge"))
        brow_row.addWidget(self.browser_chrome_radio)
        brow_row.addWidget(self.browser_firefox_radio)
        brow_row.addWidget(self.browser_edge_radio)
        site_card.addLayout(brow_row)

        # 2. Main Controls Card
        ctrl_card = self._card(parent_layout, "Controls")
        self.open_browser_button = QPushButton("↗   OPEN BROWSER")
        self.open_browser_button.setObjectName("CyanButton")
        self.open_browser_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_browser_button.clicked.connect(self.on_open_browser_button_listener)
        ctrl_card.addWidget(self.open_browser_button)

        self.start_button = QPushButton("▶   START ENGINE")
        self.start_button.setObjectName("AccentButton")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.clicked.connect(self.on_start_button_listener)
        ctrl_card.addWidget(self.start_button)

        # 3. Modes Card
        modes_card = self._card(parent_layout, "Modes")
        self.manual_mode_checkbox = QCheckBox("Manual mode (press 3)")
        self.manual_mode_checkbox.toggled.connect(lambda c: self.enable_manual_mode.set(c))
        modes_card.addWidget(self.manual_mode_checkbox)

        self.manual_mode_frame = QFrame()
        self.manual_mode_frame.setStyleSheet("background-color: #27272A; border-radius: 4px; padding: 4px;")
        mf_layout = QHBoxLayout(self.manual_mode_frame)
        mf_layout.setContentsMargins(6, 4, 6, 4)
        self.manual_mode_label = QLabel("Press 3 to make a move")
        self.manual_mode_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        mf_layout.addWidget(self.manual_mode_label)
        modes_card.addWidget(self.manual_mode_frame)
        self.manual_mode_frame.hide()

        self.mouseless_mode_checkbox = QCheckBox("Mouseless mode (Lichess only)")
        self.mouseless_mode_checkbox.toggled.connect(lambda c: self.enable_mouseless_mode.set(c))
        modes_card.addWidget(self.mouseless_mode_checkbox)

        self.puzzles_checkbox = QCheckBox("Non-stop puzzles")
        self.puzzles_checkbox.toggled.connect(lambda c: self.enable_non_stop_puzzles.set(1 if c else 0))
        modes_card.addWidget(self.puzzles_checkbox)

        self.matches_checkbox = QCheckBox("Non-stop online matches")
        self.matches_checkbox.toggled.connect(lambda c: self.enable_non_stop_matches.set(1 if c else 0))
        modes_card.addWidget(self.matches_checkbox)

        # Mouse latency slider
        lat_box = QVBoxLayout()
        lat_header = QHBoxLayout()
        lat_header.addWidget(QLabel("Mouse latency:"))
        self.lbl_mouse_latency = QLabel("0.0s")
        self.lbl_mouse_latency.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        lat_header.addWidget(self.lbl_mouse_latency)
        lat_header.addStretch()
        lat_box.addLayout(lat_header)

        self.mouse_latency_slider = QSlider(Qt.Orientation.Horizontal)
        self.mouse_latency_slider.setRange(0, 150)  # 0.0 to 15.0 step 0.1
        self.mouse_latency_slider.setValue(0)
        self.mouse_latency_slider.valueChanged.connect(self._on_mouse_latency_slider)
        lat_box.addWidget(self.mouse_latency_slider)
        modes_card.addLayout(lat_box)

        # 4. Stockfish Settings Card
        sf_card = self._card(parent_layout, "Stockfish Engine")
        row_sm = QHBoxLayout()
        row_sm.addWidget(QLabel("Slow mover (ms):"))
        self.slow_mover_spin = QSpinBox()
        self.slow_mover_spin.setRange(10, 1000)
        self.slow_mover_spin.setValue(60)
        self.slow_mover_spin.valueChanged.connect(lambda v: self.slow_mover.set(v))
        row_sm.addWidget(self.slow_mover_spin)
        sf_card.addLayout(row_sm)

        # Skill level slider
        row_sk = QVBoxLayout()
        sk_head = QHBoxLayout()
        sk_head.addWidget(QLabel("Skill level:"))
        self.lbl_skill = QLabel("20")
        self.lbl_skill.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        sk_head.addWidget(self.lbl_skill)
        sk_head.addStretch()
        row_sk.addLayout(sk_head)
        self.skill_slider = QSlider(Qt.Orientation.Horizontal)
        self.skill_slider.setRange(0, 20)
        self.skill_slider.setValue(20)
        self.skill_slider.valueChanged.connect(lambda v: (self.skill_level.set(v), self.lbl_skill.setText(str(v))))
        row_sk.addWidget(self.skill_slider)
        sf_card.addLayout(row_sk)

        # Depth slider
        row_dp = QVBoxLayout()
        dp_head = QHBoxLayout()
        dp_head.addWidget(QLabel("Depth:"))
        self.lbl_depth = QLabel("13")
        self.lbl_depth.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        dp_head.addWidget(self.lbl_depth)
        dp_head.addStretch()
        row_dp.addLayout(dp_head)
        self.depth_slider = QSlider(Qt.Orientation.Horizontal)
        self.depth_slider.setRange(1, 20)
        self.depth_slider.setValue(13)
        self.depth_slider.valueChanged.connect(lambda v: (self.stockfish_depth.set(v), self.lbl_depth.setText(str(v))))
        row_dp.addWidget(self.depth_slider)
        sf_card.addLayout(row_dp)

        # 5. Engine Binary Card
        bin_card = self._card(parent_layout, "Engine Binary")
        self.select_stockfish_button = QPushButton("SELECT STOCKFISH BINARY")
        self.select_stockfish_button.clicked.connect(self.on_select_stockfish_button_listener)
        bin_card.addWidget(self.select_stockfish_button)

        self.stockfish_path_text = QLabel("No Stockfish selected")
        self.stockfish_path_text.setWordWrap(True)
        self.stockfish_path_text.setStyleSheet("color: #DC2626; font-size: 10px; font-family: Consolas;")
        bin_card.addWidget(self.stockfish_path_text)

        self.topmost_check = QCheckBox("Keep window on top")
        self.topmost_check.setChecked(True)
        self.topmost_check.toggled.connect(lambda c: self.enable_topmost.set(1 if c else 0))
        bin_card.addWidget(self.topmost_check)

        # 6. Overlay & Visuals Card
        ov_card = self._card(parent_layout, "Overlay & Visuals")
        row_op = QVBoxLayout()
        op_head = QHBoxLayout()
        op_head.addWidget(QLabel("Overlay Opacity:"))
        self.lbl_opacity = QLabel("100%")
        self.lbl_opacity.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        op_head.addWidget(self.lbl_opacity)
        op_head.addStretch()
        row_op.addLayout(op_head)
        self.overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity_slider.setRange(10, 100)
        self.overlay_opacity_slider.setValue(100)
        self.overlay_opacity_slider.valueChanged.connect(self._on_opacity_slider)
        row_op.addWidget(self.overlay_opacity_slider)
        ov_card.addLayout(row_op)

        side_row = QHBoxLayout()
        side_row.addWidget(QLabel("Eval bar side:"))
        self.side_group = QButtonGroup(self)
        self.side_left_radio = QRadioButton("Left")
        self.side_right_radio = QRadioButton("Right")
        self.side_group.addButton(self.side_left_radio)
        self.side_group.addButton(self.side_right_radio)
        self.side_left_radio.setChecked(True)
        self.side_left_radio.toggled.connect(lambda c: self.overlay_eval_side.set("left" if c else "right"))
        side_row.addWidget(self.side_left_radio)
        side_row.addWidget(self.side_right_radio)
        ov_card.addLayout(side_row)

        self.toggle_overlay_button = QPushButton("TOGGLE OVERLAY (O)")
        self.toggle_overlay_button.clicked.connect(self.toggle_overlay)
        ov_card.addWidget(self.toggle_overlay_button)

        self.btn_calib_helper = QPushButton("CALIBRATION HELPER (DPI & MULTI-MONITOR)")
        self.btn_calib_helper.clicked.connect(self.open_calibration_helper)
        ov_card.addWidget(self.btn_calib_helper)

    def _build_moves(self, parent_layout):
        # 1. Move History Card
        moves_card = self._card(parent_layout, "Move History")
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("<b>MOVES</b>"))
        self._move_count = QLabel("0 moves")
        self._move_count.setStyleSheet("background-color: #27272A; border-radius: 4px; padding: 2px 6px; font-size: 10px;")
        top_row.addWidget(self._move_count)
        top_row.addStretch()
        moves_card.addLayout(top_row)

        self.tree = QTableWidget(0, 3)
        self.tree.setHorizontalHeaderLabels(["#", "White", "Black"])
        self.tree.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_table_context_menu)
        moves_card.addWidget(self.tree)

        btn_row = QHBoxLayout()
        self.export_pgn_button = QPushButton("EXPORT PGN")
        self.export_pgn_button.clicked.connect(self.on_export_pgn_button_listener)
        self.copy_pgn_button = QPushButton("COPY PGN")
        self.copy_pgn_button.clicked.connect(self.on_copy_pgn_button_listener)
        self.copy_fen_button = QPushButton("COPY FEN")
        self.copy_fen_button.clicked.connect(self.on_copy_fen_button_listener)
        btn_row.addWidget(self.export_pgn_button)
        btn_row.addWidget(self.copy_pgn_button)
        btn_row.addWidget(self.copy_fen_button)
        moves_card.addLayout(btn_row)

        # 2. Engine Evaluation Card
        eval_card = self._card(parent_layout, "Engine Evaluation")
        eval_head = QHBoxLayout()
        self.eval_text = QLabel("—")
        self.eval_text.setFont(QFont("Consolas", 20, QFont.Weight.Bold))
        eval_head.addWidget(self.eval_text)
        eval_head.addStretch()
        eval_card.addLayout(eval_head)

        self.eval_bar_widget = EvalBarWidget(self)
        eval_card.addWidget(self.eval_bar_widget)

        stats_grid = QGridLayout()
        self.wdl_text = QLabel("—")
        self.wdl_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.material_text = QLabel("—")
        self.material_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.white_acc_text = QLabel("—")
        self.white_acc_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.black_acc_text = QLabel("—")
        self.black_acc_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))

        stats_grid.addWidget(QLabel("<font color='#888888'>WDL</font>"), 0, 0)
        stats_grid.addWidget(self.wdl_text, 1, 0)
        stats_grid.addWidget(QLabel("<font color='#888888'>MATERIAL</font>"), 0, 1)
        stats_grid.addWidget(self.material_text, 1, 1)
        stats_grid.addWidget(QLabel("<font color='#888888'>BOT ACC</font>"), 0, 2)
        stats_grid.addWidget(self.white_acc_text, 1, 2)
        stats_grid.addWidget(QLabel("<font color='#888888'>OPP ACC</font>"), 0, 3)
        stats_grid.addWidget(self.black_acc_text, 1, 3)
        eval_card.addLayout(stats_grid)

        self.pv_text = QLabel("")
        self.pv_text.setWordWrap(True)
        self.pv_text.setStyleSheet("color: #A1A1AA; font-family: Consolas; font-size: 10px;")
        eval_card.addWidget(self.pv_text)

        # 3. Status & Telemetry Card
        tel_card = self._card(parent_layout, "Status & Telemetry")
        tel_grid = QGridLayout()
        self.nps_text = QLabel("—")
        self.nps_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.depth_reached_text = QLabel("—")
        self.depth_reached_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.move_time_text = QLabel("—")
        self.move_time_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.conn_health_text = QLabel("Connected")
        self.conn_health_text.setFont(QFont("Consolas", 10, QFont.Weight.Bold))

        tel_grid.addWidget(QLabel("<font color='#888888'>NPS</font>"), 0, 0)
        tel_grid.addWidget(self.nps_text, 1, 0)
        tel_grid.addWidget(QLabel("<font color='#888888'>DEPTH</font>"), 0, 1)
        tel_grid.addWidget(self.depth_reached_text, 1, 1)
        tel_grid.addWidget(QLabel("<font color='#888888'>TIME/MOVE</font>"), 0, 2)
        tel_grid.addWidget(self.move_time_text, 1, 2)
        tel_grid.addWidget(QLabel("<font color='#888888'>HEALTH</font>"), 0, 3)
        tel_grid.addWidget(self.conn_health_text, 1, 3)
        tel_card.addLayout(tel_grid)

        err_layout = QVBoxLayout()
        err_layout.addWidget(QLabel("<font color='#888888'>LAST ERROR</font>"))
        self.last_error_text = QLabel("None (nominal)")
        self.last_error_text.setStyleSheet("color: #71717A; font-size: 11px;")
        err_layout.addWidget(self.last_error_text)
        tel_card.addLayout(err_layout)

    def _build_footer(self):
        footer = QFrame(self)
        footer.setFixedHeight(50)
        footer.setStyleSheet("background-color: #18181B; border-top: 1px solid #27272A;")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(18, 6, 18, 6)

        lbl_warn = QLabel("Emergency stop: kill bot and browser instantly")
        lbl_warn.setStyleSheet("color: #A1A1AA; font-size: 11px;")
        layout.addWidget(lbl_warn)
        layout.addStretch()

        self.emergency_kill_button = QPushButton("☠  KILL BOT + BROWSER (Esc / F12)")
        self.emergency_kill_button.setObjectName("DangerButton")
        self.emergency_kill_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.emergency_kill_button.clicked.connect(self.emergency_kill)
        layout.addWidget(self.emergency_kill_button)

        self.centralWidget().layout().addWidget(footer)

    def _setup_system_tray(self):
        """Setup system tray icon with context menu and notification support."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        icon_path = os.path.join(os.path.dirname(__file__), "assets", "pawn_32x32.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else self.windowIcon()
        self.tray_icon = QSystemTrayIcon(icon, self)

        tray_menu = QMenu(self)
        title_action = tray_menu.addAction("Chess-X Stockfish Bot")
        title_action.setEnabled(False)
        tray_menu.addSeparator()

        act_show = tray_menu.addAction("Show / Hide Window")
        act_show.triggered.connect(self._toggle_window_visibility)

        self.act_start_stop = tray_menu.addAction("Start Engine")
        self.act_start_stop.triggered.connect(lambda: self.on_start_button_listener() if not self.running else self.on_stop_button_listener())

        act_overlay = tray_menu.addAction("Toggle Overlay (O)")
        act_overlay.triggered.connect(self.toggle_overlay)

        act_theme = tray_menu.addAction("Toggle Light/Dark Theme")
        act_theme.triggered.connect(self.toggle_theme)

        act_calib = tray_menu.addAction("Calibration Helper")
        act_calib.triggered.connect(self.open_calibration_helper)

        tray_menu.addSeparator()
        act_kill = tray_menu.addAction("Emergency Kill")
        act_kill.triggered.connect(self.emergency_kill)

        act_exit = tray_menu.addAction("Exit")
        act_exit.triggered.connect(self.on_close_listener)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_window_visibility()

    def _toggle_window_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def notify_tray(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information):
        """Send a desktop notification balloon via system tray."""
        if self.tray_icon and self.tray_icon.isVisible():
            try:
                self.tray_icon.showMessage(title, message, icon, 3000)
            except Exception:
                pass

    def _setup_shortcuts(self):
        """Global window keyboard shortcuts."""
        for key in ("Escape", "F12", "Ctrl+Q"):
            QShortcut(QKeySequence(key), self).activated.connect(self.emergency_kill)
        QShortcut(QKeySequence("O"), self).activated.connect(self.toggle_overlay)

    # --- Theme Management ---
    def set_theme(self, theme_name: str):
        self.current_theme = "light" if str(theme_name).lower() == "light" else "dark"
        if self.current_theme == "light":
            self.setStyleSheet(LIGHT_QSS)
            self.btn_theme.setText("Light")
            self._set_status_pill_colors()
        else:
            self.setStyleSheet(DARK_QSS)
            self.btn_theme.setText("Dark")
            self._set_status_pill_colors()

    def toggle_theme(self):
        new_theme = "light" if self.current_theme == "dark" else "dark"
        self.theme.set(new_theme)
        self.set_theme(new_theme)
        self._save_config()

    def _set_status_pill_colors(self):
        if not hasattr(self, "status_text"):
            return
        status = self.status_text.text()
        if status in ("RUNNING", "ACTIVE"):
            color = SUCCESS
            bg = SUCCESS_BG
        elif status == "INACTIVE":
            color = DANGER
            bg = DANGER_BG
        else:
            color = WARNING
            bg = "#FEF3C7"
        self.status_pill.setStyleSheet(f"background-color: {bg}; border-radius: 12px; padding: 4px 10px;")
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 10px;")
        self.status_text.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 10px;")

    def _set_status(self, text: str, color: str, bg: str):
        try:
            self.status_text.setText(text.upper())
            self.status_dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            self.status_text.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 10px;")
            self.status_pill.setStyleSheet(f"background-color: {bg}; border-radius: 12px; padding: 4px 10px;")
            if hasattr(self, "act_start_stop") and self.act_start_stop:
                self.act_start_stop.setText("Stop Engine" if self.running else "Start Engine")
        except Exception:
            pass

    # --- Calibration helper dialog ---
    def open_calibration_helper(self):
        try:
            from calibration_helper import CalibrationHelperDialog
            dialog = CalibrationHelperDialog(self, gui_controller=self)
            dialog.exec()
        except Exception as e:
            logger.error("Failed to open calibration helper: %s", e)
            QMessageBox.warning(self, "Calibration Error", f"Unable to open calibration helper: {e}")

    # --- Slider Handlers ---
    def _on_mouse_latency_slider(self, val: int):
        f = val / 10.0
        self.mouse_latency.set(f)
        self.lbl_mouse_latency.setText(f"{f:.1f}s")

    def _on_opacity_slider(self, val: int):
        f = val / 100.0
        self.overlay_opacity.set(f)
        self.lbl_opacity.setText(f"{val}%")

    def _update_platform_radios(self, *_):
        site = self.website.get()
        if site == "chesscom":
            self.chesscom_radio.setChecked(True)
        else:
            self.lichess_radio.setChecked(True)

    def _update_browser_radios(self, *_):
        b = self.browser.get()
        if b == "firefox":
            self.browser_firefox_radio.setChecked(True)
        elif b == "edge":
            self.browser_edge_radio.setChecked(True)
        else:
            self.browser_chrome_radio.setChecked(True)

    # --- State and Config Persistence ---
    def _config_path(self):
        return "src/config.json"

    def _load_config(self):
        try:
            from config_store import load_config as _load
            try:
                explicit = self._config_path()
            except Exception:
                explicit = None
            cfg = _load(explicit) if explicit else _load()
            d = cfg.to_dict()
            sp = d.get("stockfish_path")
            if sp and not self._is_valid_stockfish(sp, quick_check=True):
                logger.warning("Saved stockfish path looks invalid: %s", sp)
            return d
        except Exception as e:
            logger.debug("_load_config failed: %s", e)
            return {
                "stockfish_path": "",
                "website": "chesscom",
                "browser": "chrome",
                "enable_manual_mode": False,
                "enable_mouseless_mode": False,
                "enable_non_stop_puzzles": 0,
                "enable_non_stop_matches": 0,
                "mouse_latency": 0.0,
                "slow_mover": 60,
                "skill_level": 20,
                "stockfish_depth": 13,
                "enable_topmost": 1,
                "theme": "dark",
                "calibration_offset_x": 0.0,
                "calibration_offset_y": 0.0,
                "version": 3,
            }

    def _save_config(self):
        try:
            from config_store import Config as _Cfg
            from config_store import save_config as _save
            kw: dict = {}
            kw["stockfish_path"] = getattr(self, "stockfish_path", "") or ""
            kw["website"] = self.website.get() if hasattr(self, "website") else "chesscom"
            kw["browser"] = self.browser.get() if hasattr(self, "browser") else "chrome"
            kw["enable_manual_mode"] = bool(self.enable_manual_mode.get()) if hasattr(self, "enable_manual_mode") else False
            kw["enable_mouseless_mode"] = bool(self.enable_mouseless_mode.get()) if hasattr(self, "enable_mouseless_mode") else False
            kw["enable_non_stop_puzzles"] = int(self.enable_non_stop_puzzles.get()) if hasattr(self, "enable_non_stop_puzzles") else 0
            kw["enable_non_stop_matches"] = int(self.enable_non_stop_matches.get()) if hasattr(self, "enable_non_stop_matches") else 0
            kw["mouse_latency"] = float(self.mouse_latency.get()) if hasattr(self, "mouse_latency") else 0.0
            kw["slow_mover"] = int(self.slow_mover.get()) if hasattr(self, "slow_mover") else 60
            kw["skill_level"] = int(self.skill_level.get()) if hasattr(self, "skill_level") else 20
            kw["stockfish_depth"] = int(self.stockfish_depth.get()) if hasattr(self, "stockfish_depth") else 13
            kw["enable_topmost"] = int(self.enable_topmost.get()) if hasattr(self, "enable_topmost") else 1
            kw["overlay_opacity"] = float(self.overlay_opacity.get()) if hasattr(self, "overlay_opacity") else 1.0
            kw["overlay_eval_side"] = str(self.overlay_eval_side.get()) if hasattr(self, "overlay_eval_side") else "left"
            kw["theme"] = str(self.theme.get()) if hasattr(self, "theme") else getattr(self, "current_theme", "dark")
            cfg = _Cfg.from_dict(kw)
            _save(cfg, self._config_path())
        except Exception as e:
            logger.debug("save config error: %s", e)

    def _apply_config_values(self):
        cfg = getattr(self, "_config", {}) or {}
        if "website" in cfg:
            self.website.set(cfg["website"])
        if "browser" in cfg:
            self.browser.set(cfg["browser"])
            os.environ["CHESSX_BROWSER"] = cfg["browser"]
        if "enable_manual_mode" in cfg:
            self.enable_manual_mode.set(bool(cfg["enable_manual_mode"]))
            self.manual_mode_checkbox.setChecked(bool(cfg["enable_manual_mode"]))
        if "enable_mouseless_mode" in cfg:
            self.enable_mouseless_mode.set(bool(cfg["enable_mouseless_mode"]))
            self.mouseless_mode_checkbox.setChecked(bool(cfg["enable_mouseless_mode"]))
        if "enable_non_stop_puzzles" in cfg:
            self.enable_non_stop_puzzles.set(int(cfg["enable_non_stop_puzzles"]))
            self.puzzles_checkbox.setChecked(bool(cfg["enable_non_stop_puzzles"]))
        if "enable_non_stop_matches" in cfg:
            self.enable_non_stop_matches.set(int(cfg["enable_non_stop_matches"]))
            self.matches_checkbox.setChecked(bool(cfg["enable_non_stop_matches"]))
        if "mouse_latency" in cfg:
            lat = float(cfg["mouse_latency"])
            self.mouse_latency.set(lat)
            self.mouse_latency_slider.setValue(int(lat * 10))
            self.lbl_mouse_latency.setText(f"{lat:.1f}s")
        if "slow_mover" in cfg:
            sm = int(cfg["slow_mover"])
            self.slow_mover.set(sm)
            self.slow_mover_spin.setValue(sm)
        if "skill_level" in cfg:
            sk = int(cfg["skill_level"])
            self.skill_level.set(sk)
            self.skill_slider.setValue(sk)
            self.lbl_skill.setText(str(sk))
        if "stockfish_depth" in cfg:
            dp = int(cfg["stockfish_depth"])
            self.stockfish_depth.set(dp)
            self.depth_slider.setValue(dp)
            self.lbl_depth.setText(str(dp))
        if "enable_topmost" in cfg:
            tm = int(cfg["enable_topmost"])
            self.enable_topmost.set(tm)
            self.topmost_check.setChecked(bool(tm))
        if "overlay_opacity" in cfg:
            op = float(cfg["overlay_opacity"])
            self.overlay_opacity.set(op)
            self.overlay_opacity_slider.setValue(int(op * 100))
            self.lbl_opacity.setText(f"{int(op * 100)}%")
        if "overlay_eval_side" in cfg:
            side = str(cfg["overlay_eval_side"]).lower()
            self.overlay_eval_side.set(side)
            if side == "right":
                self.side_right_radio.setChecked(True)
            else:
                self.side_left_radio.setChecked(True)
        if "theme" in cfg:
            self.current_theme = cfg["theme"]
            self.set_theme(self.current_theme)

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

    def _setup_config_autosave(self):
        for prop in (
            self.website, self.browser, self.enable_manual_mode, self.enable_mouseless_mode,
            self.enable_non_stop_puzzles, self.enable_non_stop_matches, self.mouse_latency,
            self.slow_mover, self.skill_level, self.stockfish_depth, self.enable_topmost,
            self.overlay_opacity, self.overlay_eval_side,
        ):
            prop.trace_add("write", self._schedule_save)

    def _schedule_save(self, *a):
        self._save_config()

    def _setup_validation_traces(self):
        pass

    def _validate_inputs(self, *a):
        can_start = bool(self.opened_browser and self.stockfish_path and os.path.exists(self.stockfish_path))
        self.start_button.setEnabled(can_start)

    def _refresh_stockfish_label(self):
        path = self.stockfish_path or "No Stockfish selected"
        valid = bool(self.stockfish_path and os.path.exists(self.stockfish_path))
        self.stockfish_path_text.setText(path)
        self.stockfish_path_text.setStyleSheet(f"color: {'#2E7D5B' if valid else '#DC2626'}; font-size: 10px; font-family: Consolas;")
        self._validate_inputs()

    # --- Background Workers ---
    def _start_background_workers(self):
        threading.Thread(target=self.process_checker_thread, daemon=True).start()
        threading.Thread(target=self.browser_checker_thread, daemon=True).start()
        threading.Thread(target=self.process_communicator_thread, daemon=True).start()
        try:
            from hotkeys import HotkeyManager
            self._hotkey_manager = HotkeyManager(self)
            self._hotkey_manager.start()
        except Exception as e:
            logger.debug("HotkeyManager start fallback: %s", e)
            threading.Thread(target=self.keypress_listener_thread, daemon=True).start()

    def process_checker_thread(self):
        while not self.exit:
            if self.running and self.stockfish_bot_process is not None and not self.stockfish_bot_process.is_alive():
                self.after(0, self.on_stop_button_listener)
                if self.restart_after_stopping:
                    self.restart_after_stopping = False
                    self.after(100, self.on_start_button_listener)
            time.sleep(0.2)

    def _on_browser_closed_ui(self):
        self.open_browser_button.setText("↗   OPEN BROWSER")
        self.open_browser_button.setEnabled(True)
        self._validate_inputs()

    def browser_checker_thread(self):
        while not self.exit:
            try:
                bm = getattr(self, "_browser_manager", None)
                if bm is not None and self.opened_browser:
                    if bm.check_driver_log_for_close() or not bm.is_alive():
                        self.opened_browser = False
                        self.after(0, self._on_browser_closed_ui)
                        self.after(0, self.on_stop_button_listener)
                        self.chrome = bm.chrome
                        continue
                    self.chrome = bm.chrome
                    self.chrome_url = bm.chrome_url
                    self.chrome_session_id = bm.chrome_session_id
                    self.opened_browser = bm.opened_browser
                    self.opening_browser = bm.opening_browser
                elif self.opened_browser and self.chrome is not None:
                    try:
                        _ = self.chrome.current_url
                    except WebDriverException:
                        self.opened_browser = False
                        self.after(0, self._on_browser_closed_ui)
                        self.after(0, self.on_stop_button_listener)
                        self.chrome = None
            except Exception as e:
                logger.debug("browser_checker error: %s", e)
            time.sleep(0.5)

    def process_communicator_thread(self):
        while not self.exit:
            try:
                if self.stockfish_bot_pipe is not None and self.stockfish_bot_pipe.poll():
                    data = self.stockfish_bot_pipe.recv()
                    self.after(0, lambda d=data: self._handle_pipe_message(d))
            except (BrokenPipeError, OSError, EOFError):
                self.stockfish_bot_pipe = None
            except Exception as e:
                logger.debug("process_communicator error: %s", e)
            time.sleep(0.05)

    def keypress_listener_thread(self):
        """Fallback polling thread for keys if hotkey manager not active."""
        while not self.exit:
            time.sleep(0.1)
            if _kb is None or not self.opened_browser:
                continue
            try:
                if _kb.is_pressed("1"):
                    self.after(0, self.on_start_button_listener)
                    time.sleep(0.5)
                elif _kb.is_pressed("2"):
                    self.after(0, self.on_stop_button_listener)
                    time.sleep(0.5)
                elif _kb.is_pressed("3") or _kb.is_pressed("o"):
                    self.after(0, self.toggle_overlay)
                    time.sleep(0.5)
            except Exception:
                pass

    def _handle_pipe_message(self, data):
        try:
            if proto.is_typed_pipe_msg(data):
                if isinstance(data, proto.MsgStart):
                    self.clear_tree()
                    self.match_moves = []
                    self._set_status("RUNNING", SUCCESS, SUCCESS_BG)
                    self.start_button.setText("■   STOP ENGINE")
                    self.start_button.setEnabled(True)
                    self.notify_tray("Chess-X", "Engine started and listening")
                    return
                elif isinstance(data, proto.MsgRestart):
                    self.restart_after_stopping = True
                    try:
                        self.stockfish_bot_pipe.send(proto.MsgDelete())
                    except Exception:
                        pass
                    return
                elif isinstance(data, proto.MsgSingleMove):
                    self.match_moves.append(data.san)
                    self.insert_move(data.san, eval_str=data.eval_str, clock=data.clock, is_blunder=data.is_blunder)
                    self._move_count.setText(f"{len(self.match_moves)} moves")
                    return
                elif isinstance(data, proto.MsgMultiMove):
                    self.match_moves += data.sans
                    self.set_moves(data.sans, evals=data.evals, clocks=data.clocks, blunders=data.blunders)
                    self._move_count.setText(f"{len(self.match_moves)} moves")
                    return
                elif isinstance(data, proto.MsgEval):
                    self.update_evaluation_display(data.eval_str, data.wdl_str, data.material_str, data.bot_acc, data.opponent_acc)
                    return
                elif isinstance(data, proto.MsgPV):
                    self.update_pv_display(data.pv_text, data.threat_san)
                    return
                elif isinstance(data, proto.MsgTelemetry):
                    self.update_telemetry(data.nps, data.depth, data.time_per_move_ms, data.connection_health, data.last_error)
                    return
                elif isinstance(data, proto.MsgError):
                    err_msg = f"{data.code}: {data.detail}" if data.detail else data.code
                    self.last_error_text.setText(err_msg)
                    self.conn_health_text.setText("Warning")
                    data = data.code + (f"|{data.detail}" if data.detail else "")

            # Legacy string handling
            if isinstance(data, str):
                if data == "START":
                    self.clear_tree()
                    self.match_moves = []
                    self._set_status("RUNNING", SUCCESS, SUCCESS_BG)
                    self.start_button.setText("■   STOP ENGINE")
                    self.start_button.setEnabled(True)
                elif data.startswith("RESTART"):
                    self.restart_after_stopping = True
                    try: self.stockfish_bot_pipe.send(proto.MsgDelete())
                    except Exception: pass
                elif data.startswith("S_MOVE"):
                    m = data[6:]
                    self.match_moves.append(m)
                    self.insert_move(m)
                    self._move_count.setText(f"{len(self.match_moves)} moves")
                elif data.startswith("M_MOVE"):
                    moves = [m for m in data[6:].split(",") if m]
                    self.match_moves += moves
                    self.set_moves(moves)
                    self._move_count.setText(f"{len(self.match_moves)} moves")
                elif data.startswith("EVAL|"):
                    parts = data.split("|")
                    if len(parts) >= 6:
                        self.update_evaluation_display(parts[1], parts[2], parts[3], parts[4], parts[5])
                elif data.startswith("PV|"):
                    parts = data.split("|")
                    self.update_pv_display(parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")
                elif data.startswith("TELEMETRY|"):
                    parts = data.split("|")
                    if len(parts) >= 6:
                        try:
                            self.update_telemetry(int(parts[1]), int(parts[2]), float(parts[3]), parts[4], parts[5])
                        except Exception:
                            pass
                elif data.startswith("ERR_"):
                    self.last_error_text.setText(data)
                    QMessageBox.warning(self, "Chess-X Notice", f"Notice from engine/bot:\n{data}")
        except Exception as e:
            logger.debug("_handle_pipe_message error: %s", e)

    # --- Browser and Engine Lifecycle ---
    def on_open_browser_button_listener(self):
        if self.opening_browser or self.opened_browser:
            return
        self.opening_browser = True
        self.open_browser_button.setText("◷  OPENING...")
        self.open_browser_button.setEnabled(False)
        threading.Thread(target=self._open_browser_worker, daemon=True).start()

    def _open_browser_worker(self):
        bm = getattr(self, "_browser_manager", None)
        if bm is not None:
            website = self.website.get()
            browser = self.browser.get()
            os.environ["CHESSX_BROWSER"] = browser
            ok = bm.open(website, browser=browser)
            if not ok:
                self.opening_browser = False
                self.after(0, lambda: (
                    self.open_browser_button.setText("↗   OPEN BROWSER"),
                    self.open_browser_button.setEnabled(True),
                    QMessageBox.critical(self, "Browser Error", f"Failed to start {browser}. Ensure it is installed.")
                ))
                return
            self.chrome = bm.chrome
            self.chrome_url = bm.chrome_url
            self.chrome_session_id = bm.chrome_session_id
            self.opening_browser = bm.opening_browser
            self.opened_browser = bm.opened_browser
            self.after(0, lambda: (
                self.open_browser_button.setText("✓  BROWSER OPEN"),
                self.open_browser_button.setEnabled(False),
                self._validate_inputs(),
            ))
            logger.info("Browser opened successfully")

    def on_start_button_listener(self):
        if self.running:
            self.on_stop_button_listener()
            return
        if not self.stockfish_path or not os.path.exists(self.stockfish_path):
            QMessageBox.warning(self, "Stockfish Error", "Please select a valid Stockfish engine binary.")
            return

        parent_conn, child_conn = multiprocess.Pipe()
        self.stockfish_bot_pipe = parent_conn
        st_ov_queue = multiprocess.Queue()
        self._overlay_queue = st_ov_queue

        self.stockfish_bot_process = StockfishBot(
            self.chrome_url,
            self.chrome_session_id,
            self.website.get(),
            child_conn,
            st_ov_queue,
            self.stockfish_path,
            bool(self.enable_manual_mode.get()),
            bool(self.enable_mouseless_mode.get()),
            int(self.enable_non_stop_puzzles.get()),
            int(self.enable_non_stop_matches.get()),
            float(self.mouse_latency.get()),
            int(self.slow_mover.get()),
            int(self.skill_level.get()),
            int(self.stockfish_depth.get()),
            browser=self.browser.get(),
            engine_options=self._engine_options(),
        )
        self.stockfish_bot_process.start()

        from overlay import run as overlay_run
        self.overlay_screen_process = multiprocess.Process(target=overlay_run, args=(st_ov_queue,))
        self.overlay_screen_process.start()

        self.running = True
        self.start_button.setText("■   STOP ENGINE")
        self._set_status("STARTING", WARNING, "#FEF3C7")

    def _engine_options(self) -> dict:
        cfg = getattr(self, "_config", {}) or {}
        try:
            from config_store import validate as _validate
            validated = _validate(dict(cfg))
        except Exception:
            validated = dict(cfg)
        return {
            "opening_book_path": validated.get("opening_book_path", ""),
            "book_max_plies": validated.get("book_max_plies", 30),
            "syzygy_path": validated.get("syzygy_path", ""),
            "tablebase_api": validated.get("tablebase_api", True),
            "show_pv_lines": validated.get("show_pv_lines", False),
            "pv_lines_count": validated.get("pv_lines_count", 3),
            "auto_save_pgn": validated.get("auto_save_pgn", False),
            "pgn_games_dir": validated.get("pgn_games_dir", "games"),
        }

    def on_stop_button_listener(self):
        if self.stockfish_bot_process is not None:
            try:
                if self.overlay_screen_process is not None:
                    if self.overlay_screen_process.is_alive():
                        self.overlay_screen_process.terminate()
                        self.overlay_screen_process.join(timeout=1.5)
                        if self.overlay_screen_process.is_alive():
                            self.overlay_screen_process.kill()
                    self.overlay_screen_process = None
                if self.stockfish_bot_process.is_alive():
                    self.stockfish_bot_process.terminate()
                    self.stockfish_bot_process.join(timeout=2)
                    if self.stockfish_bot_process.is_alive():
                        self.stockfish_bot_process.kill()
            except Exception as e:
                logger.debug("on_stop error: %s", e)
            self.stockfish_bot_process = None

        if self.stockfish_bot_pipe is not None:
            try: self.stockfish_bot_pipe.close()
            except Exception: pass
            self.stockfish_bot_pipe = None

        if self._overlay_queue is not None:
            try:
                self._overlay_queue.close()
                self._overlay_queue.cancel_join_thread()
            except Exception: pass
            self._overlay_queue = None

        self.running = False
        self.start_button.setText("▶   START ENGINE")
        self._set_status("INACTIVE", DANGER, DANGER_BG)
        self.eval_text.setText("—")
        self._update_eval_bar("—")
        self.wdl_text.setText("—")
        self.material_text.setText("—")
        self.white_acc_text.setText("—")
        self.black_acc_text.setText("—")
        self.pv_text.setText("")

    # --- Evaluation and Telemetry UI updates ---
    def _update_eval_bar(self, eval_str: str):
        if hasattr(self, "eval_bar_widget"):
            self.eval_bar_widget.set_eval(eval_str)

    def update_evaluation_display(self, eval_str: str, wdl_str: str, material_str: str, bot_acc: str, opponent_acc: str):
        try:
            self.eval_text.setText(eval_str)
            self._update_eval_bar(eval_str)
            self.wdl_text.setText(wdl_str)
            self.material_text.setText(material_str)
            self.white_acc_text.setText(bot_acc)
            self.black_acc_text.setText(opponent_acc)
        except Exception as e:
            logger.debug("update_evaluation_display error: %s", e)

    def update_pv_display(self, pv_text: str, threat_san: str = ""):
        try:
            t = (pv_text or "").strip()
            if threat_san:
                t = f"{t}   Threat: {threat_san}".strip()
            self.pv_text.setText(t)
        except Exception:
            pass

    def update_telemetry(self, nps=0, depth=0, time_per_move_ms=0.0, connection_health="Connected", last_error=""):
        try:
            self.nps_text.setText(f"{nps:,}" if nps else "—")
            self.depth_reached_text.setText(str(depth) if depth else "—")
            self.move_time_text.setText(f"{time_per_move_ms:.1f}ms" if time_per_move_ms else "—")
            self.conn_health_text.setText(connection_health or "Connected")
            if last_error:
                self.last_error_text.setText(last_error)
        except Exception:
            pass

    # --- Move Table Operations ---
    def clear_tree(self):
        self.tree.setRowCount(0)
        self._move_count.setText("0 moves")

    def insert_move(self, move: str, eval_str="", clock="", is_blunder=False):
        row_cnt = self.tree.rowCount()
        # Even total moves -> White's move creates new row
        if len(self.match_moves) % 2 == 1:
            move_num = (len(self.match_moves) + 1) // 2
            self.tree.insertRow(row_cnt)
            num_item = QTableWidgetItem(f"{move_num}.")
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            white_item = QTableWidgetItem(move)
            white_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_blunder or "??" in move:
                white_item.setForeground(QBrush(QColor("#EF4444")))
                f = white_item.font()
                f.setBold(True)
                white_item.setFont(f)
            self.tree.setItem(row_cnt, 0, num_item)
            self.tree.setItem(row_cnt, 1, white_item)
            self.tree.setItem(row_cnt, 2, QTableWidgetItem(""))
        else:
            # Black's move fills existing row
            if row_cnt > 0:
                black_item = QTableWidgetItem(move)
                black_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_blunder or "??" in move:
                    black_item.setForeground(QBrush(QColor("#EF4444")))
                    f = black_item.font()
                    f.setBold(True)
                    black_item.setFont(f)
                self.tree.setItem(row_cnt - 1, 2, black_item)
        self.tree.scrollToBottom()

    def set_moves(self, moves: list[str], evals=None, clocks=None, blunders=None):
        self.clear_tree()
        for idx, m in enumerate(moves):
            blunder = blunders[idx] if blunders and idx < len(blunders) else False
            ev = evals[idx] if evals and idx < len(evals) else ""
            self.insert_move(m, eval_str=ev, is_blunder=blunder)

    def _on_table_context_menu(self, pos: QPoint):
        menu = QMenu(self)
        act_fen = menu.addAction("Copy FEN")
        act_fen.triggered.connect(self.on_copy_fen_button_listener)
        act_pgn = menu.addAction("Copy PGN")
        act_pgn.triggered.connect(self.on_copy_pgn_button_listener)
        act_exp = menu.addAction("Export PGN File")
        act_exp.triggered.connect(self.on_export_pgn_button_listener)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def on_copy_fen_button_listener(self):
        try:
            import chess
            board = chess.Board()
            for m in self.match_moves:
                try: board.push_san(m)
                except Exception: pass
            QGuiApplication.clipboard().setText(board.fen())
            self._set_temp_button_feedback(self.copy_fen_button, "COPIED!")
        except Exception as e:
            logger.debug("copy fen error: %s", e)

    def on_copy_pgn_button_listener(self):
        try:
            pgn_text = self._build_pgn_text()
            QGuiApplication.clipboard().setText(pgn_text)
            self._set_temp_button_feedback(self.copy_pgn_button, "COPIED!")
        except Exception as e:
            logger.debug("copy pgn error: %s", e)

    def on_export_pgn_button_listener(self):
        try:
            pgn_text = self._build_pgn_text()
            path, _ = QFileDialog.getSaveFileName(self, "Export PGN", "game.pgn", "PGN Files (*.pgn)")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(pgn_text)
                self._set_temp_button_feedback(self.export_pgn_button, "EXPORTED!")
        except Exception as e:
            logger.debug("export pgn error: %s", e)

    def _build_pgn_text(self) -> str:
        lines = ["[Event \"Casual Game\"]", f"[Date \"{datetime.datetime.now().strftime('%Y.%m.%d')}\"]", ""]
        moves_str = []
        for i in range(0, len(self.match_moves), 2):
            w = self.match_moves[i]
            b = self.match_moves[i+1] if i+1 < len(self.match_moves) else ""
            moves_str.append(f"{i//2 + 1}. {w} {b}".strip())
        lines.append(" ".join(moves_str))
        return "\n".join(lines)

    def _set_temp_button_feedback(self, btn: QPushButton, text: str):
        orig = btn.text()
        btn.setText(text)
        QTimer.singleShot(1500, lambda: btn.setText(orig))

    # --- Overlay Controls ---
    def on_overlay_opacity_change(self, *a):
        if self._overlay_queue is not None:
            try:
                self._overlay_queue.put(proto.OverlayConfig(opacity=float(self.overlay_opacity.get())))
            except Exception:
                pass

    def on_overlay_side_toggle(self, *a):
        if self._overlay_queue is not None:
            try:
                self._overlay_queue.put(proto.OverlayConfig(side=str(self.overlay_eval_side.get())))
            except Exception:
                pass

    def toggle_overlay(self, *a):
        if self._overlay_queue is not None:
            try:
                self._overlay_queue.put({"overlay_toggle": True})
            except Exception:
                pass

    def on_manual_mode_checkbox_listener(self):
        val = bool(self.enable_manual_mode.get())
        if hasattr(self, "manual_mode_frame"):
            self.manual_mode_frame.setVisible(val)

    def on_topmost_check_button_listener(self):
        tm = bool(self.enable_topmost.get())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, tm)
        self.show()

    # --- Stockfish Binary Selection & Validation ---
    def on_select_stockfish_button_listener(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Stockfish Engine Binary",
            "",
            "Executable Files (*.exe);;All Files (*)" if platform.system() == "Windows" else "All Files (*)",
        )
        if path:
            self.stockfish_path = path
            self._save_stockfish_path(path)
            self._refresh_stockfish_label()

    def _is_valid_stockfish(self, path: str, quick_check=True) -> bool:
        if not path or not os.path.exists(path):
            return False
        return True

    def _load_stockfish_path(self) -> str:
        return self._config.get("stockfish_path", "")

    def _save_stockfish_path(self, path: str):
        self._config["stockfish_path"] = path
        self._save_config()

    def _auto_find_stockfish(self) -> str:
        candidates = ["stockfish.exe", "stockfish", "src/stockfish.exe"]
        for c in candidates:
            if os.path.exists(c):
                return os.path.abspath(c)
        return ""

    def _download_stockfish(self, dest_dir="."):
        return ""

    # --- Graceful Shutdown & Emergency Kill ---
    def _cleanup(self):
        if getattr(self, "_cleaned", False):
            return
        self._cleaned = True
        self.exit = True
        self._save_config()
        self.on_stop_button_listener()

        if getattr(self, "_browser_manager", None) is not None:
            try: self._browser_manager.close()
            except Exception: pass
            self.chrome = None
        elif self.chrome is not None:
            try: self.chrome.quit()
            except Exception: pass
            self.chrome = None

    def on_close_listener(self):
        self._cleanup()
        self.close()

    def emergency_kill(self):
        logger.critical("EMERGENCY KILL triggered")
        self.exit = True
        self._cleanup()
        try:
            self.close()
        except Exception:
            pass
        sys.exit(0)

    def closeEvent(self, event):
        self._cleanup()
        event.accept()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    window = GUI()
    window.show()
    sys.exit(app.exec())
