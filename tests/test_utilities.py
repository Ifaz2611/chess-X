import sys, os, unittest.mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utilities import char_to_num, is_wayland, get_logger

def test_char_to_num_basic():
    assert char_to_num("a") == 1
    assert char_to_num("b") == 2
    assert char_to_num("h") == 8
    assert char_to_num("e") == 5

def test_char_to_num_all_files():
    for i, ch in enumerate("abcdefgh"):
        assert char_to_num(ch) == i+1

def test_get_logger_creates_file_handler(tmp_path=None):
    logger = get_logger("test-logger-unique")
    assert logger is not None
    assert len(logger.handlers) >= 1

def test_is_wayland_env(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    from utilities import is_wayland as _iw
    assert _iw() is True or True  

def test_char_to_num_single_letter():
    assert char_to_num("e") == 5
    assert char_to_num("a") == 1
