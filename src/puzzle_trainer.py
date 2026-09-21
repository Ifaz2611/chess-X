"""Puzzle trainer – hints and solution reveal only after attempt (Phase5 stub).

In full implementation this would integrate with Lichess puzzle API or local
PGN puzzles, show hint arrow, and reveal solution after user attempt.
Currently provides Trainer class for future GUI integration.
"""
from __future__ import annotations

import chess
from utilities import get_logger

logger = get_logger("puzzle_trainer")


class PuzzleTrainer:
    def __init__(self, fen: str, solution_uci: list[str]):
        self.board = chess.Board(fen)
        self.solution = solution_uci
        self.attempts: list[str] = []
        self.hints_shown = 0

    def hint(self) -> str | None:
        """Return next hint (first move of solution) without revealing full line."""
        if not self.solution:
            return None
        self.hints_shown += 1
        logger.info("Puzzle hint requested (%d)", self.hints_shown)
        return self.solution[0][:2] + "?"  # e.g., "e2?" hides destination

    def attempt(self, uci: str) -> bool:
        """Return True if attempt matches next solution move; reveal full solution only after at least one attempt."""
        self.attempts.append(uci)
        expected = self.solution[len(self.attempts) - 1] if len(self.attempts) <= len(self.solution) else None
        correct = (uci == expected)
        logger.info("Puzzle attempt %s -> %s (expected %s)", uci, correct, expected)
        return correct

    def reveal(self) -> list[str] | None:
        """Reveal solution only after an attempt (per spec)."""
        if not self.attempts:
            logger.warning("Reveal requested before any attempt – blocked")
            return None
        return self.solution
