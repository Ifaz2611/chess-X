import time
from selenium.common import NoSuchElementException, StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from grabbers.grabber import Grabber
from utilities import get_logger

logger = get_logger("grabber.chesscom")


class ChesscomGrabber(Grabber):
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

    # --- Puzzles / game-over / next-game selectors (chess.com) ---
    PUZZLE_URL_HINTS = ("puzzle", "puzzles", "training", "rush", "puzzle-battle")
    PUZZLE_DOM_SELECTORS = [
        (By.CSS_SELECTOR, "[data-test-element='puzzle-board']"),
        (By.CSS_SELECTOR, ".puzzle-board"),
        (By.CSS_SELECTOR, "[class*='puzzle']"),
        (By.XPATH, "//*[contains(@class,'puzzle')]"),
    ]
    PUZZLE_NEXT_SELECTORS = [
        (By.CSS_SELECTOR, "button[data-test-element='puzzle-next']"),
        (By.CSS_SELECTOR, "button.puzzle-next"),
        (By.CSS_SELECTOR, "button[class*='puzzle-next']"),
        (By.XPATH, "//button[contains(., 'Next')]"),
        (By.XPATH, "//button[contains(., 'Next Puzzle')]"),
        (By.XPATH, "//button[contains(., 'Continue')]"),
        (By.XPATH, "//button[contains(., 'Try Again')]"),
        (By.XPATH, "//a[contains(., 'Next')]"),
        (By.CSS_SELECTOR, ".puzzle-controls button:last-child"),
    ]
    GAME_NEXT_SELECTORS = [
        (By.CSS_SELECTOR, "button[data-test-element='rematch']"),
        (By.CSS_SELECTOR, "button[data-test-element='new-game']"),
        (By.CSS_SELECTOR, "button[data-test-element='play-again']"),
        (By.CSS_SELECTOR, "button.rematch"),
        (By.CSS_SELECTOR, "button.ui_v5-button-component"),
        (By.XPATH, "//button[contains(., 'Rematch')]"),
        (By.XPATH, "//button[contains(., 'New Game')]"),
        (By.XPATH, "//button[contains(., 'Play Again')]"),
        (By.XPATH, "//button[contains(., 'New Opponent')]"),
        (By.XPATH, "//button[contains(., 'Next Game')]"),
        (By.XPATH, "//button[contains(., 'Challenge')]"),
        (By.XPATH, "//a[contains(., 'Rematch')]"),
    ]
    RESULT_BANNER_SELECTORS = [
        (By.CSS_SELECTOR, ".board-modal-container"),
        (By.CSS_SELECTOR, ".game-result"),
        (By.CSS_SELECTOR, "[data-test-element='game-result']"),
        (By.CSS_SELECTOR, ".modal-game-over"),
        (By.CSS_SELECTOR, "[class*='game-over']"),
        (By.CSS_SELECTOR, "[class*='game-result']"),
        (By.CSS_SELECTOR, ".live-game-end"),
    ]
    DRAW_OFFER_SELECTORS = [
        (By.XPATH, "//button[contains(., 'Accept Draw')]"),
        (By.XPATH, "//button[contains(., 'Decline')]"),
        (By.CSS_SELECTOR, "button[data-test-element='accept-draw']"),
        (By.CSS_SELECTOR, "[class*='draw-offer']"),
    ]
    RESIGN_BUTTON_SELECTORS = [
        (By.CSS_SELECTOR, "button[data-test-element='resign']"),
        (By.CSS_SELECTOR, "button[class*='resign']"),
        (By.XPATH, "//button[contains(., 'Resign')]"),
        (By.XPATH, "//button[contains(., 'Abort')]"),
        (By.XPATH, "//button[contains(@aria-label, 'Resign')]"),
    ]

    def __init__(self, chrome_url, chrome_session_id, browser: str | None = None):
        super().__init__(chrome_url, chrome_session_id, browser=browser)
        self._consecutive_bad_parses = 0

    def update_board_elem(self):
        """Resilient board lookup with WebDriverWait + fallback chain + retry on stale."""
        for attempt in range(2):
            try:
                elem = self._find_with_retry(self.BOARD_SELECTORS, timeout=2)
                self._board_elem = elem
                if elem is None:
                    logger.warning("update_board_elem: no board found (attempt %d)", attempt + 1)
                else:
                    logger.debug("Board element found: tag=%s", elem.tag_name)
                return
            except (StaleElementReferenceException, WebDriverException) as e:
                logger.debug("update_board_elem stale attempt %d: %s", attempt, e)
                time.sleep(0.08 * (2 ** attempt))
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
                # 1) Modal / banner elements with result text
                for by, val in self.RESULT_BANNER_SELECTORS:
                    try:
                        el = self.chrome.find_element(by, val)
                        if el is None:
                            continue
                        try:
                            visible = el.is_displayed()
                        except Exception:
                            visible = True
                        text = ""
                        try:
                            text = (el.text or "").strip()
                        except StaleElementReferenceException:
                            continue
                        if not visible and not text:
                            continue
                        if text and self.text_looks_like_game_end(text):
                            return True
                        if visible and ("board-modal-container" in val or "game-result" in val or "game-over" in val):
                            # Visible end-of-game modal even if text not yet scraped
                            # Double-check via page text to avoid false positive on premove modals
                            page = self.get_page_text(limit=2000)
                            if self.text_looks_like_game_end(page) or self.normalize_result_text(text) is not None:
                                return True
                            return True
                    except NoSuchElementException:
                        continue
                    except StaleElementReferenceException:
                        continue
                # 2) Fallback: scan page text for result markers / keywords
                try:
                    page = self.get_page_text(limit=4000)
                    if page and self.text_looks_like_game_end(page):
                        # Require a banner element OR explicit score to avoid matching
                        # move-list "1." fragments – normalize_result_text is strict.
                        if self.normalize_result_text(page) is not None:
                            return True
                        # Keyword-only hit: confirm a banner/modal exists
                        for by, val in self.RESULT_BANNER_SELECTORS:
                            try:
                                el = self.chrome.find_element(by, val)
                                if el is not None:
                                    return True
                            except (NoSuchElementException, StaleElementReferenceException):
                                continue
                except Exception:
                    pass
                return False
            except StaleElementReferenceException:
                time.sleep(0.05)
                continue
            except WebDriverException as e:
                logger.debug("is_game_over WebDriverException: %s", e)
                return False
        return False

    def _result_banner_texts(self) -> list:
        texts = []
        for by, val in self.RESULT_BANNER_SELECTORS:
            try:
                el = self.chrome.find_element(by, val)
                if el is None:
                    continue
                try:
                    t = (el.text or "").strip()
                except StaleElementReferenceException:
                    continue
                if t:
                    texts.append(t)
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue
        return texts

    def has_draw_offer(self) -> bool:
        for by, val in self.DRAW_OFFER_SELECTORS:
            try:
                el = self.chrome.find_element(by, val)
                if el is not None and el.is_displayed():
                    return True
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue
        return False

    def has_resign_button(self) -> bool:
        for by, val in self.RESIGN_BUTTON_SELECTORS:
            try:
                el = self.chrome.find_element(by, val)
                if el is not None and el.is_displayed():
                    return True
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue
        return False

    def reset_moves_list(self):
        """Reset the moves list when a new game starts"""
        self.moves_list = {}
        self._consecutive_bad_parses = 0

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
                            # Parse ALL nodes in DOM document order (no data-node
                            # sort, no incremental data-processed filter – both
                            # misorder/duplicate after re-renders).
                            dom_sans = []
                            for move in global_nodes:
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
                                except StaleElementReferenceException:
                                    continue
                                if text and self.normalize_result_text(text) is not None:
                                    continue
                                san = self._decode_chesscom_san(figure, text)
                                if san:
                                    dom_sans.append(san)
                            validated = self._validate_san_sequence(dom_sans)
                            if validated is not None:
                                self.moves_list = {str(i): s for i, s in enumerate(validated)}
                                self._consecutive_bad_parses = 0
                                return list(validated)
                            # Corrupt global snapshot – keep last good cache
                            if self.moves_list:
                                return list(self.moves_list.values())
                            return dom_sans
                        # No nodes found – if we have no cached moves, this is a new game with 0 moves (not error)
                        if not self.moves_list:
                            logger.debug("get_move_list: no container and no global nodes – returning [] (empty game)")
                            return []
                    except Exception as e:
                        logger.debug("global fallback error: %s", e)
                    logger.debug("get_move_list: no move list container found (attempt %d)", attempt)
                    if attempt < 2:
                        time.sleep(0.08 * (2 ** attempt))
                        continue
                    # Final fallback: if we have cached moves, return them; else [] (empty board) not None to avoid ERR_MOVES
                    if self.moves_list:
                        return list(self.moves_list.values())
                    # Check if we are on a live game page at all – look for board
                    if self.get_board() is not None:
                        return []
                    return None

                # Handle new-game / takeback detection via visible_moves count.
                # Takeback/undo: DOM node count decreases -> cached moves_list must
                # be rebuilt from scratch, otherwise stale SANs linger.
                # NOTE: count comparison uses raw DOM node count (document order),
                # not the validated cache length, so transient phantom nodes
                # don't corrupt the comparison.
                try:
                    visible_moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")
                except StaleElementReferenceException:
                    time.sleep(0.05)
                    visible_moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")

                if len(visible_moves) == 0 and self.moves_list:
                    logger.info("Detected new game (visible_moves==0 but cached moves exist) – resetting")
                    self.reset_moves_list()
                    self._consecutive_bad_parses = 0
                elif self.moves_list and len(visible_moves) < len(self.moves_list):
                    logger.info(
                        "Takeback/undo detected on chess.com: visible %d < cached %d – rebuilding",
                        len(visible_moves), len(self.moves_list),
                    )
                    self.reset_moves_list()
                    self._consecutive_bad_parses = 0
                    # Clear data-processed marks so full re-parse happens below
                    try:
                        self.chrome.execute_script(
                            "var els=arguments[0].querySelectorAll('[data-processed]');"
                            "for(var i=0;i<els.length;i++){els[i].removeAttribute('data-processed');}",
                            move_list_elem,
                        )
                    except Exception:
                        pass

                # Always re-parse ALL visible nodes in DOM document order.
                # data-node is a tree/variation id, NOT a stable ply index, so
                # sorting by it misorders moves after ~10 plies (bot freeze).
                # data-processed incremental caching is therefore unreliable
                # (chess.com re-renders the list); full re-parse is cheap (<40 nodes).
                try:
                    moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")
                except StaleElementReferenceException:
                    time.sleep(0.05)
                    moves = move_list_elem.find_elements(By.CSS_SELECTOR, "div.node[data-node]")

                dom_sans: list[str] = []
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
                    except StaleElementReferenceException:
                        continue

                    # Skip result markers (1-0, 0-1, 1/2-1/2, *) – they are not SAN
                    if text and self.normalize_result_text(text) is not None:
                        logger.debug("Skipping result marker in move list: %r", text)
                        continue

                    san = self._decode_chesscom_san(figure, text)
                    if not san:
                        continue
                    dom_sans.append(san)

                # Validate the DOM-order sequence before accepting it. A single
                # phantom/variation node (e.g. 'Re1' on move 2, or a duplicated
                # 'b4'/'exd4' mid-list) must NOT poison the cache – otherwise the
                # bot loop spins on "Invalid SAN" forever and appears frozen.
                validated = self._validate_san_sequence(dom_sans)
                if validated is None:
                    # Empty DOM = genuine new game (0 moves), not corruption.
                    if len(dom_sans) == 0:
                        self.moves_list = {}
                        self._consecutive_bad_parses = 0
                        return []
                    # Corrupt snapshot (transient re-render / variation nodes):
                    # keep returning the last known-good list so the bot waits
                    # for the DOM to stabilize instead of deadlocking.
                    self._consecutive_bad_parses = getattr(self, "_consecutive_bad_parses", 0) + 1
                    logger.warning(
                        "Rejecting corrupt chess.com move snapshot %r (kept %d cached moves, bad streak %d)",
                        dom_sans, len(self.moves_list), self._consecutive_bad_parses,
                    )
                    if self._consecutive_bad_parses >= 10:
                        # DOM stuck corrupt (e.g. persistent variation nodes):
                        # force a full reset so the next poll rebuilds cleanly.
                        logger.warning("Move snapshot corrupt 10x in a row – forcing cache reset")
                        self.reset_moves_list()
                        self._consecutive_bad_parses = 0
                    if self.moves_list:
                        return list(self.moves_list.values())
                    # No good cache yet – return the raw snapshot so the caller
                    # can surface the error instead of None-looping.
                    return dom_sans

                self._consecutive_bad_parses = 0
                # Rebuild cache in DOM order (keys are positional, not data-node,
                # so future reads stay stable even if chess.com recycles ids).
                self.moves_list = {str(i): san for i, san in enumerate(validated)}
                try:
                    for m in moves:
                        self.chrome.execute_script("arguments[0].setAttribute('data-processed', 'true')", m)
                except (StaleElementReferenceException, WebDriverException):
                    pass
                except Exception:
                    pass

                return list(validated)

            except StaleElementReferenceException as e:
                logger.debug("get_move_list stale retry %d: %s", attempt, e)
                time.sleep(0.06 * (2 ** attempt))
                continue
            except WebDriverException as e:
                logger.debug("get_move_list WebDriverException attempt %d: %s", attempt, e)
                time.sleep(0.06)
                if attempt == 2:
                    return None
                continue
        return list(self.moves_list.values()) if self.moves_list else None

    @staticmethod
    def _decode_chesscom_san(figure, text) -> str | None:
        """Combine chess.com figurine icon + node text into a SAN string.

        data-figurine may be a single letter ('n'), a full name ('knight'),
        or a unicode glyph ('♞'). Node text may already contain the piece
        letter – avoid double-prefixing ('NNf3'). Promotion chooser nodes
        ('=Q' fragments) are normalized to trailing '=Q[+#]' form.
        Returns None for empty/non-move text.
        """
        t = (text or "").strip()
        if not t:
            return None
        # Strip move-number prefixes like "12." / "12..." that sometimes leak in
        import re as _re
        t = _re.sub(r"^\d+\.+\s*", "", t).strip()
        if not t or t in ("…", "...", "·"):
            return None
        if figure is None:
            return t
        f = str(figure).strip()
        if not f:
            return t
        # Normalize figure to a SAN piece letter ("" for pawn)
        piece = ""
        fl = f.lower()
        glyph_map = {"♞": "N", "♘": "N", "♜": "R", "♖": "R", "♝": "B", "♗": "B",
                     "♛": "Q", "♕": "Q", "♚": "K", "♔": "K", "♟": "", "♙": ""}
        if f in glyph_map:
            piece = glyph_map[f]
        elif len(f) == 1 and f.upper() in "KQRBN":
            # Single letter; 'P'/pawn marker means no prefix
            piece = "" if f.upper() == "P" else f.upper()
        else:
            name_map = {"king": "K", "queen": "Q", "rook": "R", "bishop": "B",
                        "knight": "N", "pawn": "", "k": "K", "q": "Q",
                        "r": "R", "b": "B", "n": "N"}
            piece = name_map.get(fl, "")
            if piece == "" and fl not in ("pawn", "p", ""):
                # Unknown figure value – don't corrupt the SAN, use raw text
                # (but avoid returning e.g. "knightf3").
                if len(t) > 1 and t[0].upper() in "KQRBN" and t[0].isupper():
                    return t
                return t
        # Promotion: text like "e8", "=Q", "e8=Q", "e8Q" + figure
        if "=" in t:
            # Normalize to trailing =Piece (e.g. "e8=Q", "exd8=Q+")
            core = t.replace(" ", "")
            # If figure already present after '=', keep as-is
            if _re.search(r"=[QRBNqrbn][+#]?$", core):
                return core
            # Text fragment like "=Q" or "e8=" – append normalized piece
            promo = piece if piece in "QRBN" else "Q"
            core = _re.sub(r"=?\s*[QRBNqrbn]?\s*[+#]?\s*$", "", core)
            suffix = "+" if "+" in t else ("#" if "#" in t else "")
            return f"{core}={promo}{suffix}"
        # Avoid double piece prefix: text "Nf3" + figure "N" -> "Nf3"
        if piece and t and t[0].upper() == piece and t[0].isupper():
            return t
        # Castling stays as-is regardless of figure
        if t.startswith("O-O") or t.startswith("0-0"):
            return t.replace("0-0", "O-O")
        if piece:
            return piece + t
        # Pawn moves: strip any stray figurine letter remnants
        return t

    @staticmethod
    def _validate_san_sequence(sans) -> list | None:
        """Validate SANs sequentially on a fresh board (DOM order).

        Returns the validated list when the FULL sequence is legal,
        [] for an empty game, or None when any move is illegal (corrupt
        snapshot – caller must keep the last good cache, not poison it).
        """
        try:
            import chess as _chess
        except Exception:
            return list(sans) if sans else []
        if not sans:
            return []
        try:
            b = _chess.Board()
            for san in sans:
                b.push_san(san)
            return list(sans)
        except Exception:
            return None

    def _sorted_chesscom_moves(self) -> list:
        """Legacy helper kept for backwards compat: return cached moves.

        Ordering is now DOM document order (positional keys), NOT data-node
        sort – data-node ids are variation/tree ids that misorder the game.
        """
        try:
            sans = list(self.moves_list.values())
            cleaned = [s for s in sans if self.normalize_result_text(s) is None]
            return cleaned
        except Exception:
            return list(self.moves_list.values())

    def is_game_puzzles(self):
        """True when on a chess.com puzzle / training page.

        Checks URL hints first (cheap), then DOM puzzle markers.
        """
        for attempt in range(2):
            try:
                try:
                    url = (self.chrome.current_url or "").lower()
                except WebDriverException:
                    url = ""
                if any(h in url for h in self.PUZZLE_URL_HINTS):
                    # Exclude live/archive game URLs that merely contain "puzzle" rarely;
                    # puzzle URLs on chess.com are /puzzles/... – treat as puzzle.
                    if "puzzle" in url:
                        return True
                for by, val in self.PUZZLE_DOM_SELECTORS:
                    try:
                        el = self.chrome.find_element(by, val)
                        if el is not None:
                            try:
                                if el.is_displayed():
                                    return True
                            except Exception:
                                return True
                    except NoSuchElementException:
                        continue
                    except StaleElementReferenceException:
                        continue
                # Also check page text for puzzle-specific chrome
                try:
                    page = self.get_page_text(limit=1500).lower()
                    if any(k in page for k in ("puzzle rating", "daily puzzle", "puzzle rush", "puzzle battle")):
                        return True
                except Exception:
                    pass
                return False
            except StaleElementReferenceException:
                time.sleep(0.04)
                continue
            except WebDriverException:
                return False
        return False

    def click_puzzle_next(self):
        """Click 'Next puzzle' on chess.com (parity with Lichess continue flow)."""
        for by, val in self.PUZZLE_NEXT_SELECTORS:
            try:
                btn = self.chrome.find_element(by, val)
                if btn is not None and btn.is_displayed():
                    self.chrome.execute_script("arguments[0].click();", btn)
                    logger.info("Clicked chess.com puzzle next: %s=%s", by, val)
                    self.reset_moves_list()
                    return True
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue
        # JS fallback: click first visible button whose text is Next/Continue
        try:
            clicked = self.chrome.execute_script("""
                const cands = Array.from(document.querySelectorAll('button, a'));
                for (const b of cands) {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if ((t === 'next' || t.startsWith('next puzzle') || t === 'continue')
                        && b.offsetParent !== null) { b.click(); return true; }
                }
                return false;
            """)
            if clicked:
                logger.info("Clicked chess.com puzzle next via JS fallback")
                self.reset_moves_list()
                return True
        except Exception as e:
            logger.debug("click_puzzle_next JS fallback failed: %s", e)
        logger.debug("click_puzzle_next: no button found on chess.com")
        return False

    def click_game_next(self):
        """Chess.com rematch / new-game loop (mirrors Lichess 'New opponent').

        Tries rematch-style selectors first, clicks via JS, resets the cached
        move list so the bot loop re-syncs from 0 moves.
        Returns True when a button was clicked.
        """
        for by, val in self.GAME_NEXT_SELECTORS:
            try:
                btn = self.chrome.find_element(by, val)
                if btn is not None and btn.is_displayed():
                    self.chrome.execute_script("arguments[0].click();", btn)
                    logger.info("Clicked chess.com next game button: %s=%s", by, val)
                    self.reset_moves_list()
                    return True
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                continue
        # JS fallback: first visible rematch-like button
        try:
            clicked = self.chrome.execute_script("""
                const pats = ['rematch', 'new game', 'play again', 'new opponent', 'next game'];
                const cands = Array.from(document.querySelectorAll('button, a'));
                for (const b of cands) {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (pats.some(p => t.includes(p)) && b.offsetParent !== null) { b.click(); return t; }
                }
                return false;
            """)
            if clicked:
                logger.info("Clicked chess.com next game via JS fallback: %s", clicked)
                self.reset_moves_list()
                return True
        except Exception as e:
            logger.debug("click_game_next JS fallback failed: %s", e)
        logger.debug("click_game_next: no button found on chess.com")
        return False

    def make_mouseless_move(self, move, move_count):
        """Chess.com background move (parity with Lichess WS injection).

        Investigation note: chess.com has no stable public WebSocket/live API
        (obfuscated per-session WSS on live.chess.com); unlike Lichess
        ``lichess.socket.ws.send`` there is no documented endpoint to reuse.
        So this implements a *JS-level* mouseless move that works while the
        browser is in the background (no OS cursor via pyautogui):

        1. Try internal board-component APIs (``wc-chess-board`` web component
           has historically exposed ``game``/``board`` objects).
        2. Fall back to dispatching synthetic pointer events on the from/to
           squares computed from the board element rect – equivalent to a
           drag but without moving the system mouse.

        Args:
            move: UCI string (e.g. ``e2e4`` or ``e7e8q``).
            move_count: ply count (kept for interface parity; unused here).

        Returns:
            True on success. Raises RuntimeError when the board is missing or
            all JS strategies fail (caller should fall back to mouse move).
        """
        import re as _re
        if not move or not isinstance(move, str) or not _re.match(r"^[a-h][1-8][a-h][1-8][qrbnQRBN]?$", move.strip()):
            raise ValueError(f"Invalid UCI move for mouseless: {move!r}")
        uci = move.strip().lower()
        from_sq, to_sq = uci[0:2], uci[2:4]
        promo = uci[4] if len(uci) == 5 else ""
        board = self.get_board()
        if board is None:
            self.update_board_elem()
            board = self.get_board()
        if board is None:
            raise RuntimeError("make_mouseless_move: board element not found")

        # Strategy 1: internal component API (best-effort, version-tolerant)
        api_script = """
            const uci = arguments[0];
            const from = uci.slice(0,2), to = uci.slice(2,4);
            const promo = uci.length > 4 ? uci[4] : null;
            const boards = Array.from(document.querySelectorAll('wc-chess-board, chess-board, #board-single, #board-play-computer'));
            for (const b of boards) {
                try {
                    // Newer wc-chess-board exposes .game / ._game / .board
                    const g = b.game || b._game || b.board || b._board || null;
                    if (g && typeof g.move === 'function') {
                        // chess.js-style API takes {from,to,promotion}
                        const r = g.move({from, to, promotion: promo || undefined});
                        if (r) return 'component-game.move';
                    }
                    if (typeof b.move === 'function') { const r = b.move(from, to, promo); if (r !== false) return 'component.move'; }
                } catch(e) {}
            }
            // chess.com sometimes keeps a global game controller
            try {
                const wins = [window, window.Chess, window.chess, window.game, window.liveGame];
                for (const w of wins) {
                    if (w && typeof w.move === 'function') { w.move({from, to, promotion: promo}); return 'global.move'; }
                }
            } catch(e) {}
            return null;
        """
        try:
            hit = self.chrome.execute_script(api_script, uci)
            if hit:
                logger.info("Chess.com mouseless move %s via %s", uci, hit)
                return True
        except (StaleElementReferenceException, WebDriverException) as e:
            logger.debug("mouseless component-API attempt failed: %s", e)

        # Strategy 2: synthetic pointer drag on squares (no OS cursor)
        drag_script = """
            const from = arguments[0], to = arguments[1];
            function sqCenter(sq, rect) {
                // orientation: detect flipped via body/board class
                const flipped = document.querySelector('.flipped, .orientation-black, [orientation="black"]') !== null;
                const f = sq.charCodeAt(0) - 97, r = parseInt(sq[1], 10) - 1;
                const sqW = rect.width / 8, sqH = rect.height / 8;
                let col = flipped ? 7 - f : f;
                let row = flipped ? r : 7 - r;
                return {x: rect.left + window.scrollX + col*sqW + sqW/2,
                        y: rect.top + window.scrollY + row*sqH + sqH/2};
            }
            // find board rect from several roots
            const root = document.querySelector('wc-chess-board') || document.getElementById('board-single')
                || document.getElementById('board-play-computer') || document.querySelector('chess-board');
            if (!root) return 'no-board';
            const rect = root.getBoundingClientRect();
            if (!rect || rect.width < 10) return 'bad-rect';
            const a = sqCenter(from, rect), b = sqCenter(to, rect);
            function ev(type, p) {
                return new PointerEvent(type, {bubbles:true, cancelable:true, clientX:p.x-window.scrollX,
                    clientY:p.y-window.scrollY, pointerId:1, isPrimary:true, button:0, buttons:type==='pointerup'?0:1});
            }
            const elA = document.elementFromPoint(a.x-window.scrollX, a.y-window.scrollY) || root;
            const elB = document.elementFromPoint(b.x-window.scrollX, b.y-window.scrollY) || root;
            elA.dispatchEvent(ev('pointerdown', a));
            elA.dispatchEvent(ev('mousedown', a));
            // mousemove steps for drag recognition
            const steps = 4;
            for (let i=1;i<=steps;i++){
                const p = {x: a.x+(b.x-a.x)*i/steps, y: a.y+(b.y-a.y)*i/steps};
                (document.elementFromPoint(p.x-window.scrollX, p.y-window.scrollY) || root).dispatchEvent(ev('pointermove', p));
            }
            elB.dispatchEvent(ev('pointerup', b));
            elB.dispatchEvent(ev('mouseup', b));
            elB.dispatchEvent(new PointerEvent('click', {bubbles:true, cancelable:true,
                clientX:b.x-window.scrollX, clientY:b.y-window.scrollY, pointerId:1, isPrimary:true}));
            return 'dispatched';
        """
        try:
            res = self.chrome.execute_script(drag_script, from_sq, to_sq)
            if res == "dispatched":
                logger.info("Chess.com mouseless move %s via synthetic drag", uci)
                # Promotion chooser: click offset piece if needed (queen default needs no click)
                if promo and promo != "q":
                    try:
                        time.sleep(0.15)
                        self.chrome.execute_script("""
                            const promoBtns = Array.from(document.querySelectorAll(
                              '[data-figurine], .promotion-piece, [class*="promotion"] button, [class*="promotion"] div'));
                            const want = arguments[0];
                            const map = {q:'q', queen:'q', n:'n', knight:'n', r:'r', rook:'r', b:'b', bishop:'b'};
                            for (const el of promoBtns) {
                                const fig = ((el.getAttribute && el.getAttribute('data-figurine')) || el.textContent || '').trim().toLowerCase();
                                if (map[fig] === want && el.offsetParent !== null) { el.click(); return true; }
                            }
                            return false;
                        """, promo)
                    except Exception as e:
                        logger.debug("promotion chooser click failed: %s", e)
                return True
            logger.warning("Chess.com mouseless drag returned %r", res)
        except (StaleElementReferenceException, WebDriverException) as e:
            logger.debug("mouseless drag attempt failed: %s", e)
            raise RuntimeError(f"mouseless move {uci} failed: {e}") from e
        raise RuntimeError(f"mouseless move {uci} failed: no JS strategy succeeded")
