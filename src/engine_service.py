"""EngineService – Stockfish settings and queries extracted from StockfishBot.

Wraps stockfish wrapper, handles parameter setup, evaluation, WDL, material,
accuracy (cp loss) logic. No behavior change.
"""
from __future__ import annotations

import os
import math
import chess

from utilities import get_logger

logger = get_logger("engine_service")


class EngineService:
    def __init__(self, stockfish_path: str, depth: int, threads: int, memory: int, slow_mover: int, skill_level: int):
        self.stockfish_path = stockfish_path
        self.depth = depth
        self.threads = threads
        self.memory = memory
        self.slow_mover = slow_mover
        self.skill_level = skill_level
        self._stockfish = None
        self.white_cp_losses: list[int] = []
        self.black_cp_losses: list[int] = []
        self._prev_eval_cp: int | None = None

    def create_stockfish(self):
        from stockfish import Stockfish
        params = {
            "Threads": self.threads,
            "Hash": self.memory,
            "Ponder": "true",
            "Slow Mover": self.slow_mover,
            "Skill Level": self.skill_level,
        }
        sf = Stockfish(path=self.stockfish_path, depth=self.depth, parameters=params)
        self._stockfish = sf
        return sf

    @property
    def stockfish(self):
        return self._stockfish

    # --- eval helpers ---
    def eval_to_white_cp(self, eval_data) -> int:
        if not eval_data or not isinstance(eval_data, dict):
            return 0
        try:
            t = eval_data.get("type", "cp")
            v = int(eval_data.get("value", 0))
            if t == "cp":
                return v
            if v > 0:
                return 10000 - (v * 10)
            elif v < 0:
                return -10000 - (v * 10)
            else:
                return 0
        except Exception:
            return 0

    def cp_loss_to_bucket(self, loss) -> str:
        try:
            l = int(loss)
        except Exception:
            l = 0
        if l <= 10:
            return "best"
        elif l <= 50:
            return "excellent"
        elif l <= 100:
            return "good"
        elif l <= 200:
            return "inaccuracy"
        elif l <= 400:
            return "mistake"
        else:
            return "blunder"

    def accuracy_from_losses(self, losses: list[int]) -> str:
        if not losses:
            return "-"
        try:
            avg = sum(losses) / len(losses)
            acc = 100 * math.exp(-avg / 550)
            if avg < 20:
                acc = min(100, acc + (20 - avg) * 0.05)
            acc = max(0, min(100, acc))
            return f"{acc:.1f}%"
        except Exception:
            return "-"

    def record_cp_loss(self, stockfish, board_before_turn_is_white: bool):
        try:
            cur_eval = stockfish.get_evaluation()
            if not cur_eval or "value" not in cur_eval:
                return
            cur_cp = self.eval_to_white_cp(cur_eval)
            if self._prev_eval_cp is None:
                self._prev_eval_cp = cur_cp
                return
            prev = self._prev_eval_cp
            if board_before_turn_is_white:
                loss = prev - cur_cp
            else:
                loss = cur_cp - prev
            loss = max(0, int(loss))
            if board_before_turn_is_white:
                self.white_cp_losses.append(loss)
            else:
                self.black_cp_losses.append(loss)
            logger.debug("cp loss %s: prev %d cur %d => loss %d (%s)", "white" if board_before_turn_is_white else "black", prev, cur_cp, loss, self.cp_loss_to_bucket(loss))
            self._prev_eval_cp = cur_cp
        except Exception as e:
            logger.debug("record_cp_loss error: %s", e)
            try:
                cur_eval = stockfish.get_evaluation()
                self._prev_eval_cp = self.eval_to_white_cp(cur_eval)
            except Exception:
                pass

    def reset_accuracy_state(self, stockfish):
        self.white_cp_losses = []
        self.black_cp_losses = []
        try:
            ev = stockfish.get_evaluation()
            self._prev_eval_cp = self.eval_to_white_cp(ev)
        except Exception:
            self._prev_eval_cp = 0

    def calculate_material_advantage(self, board: chess.Board) -> str:
        piece_values = {
            chess.PAWN: 1,
            chess.KNIGHT: 3,
            chess.BISHOP: 3,
            chess.ROOK: 5,
            chess.QUEEN: 9
        }
        white_material = 0
        black_material = 0
        for piece_type in piece_values:
            white_material += len(board.pieces(piece_type, chess.WHITE)) * piece_values[piece_type]
            black_material += len(board.pieces(piece_type, chess.BLACK)) * piece_values[piece_type]
        advantage = white_material - black_material
        if advantage > 0:
            return f"+{advantage}"
        elif advantage < 0:
            return str(advantage)
        else:
            return "0"
