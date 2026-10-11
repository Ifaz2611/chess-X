import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from harness import MockWebDriver

from grabbers.chesscom_grabber import ChesscomGrabber
from grabbers.lichess_grabber import LichessGrabber

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_chesscom_integration_in_progress():
    fixture_path = FIXTURES_DIR / "chesscom.html"
    mock_driver = MockWebDriver.from_file(str(fixture_path), current_url="https://www.chess.com/game/live")

    with patch("grabbers.grabber.attach_to_session", return_value=mock_driver):
        grabber = ChesscomGrabber("http://fake", "fake-session")

        # 1. Health check passes
        ok, details = grabber.health_check()
        assert ok is True
        assert details.get("board") is True

        # Board element detection
        grabber.update_board_elem()
        board = grabber.get_board()
        assert board is not None
        assert board.tag_name == "wc-chess-board"

        # 3. Orientation detection
        assert grabber.is_white() is True

        # 4. Move list extraction
        moves = grabber.get_move_list()
        assert isinstance(moves, list)
        assert len(moves) >= 3
        assert moves[:3] == ["e4", "e5", "Nf3"]

        # 5. Clock extraction
        w_clk, b_clk = grabber.get_clocks()
        assert w_clk == "05:00"
        assert b_clk == "04:45"

        # 6. Game over status (in-progress)
        assert grabber.is_game_over() is False

        # 7. Variant detection
        assert grabber.detect_variant() == "standard"


def test_chesscom_integration_game_over():
    fixture_path = FIXTURES_DIR / "chesscom_gameover.html"
    mock_driver = MockWebDriver.from_file(str(fixture_path), current_url="https://www.chess.com/game/live")

    with patch("grabbers.grabber.attach_to_session", return_value=mock_driver):
        grabber = ChesscomGrabber("http://fake", "fake-session")

        # 1. Board detection
        grabber.update_board_elem()
        assert grabber.get_board() is not None

        # 2. Game over detection
        assert grabber.is_game_over() is True

        # 3. Move list extraction
        moves = grabber.get_move_list()
        assert isinstance(moves, list)
        assert "Qxf7#" in moves or len(moves) >= 6


def test_lichess_integration_in_progress():
    fixture_path = FIXTURES_DIR / "lichess.html"
    mock_driver = MockWebDriver.from_file(str(fixture_path), current_url="https://lichess.org/abcd1234")

    with patch("grabbers.grabber.attach_to_session", return_value=mock_driver):
        grabber = LichessGrabber("http://fake", "fake-session")

        # 1. Health check passes
        ok, details = grabber.health_check()
        assert ok is True

        # Board element detection
        grabber.update_board_elem()
        board = grabber.get_board()
        assert board is not None

        # 3. Orientation detection
        assert grabber.is_white() is True

        # 4. Move list extraction
        moves = grabber.get_move_list()
        assert isinstance(moves, list)
        assert len(moves) >= 4
        assert moves[:4] == ["e4", "e5", "Nf3", "Nc6"]

        # 5. Clock extraction
        w_clk, b_clk = grabber.get_clocks()
        assert w_clk == "03:00"
        assert b_clk == "02:50"

        # 6. Game over status (in-progress)
        assert grabber.is_game_over() is False


def test_lichess_integration_game_over():
    fixture_path = FIXTURES_DIR / "lichess_gameover.html"
    mock_driver = MockWebDriver.from_file(str(fixture_path), current_url="https://lichess.org/abcd1234")

    with patch("grabbers.grabber.attach_to_session", return_value=mock_driver):
        grabber = LichessGrabber("http://fake", "fake-session")

        # 1. Board detection
        grabber.update_board_elem()
        assert grabber.get_board() is not None

        # 2. Game over detection
        assert grabber.is_game_over() is True
