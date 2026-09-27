from __future__ import annotations
import io
import chess
import chess.pgn
from utilities import get_logger
from engine_service import EngineService


logger = get_logger("analysis")


def load_pgn(pgn_text: str) -> chess.pgn.Game | None:
    try:
        return chess.pgn.read_game(io.StringIO(pgn_text))
    except Exception as e:
        logger.warning("load_pgn failed: %s", e)
        return None


def _white_cp(eval_data: dict) -> int:
    if not eval_data or not isinstance(eval_data, dict):
        return 0
    try:
        kind = eval_data.get("type", "cp")
        value = int(eval_data.get("value", 0))
        if kind == "cp":
            return value
        if value > 0:
            return 10000 - value * 10
        if value < 0:
            return -10000 - value * 10
        return 0
    except Exception:
        return 0


def _bucket(loss: int) -> str:
    loss = max(0, int(loss))
    if loss <= 10:
        return "best"
    if loss <= 50:
        return "excellent"
    if loss <= 100:
        return "good"
    if loss <= 200:
        return "inaccuracy"
    if loss <= 400:
        return "mistake"
    return "blunder"


def eval_game(pgn_text: str, stockfish_path: str, depth: int = 12,
              movetime_ms: int = 150) -> dict:

    game = load_pgn(pgn_text)
    if game is None:
        return {"error": "invalid PGN"}
    moves = list(game.mainline_moves())
    svc = EngineService(stockfish_path, depth, 2, 128, 100, 20)
    try:
        sf = svc.create_stockfish()
    except Exception as e:
        logger.warning("Analysis eval skipped (no Stockfish): %s", e)
        board = game.board()
        sans = []
        for move in moves:
            sans.append(board.san(move))
            board.push(move)
        return {"moves": [m.uci() for m in moves], "sans": sans, "evals": [],
                "nags": [None] * len(moves), "accuracy": "-", "blunders": []}
    try:
        sf.set_position([])
        prev_cp = _white_cp(sf.get_evaluation())
    except Exception:
        prev_cp = 0
    board = game.board()
    sans, evals, nags, blunders = [], [], [], []
    white_losses, black_losses = [], []
    for i, move in enumerate(moves):
        mover_is_white = board.turn == chess.WHITE
        try:
            san = board.san(move)
        except Exception:
            san = move.uci()
        board.push(move)
        try:
            sf.set_position([m.uci() for m in board.move_stack])
            ev = sf.get_evaluation() or {"type": "cp", "value": 0}
        except Exception as e:
            logger.debug("eval failed at %s: %s", move.uci(), e)
            ev = {"type": "cp", "value": prev_cp}
        cur_cp = _white_cp(ev)
        loss = max(0, (prev_cp - cur_cp) if mover_is_white else (cur_cp - prev_cp))
        prev_cp = cur_cp
        (white_losses if mover_is_white else black_losses).append(loss)
        bucket = _bucket(loss)
        try:
            from pgn_export import bucket_to_nag
            nag = bucket_to_nag(bucket)
        except Exception:
            nag = None
        sans.append(san)
        evals.append({"san": san, "uci": move.uci(), "cp": cur_cp,
                      "type": ev.get("type", "cp"), "value": ev.get("value", 0),
                      "loss": loss, "bucket": bucket, "nag": nag})
        nags.append(nag)
        if bucket in ("mistake", "blunder"):
            blunders.append({"ply": i + 1, "san": san, "loss": loss, "bucket": bucket})
    return {"moves": [m.uci() for m in moves], "sans": sans, "evals": evals,
            "nags": nags, "accuracy": svc.accuracy_from_losses(white_losses + black_losses),
            "white_accuracy": svc.accuracy_from_losses(white_losses),
            "black_accuracy": svc.accuracy_from_losses(black_losses),
            "blunders": blunders}


def analyze_position(fen: str, stockfish_path: str, depth: int = 12,
                     num_moves: int = 3, movetime_ms: int = 200) -> dict:


    out: dict = {"fen": fen, "eval": None, "top": [], "pv_text": "",
                 "threat_uci": None, "tablebase": None}
    try:
        board = chess.Board(fen)
    except Exception as e:
        out["error"] = f"invalid FEN: {e}"
        return out
    svc = EngineService(stockfish_path, depth, 2, 128, 100, 20)
    try:
        sf = svc.create_stockfish()
    except Exception as e:
        logger.warning("analyze_position skipped (no Stockfish): %s", e)
        out["error"] = f"no engine: {e}"
        return out
    try:
        sf.set_position([m.uci() for m in board.move_stack])
        out["eval"] = sf.get_evaluation()
    except Exception as e:
        logger.debug("analyze eval failed: %s", e)
    try:
        # Tablebase first (cheap, exact for <=7 pieces)
        out["tablebase"] = svc.probe_tablebase(board)
    except Exception as e:
        logger.debug("analyze tablebase failed: %s", e)
    try:
        sf.set_position([m.uci() for m in board.move_stack])
        try:
            best = sf.get_best_move_time(int(movetime_ms))
            if best:
                out["best_move"] = best
        except Exception:
            pass
        top = svc.get_top_moves(sf, num_moves=num_moves)
        out["top"] = svc.top_moves_to_pv(board, top, max_lines=num_moves)
        out["pv_text"] = svc.format_pv_text(out["top"])
        out["threat_uci"] = svc.threat_move(board, sf)
    except Exception as e:
        logger.debug("analyze top-moves failed: %s", e)
    return out


def self_play(stockfish_path: str, depth: int = 12, max_plies: int = 60,
              movetime_ms: int = 100, fen: str | None = None) -> dict:
    """Headless engine self-play. Returns {ucis, sans, pgn, result}."""
    from local_board import LocalBoard
    lb = LocalBoard(fen=fen, stockfish_path=stockfish_path, depth=depth)
    ucis = lb.self_play(max_plies=max_plies, movetime_ms=movetime_ms)
    try:
        from pgn_export import board_result, sans_from_board
        sans = sans_from_board(lb.board)
        result = board_result(lb.board)
    except Exception:
        sans, result = [], "*"
    return {"ucis": ucis, "sans": sans, "pgn": lb.to_pgn(result=result), "result": result}
