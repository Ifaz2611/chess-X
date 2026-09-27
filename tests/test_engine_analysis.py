import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import MagicMock, patch

import chess

# ---------- Opening book ----------

def test_book_disabled_without_path():
    from opening_book import OpeningBook
    book = OpeningBook(None)
    assert book.enabled is False
    assert book.pick_move(chess.Board()) is None
    book2 = OpeningBook("", max_plies=30)
    assert book2.pick_move_uci(chess.Board()) is None


def test_book_missing_file_disables():
    from opening_book import OpeningBook
    book = OpeningBook("/nonexistent/book.bin", max_plies=30)
    assert book.pick_move(chess.Board()) is None


def test_book_max_plies_cutoff():
    from opening_book import OpeningBook
    book = OpeningBook("/nonexistent/book.bin", max_plies=0)
    assert book.enabled is False
    assert book.pick_move(chess.Board()) is None


def _mock_entry(uci, weight):
    entry = MagicMock()
    entry.weight = weight
    entry.move = chess.Move.from_uci(uci)
    return entry


def test_book_weighted_pick_with_mock_reader():
    from opening_book import OpeningBook
    book = OpeningBook("dummy.bin", max_plies=30, seed=1)
    fake_reader = MagicMock()
    fake_reader.find_all.return_value = [_mock_entry("e2e4", 10), _mock_entry("d2d4", 90)]
    book._reader = fake_reader
    mv = book.pick_move(chess.Board())
    assert mv in (chess.Move.from_uci("e2e4"), chess.Move.from_uci("d2d4"))
    assert book.pick_move_uci(chess.Board()) in ("e2e4", "d2d4")


def test_book_ignores_illegal_entries():
    from opening_book import OpeningBook
    book = OpeningBook("dummy.bin", max_plies=30, seed=0)
    fake_reader = MagicMock()
    fake_reader.find_all.return_value = [_mock_entry("e2e5", 100)]  # illegal
    book._reader = fake_reader
    assert book.pick_move(chess.Board()) is None


# ---------- Tablebase ----------

def test_piece_count_and_skip_when_too_many():
    from tablebase import TablebaseProbe, piece_count
    assert piece_count(chess.Board()) == 32
    probe = TablebaseProbe(syzygy_path=None, use_api=False)
    assert probe.probe(chess.Board()) is None  # no backends


def test_tablebase_local_missing_dir():
    from tablebase import TablebaseProbe
    probe = TablebaseProbe(syzygy_path="/nonexistent/syzygy", use_api=False)
    endgame = chess.Board("8/8/8/8/8/3k4/8/3K4 w - - 0 1")
    assert probe.probe_local(endgame) is None


def test_tablebase_api_capped_by_pieces():
    from tablebase import TablebaseProbe
    probe = TablebaseProbe(syzygy_path=None, use_api=True, max_pieces=7)
    assert probe.probe_api(chess.Board()) is None  # 32 pieces, no network


def test_tablebase_api_parses_best_move():
    from tablebase import TablebaseProbe
    probe = TablebaseProbe(syzygy_path=None, use_api=True)
    board = chess.Board("8/8/8/8/8/1k6/8/1K6 w - - 0 1")
    assert chess.Move.from_uci("b1c1") in board.legal_moves
    payload = {"wdl": 2, "dtz": 5, "moves": {
        "b1c1": {"uci": "b1c1", "san": "Kc1", "wdl": 2, "dtz": 3},
        "b1a1": {"uci": "b1a1", "san": "Ka1", "wdl": 1, "dtz": 10},
    }}
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps(payload).encode()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    with patch("tablebase.urllib.request.urlopen", return_value=fake_resp):
        hit = probe.probe_api(board)
    assert hit is not None
    assert hit["best_move"] == "b1c1"
    assert hit["source"] == "lichess-api"


