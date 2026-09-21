"""Analysis mode – PGN import, step-through, eval graph, MultiPV (stub Phase5).

Provides PGN parsing, blunder/accuracy summary without requiring a browser.
Uses EngineService for eval; can run headless for local analysis.
"""
from __future__ import annotations

import io
import chess
import chess.pgn

from utilities import get_logger
from engine_service import EngineService

logger = get_logger("analysis")


def load_pgn(pgn_text: str) -> chess.pgn.Game:
    return chess.pgn.read_game(io.StringIO(pgn_text))


def eval_game(pgn_text: str, stockfish_path: str, depth: int = 12) -> dict:
    """Run engine over PGN and return per-move evals + accuracy summary (stub)."""
    game = load_pgn(pgn_text)
    if game is None:
        return {"error": "invalid PGN"}
    board = game.board()
    svc = EngineService(stockfish_path, depth, 2, 128, 100, 20)
    try:
        sf = svc.create_stockfish()
    except Exception as e:
        logger.warning("Analysis eval skipped (no Stockfish): %s", e)
        return {"moves": [], "accuracy": "-", "blunders": []}
    # Walk moves, collect evals (simplified, no MultiPV yet)
    evals = []
    for move in game.mainline_moves():
        board.push(move)
        try:
            sf.set_position([m.uci() for m in board.move_stack])
            ev = sf.get_evaluation()
            evals.append(ev)
        except Exception as e:
            logger.debug("eval failed at %s: %s", move.uci(), e)
    # Accuracy via cp losses would need full cp tracking; stub returns evals
    return {"moves": [m.uci() for m in game.mainline_moves()], "evals": evals, "accuracy": "-", "blunders": []}
