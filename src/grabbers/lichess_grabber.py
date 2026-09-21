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
        # Fallbacks for obfuscated builds (tags like aPp, i5d, Z7yx etc. change each deploy)
        # These try to locate the moves container via the stable active class .a1t
        (By.CSS_SELECTOR, ".a1t"),
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

    # --- JS helpers for obfuscated Lichess tags (rm6/l4x/kwdb are randomized each build) ---
    def _js_extract_moves(self):
        """Try to extract SAN list via JavaScript, independent of obfuscated tag names.
        Uses stable .a1t (active move) class + SAN regex scan. Returns list or None."""
        try:
            sans = self.chrome.execute_script("""
                const sanRe = /^(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?[+#]?)$/;
                function isSan(t){
                    if(!t) return false;
                    t=t.trim();
                    if(/^[0-9]+\\.?$/.test(t)) return false;
                    if(t==='…' || t==='...') return false;
                    if(t[0]==='P') t=t.slice(1);
                    // strip unicode piece symbols
                    const clean = t.replace(/[^a-zA-Z0-9xO\\-+#=]/g,'');
                    // also try original
                    return sanRe.test(t) || sanRe.test(clean);
                }
                function clean(t){ return t.replace(/[^a-zA-Z0-9xO\\-+#=]/g,'').trim(); }
                // 1) try to find container via .a1t (stable active class)
                let container = null;
                const active = document.querySelector('.a1t');
                if(active && active.parentElement) container = active.parentElement;
                // 2) try old + new known rm/moves tags (rm6, i5d, aPp, l4x, etc.) – query all candidates
                if(!container){
                    const cands = document.querySelectorAll('rm6, l4x, aPp, i5d, rm6 l4x, i5d aPp');
                    for(const c of cands){ if(c && c.children.length>0){ container=c; break; } }
                }
                // 3) fallback: element with most SAN children
                if(!container){
                    let best=null,bestCount=0;
                    const all=document.querySelectorAll('*');
                    for(const el of all){
                        if(el.children.length<2) continue;
                        let cnt=0;
                        for(const ch of el.children){
                            const txt=(ch.textContent||'').trim().split(/\\s+/)[0]||'';
                            if(isSan(txt) || isSan(clean(txt))) cnt++;
                        }
                        if(cnt>bestCount){
                            const r=el.getBoundingClientRect();
                            if(r.width>80 && r.height>15){ best=el; bestCount=cnt; }
                        }
                    }
                    if(bestCount>=1) container=best;
                }
                if(!container) return null;
                // extract SANs from container children (filter out move numbers)
                const res=[];
                for(const c of container.children){
                    let txt=(c.textContent||'').trim().split(/\\s+/)[0]||'';
                    if(!txt) continue;
                    if(/^[0-9]+\\.?$/.test(txt)) continue;
                    if(txt==='…'|| txt==='...') continue;
                    // remove move number prefix like "1."
                    if(isSan(txt)) res.push(txt);
                    else {
                        const cl=clean(txt);
                        if(isSan(cl)) res.push(cl);
                    }
                }
                if(res.length===0){
                    // deep search as last resort (nested)
                    const all=container.querySelectorAll('*');
                    for(const c of all){
                        const txt=(c.textContent||'').trim();
                        if(txt.length>=2 && txt.length<=7 && (isSan(txt)||isSan(clean(txt)))){
                            // avoid duplicates of already collected? collect all
                            // but only if parent is container or container descendant
                            res.push(txt);
                            if(res.length>200) break;
                        }
                    }
                    // deduplicate while preserving order
                    const seen=new Set(); const uniq=[];
                    for(const s of res){ if(!seen.has(s)){ seen.add(s); uniq.push(s);} }
                    // if uniq still looks like SAN list, return uniq filtered by SAN
                    const filtered=uniq.filter(s=>isSan(s)||isSan(clean(s)));
                    if(filtered.length>0) return filtered;
                }
                // return empty array if container exists but no moves yet (new game) vs null if no container
                return res;
            """)
            if sans is None:
                return None
            # sans is list from JS (could be empty)
            if not isinstance(sans, list):
                return None
            # Clean via python regex as well
            cleaned = []
            for s in sans:
                if not isinstance(s, str):
                    continue
                s = s.strip()
                if not s or s in ("…", "..."):
                    continue
                # strip leading move numbers if any slipped through
                if re.match(r"^[0-9]+\.?$", s):
                    continue
                # re.sub to keep only SAN chars
                cs = re.sub(r"[^a-zA-Z0-9xO#+=\\-]", "", s)
                # fallback: if original had unicode, cs may be valid
                # validate with SAN-ish pattern
                if re.match(r"^(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?[+#]?)$", s) or re.match(r"^(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?[+#]?)$", cs):
                    # prefer s if it already matches, else cs
                    if re.match(r"^(?:[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?[+#]?)$", s):
                        cleaned.append(s)
                    else:
                        cleaned.append(cs)
                elif s:
                    # keep as is if looks like SAN
                    cleaned.append(s)
            return cleaned
        except Exception as e:
            logger.debug("_js_extract_moves failed: %s", e)
            return None

    def get_move_list(self):
        # First: try robust JS extraction (handles obfuscated tags)
        # This is the primary path for live games – works regardless of rm6/l4x randomization
        for attempt in range(2):
            try:
                if not self.is_game_puzzles():
                    js_moves = self._js_extract_moves()
                    if js_moves is not None:
                        # js_moves could be [] for new game (0 moves) – that's valid, don't treat as failure
                        # Sync with moves_list cache for compatibility with incremental logic
                        # If we got a valid list (empty or not), return it and keep cache in sync
                        # For non-empty, rebuild moves_list to reflect current board
                        self.moves_list = {str(i): san for i, san in enumerate(js_moves)}
                        return js_moves
            except Exception as e:
                logger.debug("_js_extract_moves outer error: %s", e)
            time.sleep(0.08 * (attempt + 1))

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
                        # Last chance: JS already tried, but try one more direct JS before failing
                        js_fallback = self._js_extract_moves()
                        if js_fallback is not None:
                            self.moves_list = {str(i): san for i, san in enumerate(js_fallback)}
                            return js_fallback
                        return None
                    if (not move_list_elem) or (self.tag_name is None and self.set_moves_tag_name() is False):
                        # If set_moves_tag_name failed, try JS fallback before returning empty
                        js_fallback = self._js_extract_moves()
                        if js_fallback is not None:
                            self.moves_list = {str(i): san for i, san in enumerate(js_fallback)}
                            return js_fallback
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
        # Try primary selectors (including .a1t which may return active move, not container)
        for by, val in self.MOVE_LIST_SELECTORS:
            try:
                elem = self.chrome.find_element(by, val)
                if elem:
                    # If we matched .a1t (active move), return its parent (the moves container)
                    try:
                        if val == ".a1t" or ".a1t" in val:
                            parent = self.chrome.execute_script("return arguments[0].parentElement", elem)
                            if parent:
                                return parent
                    except Exception:
                        pass
                    # If element is the move itself (kwdb/move etc.), check if it has siblings that look like moves -> return parent
                    # Heuristic: if element.tagName matches move-like and it has siblings, use parent
                    try:
                        tag = (elem.tag_name or "").lower()
                        if tag in ("move", "kwdb", "z7yx") or len(tag) <= 4:
                            pe = self.chrome.execute_script("return arguments[0].parentElement", elem)
                            if pe and len(pe.find_elements(By.XPATH, "./*")) > 1:
                                return pe
                    except Exception:
                        pass
                    return elem
            except NoSuchElementException:
                continue
        # JS fallback: find container via .a1t or scan
        try:
            container = self.chrome.execute_script("""
                const active = document.querySelector('.a1t');
                if(active && active.parentElement) return active.parentElement;
                const cands = document.querySelectorAll('rm6, l4x, aPp, i5d');
                for(const c of cands){ if(c && c.children.length>=0) return c; }
                // search for any element inside #main-wrap that could be moves container
                let best=null,bestCount=0;
                const all=document.querySelectorAll('#main-wrap *');
                for(const el of all){
                    if(el.children.length<1) continue;
                    // look for children that are moves-like (short text)
                    let cnt=0;
                    for(const ch of el.children){
                        const txt=(ch.textContent||'').trim();
                        if(txt.length>=2 && txt.length<=7) cnt++;
                    }
                    if(cnt>bestCount && cnt>=1){
                        const r=el.getBoundingClientRect();
                        if(r.width>80){ best=el; bestCount=cnt; }
                    }
                }
                if(best) return best;
                return null;
            """)
            if container:
                return container
        except Exception as e:
            logger.debug("get_normal_move_list_elem JS fallback failed: %s", e)

        # Check for empty board case: rm6 / i5d exists but no moves yet -> return [] (not None)
        for empty_sel in [(By.CSS_SELECTOR, "rm6"), (By.CSS_SELECTOR, "i5d"), (By.CSS_SELECTOR, "rm6, i5d")]:
            try:
                by, val = empty_sel
                self.chrome.find_element(by, val)
                return []
            except NoSuchElementException:
                continue
        try:
            self.chrome.find_element(By.XPATH, '//*[@id="main-wrap"]/main/div[1]/rm6')
            return []
        except NoSuchElementException:
            pass
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