def test_tablebase_api_rejects_illegal_suggestion():
    from tablebase import TablebaseProbe
    probe = TablebaseProbe(syzygy_path=None, use_api=True)
    board = chess.Board("8/8/8/8/8/3k4/8/3K4 w - - 0 1")
    assert chess.Move.from_uci("d1c1") in board.legal_moves
    payload = {"moves": {"e2e4": {"uci": "e2e4", "wdl": 2, "dtz": 1}}}
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps(payload).encode()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    with patch("tablebase.urllib.request.urlopen", return_value=fake_resp):
        assert probe.probe_api(board) is None


# ---------- PGN export ----------

def test_pgn_headers_and_movetext():
    from pgn_export import build_pgn, default_headers
    sans = ["e4", "c5", "Nf3", "Nc6"]
    headers = default_headers(site="chess.com", white="Me", black="Opp",
                              result="*", time_control="180+2")
    text = build_pgn(sans, headers=headers, result="*")
    assert '[Site "chess.com"]' in text
    assert '[Date "' in text
    assert '[White "Me"]' in text
    assert '[Black "Opp"]' in text
    assert '[Result "*"]' in text
    assert '[TimeControl "180+2"]' in text
    assert "1. e4 c5 2. Nf3 Nc6 *" in text


def test_pgn_check_mate_suffix_and_nags():
    import chess

    from pgn_export import board_result, build_pgn, sans_from_board
    board = chess.Board()
    for san in ["f3", "e5", "g4", "Qh4#"]:
        board.push_san(san)
    sans = sans_from_board(board)
    assert sans[-1] == "Qh4#"
    assert board_result(board) == "0-1"
    text = build_pgn(sans, nags=[None, None, None, 4], result="0-1")
    assert "Qh4# $4 0-1" in text


def test_pgn_nags_from_losses():
    from pgn_export import NAG_BLUNDER, NAG_DUBIOUS, NAG_MISTAKE, nags_from_losses
    nags = nags_from_losses([500], [300, 150])
    assert nags[0] == NAG_BLUNDER
    assert nags[1] == NAG_MISTAKE
    assert nags[2] == NAG_DUBIOUS


def test_pgn_unique_and_autosave(tmp_path):
    from pgn_export import auto_save_game, unique_pgn_path
    p1 = unique_pgn_path(str(tmp_path))
    with open(p1, "w") as fh:
        fh.write("x")
    p2 = unique_pgn_path(str(tmp_path))
    assert p1 != p2
    saved = auto_save_game(str(tmp_path), ["e4", "e5"], result="*")
    assert saved and os.path.exists(saved)
    with open(saved) as fh:
        assert "1. e4 e5" in fh.read()
    assert auto_save_game(str(tmp_path), [], result="*") is None


def test_pgn_board_result_draw():
    from pgn_export import board_result
    board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")  # stalemate for black
    assert board.is_stalemate()
    assert board_result(board) == "1/2-1/2"
    assert board_result(chess.Board()) == "*"


# ---------- EngineService: book / tablebase / PV ----------

def test_engine_service_book_disabled_by_default():
    from engine_service import EngineService
    svc = EngineService("fake", 10, 2, 128, 100, 20)
    assert svc.get_book_move(chess.Board()) is None


def test_engine_service_tablebase_no_backend():
    from engine_service import EngineService
    svc = EngineService("fake", 10, 2, 128, 100, 20,
                        syzygy_path=None, tablebase_api=False)
    assert svc.probe_tablebase(chess.Board("8/8/8/8/8/3k4/8/3K4 w - - 0 1")) is None


def test_engine_service_top_moves_and_pv_text():
    from engine_service import EngineService
    svc = EngineService("fake", 10, 2, 128, 100, 20)
    fake_sf = MagicMock()
    fake_sf.get_top_moves.return_value = [
        {"Move": "e2e4", "Centipawn": 35, "Mate": None},
        {"Move": "d2d4", "Centipawn": None, "Mate": 3},
        {"NotAMove": True},
    ]
    top = svc.get_top_moves(fake_sf, num_moves=3)
    assert len(top) == 2
    board = chess.Board()
    pvs = svc.top_moves_to_pv(board, top)
    assert pvs[0]["san"] == "e4"  # SAN, no check suffix at startpos
    text = svc.format_pv_text(pvs)
    assert "e4" in text and "+0.35" in text and "M3" in text


