import logging
import os
import sys
import time
import platform
import functools
from selenium.webdriver.remote.webdriver import WebDriver
from selenium import webdriver
from selenium.common.exceptions import (
    StaleElementReferenceException,
    NoSuchElementException,
    WebDriverException,
    TimeoutException,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name="chess-x", log_file="logs/chess-x.log", max_bytes=5*1024*1024, backup_count=3):
    """Structured logging to logs/chess-x.log with rotation; console INFO, file DEBUG.
    Replaces previous bare print(e) in overlay.py:283 & stockfish_bot.py:355. Supports --verbose flag via LOGLEVEL env."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    # allow verbose via env or --verbose-like flag
    verbose = os.environ.get("CHES_X_VERBOSE") or ("--verbose" in sys.argv)
    level = logging.DEBUG if verbose else logging.DEBUG
    logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Console handler (INFO, or DEBUG if verbose)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    # File handler with rotation (P1 Logging polish)
    try:
        os.makedirs(os.path.dirname(log_file) if os.path.dirname(log_file) else ".", exist_ok=True)
        try:
            from logging.handlers import RotatingFileHandler
            fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        except Exception:
            fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception:
        pass  # file logging is best-effort
    # avoid duplicate propagation to root
    logger.propagate = False
    return logger


logger = get_logger()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def char_to_num(char):
    """Converts a chess character into an int. Examples: a -> 1, b -> 2, h -> 8"""
    return ord(char) - ord("a") + 1


def is_wayland():
    """Detect if running under Wayland (PyAutoGUI / overlay unsupported)."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def check_linux_input_permissions():
    """Return (ok:bool, message:str) describing keyboard/input group status on Linux."""
    if platform.system() != "Linux":
        return True, "not linux"
    # keyboard library needs read access to /dev/input or sudo
    has_input_group = False
    try:
        import grp
        import getpass
        user = getpass.getuser()
        groups = [g.gr_name for g in grp.getgrall() if user in g.gr_mem]
        # also check primary group
        try:
            import pwd
            gid = pwd.getpwnam(user).pw_gid
            groups.append(grp.getgrgid(gid).gr_name)
        except Exception:
            pass
        has_input_group = "input" in groups
    except Exception:
        pass
    is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
    if is_root or has_input_group:
        return True, "ok"
    return False, (
        "User not in 'input' group and not running as root. "
        "Keyboard hotkeys (1/2/3) may fail. Fix: sudo usermod -aG input $USER then re-login, "
        "or run with sudo. Falling back to pynput if available."
    )


def get_keyboard_handler():
    """Try to return a keyboard handler. Falls back from `keyboard` to `pynput`."""
    # Try `keyboard` first
    try:
        import keyboard as kb
        # quick sanity check – on Linux this may still fail without perms
        return kb, "keyboard"
    except Exception as e:
        logger.warning("keyboard import failed (%s), trying pynput fallback", e)
    try:
        from pynput import keyboard as pkb  # noqa: F401
        # Wrap pynput to expose is_pressed-like API
        class PynputWrapper:
            def __init__(self):
                from pynput.keyboard import Listener, Key, KeyCode
                self._pressed = set()
                self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
                self._listener.daemon = True
                self._listener.start()
                self.Key = Key
                self.KeyCode = KeyCode

            def _on_press(self, key):
                try:
                    self._pressed.add(key)
                except Exception:
                    pass

            def _on_release(self, key):
                try:
                    self._pressed.discard(key)
                except Exception:
                    pass

            def is_pressed(self, key_str):
                # key_str like "1","2","3"
                from pynput.keyboard import KeyCode
                kc = KeyCode.from_char(key_str)
                return kc in self._pressed

        return PynputWrapper(), "pynput"
    except Exception as e2:
        logger.error("No keyboard backend available (keyboard & pynput both failed): %s", e2)
        return None, None


# ---------------------------------------------------------------------------
# Retry with exponential backoff for stale / detached DOM
# ---------------------------------------------------------------------------

def retry_on_stale(max_retries=3, base_delay=0.15, exceptions=(StaleElementReferenceException,)):
    """Decorator: retry function on stale/detached DOM with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_retries:
                        break
                    logger.debug("Stale/retry %s attempt %d/%d: %s", func.__name__, attempt + 1, max_retries, e)
                    time.sleep(delay)
                    delay *= 2  # exponential backoff
                except (NoSuchElementException, WebDriverException) as e:
                    # Also retry on transient WebDriver errors, but fewer times
                    last_exc = e
                    if attempt >= 1:
                        break
                    logger.debug("Transient WebDriver retry %s: %s", func.__name__, e)
                    time.sleep(delay)
            if last_exc:
                raise last_exc
            return None
        return wrapper
    return decorator


def with_retry(max_retries=3, base_delay=0.15):
    """Inline retry helper for ad-hoc blocks."""
    def _retry(fn, *a, **kw):
        delay = base_delay
        last = None
        for i in range(max_retries + 1):
            try:
                return fn(*a, **kw)
            except (StaleElementReferenceException, NoSuchElementException, WebDriverException) as e:
                last = e
                if i == max_retries:
                    raise
                time.sleep(delay)
                delay *= 2
        if last:
            raise last
    return _retry


# ---------------------------------------------------------------------------
# WebDriver helpers – resilient waiting / fallback chain
# ---------------------------------------------------------------------------

def wait_for_any_element(driver, selectors, timeout=8):
    """Try each (By, value) selector in order until one is found via WebDriverWait. Returns WebElement or None."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    for by, value in selectors:
        try:
            elem = WebDriverWait(driver, timeout / max(len(selectors), 1)).until(
                EC.presence_of_element_located((by, value))
            )
            logger.debug("Selector success: %s=%s", by, value)
            return elem
        except (TimeoutException, NoSuchElementException):
            logger.debug("Selector miss: %s=%s", by, value)
            continue
        except StaleElementReferenceException:
            time.sleep(0.1)
            continue
    return None


def find_element_with_fallback(driver, selectors, timeout=6):
    """Try selectors sequentially (CSS/XPATH) without explicit wait – fast fallback chain."""
    for by, value in selectors:
        try:
            elem = driver.find_element(by, value)
            if elem:
                return elem
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    # Fallback to wait if nothing found quickly
    return wait_for_any_element(driver, selectors, timeout=timeout)


# ---------------------------------------------------------------------------
# attach_to_session – Selenium 4.9 → 4.49 compatible
# ---------------------------------------------------------------------------

def attach_to_session(executor_url, session_id):
    """Attaches to a running webdriver. Compatible with Selenium 4.9 .. 4.49.

    The old implementation used desired_capabilities={} which is removed in newer
    Selenium. We try the modern `options` path first, then fall back to the
    legacy capabilities path.
    Returns the webdriver.
    Taken from https://stackoverflow.com/a/48194907/5868441 (adapted).
    """
    original_execute = WebDriver.execute

    def new_command_execute(self, command, params=None):
        if command == "newSession":
            return {"success": 0, "value": None, "sessionId": session_id}
        else:
            return original_execute(self, command, params)

    WebDriver.execute = new_command_execute
    driver = None
    try:
        # Preferred modern path: pass options
        try:
            options = webdriver.ChromeOptions()
            driver = webdriver.Remote(command_executor=executor_url, options=options)
        except TypeError as e:
            logger.debug("Remote(options=) failed (%s), trying desired_capabilities fallback", e)
            # Legacy fallback (Selenium <4.10)
            driver = webdriver.Remote(command_executor=executor_url, desired_capabilities={})
        driver.session_id = session_id
    finally:
        WebDriver.execute = original_execute

    return driver
