"""GameLoop – main bot loop extracted from StockfishBot (no behavior change).

Currently a placeholder that documents the loop steps. The actual loop remains
in StockfishBot for backward compat, but this module exposes helpers that can
be unit-tested independently (e.g., loop state transitions).
"""
from __future__ import annotations

import time
import chess

from utilities import get_logger

logger = get_logger("game_loop")


class GameLoopState:
    """Simple state holder for testing loop transitions."""
    def __init__(self, board: chess.Board, is_white: bool | None):
        self.board = board
        self.is_white = is_white
        self.move_list: list[str] = []

    def is_bot_turn(self) -> bool:
        if self.is_white is None:
            return False
        return (self.is_white and self.board.turn == chess.WHITE) or (not self.is_white and self.board.turn == chess.BLACK)
