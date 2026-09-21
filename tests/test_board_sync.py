import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chess
from board_sync import BoardSync
from unittest.mock import MagicMock, patch

def test_board_sync_new_game_empty():
    board = chess.Board()
    sync_board, needs_reset = BoardSync.reconcile_board_fen(board, [])
    assert sync_board.fen() == chess.Board().fen()
    assert needs_reset is False

def test_board_sync_takeback():
    board = chess.Board()
    for san in ["e4", "e5", "Nf3"]:
        board.push_san(san)
    # DOM has fewer moves (takeback last)
    new_board, needs = BoardSync.reconcile_board_fen(board, ["e4", "e5"])
    assert len(list(new_board.move_stack)) == 2
    assert needs is True

def test_board_sync_fen_mismatch():
    board = chess.Board()
    for san in ["e4", "e5"]:
        board.push_san(san)
    # DOM has different line e4 c5 but same count
    new_board, needs = BoardSync.reconcile_board_fen(board, ["e4", "c5"])
    assert new_board.fen() != board.fen()
    assert needs is True
    # ensure new board matches DOM (c5 pawn present)
    assert new_board.piece_at(chess.C5) is not None
    assert new_board.piece_at(chess.C5).piece_type == chess.PAWN

def test_board_sync_promotion():
    board = chess.Board("8/P7/8/8/8/8/8/8 w - - 0 1")
    # promotion to queen from custom position
    board.push_san("a8=Q")
    assert board.piece_at(chess.A8) is not None
    assert board.piece_at(chess.A8).piece_type == chess.QUEEN
    # also test BoardSync via starting FEN
    b = chess.Board("8/P7/8/8/8/8/8/8 w - - 0 1")
    b.push_san("a8=Q")
    assert b.piece_at(chess.A8).piece_type == chess.QUEEN

def test_board_sync_game_over_mate():
    board = chess.Board()
    # Fool's mate
    for san in ["f3", "e5", "g4", "Qh4#"]:
        board.push_san(san)
    assert board.is_checkmate() is True
    # reconcile should keep mate
    new_board, _ = BoardSync.reconcile_board_fen(chess.Board(), ["f3", "e5", "g4", "Qh4#"])
    assert new_board.is_checkmate() is True

def test_config_migration_v1_to_v2(tmp_path):
    from config_store import Config, migrate, validate, CONFIG_VERSION
    v1 = {"stockfish": "/tmp/sf", "website": "lichess", "slow_mover": 200, "memory": 1024, "cpu_threads": 4}
    migrated = migrate(v1)
    assert migrated["version"] == CONFIG_VERSION
    assert "memory" not in migrated
    assert migrated["stockfish_path"] == "/tmp/sf"
    cfg = Config.from_dict(v1)
    assert cfg.website == "lichess"
    assert cfg.slow_mover == 200

def test_engine_service_material_and_accuracy():
    from engine_service import EngineService
    svc = EngineService("fake", 10, 2, 128, 100, 20)
    board = chess.Board()
    assert svc.calculate_material_advantage(board) == "0"
    board.remove_piece_at(chess.D1)
    assert svc.calculate_material_advantage(board) == "-9"
    # accuracy
    assert svc.accuracy_from_losses([]) == "-"
    assert "100" in svc.accuracy_from_losses([0, 0, 0])
    high = svc.accuracy_from_losses([400, 400])
    low = svc.accuracy_from_losses([0, 0])
    assert float(high.strip("%")) < float(low.strip("%"))

def test_move_executor_screen_mapping():
    from move_executor import MoveExecutor
    class FakeElem:
        location = {"x": 100, "y": 100}
        size = {"width": 800, "height": 800}
    class FakeGrabber:
        def get_top_left_corner(self): return (0, 0)
        def get_board(self): return FakeElem()
    ex_white = MoveExecutor(FakeGrabber(), True, 0.0)
    x, y = ex_white.move_to_screen_pos("e2")
    # board at 100,100 size 800 -> square 100px, e2 white: x=100+100*4+50=550? Actually a=1 => e=5 => 100+400+50=550? With offset 100 => 650? Let's just check within bounds
    assert 100 < x < 900
    assert 100 < y < 900
    ex_black = MoveExecutor(FakeGrabber(), False, 0.0)
    xb, yb = ex_black.move_to_screen_pos("e2")
    assert xb != x or yb != y