def test_engine_service_threat_move():
    import chess

    from engine_service import EngineService
    svc = EngineService("fake", 10, 2, 128, 100, 20)
    board = chess.Board()
    seen = []
    fake_sf = MagicMock()
    def _setpos(moves):
        seen.append(list(moves))
    fake_sf.set_position.side_effect = _setpos
    fake_sf.get_best_move_time.return_value = "e7e5"
    threat = svc.threat_move(board, fake_sf)
    assert threat == "e7e5"
    # position restored to the real board (empty stack)
    assert seen[-1] == []


# ---------- Protocol: MsgPV / OverlayLines ----------

def test_protocol_pv_roundtrip():
    import protocol as proto
    msg = proto.MsgPV(pv_text="1. e4 (+0.30)", threat_san="c5")
    assert proto.is_typed_pipe_msg(msg)
    legacy = proto.bot_msg_to_legacy(msg)
    assert legacy.startswith("PV|")
    back = proto.legacy_to_bot_msg(legacy)
    assert isinstance(back, proto.MsgPV)
    assert back.pv_text == "1. e4 (+0.30)" and back.threat_san == "c5"


def test_protocol_overlay_lines_roundtrip():
    import protocol as proto
    msg = proto.OverlayLines(lines=[{"uci": "e2e4", "san": "e4"}], threat_uci="c7c5")
    legacy = proto.overlay_to_legacy(msg)
    assert legacy["threat_uci"] == "c7c5"
    back = proto.legacy_to_overlay_msg(legacy)
    assert isinstance(back, proto.OverlayLines)
    assert back.lines[0]["uci"] == "e2e4"


# ---------- LocalBoard: FEN/PGN/step-through/self-play ----------

def test_localboard_fen_and_step_through():
    from local_board import LocalBoard
    lb = LocalBoard()
    assert lb.load_fen(chess.STARTING_FEN)
    assert lb.push_san("e4") and lb.push_san("e5")
    assert lb.total_plies == 2 and lb.ply == 2
    assert lb.step_back() and lb.ply == 1
    assert lb.board.fen() == chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1").fen()
    assert lb.step_forward() and lb.ply == 2
    assert lb.goto_ply(0) and lb.ply == 0
    assert lb.board.fen() == chess.Board().fen()
    assert lb.step_back() is False  # already at start


def test_localboard_load_pgn_and_eval_no_engine():
    from local_board import LocalBoard
    lb = LocalBoard()
    assert lb.load_pgn('1. e4 e5 2. Nf3 *\n') is True
    assert lb.total_plies == 3
    ev = lb.current_eval()
    assert ev["engine"] is False and ev["eval_type"] == "cp"
    assert lb.load_pgn("not a game at all $$$") is False
    assert lb.self_play(max_plies=10) == []  # no engine


def test_localboard_to_pgn_headers():
    from local_board import LocalBoard
    lb = LocalBoard()
    lb.push_san("e4")
    text = lb.to_pgn()
    assert '[Site "local"]' in text and "1. e4" in text


# ---------- Analysis ----------

def test_analysis_invalid_pgn():
    from analysis import eval_game
    out = eval_game("definitely not pgn \x00\x01", "/nonexistent/sf", depth=5)
    assert "error" in out or out.get("moves") == []


def test_analysis_no_engine_returns_moves():
    from analysis import eval_game
    out = eval_game("1. e4 e5 *\n", "/nonexistent/sf", depth=5)
    assert out["moves"] == ["e2e4", "e7e5"]
    assert out["sans"] == ["e4", "e5"]


def test_analysis_position_invalid_fen():
    from analysis import analyze_position
    out = analyze_position("bogus", "/nonexistent/sf")
    assert "error" in out


