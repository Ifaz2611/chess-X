import sys, os, json, tempfile, pathlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import MagicMock, patch

def test_config_roundtrip(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    import gui as gui_module

    data = {
        "stockfish_path": "",
        "website": "lichess",
        "enable_manual_mode": True,
        "enable_mouseless_mode": False,
        "enable_non_stop_puzzles": 1,
        "enable_non_stop_matches": 0,
        "mouse_latency": 1.5,
        "slow_mover": 200,
        "skill_level": 10,
        "stockfish_depth": 12,
        "enable_topmost": 0,
    }
    with open(cfg_path, "w") as f:
        json.dump(data, f)

    class Dummy:
        def _is_valid_stockfish(self, p, quick_check=True): return True
        def _config_path(self): return str(cfg_path)

    from gui import GUI
    dummy = Dummy()
    import types
    dummy._load_config = types.MethodType(GUI._load_config, dummy)
    dummy._save_config = types.MethodType(GUI._save_config, dummy)

    loaded = dummy._load_config()
    assert loaded["website"] == "lichess"
    assert loaded["skill_level"] == 10
    assert loaded["mouse_latency"] == 1.5

    class Var:
        def __init__(self, v): self._v = v
        def get(self): return self._v
        def set(self, v): self._v = v

    dummy.website = Var("chesscom")
    dummy.enable_manual_mode = Var(False)
    dummy.enable_mouseless_mode = Var(True)
    dummy.enable_non_stop_puzzles = Var(0)
    dummy.enable_non_stop_matches = Var(1)
    dummy.mouse_latency = Var(2.0)
    dummy.slow_mover = Var(150)
    dummy.skill_level = Var(15)
    dummy.stockfish_depth = Var(18)
    dummy.enable_topmost = Var(1)
    dummy.stockfish_path = "/tmp/fake_sf"

    dummy._save_config()
    with open(cfg_path) as f:
        saved = json.load(f)
    assert saved["website"] == "chesscom"
    assert saved["mouse_latency"] == 2.0
    assert saved["skill_level"] == 15

def test_config_defaults_when_missing(tmp_path):
    cfg_path = tmp_path / "nonexistent.json"
    from gui import GUI
    class Dummy:
        def _is_valid_stockfish(self, p, quick_check=True): return True
        def _config_path(self): return str(cfg_path)
    import types
    dummy = Dummy()
    dummy._load_config = types.MethodType(GUI._load_config, dummy)
    loaded = dummy._load_config()
    assert loaded["website"] == "chesscom"
    assert loaded["skill_level"] == 20
    assert loaded["slow_mover"] == 60


def test_gui_save_preserves_custom_and_unmapped_fields(tmp_path):
    cfg_path = tmp_path / "config.json"
    initial_data = {
        "version": 3,
        "website": "chesscom",
        "calibration_offset_x": 14.5,
        "calibration_offset_y": -3.5,
        "opening_book_path": "/path/to/book.bin",
        "syzygy_path": "/path/to/syzygy",
    }
    with open(cfg_path, "w") as f:
        json.dump(initial_data, f)

    from gui import GUI
    class Dummy:
        def _is_valid_stockfish(self, p, quick_check=True): return True
        def _config_path(self): return str(cfg_path)

    import types
    dummy = Dummy()
    dummy._load_config = types.MethodType(GUI._load_config, dummy)
    dummy._save_config = types.MethodType(GUI._save_config, dummy)
    dummy._config = dummy._load_config()

    class Var:
        def __init__(self, v): self._v = v
        def get(self): return self._v
        def set(self, v): self._v = v

    dummy.website = Var("lichess")
    dummy._save_config()

    with open(cfg_path) as f:
        saved = json.load(f)

    assert saved["website"] == "lichess"
    assert saved["calibration_offset_x"] == 14.5
    assert saved["calibration_offset_y"] == -3.5
    assert saved["opening_book_path"] == "/path/to/book.bin"
    assert saved["syzygy_path"] == "/path/to/syzygy"
    assert saved["version"] == 3