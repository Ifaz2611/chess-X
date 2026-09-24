from __future__ import annotations

import os
import platform
import time
import atexit
import threading
from typing import Optional

from selenium.common import WebDriverException

from utilities import get_logger
import logging
# Don't spam pytest output when handler stream closed at shutdown
logging.raiseExceptions = False

logger = get_logger("browser_session")

try:
    from browser_factory import create_driver, normalize_browser
except Exception:  # fallback if factory not available
    create_driver = None  # type: ignore
    def normalize_browser(b):  # type: ignore
        return (b or "chrome").lower()

try:
    from platform_info import is_macos, check_macos_chromedriver, check_macos_permissions
except Exception:
    def is_macos():  # type: ignore
        return platform.system() == "Darwin"
    def check_macos_chromedriver():  # type: ignore
        return True, ""
    def check_macos_permissions():  # type: ignore
        return True, ""


class BrowserSessionManager:
    """Owns a single WebDriver session (Chrome/Firefox/Edge)."""

    def __init__(self, browser: str = "chrome"):
        self.browser: str = normalize_browser(browser)
        self.chrome: Optional[object] = None  # keep legacy attr name; holds any WebDriver
        self.driver: Optional[object] = None  # alias
        self.chrome_url: Optional[str] = None
        self.chrome_session_id: Optional[str] = None
        self.opened_browser: bool = False
        self.opening_browser: bool = False
        self._lock = threading.Lock()
        atexit.register(self.close)

    @property
    def current_browser(self) -> str:
        return self.browser

    # --- creation ---
    def open(self, website: str, browser: Optional[str] = None) -> bool:
        """Blocking open – delegates to browser_factory.

        Args:
            website: "chesscom" or "lichess" (determines target URL)
            browser: override browser id (chrome|firefox|edge). If None, uses
                     instance default or CHESSX_BROWSER env.
        """
        # Allow per-call browser override
        if browser is not None:
            self.browser = normalize_browser(browser)
        else:
            # Also respect env if instance default is chrome and env sets otherwise
            env_b = os.environ.get("CHESSX_BROWSER")
            if env_b and self.browser == "chrome":
                # Only override if caller didn't explicitly pick via config
                # We check if BrowserSessionManager was constructed with default
                # vs explicit – here we just honor env
                self.browser = normalize_browser(env_b)

        with self._lock:
            if self.opening_browser or self.opened_browser:
                logger.debug("Browser open requested but already opening/opened")
                return False
            self.opening_browser = True

        # macOS pre-flight hints
        if is_macos():
            ok, msg = check_macos_permissions()
            if not ok:
                logger.warning("macOS permission check: %s", msg)
            else:
                logger.debug("macOS permissions: %s", msg)
            _, hint = check_macos_chromedriver()
            logger.info(hint)

        # Create driver via factory (or legacy fallback)
        driver = None
        if create_driver is not None:
            try:
                driver = create_driver(self.browser, headless=False)
            except Exception as e:
                import traceback
                diag = traceback.format_exc()
                logger.error("Failed to create %s driver: %s\n%s", self.browser, e, diag)
                with self._lock:
                    self.opening_browser = False
                return False
        else:
            # Legacy Chrome-only fallback (should not happen after factory added)
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service as ChromeService
            from webdriver_manager.chrome import ChromeDriverManager
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
                        sibling = os.path.join(os.path.dirname(norm), "chromedriver.exe" if platform.system()=="Windows" else "chromedriver")
                        if os.path.exists(sibling):
                            chromedriver_path = sibling
                    logger.info("Using chromedriver: %s", chromedriver_path)
                    service = ChromeService(executable_path=chromedriver_path)
                    driver = webdriver.Chrome(service=service, options=options)
                else:
                    raise RuntimeError("ChromeDriverManager returned empty path")
            except Exception as wdm_error:
                logger.warning("webdriver-manager failed (%s), trying Selenium Manager fallback", wdm_error)
                try:
                    if service is not None:
                        try:
                            service.stop()
                        except Exception:
                            pass
                    driver = webdriver.Chrome(options=options)
                    logger.info("Selenium Manager fallback succeeded")
                except Exception as sm_error:
                    import traceback
                    diag = traceback.format_exc()
                    logger.error("Selenium Manager also failed: %s\n%s", sm_error, diag)
                    with self._lock:
                        self.opening_browser = False
                    return False

        if driver is None:
            logger.error("Driver is None after creation attempts for %s", self.browser)
            with self._lock:
                self.opening_browser = False
            return False

        try:
            target = "https://www.chess.com" if website == "chesscom" else "https://www.lichess.org"
            driver.get(target)
            # Extract executor URL / session id
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
            except Exception as e:
                logger.debug("chrome_url fallback error: %s", e)
                try:
                    chrome_url = driver.command_executor._url
                except Exception:
                    chrome_url = ""
            chrome_session_id = getattr(driver, "session_id", None)
            with self._lock:
                self.chrome = driver
                self.driver = driver
                self.chrome_url = chrome_url
                self.chrome_session_id = chrome_session_id
                self.opening_browser = False
                self.opened_browser = True
            logger.info("Browser opened (%s): %s session=%s", self.browser, chrome_url, chrome_session_id)
            return True
        except Exception as e:
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
            _ = self.chrome.current_url  # type: ignore
            return True
        except WebDriverException as e:
            msg = str(e).lower()
            if "invalid session" in msg or "disconnected" in msg or "no such window" in msg:
                return False
            logger.debug("is_alive check WebDriverException: %s", e)
            return True
        except Exception as e:
            logger.debug("is_alive unexpected: %s", e)
            return False

    def check_driver_log_for_close(self) -> bool:
        """Return True if driver log indicates window closed."""
        if not self.opened_browser or self.chrome is None:
            return False
        try:
            logs = self.chrome.get_log("driver")  # type: ignore
            if logs and "target window already closed" in logs[-1].get("message", ""):
                return True
        except (WebDriverException, AttributeError) as e:
            logger.debug("get_log check failed: %s", e)
        except Exception as e:
            logger.debug("check_driver_log unexpected: %s", e)
        return False

    def close(self):
        with self._lock:
            if self.chrome is not None:
                try:
                    self.chrome.quit()  # type: ignore
                except Exception as e:
                    logger.debug("chrome.quit error: %s", e)
                self.chrome = None
                self.driver = None
            self.chrome_url = None
            self.chrome_session_id = None
            self.opened_browser = False
            self.opening_browser = False
        try:
            logger.info("BrowserSessionManager closed")
        except ValueError:
            pass  # stream closed at interpreter shutdown (pytest atexit)

    # context manager support
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
