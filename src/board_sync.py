from __future__ import annotations
import chess
from utilities import get_logger
logger = get_logger("board_sync")

SUPPORTED_VARIANTS = {"standard", "chess960", "threecheck", "kingofthehill"}
CHESS960_ALIASES = {"chess960", "960", "fischer", "franklin"}


class BoardSync:
    @staticmethod
    def reconcile_board_fen(board: chess.Board, move_list: list[str], variant: str | None = None) -> tuple[chess.Board, bool]:
        """Reconcile internal board with DOM move list via FEN. Handles takeback/undo.
        Returns (new_board, needs_reset:bool)"""
        try:
            expected = BoardSync.create_board(variant=variant)
            for san in move_list:
                expected.push_san(san)
            if expected.fen() == board.fen():
                return board, False
            if len(move_list) < len(list(board.move_stack)):
                logger.info("Takeback/abort detected: DOM moves %d < internal %d – resyncing", len(move_list), len(list(board.move_stack)))
                return expected, True
            try:
                tmp = BoardSync.create_board(variant=variant)
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
    def board_from_sans(sans: list[str], variant: str | None = None) -> chess.Board:
        b = BoardSync.create_board(variant=variant)
        for san in sans:
            b.push_san(san)
        return b

    @staticmethod
    def normalize_variant(variant: str | None) -> str:
        """Normalize UI/grabber variant names to a canonical key."""
        if not variant:
            return "standard"
        v = str(variant).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        aliases = {
            "standard": "standard", "classical": "standard", "chess": "standard",
            "chess960": "chess960", "960": "chess960", "fischer": "chess960",
            "fischerrandom": "chess960", "franklin": "chess960",
            "threecheck": "threecheck", "3check": "threecheck",
            "kingofthehill": "kingofthehill", "koth": "kingofthehill",
            "crazyhouse": "crazyhouse", "zh": "crazyhouse",
            "atomic": "atomic", "antichess": "antichess", "horde": "horde",
            "racingkings": "racingkings",
        }
        return aliases.get(v, v)

    @classmethod
    def is_variant_supported(cls, variant: str | None) -> bool:
        return cls.normalize_variant(variant) in SUPPORTED_VARIANTS

    @classmethod
    def create_board(cls, variant: str | None = None, fen: str | None = None,
                     chess960: bool = False) -> chess.Board:
        norm = cls.normalize_variant(variant)
        if norm in CHESS960_ALIASES or norm == "chess960" or chess960:
            try:
                if fen:
                    return chess.Board(fen, chess960=True)
                return chess.Board(chess960=True)
            except TypeError:
                # Very old python-chess without chess960 kwarg
                logger.warning("python-chess lacks chess960 support – using standard board")
                return chess.Board(fen) if fen else chess.Board()
        if norm == "threecheck":
            try:
                from chess.variant import ThreeCheckBoard
                return ThreeCheckBoard(fen) if fen else ThreeCheckBoard()
            except Exception as e:
                logger.warning("ThreeCheck variant unavailable (%s) – using standard board", e)
                return chess.Board(fen) if fen else chess.Board()
        if norm == "kingofthehill":
            try:
                from chess.variant import KingOfTheHillBoard
                return KingOfTheHillBoard(fen) if fen else KingOfTheHillBoard()
            except Exception as e:
                logger.warning("KingOfTheHill variant unavailable (%s) – using standard board", e)
                return chess.Board(fen) if fen else chess.Board()
        if norm not in ("standard",):
            logger.warning("Unsupported variant %r – engine play falls back to standard rules", variant)
        return chess.Board(fen) if fen else chess.Board()

    @staticmethod
    def trim_history_to_ply(white_moves: list, white_best: list, black_moves: list,
                            black_best: list, white_losses: list, black_losses: list,
                            total_plies: int):
        w_n = (total_plies + 1) // 2
        b_n = total_plies // 2
        return (
            list(white_moves)[:w_n], list(white_best)[:w_n],
            list(black_moves)[:b_n], list(black_best)[:b_n],
            list(white_losses)[:w_n], list(black_losses)[:b_n],
        )

    @staticmethod
    def sans_to_uci_list(board: chess.Board) -> list[str]:
        return [m.uci() for m in board.move_stack]
