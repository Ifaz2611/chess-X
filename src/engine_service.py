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
    def __init__(self, stockfish_path: str, depth: int, threads: int, memory: int, slow_mover: int, skill_level: int,
                 opening_book_path: str | None = None, book_max_plies: int = 30,
                 syzygy_path: str | None = None, tablebase_api: bool = True):
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
        # Optional opening book + tablebase (lazy, disabled when paths empty)
        self._book = None
        self._book_path = (opening_book_path or "").strip()
        self._book_max_plies = max(0, int(book_max_plies))
        self._probe = None
        self._syzygy_path = (syzygy_path or "").strip()
        self._tablebase_api = bool(tablebase_api)

    def create_stockfish(self):
        from stockfish import Stockfish
        params = {
            "Threads": self.threads,
            "Hash": self.memory,
            "Ponder": "false",
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

    # --- Opening book (optional Polyglot book.bin for first N moves) ---

    def get_book(self):
        """Lazy OpeningBook instance (None when no path configured)."""
        if self._book is not None:
            return self._book
        if not self._book_path:
            return None
        try:
            from opening_book import OpeningBook
            self._book = OpeningBook(self._book_path, max_plies=self._book_max_plies)
            return self._book
        except Exception as e:
            logger.debug("OpeningBook init failed: %s", e)
            return None

    def get_book_move(self, board: chess.Board) -> str | None:
        """Book UCI move for this position, or None (out of book/disabled)."""
        try:
            book = self.get_book()
            if book is None:
                return None
            return book.pick_move_uci(board)
        except Exception as e:
            logger.debug("get_book_move failed: %s", e)
            return None

    # --- Endgame tablebase probing (local Syzygy or Lichess API, <=7 pieces) ---

    def get_probe(self):
        """Lazy TablebaseProbe instance (always available; backends optional)."""
        if self._probe is not None:
            return self._probe
        try:
            from tablebase import TablebaseProbe
            self._probe = TablebaseProbe(syzygy_path=self._syzygy_path or None,
                                         use_api=self._tablebase_api)
            return self._probe
        except Exception as e:
            logger.debug("TablebaseProbe init failed: %s", e)
            return None

    def probe_tablebase(self, board: chess.Board) -> dict | None:
        """Probe tablebase for this position. Dict(wdl, dtz, best_move, source) or None."""
        try:
            probe = self.get_probe()
            if probe is None:
                return None
            return probe.probe(board)
        except Exception as e:
            logger.debug("probe_tablebase failed: %s", e)
            return None

    # --- Threat & line display (MultiPV via get_top_moves) ---

    def get_top_moves(self, stockfish, num_moves: int = 3) -> list[dict]:
        """Top-N moves with evals via the wrapper's get_top_moves (never raises)."""
        try:
            moves = stockfish.get_top_moves(max(1, min(5, int(num_moves))))
            return [m for m in (moves or []) if isinstance(m, dict) and m.get("Move")]
        except Exception as e:
            logger.debug("get_top_moves failed: %s", e)
            return []

    @staticmethod
    def top_moves_to_pv(board: chess.Board, top_moves: list[dict], max_lines: int = 3) -> list[dict]:
        """Convert get_top_moves dicts to display PVs: [{uci, san, cp, mate}].

        SAN is computed for the first ply on a copy so ``+``/``#`` show.
        The wrapper only provides single-ply entries, so each PV is one move.
        """
        pvs: list[dict] = []
        for entry in top_moves[:max(1, int(max_lines))]:
            uci = entry.get("Move")
            if not uci:
                continue
            try:
                probe = board.copy()
                san = probe.san(chess.Move.from_uci(uci))
            except Exception:
                san = uci
            pvs.append({"uci": uci, "san": san,
                        "cp": entry.get("Centipawn"), "mate": entry.get("Mate")})
        return pvs

    @staticmethod
    def format_pv_text(pvs: list[dict]) -> str:
        """One-line PV text for the GUI, e.g. '1. Nf3 (+0.42)  2. c4 (#3)'."""
        parts = []
        for i, pv in enumerate(pvs, start=1):
            if pv.get("mate") is not None:
                score = f"M{pv['mate']}"
            elif pv.get("cp") is not None:
                score = f"{pv['cp'] / 100:+.2f}"
            else:
                score = "?"
            parts.append(f"{i}. {pv.get('san', pv.get('uci', '?'))} ({score})")
        return "  ".join(parts)

    @staticmethod
    def threat_move(board: chess.Board, stockfish) -> str | None:
        """Opponent's threat: null-move trick – best reply if we passed.

        Returns UCI of the opponent's best move after a null move, or None.
        Falls back gracefully when the position has no legal null (check).
        """
        try:
            probe = board.copy()
            probe.push(chess.Move.null())
            stockfish.set_position([m.uci() for m in probe.move_stack])
            mv = stockfish.get_best_move_time(50)
            # Restore our real position for the caller
            stockfish.set_position([m.uci() for m in board.move_stack])
            if mv and chess.Move.from_uci(mv) in probe.legal_moves:
                return mv
        except Exception as e:
            logger.debug("threat_move failed: %s", e)
            try:
                stockfish.set_position([m.uci() for m in board.move_stack])
            except Exception as restore_error:
                logger.debug("threat_move restore failed: %s", restore_error)
        return None
