import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import MagicMock, patch
from selenium.common import NoSuchElementException
from selenium.webdriver.common.by import By


def _make_chesscom(url="https://www.chess.com/game/live/123", page_text=""):
    from grabbers.chesscom_grabber import ChesscomGrabber
    with patch("grabbers.grabber.attach_to_session") as mock_attach:
        fake = MagicMock()
        mock_attach.return_value = fake
        g = ChesscomGrabber("http://fake", "sess")
    g.chrome = fake
    fake.current_url = url
    fake.execute_script.return_value = page_text
    return g, fake


def _elem(text="", displayed=True):
    e = MagicMock()
    e.text = text
    e.is_displayed.return_value = displayed
    return e


def test_result_normalization():
    from grabbers.grabber import Grabber
    assert Grabber.normalize_result_text("1-0") == "1-0"
    assert Grabber.normalize_result_text("0-1") == "0-1"
    assert Grabber.normalize_result_text("1/2-1/2") == "1/2-1/2"
    assert Grabber.normalize_result_text("½-½") == "1/2-1/2"
    assert Grabber.normalize_result_text("e4") is None
    assert Grabber.text_looks_like_game_end("Checkmate! White won") is True
    assert Grabber.text_looks_like_game_end("e4") is False


def test_chesscom_is_game_puzzles_url():
    g, fake = _make_chesscom("https://www.chess.com/puzzles/rush")
    fake.find_element.side_effect = NoSuchElementException("no")
    assert g.is_game_puzzles() is True


def test_chesscom_is_game_puzzles_negative():
    g, fake = _make_chesscom("https://www.chess.com/game/live/123", page_text="live game")
    fake.find_element.side_effect = NoSuchElementException("no")
    assert g.is_game_puzzles() is False


def test_chesscom_is_game_over_modal_with_score():
    g, fake = _make_chesscom(page_text="You won 1-0")
    modal = _elem("You won! 1-0", True)
    def fe(by, val):
        if "board-modal-container" in val:
            return modal
        raise NoSuchElementException("no")
    fake.find_element.side_effect = fe
    assert g.is_game_over() is True


def test_chesscom_is_game_over_negative():
    g, fake = _make_chesscom(page_text="e4 c5 Nf3")
    fake.find_element.side_effect = NoSuchElementException("no")
    assert g.is_game_over() is False


def test_chesscom_click_game_next_resets():
    g, fake = _make_chesscom()
    btn = _elem("Rematch", True)
    fake.find_element.return_value = btn
    fake.execute_script.return_value = None
    g.moves_list = {"1": "e4"}
    assert g.click_game_next() is True
    assert g.moves_list == {}


def test_chesscom_click_puzzle_next():
    g, fake = _make_chesscom("https://www.chess.com/puzzles/1")
    btn = _elem("Next", True)
    fake.find_element.return_value = btn
    assert g.click_puzzle_next() is True


def test_chesscom_takeback_rebuild():
    g, fake = _make_chesscom()
    # cached 3 moves, DOM now shows 2 -> takeback
    g.moves_list = {"1": "e4", "2": "e5", "3": "Nf3"}
    container = MagicMock()
    n1 = MagicMock(); n1.get_attribute.side_effect = lambda k: "node white-move" if k == "class" else "1"
    n1.text = "e4"
    n1.find_element.side_effect = NoSuchElementException("no")
    n2 = MagicMock(); n2.get_attribute.side_effect = lambda k: "node black-move" if k == "class" else "2"
    n2.text = "e5"
    n2.find_element.side_effect = NoSuchElementException("no")
    container.find_elements.return_value = [n1, n2]
    fake.find_element.return_value = container
    fake.execute_script.return_value = None
    moves = g.get_move_list()
    assert moves == ["e4", "e5"]


def test_chesscom_mouseless_invalid_uci():
    g, fake = _make_chesscom()
    try:
        g.make_mouseless_move("bad", 1)
        assert False, "should raise"
    except ValueError:
        pass


def test_chesscom_mouseless_component_api():
    g, fake = _make_chesscom()
    board_elem = MagicMock(tag_name="wc-chess-board")
    g.get_board = MagicMock(return_value=board_elem)
    fake.execute_script.return_value = "component-game.move"
    assert g.make_mouseless_move("e2e4", 1) is True


def test_variant_detection_and_boards():
    from board_sync import BoardSync
    assert BoardSync.normalize_variant("Chess960") == "chess960"
    assert BoardSync.normalize_variant("3-check") == "threecheck"
    assert BoardSync.normalize_variant("King of the Hill") == "kingofthehill"
    assert BoardSync.is_variant_supported("chess960") is True
    assert BoardSync.is_variant_supported("crazyhouse") is False
    b = BoardSync.create_board("standard")
    import chess
    assert b.fen() == chess.Board().fen()
    b960 = BoardSync.create_board("chess960")
    assert isinstance(b960, chess.Board)
    # takeback trim helper
    wm, wb, bm, bb, wl, bl = BoardSync.trim_history_to_ply([1, 2, 3], [1, 2, 3], [1, 2], [1, 2], [10, 20, 30], [5, 15], 3)
    assert len(wm) == 2 and len(bm) == 1 and len(wl) == 2 and len(bl) == 1


def test_lichess_result_filter_and_variant():
    from grabbers.lichess_grabber import LichessGrabber
    with patch("grabbers.grabber.attach_to_session") as mock_attach:
        fake = MagicMock()
        mock_attach.return_value = fake
        g = LichessGrabber("http://fake", "sess")
    g.chrome = fake
    fake.current_url = "https://lichess.org/training/123"
    fake.find_element.return_value = _elem("puzzle", True)
    assert g.is_game_puzzles() is True
    fake.current_url = "https://lichess.org/variant/kingOfTheHill"
    fake.execute_script.return_value = ""
    assert g.detect_variant() == "kingofthehill"