def test_analysis_position_no_engine():
    from analysis import analyze_position
    out = analyze_position(chess.STARTING_FEN, "/nonexistent/sf")
    assert out["error"].startswith("no engine")


# ---------- Config ----------

def test_config_engine_defaults_and_clamps():
    from config_store import Config, validate
    cfg = Config.defaults()
    assert cfg.opening_book_path == ""
    assert cfg.book_max_plies == 30
    assert cfg.tablebase_api is True
    assert cfg.show_pv_lines is False
    assert cfg.pv_lines_count == 3
    assert cfg.auto_save_pgn is False
    assert cfg.pgn_games_dir == "games"
    out = validate({"book_max_plies": 9999, "pv_lines_count": 99,
                    "show_pv_lines": 1, "opening_book_path": "  x.bin  "})
    assert out["book_max_plies"] == 200
    assert out["pv_lines_count"] == 5
    assert out["show_pv_lines"] is True
    assert out["opening_book_path"] == "x.bin"
    # old configs without new keys still migrate cleanly
    old = {"stockfish": "/tmp/sf", "website": "lichess"}
    cfg2 = Config.from_dict(old)
    assert cfg2.opening_book_path == "" and cfg2.pgn_games_dir == "games"


# ---------- StockfishBot helpers ----------

def _bare_bot(**kw):
    import sys
    import types
    if "pyautogui" not in sys.modules:
        sys.modules["pyautogui"] = types.ModuleType("pyautogui")
    from stockfish_bot import StockfishBot
    bot = StockfishBot.__new__(StockfishBot)
    bot.white_cp_losses = []
    bot.black_cp_losses = []
    bot._prev_eval_cp = None
    bot._engine_service = None
    bot.opening_book_path = kw.get("opening_book_path", "")
    bot.book_max_plies = kw.get("book_max_plies", 30)
    bot.syzygy_path = kw.get("syzygy_path", "")
    bot.tablebase_api = kw.get("tablebase_api", False)
    bot.show_pv_lines = kw.get("show_pv_lines", False)
    bot.pv_lines_count = kw.get("pv_lines_count", 3)
    bot.auto_save_pgn = kw.get("auto_save_pgn", False)
    bot.pgn_games_dir = kw.get("pgn_games_dir", "games")
    bot.website = kw.get("website", "chesscom")
    bot.is_white = True
    bot.stockfish_path = kw.get("stockfish_path", "fake")
    bot.stockfish_depth = 10
    bot.cpu_threads = 1
    bot.memory = 128
    bot.slow_mover = 60
    bot.skill_level = 20
    return bot


def test_bot_book_disabled_by_default():
    bot = _bare_bot()
    assert bot._choose_book_move(chess.Board()) is None


def test_bot_tablebase_no_backend():
    bot = _bare_bot()
    assert bot._choose_tablebase_move(chess.Board()) is None


def test_bot_autosave_disabled_by_default():
    bot = _bare_bot()
    board = chess.Board()
    board.push_san("e4")
    assert bot._maybe_autosave_game(["e4"], board) is None


def test_bot_autosave_writes_pgn(tmp_path):
    bot = _bare_bot(auto_save_pgn=True, pgn_games_dir=str(tmp_path))
    board = chess.Board()
    for san in ["f3", "e5", "g4", "Qh4#"]:
        board.push_san(san)
    path = bot._maybe_autosave_game(["f3", "e5", "g4", "Qh4#"], board)
    assert path and os.path.exists(path)
    with open(path) as fh:
        text = fh.read()
    assert '[Site "chess.com"]' in text
    assert '[Result "0-1"]' in text
    assert "Qh4#" in text


def test_bot_maybe_send_pv_disabled_noop():
    bot = _bare_bot()
    bot._safe_send = MagicMock()
    bot.overlay_queue = MagicMock()
    bot._maybe_send_pv(chess.Board(), MagicMock())
    bot._safe_send.assert_not_called()
