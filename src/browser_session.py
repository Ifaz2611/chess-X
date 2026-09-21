"""Browser session manager – ChromeDriver lifecycle extracted from gui.py.

Handles creation (ChromeDriverManager + Selenium Manager fallback),
navigation, health probing, and clean shutdown (context manager + atexit).
No behavior change: same options, same URL/session_id handling.
"""
from __future__ import annotations

import os
import platform
import time
import atexit
import threading
from typing import Optional, Tuple

from selenium import webdriver
from selenium.common import WebDriverException
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

from utilities import get_logger

logger = get_logger("browser_session")


class BrowserSessionManager:
    """Owns a single Chrome WebDriver session."""

    def __init__(self):
        self.chrome: Optional[webdriver.Chrome] = None
        self.chrome_url: Optional[str] = None
        self.chrome_session_id: Optional[str] = None
        self.opened_browser: bool = False
        self.opening_browser: bool = False
        self._lock = threading.Lock()
        atexit.register(self.close)

    # --- creation ---
    def open(self, website: str) -> bool:
        """Blocking open – same logic as GUI._open_browser_worker but without Tk."""
        with self._lock:
            if self.opening_browser or self.opened_browser:
                logger.debug("Browser open requested but already opening/opened")
                return False
            self.opening_browser = True
        # Build options outside lock
        options = webdriver.ChromeOptions()
        options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('useAutomationExtension', False)
        if platform.system() == "Linux":
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

        chromedriver_path = None
        service = None
        driver = None
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
                driver = webdriver.Chrome(service=service, options=options)
            else:
                raise RuntimeError("ChromeDriverManager returned empty path")
        except Exception as wdm_error:  # noqa: BLE001
            logger.warning("webdriver-manager failed (%s), trying Selenium Manager fallback", wdm_error)
            try:
                if service is not None:
                    try:
                        service.stop()
                    except Exception as e:
                        logger.debug("service stop failed: %s", e)
                driver = webdriver.Chrome(options=options)
                logger.info("Selenium Manager fallback succeeded")
            except Exception as sm_error:  # noqa: BLE001
                import traceback
                diag = traceback.format_exc()
                logger.error("Selenium Manager also failed: %s\n%s", sm_error, diag)
                with self._lock:
                    self.opening_browser = False
                return False

        if driver is None:
            logger.error("Chrome driver is None after creation attempts")
            with self._lock:
                self.opening_browser = False
            return False

        try:
            target = "https://www.chess.com" if website == "chesscom" else "https://www.lichess.org"
            driver.get(target)
            # Extract service_url / session id like GUI did
            try:
                if getattr(driver, "service", None) and getattr(driver.service, "service_url", None):
                    chrome_url = driver.service.service_url
                else:
                    chrome_url = getattr(driver.command_executor, "_url", "")
                    if "/session" in chrome_url:
                        chrome_url = chrome_url.split("/session")[0]
                    if not chrome_url.startswith("http"):
                        chrome_url = driver.command_executor._url
                if not chrome_url:
                    chrome_url = getattr(driver.command_executor, "_url", "")
            except Exception as e:  # noqa: BLE001
                logger.debug("chrome_url fallback error: %s", e)
                try:
                    chrome_url = driver.command_executor._url
                except Exception:
                    chrome_url = ""
            chrome_session_id = getattr(driver, "session_id", None)
            with self._lock:
                self.chrome = driver
                self.chrome_url = chrome_url
                self.chrome_session_id = chrome_session_id
                self.opening_browser = False
                self.opened_browser = True
            logger.info("Browser opened: %s session=%s", chrome_url, chrome_session_id)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("Error navigating: %s", e, exc_info=True)
            try:
                driver.quit()
            except Exception:
                pass
            with self._lock:
                self.opening_browser = False
            return False

    def is_alive(self) -> bool:
        if not self.opened_browser or self.chrome is None:
            return False
        try:
            _ = self.chrome.current_url
            return True
        except WebDriverException as e:
            msg = str(e).lower()
            if "invalid session" in msg or "disconnected" in msg or "no such window" in msg:
                return False
            logger.debug("is_alive check WebDriverException: %s", e)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("is_alive unexpected: %s", e)
            return False

    def check_driver_log_for_close(self) -> bool:
        """Return True if driver log indicates window closed."""
        if not self.opened_browser or self.chrome is None:
            return False
        try:
            logs = self.chrome.get_log("driver")
            if logs and "target window already closed" in logs[-1].get("message", ""):
                return True
        except (WebDriverException, AttributeError) as e:
            logger.debug("get_log check failed: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.debug("check_driver_log unexpected: %s", e)
        return False

    def close(self):
        with self._lock:
            if self.chrome is not None:
                try:
                    self.chrome.quit()
                except Exception as e:  # noqa: BLE001
                    logger.debug("chrome.quit error: %s", e)
                self.chrome = None
            self.chrome_url = None
            self.chrome_session_id = None
            self.opened_browser = False
            self.opening_browser = False
        logger.info("BrowserSessionManager closed")

    # context manager support
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
