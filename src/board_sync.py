"""BoardSync – DOM moves <-> chess.Board, takeback/FEN resync extracted from StockfishBot.

No behavior change: same reconcile logic, same API for backward compat.
"""
from __future__ import annotations

import chess

from utilities import get_logger

logger = get_logger("board_sync")


class BoardSync:
    """Handles synchronization between internal chess.Board and DOM move list."""

    @staticmethod
    def reconcile_board_fen(board: chess.Board, move_list: list[str]) -> tuple[chess.Board, bool]:
        """Reconcile internal board with DOM move list via FEN. Handles takeback/undo.
        Returns (new_board, needs_reset:bool)"""
        try:
            expected = chess.Board()
            for san in move_list:
                expected.push_san(san)
            if expected.fen() == board.fen():
                return board, False
            if len(move_list) < len(list(board.move_stack)):
                logger.info("Takeback/abort detected: DOM moves %d < internal %d – resyncing", len(move_list), len(list(board.move_stack)))
                return expected, True
            try:
                tmp = chess.Board()
                for san in move_list:
                    tmp.push_san(san)
                logger.info("FEN mismatch detected – resyncing board. DOM FEN: %s | Internal FEN: %s", tmp.fen(), board.fen())
                return tmp, True
            except Exception:
                return expected, True
        except Exception as e:
            logger.debug("FEN reconcile error: %s", e)
            return board, False
        return board, False

    @staticmethod
    def board_from_sans(sans: list[str]) -> chess.Board:
        b = chess.Board()
        for san in sans:
            b.push_san(san)
        return b

    @staticmethod
    def sans_to_uci_list(board: chess.Board) -> list[str]:
        return [m.uci() for m in board.move_stack]
