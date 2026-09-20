import sys, os, types, chess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import MagicMock, patch
for _m in ["pyautogui"]:
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)

from stockfish_bot import StockfishBot
from utilities import char_to_num

class FakeStockfish:
    def __init__(self, evals):
        self._evals = evals
        self._idx = 0
    def get_evaluation(self):
        if self._idx < len(self._evals):
            v = self._evals[self._idx]
            self._idx += 1
            return v
        return self._evals[-1]
    def get_wdl_stats(self):
        return [300, 400, 300]
    def set_position(self, *a, **kw): pass
    def make_moves_from_current_position(self, *a, **kw): pass

def _make_bot():
    bot = StockfishBot.__new__(StockfishBot)
    bot.white_cp_losses = []
    bot.black_cp_losses = []
    bot._prev_eval_cp = None
    # need logger
    return bot

def test_eval_to_white_cp_cp():
    bot = _make_bot()
    assert bot._eval_to_white_cp({"type":"cp","value":150}) == 150
    assert bot._eval_to_white_cp({"type":"cp","value":-200}) == -200

def test_eval_to_white_cp_mate():
    bot = _make_bot()
    cp = bot._eval_to_white_cp({"type":"mate","value":3})
    assert cp > 9000
    cp2 = bot._eval_to_white_cp({"type":"mate","value":-2})
    assert cp2 < -9000

def test_cp_loss_to_bucket():
    bot = _make_bot()
    assert bot._cp_loss_to_bucket(5) == "best"
    assert bot._cp_loss_to_bucket(30) == "excellent"
    assert bot._cp_loss_to_bucket(75) == "good"
    assert bot._cp_loss_to_bucket(150) == "inaccuracy"
    assert bot._cp_loss_to_bucket(300) == "mistake"
    assert bot._cp_loss_to_bucket(500) == "blunder"

def test_accuracy_from_losses_empty():
    bot = _make_bot()
    assert bot._accuracy_from_losses([]) == "-"

def test_accuracy_from_losses_perfect():
    bot = _make_bot()
    # 0 loss => 100%
    acc = bot._accuracy_from_losses([0,0,0])
    assert "100" in acc

def test_accuracy_from_losses_high_loss():
    bot = _make_bot()
    acc_low = bot._accuracy_from_losses([0,0,0])
    acc_high = bot._accuracy_from_losses([400,400,400])
    assert float(acc_high.strip("%")) < float(acc_low.strip("%"))

def test_record_cp_loss_white():
    bot = _make_bot()
    bot._prev_eval_cp = 100  
    stock = FakeStockfish([{"type":"cp","value":50}])
    bot._record_cp_loss(stock, True)
    assert bot.white_cp_losses == [50]
    assert bot.black_cp_losses == []
    assert bot._prev_eval_cp == 50

def test_record_cp_loss_black():
    bot = _make_bot()
    bot._prev_eval_cp = 100
    stock = FakeStockfish([{"type":"cp","value":150}])
    bot._record_cp_loss(stock, False)
    assert bot.black_cp_losses == [50]

def test_record_cp_loss_negative_clamped():
    bot = _make_bot()
    bot._prev_eval_cp = 0
    stock = FakeStockfish([{"type":"cp","value":100}])
    bot._record_cp_loss(stock, True)
    assert bot.white_cp_losses == [0]

def test_calculate_material_advantage():
    bot = _make_bot()
    board = chess.Board()
    assert bot.calculate_material_advantage(board) == "0"
    # remove black queen
    board.remove_piece_at(chess.D8)
    assert bot.calculate_material_advantage(board) == "+9"
    board2 = chess.Board()
    board2.remove_piece_at(chess.D1)
    assert bot.calculate_material_advantage(board2) == "-9"

def test_move_to_screen_pos_white():
    # mock grabber and board element
    bot = _make_bot()
    bot.is_white = True
    bot.grabber = unittest_mock_grabber(0, 0, 800, white=True)
    x, y = bot.move_to_screen_pos("e2")
    assert abs(x - 450) < 1
    assert abs(y - 650) < 1

def test_move_to_screen_pos_black():
    bot = _make_bot()
    bot.is_white = False
    bot.grabber = unittest_mock_grabber(0, 0, 800, white=False)
    x, y = bot.move_to_screen_pos("e2")
    assert abs(x - 350) < 1
    assert abs(y - 150) < 1

def unittest_mock_grabber(x, y, board_size, white=True):
    class FakeElem:
        location = {"x": x, "y": y}
        size = {"width": board_size, "height": board_size}
    class FakeGrabber:
        def get_top_left_corner(self): return (0,0)
        def get_board(self): return FakeElem()
    return FakeGrabber()
