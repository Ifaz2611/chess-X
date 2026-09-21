import re
import time

from selenium.common import NoSuchElementException, StaleElementReferenceException, WebDriverException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from grabbers.grabber import Grabber
from utilities import get_logger

logger = get_logger("grabber.lichess")


class LichessGrabber(Grabber):
    BOARD_SELECTORS = [
        (By.CSS_SELECTOR, "cg-container"),
        (By.CSS_SELECTOR, ".cg-wrap cg-container"),
        (By.CSS_SELECTOR, "div.cg-wrap"),
        (By.CSS_SELECTOR, ".cg-board-wrap cg-container"),
        (By.XPATH, '//*[@id="main-wrap"]/main/div[1]/div[1]/div/cg-container'),
        (By.XPATH, '/html/body/div[2]/main/div[1]/div/cg-container'),
    ]

    MOVE_LIST_SELECTORS = [
        (By.CSS_SELECTOR, "rm6 l4x"),
        (By.XPATH, '//*[@id="main-wrap"]/main/div[1]/rm6/l4x'),
        (By.CSS_SELECTOR, ".mselect + rm6 l4x"),
        (By.CSS_SELECTOR, "l4x"),
    ]

    PUZZLE_MOVE_LIST_SELECTORS = [
        (By.XPATH, '/html/body/div[2]/main/div[2]/div[2]/div'),
        (By.CSS_SELECTOR, "div.puzzle-move-list"),
    ]

    def __init__(self, chrome_url, chrome_session_id):
        super().__init__(chrome_url, chrome_session_id)
        self.tag_name = None

    def update_board_elem(self):
        for attempt in range(3):
            try:
                elem = self._find_with_retry(self.BOARD_SELECTORS, timeout=2)
                if elem is not None:
                    self._board_elem = elem
                    logger.debug("Lichess board found: %s", elem.tag_name)
                    return
                # If not found, wait a bit and retry (board may be loading)
                time.sleep(0.15 * (attempt + 1))
            except (StaleElementReferenceException, WebDriverException) as e:
                logger.debug("update_board_elem stale attempt %d: %s", attempt, e)
                time.sleep(0.08 * (2 ** attempt))
        self._board_elem = None
        logger.warning("update_board_elem: no lichess board found after retries")

    def is_white(self):
        for attempt in range(3):
            try:
                if self._board_elem is None:
                    self.update_board_elem()
                    if self._board_elem is None:
                        return None
                children = self._board_elem.find_elements(By.XPATH, "./*")
                # Find ranks element
                ranks = [x for x in children if "ranks" in (x.get_attribute("class") or "")]
                if not ranks:
                    # Fallback: check board classes for orientation
                    try:
                        cls = (self._board_elem.get_attribute("class") or "")
                        # lichess uses orientation-white / orientation-black on cg-wrap
                        parent = self.chrome.find_element(By.CSS_SELECTOR, ".cg-wrap")
                        pcls = parent.get_attribute("class") or ""
                        if "orientation-black" in pcls or "orientation-black" in cls:
                            return False
                        if "orientation-white" in pcls or "orientation-white" in cls:
                            return True
                    except Exception:
                        pass
                    logger.debug("is_white: no ranks element found")
                    return None
                child = ranks[0]
                cls = child.get_attribute("class") or ""
                if cls == "ranks":
                    return True
                elif "ranks black" in cls or "black" in cls:
                    return False
                else:
                    return "black" not in cls
            except StaleElementReferenceException:
                time.sleep(0.06 * (2 ** attempt))
                continue
            except Exception as e:
                logger.debug("is_white error attempt %d: %s", attempt, e)
                time.sleep(0.06)
                continue
        return None

    def is_game_over(self):
        for attempt in range(2):
            try:
                try:
                    self.chrome.find_element(By.XPATH, '//*[@id="main-wrap"]/main/aside/div/section[2]')
                    return True
                except NoSuchElementException:
                    pass
                try:
                    game_over_window = self.chrome.find_element(By.XPATH, '/html/body/div[2]/main/div[2]/div[3]/div[1]')
                    if game_over_window.get_attribute("class") == "complete":
                        return True
                    return False
                except NoSuchElementException:
                    pass
                # Additional fallback: result banner
                try:
                    banner = self.chrome.find_element(By.CSS_SELECTOR, ".result, .status, #promotion_choice")
                    if banner and banner.is_displayed():
                        text = banner.text or ""
                        if any(x in text for x in ["1-0", "0-1", "1/2-1/2", "Checkmate", "Draw"]):
                            return True
                except NoSuchElementException:
                    pass
                return False
            except StaleElementReferenceException:
                time.sleep(0.05)
                continue
            except WebDriverException as e:
                logger.debug("is_game_over error: %s", e)
                return False
        return False

    def set_moves_tag_name(self):
        if self.is_game_puzzles():
            return False
        move_list_elem = self.get_normal_move_list_elem()
        if move_list_elem is None or move_list_elem == []:
            return False
        for attempt in range(2):
            try:
                last_child = move_list_elem.find_element(By.XPATH, "*[last()]")
                self.tag_name = last_child.tag_name
                return True
            except (NoSuchElementException, StaleElementReferenceException):
                time.sleep(0.15)
                continue
        return False

    def get_move_list(self):
        for attempt in range(3):
            try:
                is_puzzles = self.is_game_puzzles()

                if is_puzzles:
                    move_list_elem = self.get_puzzles_move_list_elem()
                    if move_list_elem is None:
                        if attempt < 2:
                            time.sleep(0.06 * (2 ** attempt))
                            continue
                        return None
                else:
                    move_list_elem = self.get_normal_move_list_elem()
                    if move_list_elem is None:
                        if attempt < 2:
                            time.sleep(0.06 * (2 ** attempt))
                            continue
                        return None
                    if (not move_list_elem) or (self.tag_name is None and self.set_moves_tag_name() is False):
                        return []

                # Get move elements
                try:
                    if not is_puzzles:
                        if not self.moves_list:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, self.tag_name)
                        else:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, self.tag_name + ":not([data-processed])")
                    else:
                        if not self.moves_list:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, "move")
                        else:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, "move:not([data-processed])")
                except StaleElementReferenceException:
                    time.sleep(0.05)
                    # retry once
                    if not is_puzzles:
                        if not self.moves_list:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, self.tag_name)
                        else:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, self.tag_name + ":not([data-processed])")
                    else:
                        if not self.moves_list:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, "move")
                        else:
                            children = move_list_elem.find_elements(By.CSS_SELECTOR, "move:not([data-processed])")

                for move_element in children:
                    try:
                        move = re.sub(r"[^a-zA-Z0-9+-]", "", move_element.text)
                    except StaleElementReferenceException:
                        continue
                    if move != "":
                        try:
                            # Use element id or data-node or index as key
                            key = getattr(move_element, "id", None) or move_element.get_attribute("data-node") or str(id(move_element))
                        except StaleElementReferenceException:
                            key = str(id(move_element))
                        self.moves_list[key] = move
                    try:
                        self.chrome.execute_script("arguments[0].setAttribute('data-processed', 'true')", move_element)
                    except (StaleElementReferenceException, WebDriverException):
                        pass

                return [val for val in self.moves_list.values()]

            except StaleElementReferenceException as e:
                logger.debug("get_move_list stale attempt %d: %s", attempt, e)
                time.sleep(0.06 * (2 ** attempt))
                continue
            except WebDriverException as e:
                logger.debug("get_move_list WebDriverException attempt %d: %s", attempt, e)
                time.sleep(0.06)
                if attempt == 2:
                    return None
                continue
        return [val for val in self.moves_list.values()] if self.moves_list else None

    def get_puzzles_move_list_elem(self):
        for by, val in self.PUZZLE_MOVE_LIST_SELECTORS:
            try:
                elem = self.chrome.find_element(by, val)
                if elem:
                    return elem
            except NoSuchElementException:
                continue
        return None

    def get_normal_move_list_elem(self):
        # Try primary selectors
        for by, val in self.MOVE_LIST_SELECTORS:
            try:
                elem = self.chrome.find_element(by, val)
                if elem:
                    return elem
            except NoSuchElementException:
                continue
        # Check for empty board case: rm6 exists but no l4x yet
        try:
            self.chrome.find_element(By.XPATH, '//*[@id="main-wrap"]/main/div[1]/rm6')
            return []
        except NoSuchElementException:
            pass
        # Try CSS rm6
        try:
            self.chrome.find_element(By.CSS_SELECTOR, "rm6")
            return []
        except NoSuchElementException:
            return None

    def is_game_puzzles(self):
        for attempt in range(2):
            try:
                self.chrome.find_element(By.XPATH, "/html/body/div[2]/main/aside/div[1]/div[1]/div/p[1]")
                return True
            except NoSuchElementException:
                pass
            except StaleElementReferenceException:
                time.sleep(0.04)
                continue
            # Fallback: check URL
            try:
                if "/training" in self.chrome.current_url or "puzzle" in self.chrome.current_url.lower():
                    return True
            except WebDriverException:
                pass
            return False
        return False

    def click_puzzle_next(self):
        selectors = [
            (By.XPATH, "/html/body/div[2]/main/div[2]/div[3]/a"),
            (By.XPATH, '//*[@id="main-wrap"]/main/div[2]/div[3]/div[3]/a[2]'),
            (By.CSS_SELECTOR, "a.continue"),
            (By.CSS_SELECTOR, ".puzzle__controls a"),
            (By.XPATH, "//a[contains(., 'Continue')]"),
        ]
        for by, val in selectors:
            try:
                btn = self.chrome.find_element(by, val)
                if btn and btn.is_displayed():
                    self.chrome.execute_script("arguments[0].click();", btn)
                    logger.info("Clicked puzzle next: %s=%s", by, val)
                    return
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue

    def click_game_next(self):
        selectors = [
            (By.XPATH, "//*[contains(text(), 'New opponent')]"),
            (By.CSS_SELECTOR, "button.new-opponent"),
            (By.XPATH, "//button[contains(., 'New opponent')]"),
            (By.CSS_SELECTOR, "a.new-opponent"),
        ]
        for by, val in selectors:
            try:
                btn = self.chrome.find_element(by, val)
                if btn and btn.is_displayed():
                    self.chrome.execute_script("arguments[0].click();", btn)
                    logger.info("Clicked new opponent: %s=%s", by, val)
                    return
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue
        logger.debug("click_game_next: no button found")

    def make_mouseless_move(self, move, move_count):
        for attempt in range(2):
            try:
                message = '{"t":"move","d":{"u":"' + move + '","b":1,"a":' + str(move_count) + '}}'
                script = 'lichess.socket.ws.send(JSON.stringify(' + message + '))'
                self.chrome.execute_script(script)
                return
            except (StaleElementReferenceException, WebDriverException) as e:
                logger.debug("make_mouseless_move attempt %d failed: %s", attempt, e)
                time.sleep(0.2)
                continue
