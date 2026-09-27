"""Local vs-engine board (python-chess, no scraping) – headless self-play / analysis.

Run without a browser: load FEN/PGN, step through positions with eval data,
or let the engine play itself. GUI can instantiate LocalBoard for offline
analysis / vs-engine mode.
"""
from __future__ import annotations

import io

import chess
import chess.pgn
from engine_service import EngineService
from utilities import get_logger

logger = get_logger("local_board")

# python-chess fills these placeholder headers even for garbage input.
_PLACEHOLDER_HEADERS = {"?", "????.??.??", "*"}


def _has_real_headers(game) -> bool:
    """True if the PGN carries at least one non-placeholder header tag."""
    try:
        return any(str(v).strip() not in _PLACEHOLDER_HEADERS
                   for v in game.headers.values())
    except Exception:
        return False


class LocalBoard:
    def __init__(self, fen: str | None = None, stockfish_path: str | None = None, depth: int = 12):
        self.board = chess.Board(fen) if fen else chess.Board()
        self.stockfish_path = stockfish_path
        self.depth = depth
        self._svc: EngineService | None = None
        # Step-through cursor: index into move_stack actually applied to board.
        # Loads (PGN/FEN) set _full_stack; goto_ply truncates/applies from root.
        self._full_stack: list[chess.Move] = list(self.board.move_stack)
        self._ply = len(self._full_stack)
        if stockfish_path:
            try:
                self._svc = EngineService(stockfish_path, depth, 2, 128, 100, 20)
                self._svc.create_stockfish()
            except Exception as e:
                logger.warning("LocalBoard engine init failed: %s", e)

    # --- loading (FEN / PGN, no browser) ---

    def load_fen(self, fen: str) -> bool:
        try:
            self.board = chess.Board(fen)
            self._full_stack = list(self.board.move_stack)
            self._ply = len(self._full_stack)
            return True
        except Exception as e:
            logger.warning("load_fen failed: %s", e)
            return False

    def load_pgn(self, pgn_text: str) -> bool:
        """Load the mainline of a PGN string; cursor starts at the final ply."""
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None:
                return False
            moves = list(game.mainline_moves())
            if not moves and not _has_real_headers(game):
                return False  # garbage input parses to a placeholder game
            board = game.board()
            for move in moves:
                board.push(move)
            self.board = board
            self._full_stack = list(board.move_stack)
            self._ply = len(self._full_stack)
            return True
        except Exception as e:
            logger.warning("load_pgn failed: %s", e)
            return False

    # --- step-through with eval-bar data ---

    @property
    def ply(self) -> int:
        return self._ply

    @property
    def total_plies(self) -> int:
        return len(self._full_stack)

    def goto_ply(self, ply: int) -> bool:
        """Step to an absolute ply (0 = initial position)."""
        try:
            ply = max(0, min(int(ply), len(self._full_stack)))
            root = self.board.root()
            root.reset()
            for move in self._full_stack[:ply]:
                root.push(move)
            self.board = root
            self._ply = ply
            return True
        except Exception as e:
            logger.warning("goto_ply failed: %s", e)
            return False

    def step_forward(self) -> bool:
        if self._ply >= len(self._full_stack):
            return False
        return self.goto_ply(self._ply + 1)

    def step_back(self) -> bool:
        if self._ply <= 0:
            return False
        return self.goto_ply(self._ply - 1)

    def current_eval(self) -> dict:
        """Eval-bar payload for the current position (no engine = neutral)."""
        if not self._svc or not self._svc.stockfish:
            return {"eval": 0.0, "eval_type": "cp", "fen": self.board.fen(),
                    "is_white": self.board.turn == chess.WHITE, "engine": False}
        try:
            sf = self._svc.stockfish
            sf.set_position([m.uci() for m in self.board.move_stack])
            ev = sf.get_evaluation() or {"type": "cp", "value": 0}
            cp = self._svc.eval_to_white_cp(ev)
            return {"eval": cp / 100.0 if ev.get("type") == "cp" else float(cp),
                    "eval_type": ev.get("type", "cp"),
                    "fen": self.board.fen(),
                    "is_white": self.board.turn == chess.WHITE, "engine": True}
        except Exception as e:
            logger.debug("current_eval failed: %s", e)
            return {"eval": 0.0, "eval_type": "cp", "fen": self.board.fen(),
                    "is_white": self.board.turn == chess.WHITE, "engine": False}

    # --- engine play (self-play / vs-engine) ---

    def push_san(self, san: str) -> bool:
        try:
            self.board.push_san(san)
            self._full_stack = list(self.board.move_stack)
            self._ply = len(self._full_stack)
            return True
        except Exception as e:
            logger.warning("push_san %s failed: %s", san, e)
            return False

    def push_uci(self, uci: str) -> bool:
        try:
            self.board.push_uci(uci)
            self._full_stack = list(self.board.move_stack)
            self._ply = len(self._full_stack)
            return True
        except Exception as e:
            logger.warning("push_uci %s failed: %s", uci, e)
            return False

    def engine_move(self, movetime_ms: int = 200) -> str | None:
        if not self._svc or not self._svc.stockfish:
            logger.warning("No engine available for LocalBoard")
            return None
        try:
            sf = self._svc.stockfish
            sf.set_position([m.uci() for m in self.board.move_stack])
            try:
                mv = sf.get_best_move_time(int(movetime_ms))
            except Exception:
                mv = sf.get_best_move()
            if mv:
                self.board.push_uci(mv)
                self._full_stack = list(self.board.move_stack)
                self._ply = len(self._full_stack)
            return mv
        except Exception as e:
            logger.error("engine_move failed: %s", e, exc_info=True)
            return None

    def self_play(self, max_plies: int = 60, movetime_ms: int = 100) -> list[str]:
        """Engine vs itself from the current position. Returns UCI moves played."""
        played: list[str] = []
        for _ in range(max(0, int(max_plies))):
            if self.board.is_game_over():
                break
            mv = self.engine_move(movetime_ms=movetime_ms)
            if not mv:
                break
            played.append(mv)
        return played

    def to_pgn(self, headers: dict | None = None, result: str | None = None) -> str:
        """Export current game via pgn_export (headers + NAG-free movetext)."""
        try:
            from pgn_export import board_result, build_pgn, default_headers, sans_from_board
            sans = sans_from_board(self.board)
            res = result or board_result(self.board)
            hdrs = dict(default_headers(site="local", white="Local", black="Engine", result=res))
            if headers:
                hdrs.update(headers)
            return build_pgn(sans, headers=hdrs, result=res)
        except Exception as e:
            logger.warning("to_pgn failed: %s", e)
            return ""

    def fen(self) -> str:
        return self.board.fen()

    def is_game_over(self) -> bool:
        return self.board.is_game_over()
