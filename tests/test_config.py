import sys, os, json, tempfile, pathlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import MagicMock, patch

def test_config_roundtrip(tmp_path, monkeypatch):
    # Create temp config file
    cfg_path = tmp_path / "config.json"
    # Patch _config_path to use tmp
    import gui as gui_module
    # We can't easily instantiate GUI without Tk; test _load_config/_save_config logic via isolated class
    # Create minimal mock GUI object with needed methods

    # Simulate config data
    data = {
        "stockfish_path": "",
        "website": "lichess",
        "enable_manual_mode": True,
        "enable_mouseless_mode": False,
        "enable_non_stop_puzzles": 1,
        "enable_non_stop_matches": 0,
        "enable_bongcloud": 1,
        "mouse_latency": 1.5,
        "slow_mover": 200,
        "skill_level": 10,
        "stockfish_depth": 12,
        "enable_topmost": 0,
    }
    with open(cfg_path, "w") as f:
        json.dump(data, f)

    # Patch os.path.join to return our tmp path for config
    # Instead directly test load via function mocking
    # Create a dummy instance that has _is_valid_stockfish and _config_path
    class Dummy:
        def _is_valid_stockfish(self, p, quick_check=True): return True
        def _config_path(self): return str(cfg_path)

    # Borrow methods from GUI class
    from gui import GUI
    dummy = Dummy()
    # bind methods
    import types
    dummy._load_config = types.MethodType(GUI._load_config, dummy)
    dummy._save_config = types.MethodType(GUI._save_config, dummy)

    loaded = dummy._load_config()
    assert loaded["website"] == "lichess"
    assert loaded["skill_level"] == 10
    assert loaded["mouse_latency"] == 1.5

    # Modify and save
    # Setup vars as dummy attributes with .get() interface
    class Var:
        def __init__(self, v): self._v = v
        def get(self): return self._v
        def set(self, v): self._v = v

    dummy.website = Var("chesscom")
    dummy.enable_manual_mode = Var(False)
    dummy.enable_mouseless_mode = Var(True)
    dummy.enable_non_stop_puzzles = Var(0)
    dummy.enable_non_stop_matches = Var(1)
    dummy.enable_bongcloud = Var(0)
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
    assert loaded["slow_mover"] == 100
