"""Browser factory – abstract webdriver creation for Chrome/Firefox/Edge.

Supports:
  - chrome  (ChromeDriver via webdriver-manager or Selenium Manager)
  - firefox (GeckoDriver)
  - edge    (msedgedriver)

Wayland / macOS / X11 handled uniformly. On macOS, quarantine / arch
hints are logged. On Wayland, Chrome gets --ozone-platform-hint=x11.

The factory is used by BrowserSessionManager; direct use is also allowed:

    from browser_factory import create_driver
    driver = create_driver("firefox", headless=False)

Selenium 4.15+ is required. webdriver-manager is optional (fallback to
Selenium Manager if not installed).

Env vars:
  CHESSX_BROWSER  – default browser if not passed (chrome|firefox|edge)
  CHROME_BIN / FIREFOX_BIN / EDGE_BIN – binary overrides
"""

from __future__ import annotations

import os
import platform
import shutil
from typing import Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import WebDriverException

try:
    from platform_info import get_chrome_args_for_platform, is_macos, is_wayland
except Exception:
    def get_chrome_args_for_platform(browser="chrome"):  # type: ignore
        return []
    def is_macos():  # type: ignore
        return platform.system() == "Darwin"
    def is_wayland():  # type: ignore
        return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))

from utilities import get_logger

logger = get_logger("browser_factory")

SUPPORTED_BROWSERS = ("chrome", "firefox", "edge", "chromium")

BROWSER_ALIASES = {
    "chromium": "chrome",
    "google-chrome": "chrome",
    "chrome": "chrome",
    "firefox": "firefox",
    "ff": "firefox",
    "gecko": "firefox",
    "edge": "edge",
    "msedge": "edge",
    "msedgedriver": "edge",
}

def normalize_browser(name: Optional[str]) -> str:
    """Normalize user input to canonical browser id."""
    if not name:
        name = os.environ.get("CHESSX_BROWSER", "chrome")
    name = str(name).strip().lower()
    return BROWSER_ALIASES.get(name, name if name in SUPPORTED_BROWSERS else "chrome")


def get_available_browsers() -> list[str]:
    """Return browsers for which a binary or driver is plausibly available."""
    avail = []
    # We consider a browser available if either webdriver-manager can fetch it
    # or Selenium Manager can. For now we report all as available and let
    # create_driver fail with a clear error; we do probe binary existence for UX.
    for b in ("chrome", "firefox", "edge"):
        avail.append(b)
    return avail

# ------------------------------------------------------------------
# Options builders
# ------------------------------------------------------------------

def _build_chrome_options(headless: bool = False) -> webdriver.ChromeOptions:
    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("useAutomationExtension", False)
    # Platform-specific args
    for a in get_chrome_args_for_platform("chrome"):
        opts.add_argument(a)
    if headless:
        opts.add_argument("--headless=new")
    # Binary override
    bin_path = os.environ.get("CHROME_BIN") or os.environ.get("CHROMIUM_BIN")
    if bin_path and os.path.exists(bin_path):
        opts.binary_location = bin_path
        logger.info("Using Chrome binary override: %s", bin_path)
    return opts


def _build_firefox_options(headless: bool = False) -> webdriver.FirefoxOptions:
    opts = webdriver.FirefoxOptions()
    # Reduce automation fingerprint
    opts.set_preference("dom.webdriver.enabled", False)
    opts.set_preference("useAutomationExtension", False)
    if headless:
        opts.add_argument("--headless")
    bin_path = os.environ.get("FIREFOX_BIN")
    if bin_path and os.path.exists(bin_path):
        opts.binary_location = bin_path
        logger.info("Using Firefox binary override: %s", bin_path)
    return opts


def _build_edge_options(headless: bool = False) -> webdriver.EdgeOptions:
    opts = webdriver.EdgeOptions()
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("useAutomationExtension", False)
    for a in get_chrome_args_for_platform("edge"):
        # Edge is Chromium-based, same args apply (sans ozone hint duplicates)
        if a not in opts.arguments:
            opts.add_argument(a)
    if headless:
        opts.add_argument("--headless=new")
    bin_path = os.environ.get("EDGE_BIN") or os.environ.get("MSEDGE_BIN")
    if bin_path and os.path.exists(bin_path):
        opts.binary_location = bin_path
        logger.info("Using Edge binary override: %s", bin_path)
    return opts

# ------------------------------------------------------------------
# Driver creation with webdriver-manager -> Selenium Manager fallback
# ------------------------------------------------------------------

