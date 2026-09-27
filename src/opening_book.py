from __future__ import annotations
import os
import random
import chess
from utilities import get_logger
logger = get_logger("opening_book")

class OpeningBook:
    def __init__(self, path: str | None = None, max_plies: int = 30, seed: int | None = None):
        self.path = (path or "").strip()
        self.max_plies = max(0, int(max_plies))
        self._rng = random.Random(seed)
        self._reader = None
        self._error_logged = False
    @property
    def enabled(self) -> bool:
        return bool(self.path) and self.max_plies > 0
    def _get_reader(self):
        if self._reader is not None:
            return self._reader
        if not self.enabled:
            return None
        if not os.path.exists(self.path):
            if not self._error_logged:
                logger.warning("Opening book not found: %s – book disabled", self.path)
                self._error_logged = True
            return None
        try:
            import chess.polyglot
            self._reader = chess.polyglot.open_reader(self.path)
            return self._reader
        except Exception as e:
            if not self._error_logged:
                logger.warning("Opening book open failed (%s): %s – book disabled", self.path, e)
                self._error_logged = True
            return None
    def find_moves(self, board: chess.Board) -> list:
        """Return all book entries for this position (may be empty)."""
        reader = self._get_reader()
        if reader is None:
            return []
        try:
            return list(reader.find_all(board))
        except Exception as e:
            logger.debug("Opening book lookup failed: %s", e)
            return []

    def pick_move(self, board: chess.Board):
        if not self.enabled:
            return None
        if len(board.move_stack) >= self.max_plies:
            return None
        entries = self.find_moves(board)
        entries = [e for e in entries if getattr(e, "weight", 0) > 0 and e.move in board.legal_moves]
        if not entries:
            return None
        total = sum(e.weight for e in entries)
        roll = self._rng.uniform(0, total)
        acc = 0
        for entry in entries:
            acc += entry.weight
            if roll <= acc:
                logger.info("Book move %s (weight %d/%d, ply %d)",
                            entry.move.uci(), entry.weight, total, len(board.move_stack))
                return entry.move
        logger.info("Book move %s (fallback)", entries[-1].move.uci())
        return entries[-1].move
    def pick_move_uci(self, board: chess.Board) -> str | None:
        move = self.pick_move(board)
        return move.uci() if move is not None else None
    def close(self):
        try:
            if self._reader is not None:
                self._reader.close()
        except Exception:
            pass
        finally:
            self._reader = None
