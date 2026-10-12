import re
import time
from abc import ABC, abstractmethod

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)

from utilities import (
    attach_to_session,
    find_element_with_fallback,
    get_logger,
    wait_for_any_element,
)

logger = get_logger("grabber")


class Grabber(ABC):
    """Base abstract class for different chess sites with resilient helpers."""

    # Subclasses define their own selector fallback chains
    BOARD_SELECTORS = []
    MOVE_LIST_SELECTORS = []

    # --- Shared game-result patterns (draw / resign / abort handling) ---
    # Normalized results: "1-0", "0-1", "1/2-1/2", "*"
    RESULT_SCORE_RE = re.compile(r"(1\s*-\s*0|0\s*-\s*1|1\s*/\s*2\s*-\s*1\s*/\s*2|½\s*-\s*½|\*)")
    RESULT_KEYWORDS = (
        "checkmate", "stalemate", "draw", "resign", "abort", "abandon",
        "time out", "timeout", "flag", "threefold", "repetition",
        "insufficient", "50-move", "you won", "you lose", "game over",
        "white won", "black won", "white wins", "black wins",
    )
    RESULT_TERMS = ("1-0", "0-1", "1/2-1/2", "½-½", "*")

    # Variant hints matched against URL / DOM text (lowercased)
    VARIANT_PATTERNS = {
        "chess960": ("960", "fischer", "chess960", "franklin"),
        "crazyhouse": ("crazyhouse", "zh", "zhuri"),
        "kingofthehill": ("king of the hill", "kingofthehill", "koth"),
        "threecheck": ("3-check", "3 check", "threecheck", "three-check"),
        "antichess": ("antichess", "anti-chess", "suicide"),
        "atomic": ("atomic",),
        "horde": ("horde",),
        "racingkings": ("racing kings", "racingkings"),
    }

    def __init__(self, chrome_url, chrome_session_id, browser: str | None = None):
        # browser-aware attach (chrome/firefox/edge) – falls back to Chrome for compat
        try:
            # Prefer explicit browser, then env, then config
            if browser is None:
                import os
                browser = os.environ.get("CHESSX_BROWSER")
                if not browser:
                    try:
                        from config_store import load_config
                        cfg = load_config()
                        browser = getattr(cfg, "browser", "chrome")
                    except Exception:
                        browser = "chrome"
            self.chrome = attach_to_session(chrome_url, chrome_session_id, browser=browser)
        except TypeError:
            # legacy attach without browser arg
            self.chrome = attach_to_session(chrome_url, chrome_session_id)
        self._board_elem = None
        self.moves_list = {}
        self.browser = browser or "chrome"
        self.calibration_offset_x = 0.0
        self.calibration_offset_y = 0.0
        try:
            from config_store import load_config
            cfg = load_config()
            self.calibration_offset_x = float(getattr(cfg, "calibration_offset_x", 0.0) or 0.0)
            self.calibration_offset_y = float(getattr(cfg, "calibration_offset_y", 0.0) or 0.0)
        except Exception:
            pass

    def get_board(self):
        return self._board_elem

    def reset_moves_list(self):
        """Reset the moves list when a new game starts"""
        self.moves_list = {}

    def set_calibration_offset(self, x: float, y: float):
        """Set manual or calibrated pixel offset adjustment."""
        self.calibration_offset_x = float(x)
        self.calibration_offset_y = float(y)

    def get_calibration_offset(self) -> tuple[float, float]:
        """Return the currently configured (dx, dy) calibration offset."""
        return getattr(self, "calibration_offset_x", 0.0), getattr(self, "calibration_offset_y", 0.0)

    def get_viewport_metrics(self) -> dict:
        """Query detailed window, screen, and viewport metrics from the browser.
        
        Returns a dict containing screen coordinates, inner/outer dimensions,
        per-monitor devicePixelRatio, maximized detection, and frame borders.
        """
        js_code = (
            "const dpr = window.devicePixelRatio || 1.0;\n"
            "const sx = (window.screenX !== undefined) ? window.screenX : (window.screenLeft || 0);\n"
            "const sy = (window.screenY !== undefined) ? window.screenY : (window.screenTop || 0);\n"
            "const ow = window.outerWidth || 0;\n"
            "const oh = window.outerHeight || 0;\n"
            "const iw = window.innerWidth || ow;\n"
            "const ih = window.innerHeight || oh;\n"
            "const scrollX = window.scrollX || window.pageXOffset || 0;\n"
            "const scrollY = window.scrollY || window.pageYOffset || 0;\n"
            "const scr = window.screen || {};\n"
            "const screenW = scr.width || 0;\n"
            "const screenH = scr.height || 0;\n"
            "const availW = scr.availWidth || screenW;\n"
            "const availH = scr.availHeight || screenH;\n"
            "const availLeft = scr.availLeft || 0;\n"
            "const availTop = scr.availTop || 0;\n"
            "const isFullscreen = !!document.fullscreenElement;\n"
            "const isMaximized = !isFullscreen && (\n"
            "    (Math.abs(ow - availW) <= 25 && Math.abs(oh - availH) <= 25) ||\n"
            "    (Math.abs(ow - screenW) <= 25 && Math.abs(oh - screenH) <= 25)\n"
            ");\n"
            "const horizDiff = ow - iw;\n"
            "const vertDiff = oh - ih;\n"
            "let borderLeft = 0;\n"
            "let borderBottom = 0;\n"
            "let topChrome = 0;\n"
            "if (isMaximized || isFullscreen) {\n"
            "    borderLeft = 0;\n"
            "    borderBottom = 0;\n"
            "    topChrome = vertDiff > 0 ? vertDiff : 0;\n"
            "} else {\n"
            "    borderLeft = (horizDiff > 0) ? (horizDiff / 2.0) : 0;\n"
            "    borderBottom = borderLeft;\n"
            "    topChrome = vertDiff - borderBottom;\n"
            "    if (topChrome < 0) topChrome = vertDiff;\n"
            "}\n"
            "return {\n"
            "    screenX: sx,\n"
            "    screenY: sy,\n"
            "    outerWidth: ow,\n"
            "    outerHeight: oh,\n"
            "    innerWidth: iw,\n"
            "    innerHeight: ih,\n"
            "    scrollX: scrollX,\n"
            "    scrollY: scrollY,\n"
            "    devicePixelRatio: dpr,\n"
            "    isMaximized: isMaximized,\n"
            "    isFullscreen: isFullscreen,\n"
            "    borderLeft: borderLeft,\n"
            "    borderBottom: borderBottom,\n"
            "    topChrome: topChrome,\n"
            "    screenWidth: screenW,\n"
            "    screenHeight: screenH,\n"
            "    availWidth: availW,\n"
            "    availHeight: availH,\n"
            "    availLeft: availLeft,\n"
            "    availTop: availTop\n"
            "};"
        )
        return self.chrome.execute_script(js_code)

    def get_top_left_corner(self):
        """Returns the coordinates of the top left corner of the browser viewport.
        
        Resilient across:
        - Multi-monitor setups with secondary screen coordinates
        - Per-monitor DPI scaling and fractional DPI (e.g. 125%, 150%)
        - Non-maximized browser windows (compensating for bottom frame borders)
        - Sub-pixel scroll offsets
        - Fine-tune calibration offsets
        """
        for attempt in range(2):
            try:
                metrics = self.get_viewport_metrics()
                if isinstance(metrics, dict):
                    sx = float(metrics.get("screenX", 0) or 0)
                    sy = float(metrics.get("screenY", 0) or 0)
                    bl = float(metrics.get("borderLeft", 0) or 0)
                    tc = float(metrics.get("topChrome", 0) or 0)
                    scx = float(metrics.get("scrollX", 0) or 0)
                    scy = float(metrics.get("scrollY", 0) or 0)
                    cal_x = float(getattr(self, "calibration_offset_x", 0.0) or 0.0)
                    cal_y = float(getattr(self, "calibration_offset_y", 0.0) or 0.0)
                    canvas_x_offset = sx + bl - scx + cal_x
                    canvas_y_offset = sy + tc - scy + cal_y
                    return canvas_x_offset, canvas_y_offset
                elif isinstance(metrics, (tuple, list)) and len(metrics) >= 2:
                    cal_x = float(getattr(self, "calibration_offset_x", 0.0) or 0.0)
                    cal_y = float(getattr(self, "calibration_offset_y", 0.0) or 0.0)
                    return float(metrics[0]) + cal_x, float(metrics[1]) + cal_y
            except (StaleElementReferenceException, WebDriverException) as e:
                logger.debug("get_top_left_corner attempt %d failed: %s", attempt, e)
                time.sleep(0.05 * (2 ** attempt))
            except Exception as e:
                logger.debug("get_top_left_corner unexpected attempt %d: %s", attempt, e)
                time.sleep(0.05)
        # Fallback to (0, 0) - better than crashing
        logger.warning("get_top_left_corner failed after retries, returning (0,0)")
        return 0, 0

    # -----------------------------------------------------------------------
    # Resilient helpers
    # -----------------------------------------------------------------------

    def _find_with_retry(self, selectors, timeout=2):
        """Helper: try selectors with fallback + WebDriverWait."""
        elem = find_element_with_fallback(self.chrome, selectors, timeout=timeout)
        return elem

    def _wait_for_any(self, selectors, timeout=6):
        return wait_for_any_element(self.chrome, selectors, timeout=timeout)

    def _retry_call(self, func, *args, max_retries=2, base_delay=0.05, **kwargs):
        delay = base_delay
        last = None
        for i in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except (StaleElementReferenceException, NoSuchElementException) as e:
                last = e
                if i == max_retries:
                    raise
                logger.debug("_retry_call %s attempt %d: %s", func.__name__, i, e)
                time.sleep(delay)
                delay *= 2
            except WebDriverException as e:
                last = e
                if i >= 1:
                    raise
                time.sleep(delay)
        if last:
            raise last

    # -----------------------------------------------------------------------
    # Health check – to be called on startup before starting bot loop
    # -----------------------------------------------------------------------

    def health_check(self):
        """Check that board (and optionally move list) selectors resolve. Returns (ok:bool, details:dict)."""
        details = {"board": False, "move_list": False, "board_selector": None, "move_selector": None}
        # Board
        board = self._find_with_retry(getattr(self, "BOARD_SELECTORS", []), timeout=2)
        if board is not None:
            details["board"] = True
            # try to find which selector worked
            for by, val in getattr(self, "BOARD_SELECTORS", []):
                try:
                    if self.chrome.find_element(by, val):
                        details["board_selector"] = f"{by}={val}"
                        break
                except Exception:
                    continue
        # Move list (optional – may be empty at game start)
        ml_selectors = getattr(self, "MOVE_LIST_SELECTORS", [])
        if ml_selectors:
            ml = self._find_with_retry(ml_selectors, timeout=1)
            if ml is not None:
                details["move_list"] = True
                for by, val in ml_selectors:
                    try:
                        if self.chrome.find_element(by, val):
                            details["move_selector"] = f"{by}={val}"
                            break
                    except Exception:
                        continue
            else:
                # move list may legitimately be absent on fresh board
                details["move_list"] = None
        ok = details["board"]
        # Warn if only last-resort fallback matched (brittle DOM)
        try:
            selectors = getattr(self, "BOARD_SELECTORS", [])
            if ok and selectors and details.get("board_selector"):
                last = selectors[-1]
                last_str = f"{last[0]}={last[1]}"
                if details["board_selector"] == last_str:
                    logger.warning("Board selector matched only last-resort fallback %s – DOM may have changed! Consider updating BOARD_SELECTORS", last_str)
            ml_selectors = getattr(self, "MOVE_LIST_SELECTORS", [])
            if ml_selectors and details.get("move_selector"):
                last_ml = ml_selectors[-1]
                last_ml_str = f"{last_ml[0]}={last_ml[1]}"
                if details["move_selector"] == last_ml_str:
                    logger.warning("Move-list selector matched only last-resort fallback %s", last_ml_str)
        except Exception:
            pass
        logger.info("Health check %s: %s", "PASS" if ok else "FAIL", details)
        return ok, details

    def _clear_overlay_queue(self, overlay_queue):
        """Drain overlay queue to avoid stale arrows on new game."""
        if overlay_queue is None:
            return
        try:
            while not overlay_queue.empty():
                try:
                    overlay_queue.get_nowait()
                except Exception:
                    break
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Shared result / variant helpers (concrete so subclasses can reuse)
    # -----------------------------------------------------------------------

    @classmethod
    def normalize_result_text(cls, text: str | None) -> str | None:
        """Normalize arbitrary banner text to '1-0' / '0-1' / '1/2-1/2' / '*'.

        Returns None when no result marker is present.
        """
        if not text or not isinstance(text, str):
            return None
        t = text.strip()
        # Unicode half first
        if "½-½" in t or "½ – ½" in t or "½–½" in t:
            return "1/2-1/2"
        m = cls.RESULT_SCORE_RE.search(t)
        if not m:
            return None
        raw = re.sub(r"\s+", "", m.group(1))
        if raw in ("1-0", "0-1"):
            return raw
        if raw in ("1/2-1/2", "½-½"):
            return "1/2-1/2"
        if raw == "*":
            return "*"
        return None

    @classmethod
    def text_looks_like_game_end(cls, text: str | None) -> bool:
        """True if text contains a result marker or a game-end keyword."""
        if not text or not isinstance(text, str):
            return False
        if cls.normalize_result_text(text) is not None:
            # "*" alone is ambiguous (live clock uses *?) – require keyword context
            norm = cls.normalize_result_text(text)
            if norm != "*":
                return True
        low = text.lower()
        return any(k in low for k in cls.RESULT_KEYWORDS)

    def get_page_text(self, limit: int = 8000) -> str:
        """Best-effort body innerText via JS (for result-banner scanning)."""
        try:
            txt = self.chrome.execute_script(
                "return (document.body && document.body.innerText) ? document.body.innerText.slice(0, arguments[0]) : '';",
                int(limit),
            )
            return txt or ""
        except Exception:
            return ""

    def get_clocks(self) -> tuple[str, str]:
        """Return (white_clock, black_clock) if visible, or ('', '')."""
        return "", ""

    def detect_variant(self) -> str:
        """Detect chess variant from URL + page text. Returns 'standard' by default.

        Covers Chess960 / 3-check / King of the Hill (+ common lichess/chess.com
        variants) for board orientation + validation downstream.
        """
        try:
            url = ""
            try:
                url = (self.chrome.current_url or "").lower()
            except Exception:
                url = ""
            hay = url + " " + self.get_page_text(limit=2000).lower()
            for variant, hints in self.VARIANT_PATTERNS.items():
                if any(h in hay for h in hints):
                    return variant
        except Exception:
            pass
        return "standard"

    def get_game_result(self) -> str | None:
        """Return normalized result if the page shows one, else None.

        Subclasses override `_result_banner_texts()` for site-specific DOM;
        this base implementation falls back to scanning body text.
        """
        try:
            for txt in self._result_banner_texts():
                norm = self.normalize_result_text(txt)
                if norm is not None and norm != "*":
                    return norm
                if self.text_looks_like_game_end(txt):
                    # Keyword hit without explicit score – map common phrasing
                    low = (txt or "").lower()
                    if "white" in low and ("won" in low or "win" in low):
                        return "1-0"
                    if "black" in low and ("won" in low or "win" in low):
                        return "0-1"
                    if any(k in low for k in ("draw", "stalemate", "repetition", "insufficient", "50-move")):
                        return "1/2-1/2"
                    return "*"
        except Exception:
            pass
        # Fallback: scan whole page text
        try:
            page = self.get_page_text()
            norm = self.normalize_result_text(page)
            if norm is not None and norm != "*":
                return norm
            if self.text_looks_like_game_end(page):
                return "*"
        except Exception:
            pass
        return None

    def _result_banner_texts(self) -> list:
        """Site-specific banner texts to scan. Base: empty (page-text fallback)."""
        return []

    def has_draw_offer(self) -> bool:
        """True if a draw-offer accept/decline UI is currently visible."""
        return False

    def has_resign_button(self) -> bool:
        """True if a resign/abort button is currently visible (game in progress)."""
        return False

    # -----------------------------------------------------------------------
    # Abstract interface
    # -----------------------------------------------------------------------

    @abstractmethod
    def update_board_elem(self):
        pass

    @abstractmethod
    def is_white(self):
        pass

    @abstractmethod
    def is_game_over(self):
        pass

    @abstractmethod
    def get_move_list(self):
        pass

    @abstractmethod
    def is_game_puzzles(self):
        pass

    @abstractmethod
    def click_puzzle_next(self):
        pass

    @abstractmethod
    def click_game_next(self):
        pass

    @abstractmethod
    def make_mouseless_move(self, move, move_count):
        pass
