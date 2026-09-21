"""Local vs-engine board (python-chess, no scraping) – Phase5 stub.

Provides a headless board that can play against EngineService without Selenium.
GUI can instantiate LocalBoard for offline analysis / vs-engine mode.
"""
from __future__ import annotations

import chess
from engine_service import EngineService
from utilities import get_logger

logger = get_logger("local_board")


class LocalBoard:
    def __init__(self, fen: str | None = None, stockfish_path: str | None = None, depth: int = 12):
        self.board = chess.Board(fen) if fen else chess.Board()
        self.stockfish_path = stockfish_path
        self.depth = depth
        self._svc: EngineService | None = None
        if stockfish_path:
            try:
                self._svc = EngineService(stockfish_path, depth, 2, 128, 100, 20)
                self._svc.create_stockfish()
            except Exception as e:
                logger.warning("LocalBoard engine init failed: %s", e)

    def push_san(self, san: str) -> bool:
        try:
            self.board.push_san(san)
            return True
        except Exception as e:
            logger.warning("push_san %s failed: %s", san, e)
            return False

    def push_uci(self, uci: str) -> bool:
        try:
            self.board.push_uci(uci)
            return True
        except Exception as e:
            logger.warning("push_uci %s failed: %s", uci, e)
            return False

    def engine_move(self) -> str | None:
        if not self._svc or not self._svc.stockfish:
            logger.warning("No engine available for LocalBoard")
            return None
        try:
            sf = self._svc.stockfish
            sf.set_position([m.uci() for m in self.board.move_stack])
            mv = sf.get_best_move()
            if mv:
                self.board.push_uci(mv)
            return mv
        except Exception as e:
            logger.error("engine_move failed: %s", e, exc_info=True)
            return None

    def fen(self) -> str:
        return self.board.fen()

    def is_game_over(self) -> bool:
        return self.board.is_game_over()