def _create_chrome(headless: bool = False):
    opts = _build_chrome_options(headless=headless)
    # Try webdriver-manager first
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service as ChromeService
        import os as _os
        raw = ChromeDriverManager().install()
        path = raw
        # Fixup for webdriver-manager returning license file path on some versions
        if path:
            norm = _os.path.normpath(path)
            if norm.lower().endswith(("third_party_notices.chromedriver", "license.chromedriver")) or not norm.lower().endswith(("chromedriver.exe", "chromedriver")):
                sibling = _os.path.join(_os.path.dirname(norm), "chromedriver.exe" if platform.system() == "Windows" else "chromedriver")
                if _os.path.exists(sibling):
                    logger.info("Fixing ChromeDriver path %s -> %s", path, sibling)
                    path = sibling
                else:
                    # Walk search
                    search_root = _os.path.dirname(norm)
                    for _ in range(3):
                        if not search_root or search_root == _os.path.dirname(search_root):
                            break
                        for root, _, files in _os.walk(search_root):
                            target = "chromedriver.exe" if platform.system() == "Windows" else "chromedriver"
                            if target in files:
                                found = _os.path.join(root, target)
                                logger.info("Found chromedriver via walk: %s", found)
                                path = found
                                search_root = ""
                                break
                        if path != raw and _os.path.exists(path):
                            break
                        search_root = _os.path.dirname(search_root)
        if path and _os.path.exists(path):
            logger.info("Using chromedriver: %s", path)
            # macOS quarantine hint
            if is_macos():
                logger.info("macOS: if driver fails with quarantine, run: xattr -d com.apple.quarantine '%s'", path)
            service = ChromeService(executable_path=path)
            return webdriver.Chrome(service=service, options=opts)
        raise RuntimeError("ChromeDriverManager returned empty/invalid path")
    except Exception as wdm_err:
        logger.warning("webdriver-manager Chrome failed (%s), trying Selenium Manager fallback", wdm_err)
        # Selenium Manager fallback – no service path
        return webdriver.Chrome(options=opts)


def _create_firefox(headless: bool = False):
    opts = _build_firefox_options(headless=headless)
    try:
        from webdriver_manager.firefox import GeckoDriverManager
        from selenium.webdriver.firefox.service import Service as FirefoxService
        raw = GeckoDriverManager().install()
        path = raw
        if path and os.path.exists(path):
            logger.info("Using geckodriver: %s", path)
            if is_macos():
                logger.info("macOS: if geckodriver fails with quarantine, run: xattr -d com.apple.quarantine '%s'", path)
            service = FirefoxService(executable_path=path)
            return webdriver.Firefox(service=service, options=opts)
        raise RuntimeError("GeckoDriverManager returned empty path")
    except Exception as wdm_err:
        logger.warning("webdriver-manager Firefox failed (%s), trying Selenium Manager fallback", wdm_err)
        return webdriver.Firefox(options=opts)


def _create_edge(headless: bool = False):
    opts = _build_edge_options(headless=headless)
    try:
        from webdriver_manager.microsoft import EdgeChromiumDriverManager
        from selenium.webdriver.edge.service import Service as EdgeService
        raw = EdgeChromiumDriverManager().install()
        path = raw
        if path and os.path.exists(path):
            logger.info("Using msedgedriver: %s", path)
            service = EdgeService(executable_path=path)
            return webdriver.Edge(service=service, options=opts)
        raise RuntimeError("EdgeChromiumDriverManager returned empty path")
    except Exception as wdm_err:
        logger.warning("webdriver-manager Edge failed (%s), trying Selenium Manager fallback", wdm_err)
        return webdriver.Edge(options=opts)


def create_driver(browser: str = "chrome", headless: bool = False, **kwargs):
    """Create a WebDriver for the requested browser.

    Args:
        browser: chrome | firefox | edge (aliases accepted)
        headless: run headless
        **kwargs: forwarded for future use (e.g., profile)

    Returns:
        WebDriver instance

    Raises:
        ValueError if browser unsupported
        WebDriverException if creation fails
    """
    b = normalize_browser(browser)
    logger.info("Creating %s driver (headless=%s)", b, headless)
    if b == "chrome":
        return _create_chrome(headless=headless)
    elif b == "firefox":
        return _create_firefox(headless=headless)
    elif b == "edge":
        return _create_edge(headless=headless)
    else:
        raise ValueError(f"Unsupported browser '{browser}' – choose from chrome/firefox/edge")


def get_driver_info(driver) -> Tuple[str, str]:
    """Return (browser_name, version) for a live driver, best-effort."""
    try:
        caps = getattr(driver, "capabilities", {}) or {}
        name = caps.get("browserName", "unknown")
        ver = caps.get("browserVersion") or caps.get("version") or ""
        return str(name), str(ver)
    except Exception:
        return "unknown", ""

# ------------------------------------------------------------------
# Attach helper – browser-agnostic attach_to_session
# ------------------------------------------------------------------

def attach_to_session_generic(executor_url: str, session_id: str, browser: str = "chrome"):
    """Attach to existing session for any browser (Chrome/Firefox/Edge).

    Uses same hack as utilities.attach_to_session but passes correct options
    class per browser.
    """
    from selenium.webdriver.remote.webdriver import WebDriver
    original_execute = WebDriver.execute

    def new_command_execute(self, command, params=None):
        if command == "newSession":
            return {"success": 0, "value": None, "sessionId": session_id}
        return original_execute(self, command, params)

    WebDriver.execute = new_command_execute
    driver = None
    b = normalize_browser(browser)
    try:
        if b == "firefox":
            opts = webdriver.FirefoxOptions()
            try:
                driver = webdriver.Remote(command_executor=executor_url, options=opts)
            except TypeError:
                driver = webdriver.Remote(command_executor=executor_url, desired_capabilities={})
        elif b == "edge":
            opts = webdriver.EdgeOptions()
            try:
                driver = webdriver.Remote(command_executor=executor_url, options=opts)
            except TypeError:
                driver = webdriver.Remote(command_executor=executor_url, desired_capabilities={})
        else:
            opts = webdriver.ChromeOptions()
            try:
                driver = webdriver.Remote(command_executor=executor_url, options=opts)
            except TypeError:
                driver = webdriver.Remote(command_executor=executor_url, desired_capabilities={})
        driver.session_id = session_id  # type: ignore
    finally:
        WebDriver.execute = original_execute
    return driver
