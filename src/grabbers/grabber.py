import time
from abc import ABC, abstractmethod

from selenium.common.exceptions import (
    StaleElementReferenceException,
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from utilities import attach_to_session, get_logger, retry_on_stale, find_element_with_fallback, wait_for_any_element

logger = get_logger("grabber")


class Grabber(ABC):
    """Base abstract class for different chess sites with resilient helpers."""

    # Subclasses define their own selector fallback chains
    BOARD_SELECTORS = []
    MOVE_LIST_SELECTORS = []

    def __init__(self, chrome_url, chrome_session_id):
        self.chrome = attach_to_session(chrome_url, chrome_session_id)
        self._board_elem = None
        self.moves_list = {}

    def get_board(self):
        return self._board_elem

    def reset_moves_list(self):
        """Reset the moves list when a new game starts"""
        self.moves_list = {}

    def get_top_left_corner(self):
        """Returns the coordinates of the top left corner of the Chrome window, resilient to stale/disconnect."""
        for attempt in range(3):
            try:
                canvas_x_offset = self.chrome.execute_script(
                    "return window.screenX + (window.outerWidth - window.innerWidth) / 2 - window.scrollX;"
                )
                canvas_y_offset = self.chrome.execute_script(
                    "return window.screenY + (window.outerHeight - window.innerHeight) - window.scrollY;"
                )
                return canvas_x_offset, canvas_y_offset
            except (StaleElementReferenceException, WebDriverException) as e:
                logger.debug("get_top_left_corner attempt %d failed: %s", attempt, e)
                time.sleep(0.15 * (2 ** attempt))
        # Fallback to 0,0 – better than crashing
        logger.warning("get_top_left_corner failed after retries, returning (0,0)")
        return 0, 0

    # -----------------------------------------------------------------------
    # Resilient helpers
    # -----------------------------------------------------------------------

    def _find_with_retry(self, selectors, timeout=6):
        """Helper: try selectors with fallback + WebDriverWait."""
        elem = find_element_with_fallback(self.chrome, selectors, timeout=timeout)
        return elem

    def _wait_for_any(self, selectors, timeout=6):
        return wait_for_any_element(self.chrome, selectors, timeout=timeout)

    def _retry_call(self, func, *args, max_retries=3, base_delay=0.15, **kwargs):
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
        board = self._find_with_retry(getattr(self, "BOARD_SELECTORS", []), timeout=5)
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
            ml = self._find_with_retry(ml_selectors, timeout=3)
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
