import time
from selenium.common import NoSuchElementException, StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from grabbers.grabber import Grabber
from utilities import get_logger

logger = get_logger("grabber.chesscom")


class ChesscomGrabber(Grabber):
    # Resilient fallback chains: CSS first (fast), then XPath. Order matters – most likely first.
    BOARD_SELECTORS = [
        (By.CSS_SELECTOR, "wc-chess-board"),
        (By.ID, "board-single"),
        (By.ID, "board-play-computer"),
        (By.CSS_SELECTOR, "chess-board"),
        (By.CSS_SELECTOR, ".board"),
        (By.CSS_SELECTOR, "div[class*='board']"),
        (By.XPATH, "//*[@id='board-single']"),
        (By.XPATH, "//*[@id='board-play-computer']"),
    ]

    MOVE_LIST_SELECTORS = [
        (By.CSS_SELECTOR, ".play-controller-scrollable"),
        (By.CSS_SELECTOR, ".mode-swap-move-list-wrapper-component"),
        (By.CSS_SELECTOR, "[class*='move-list']"),
        (By.CSS_SELECTOR, "div.vertical-move-list"),
        (By.CSS_SELECTOR, "wc-move-list"),
        (By.CSS_SELECTOR, "wc-simple-move-list"),
        (By.CSS_SELECTOR, "div[data-test-element='move-list']"),
        (By.CSS_SELECTOR, "div.live-game-buttons + div"),  # fallback near live buttons
        (By.XPATH, "//div[contains(@class,'move-list')]"),
    ]

    def __init__(self, chrome_url, chrome_session_id):
        super().__init__(chrome_url, chrome_session_id)

    def update_board_elem(self):
        """Resilient board lookup with WebDriverWait + fallback chain + retry on stale."""
        for attempt in range(3):
            try:
                elem = self._find_with_retry(self.BOARD_SELECTORS, timeout=6)
                self._board_elem = elem
                if elem is None:
                    logger.warning("update_board_elem: no board found (attempt %d)", attempt + 1)
                else:
                    logger.debug("Board element found: tag=%s", elem.tag_name)
                return
            except (StaleElementReferenceException, WebDriverException) as e:
                logger.debug("update_board_elem stale attempt %d: %s", attempt, e)
                time.sleep(0.2 * (2 ** attempt))
        self._board_elem = None

    def is_white(self):
        """Determine if player is white via board orientation. Multiple fallbacks + retry."""
        for attempt in range(3):
            try:
                # Try primary: coordinates SVG inside board
                # Try several board roots
                square_names = None
                # Strategy 1: wc-chess-board svg
                selectors_to_try = [
                    (By.CSS_SELECTOR, "wc-chess-board svg"),
                    (By.XPATH, "//*[@id='board-play-computer']//*[name()='svg']"),
                    (By.XPATH, "//*[@id='board-single']//*[name()='svg']"),
                    (By.CSS_SELECTOR, "chess-board svg"),
                    (By.CSS_SELECTOR, ".board svg"),
                ]
                coordinates = None
                for by, val in selectors_to_try:
                    try:
                        # for SVG name() xpath we need find_element with that xpath
                        if "name()" in val:
                            coordinates = self.chrome.find_element(By.XPATH, val)
                        else:
                            coordinates = self.chrome.find_element(by, val)
                        if coordinates:
                            break
                    except NoSuchElementException:
                        continue

                if coordinates is None:
                    # Fallback: look for any coordinates element with class "coordinates"
                    try:
                        candidates = self.chrome.find_elements(By.CSS_SELECTOR, ".coordinates")
                        if candidates:
                            coordinates = candidates[0]
                        else:
                            # try generic svg
                            svgs = self.chrome.find_elements(By.CSS_SELECTOR, "svg.coordinates")
                            if svgs:
                                coordinates = svgs[0]
                    except Exception:
                        pass

                if coordinates is None:
                    # Last fallback: board orientation via piece placement? Check for class "flipped" / "orientation-black"
                    try:
                        for by, val in self.BOARD_SELECTORS:
                            try:
                                b = self.chrome.find_element(by, val)
                                cls = (b.get_attribute("class") or "") + " " + (b.get_attribute("orientation") or "")
                                if "flipped" in cls.lower() or "black" in cls.lower():
                                    return False
                            except Exception:
                                continue
                    except Exception:
                        pass
                    logger.warning("is_white: no coordinates element found")
                    return None

                # Get square names inside coordinates
                try:
                    square_names = coordinates.find_elements(By.XPATH, ".//*")
                except StaleElementReferenceException:
                    time.sleep(0.1)
                    square_names = coordinates.find_elements(By.XPATH, ".//*")

                if not square_names:
                    return None

                # Find bottom-left square (smallest x, biggest y)
                elem = None
                min_x = None
                max_y = None
                for i, name_element in enumerate(square_names):
                    try:
                        x = float(name_element.get_attribute("x") or 0)
                        y = float(name_element.get_attribute("y") or 0)
                    except (ValueError, TypeError, StaleElementReferenceException):
                        continue
                    if i == 0 or (x <= (min_x if min_x is not None else x) and y >= (max_y if max_y is not None else y)):
                        min_x = x
                        max_y = y
                        elem = name_element

                if elem is None:
                    return None
                try:
                    num = elem.text.strip() if hasattr(elem, "text") else elem.get_attribute("textContent")
                except StaleElementReferenceException:
                    time.sleep(0.1)
                    num = elem.text.strip()
                return num == "1"
            except StaleElementReferenceException as e:
                logger.debug("is_white stale retry %d: %s", attempt, e)
                time.sleep(0.2 * (2 ** attempt))
                continue
            except Exception as e:
                logger.debug("is_white error attempt %d: %s", attempt, e)
                time.sleep(0.2)
                continue
        return None

    def is_game_over(self):
        for attempt in range(2):
            try:
                try:
                    game_over_window = self.chrome.find_element(By.CSS_SELECTOR, ".board-modal-container")
                    return game_over_window is not None and game_over_window.is_displayed()
                except NoSuchElementException:
                    pass
                # Fallback: legacy class name search
                try:
                    game_over_window = self.chrome.find_element(By.CLASS_NAME, "board-modal-container")
                    return game_over_window is not None
                except NoSuchElementException:
                    return False
            except StaleElementReferenceException:
                time.sleep(0.15)
                continue
            except WebDriverException as e:
                logger.debug("is_game_over WebDriverException: %s", e)
                return False
        return False

    def reset_moves_list(self):
        """Reset the moves list when a new game starts"""
        self.moves_list = {}

    def get_move_list(self):
        """Resilient move list scraping with stale retry + exponential backoff + fallback selectors."""
        for attempt in range(3):
            try:
                # Find moves list container with fallback
                move_list_elem = None
                for by, val in self.MOVE_LIST_SELECTORS:
                    try:
                        move_list_elem = self.chrome.find_element(by, val)
                        if move_list_elem:
                            break
                    except NoSuchElementException:
                        continue

                if move_list_elem is None:
                    # Fallback: search globally for move nodes (chess.com DOM may have changed container class)
                    try:
                        global_nodes = self.chrome.find_elements(By.CSS_SELECTOR, "div.node[data-node]")
                        if global_nodes:
                            logger.info("get_move_list: using global fallback, found %d nodes", len(global_nodes))
                            # Process global nodes directly (no container)
                            moves = [n for n in global_nodes if not self.moves_list or n.get_attribute("data-processed") is None]
                            # Reuse same parsing loop but with global_nodes – simplify by treating global as container
                            for move in moves:
                                try:
                                    move_class = move.get_attribute("class") or ""
                                except StaleElementReferenceException:
                                    continue
                                if "white-move" not in move_class and "black-move" not in move_class and "node" not in move_class:
                                    continue
                                try:
                                    figurine_elem = move.find_element(By.CSS_SELECTOR, "[data-figurine]")
                                    figure = figurine_elem.get_attribute("data-figurine")
                                except (NoSuchElementException, StaleElementReferenceException):
                                    figure = None
                                try:
                                    text = move.text.strip()
                                    data_node = move.get_attribute("data-node")
                                except StaleElementReferenceException:
                                    continue
                                if not data_node:
                                    continue
                                if figure is None:
                                    self.moves_list[data_node] = text
                                elif "=" in text:
                                    m = text + figure
                                    if "+" in m:
                                        m = m.replace("+", "")
                                        m += "+"
                                    self.moves_list[data_node] = m
                                else:
                                    self.moves_list[data_node] = figure + text
                                try:
                                    self.chrome.execute_script("arguments[0].setAttribute('data-processed', 'true')", move)
                                except Exception:
                                    pass
                            return list(self.moves_list.values())
                        # No nodes found – if we have no cached moves, this is a new game with 0 moves (not error)
                        if not self.moves_list:
                            logger.debug("get_move_list: no container and no global nodes – returning [] (empty game)")
                            return []
                    except Exception as e:
                        logger.debug("global fallback error: %s", e)
                    logger.debug("get_move_list: no move list container found (attempt %d)", attempt)
                    if attempt < 2:
                        time.sleep(0.3 * (2 ** attempt))
                        continue
                    # Final fallback: if we have cached moves, return them; else [] (empty board) not None to avoid ERR_MOVES
                    if self.moves_list:
                        return list(self.moves_list.values())
                    # Check if we are on a live game page at all – look for board
                    if self.get_board() is not None:
                        return []
                    return None

                # Handle new-game detection via visible_moves count
                try:
                    visible_moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")
                except StaleElementReferenceException:
                    time.sleep(0.15)
                    visible_moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")

                if len(visible_moves) == 0 and self.moves_list:
                    logger.info("Detected new game (visible_moves==0 but cached moves exist) – resetting")
                    self.reset_moves_list()

                # Determine which nodes to fetch
                try:
                    if not self.moves_list:
                        moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")
                    else:
                        moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]:not([data-processed])")
                except StaleElementReferenceException:
                    time.sleep(0.15)
                    if not self.moves_list:
                        moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")
                    else:
                        moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]:not([data-processed])")

                for move in moves:
                    try:
                        move_class = move.get_attribute("class") or ""
                    except StaleElementReferenceException:
                        continue

                    if "white-move" not in move_class and "black-move" not in move_class:
                        # Still check if it's a node – some themes use different class
                        if "node" not in move_class:
                            continue

                    try:
                        figurine_elem = move.find_element(By.CSS_SELECTOR, "[data-figurine]")
                        figure = figurine_elem.get_attribute("data-figurine")
                    except (NoSuchElementException, StaleElementReferenceException):
                        figure = None

                    try:
                        text = move.text.strip()
                        data_node = move.get_attribute("data-node")
                    except StaleElementReferenceException:
                        continue

                    # Skip if no data-node (should not happen)
                    if not data_node:
                        continue

                    if figure is None:
                        self.moves_list[data_node] = text
                    elif "=" in text:
                        m = text + figure
                        if "+" in m:
                            m = m.replace("+", "")
                            m += "+"
                        self.moves_list[data_node] = m
                    else:
                        self.moves_list[data_node] = figure + text

                    try:
                        self.chrome.execute_script("arguments[0].setAttribute('data-processed', 'true')", move)
                    except (StaleElementReferenceException, WebDriverException):
                        pass

                return list(self.moves_list.values())

            except StaleElementReferenceException as e:
                logger.debug("get_move_list stale retry %d: %s", attempt, e)
                time.sleep(0.2 * (2 ** attempt))
                continue
            except WebDriverException as e:
                logger.debug("get_move_list WebDriverException attempt %d: %s", attempt, e)
                time.sleep(0.2)
                if attempt == 2:
                    return None
                continue
        return list(self.moves_list.values()) if self.moves_list else None

    def is_game_puzzles(self):
        return False

    def click_puzzle_next(self):
        pass

    def click_game_next(self):
        """Chess.com rematch / new game – try multiple selectors."""
        selectors = [
            (By.CSS_SELECTOR, "button[data-test-element='new-game']"),
            (By.CSS_SELECTOR, "button.ui_v5-button-component"),
            (By.XPATH, "//button[contains(., 'New Game')]"),
            (By.XPATH, "//button[contains(., 'Rematch')]"),
            (By.XPATH, "//button[contains(., 'Play Again')]"),
        ]
        for by, val in selectors:
            try:
                btn = self.chrome.find_element(by, val)
                if btn and btn.is_displayed():
                    self.chrome.execute_script("arguments[0].click();", btn)
                    logger.info("Clicked chess.com next game button: %s=%s", by, val)
                    return
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue
        logger.debug("click_game_next: no button found on chess.com")

    def make_mouseless_move(self, move, move_count):
        pass
