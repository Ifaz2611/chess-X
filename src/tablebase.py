from __future__ import annotations
import json
import urllib.parse
import urllib.request
import chess
from utilities import get_logger

logger = get_logger("tablebase")

LICHESS_TB_URL = "https://tablebase.lichess.ovh/standard"


def piece_count(board: chess.Board) -> int:
    return len(board.piece_map())


class TablebaseProbe:
    def __init__(self, syzygy_path: str | None = None, use_api: bool = True,
                 max_pieces: int = 7, api_timeout: float = 4.0):
        self.syzygy_path = (syzygy_path or "").strip()
        self.use_api = bool(use_api)
        self.max_pieces = max(2, min(7, int(max_pieces)))
        self.api_timeout = float(api_timeout)
        self._tb = None
        self._tb_error_logged = False

    @property
    def local_available(self) -> bool:
        return bool(self.syzygy_path)

    def _get_local(self):
        if self._tb is not None:
            return self._tb
        if not self.syzygy_path:
            return None
        try:
            import chess.syzygy
            tb = chess.syzygy.open_tablebase(self.syzygy_path)
            self._tb = tb
            return tb
        except Exception as e:
            if not self._tb_error_logged:
                logger.warning("Syzygy open failed (%s): %s", self.syzygy_path, e)
                self._tb_error_logged = True
            return None

    def probe_local(self, board: chess.Board) -> dict | None:
        if piece_count(board) > self.max_pieces:
            return None
        tb = self._get_local()
        if tb is None:
            return None
        try:
            wdl = tb.probe_wdl(board)
            dtz = tb.probe_dtz(board)
        except KeyError:
            return None
        except Exception as e:
            logger.debug("Syzygy probe failed: %s", e)
            return None
        best = self._best_dtz_move(board, tb)
        return {"wdl": int(wdl), "dtz": int(dtz), "best_move": best, "source": "syzygy"}

    def _best_dtz_move(self, board: chess.Board, tb) -> str | None:
        best_move = None
        best_dtz = None
        try:
            for move in board.legal_moves:
                board.push(move)
                try:
                    dtz = tb.probe_dtz(board)
                except Exception:
                    board.pop()
                    continue
                board.pop()
                if best_dtz is None or dtz < best_dtz:
                    best_dtz = dtz
                    best_move = move.uci()
            return best_move
        except Exception as e:
            logger.debug("Syzygy best-move search failed: %s", e)
            return None

    def probe_api(self, board: chess.Board) -> dict | None:
        if not self.use_api:
            return None
        if piece_count(board) > self.max_pieces:
            return None
        try:
            fen = board.fen()
            url = LICHESS_TB_URL + "?" + urllib.parse.urlencode({"fen": fen})
            req = urllib.request.Request(url, headers={"User-Agent": "chess-x/1.0"})
            with urllib.request.urlopen(req, timeout=self.api_timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug("Tablebase API query failed: %s", e)
            return None
        try:
            moves = payload.get("moves") or {}
            best_uci, best_entry = None, None
            for uci, info in moves.items():
                if best_entry is None or (info.get("wdl", -99), -(info.get("dtz") or 0)) > \
                        (best_entry.get("wdl", -99), -(best_entry.get("dtz") or 0)):
                    best_uci, best_entry = uci, info
            if best_uci is None:
                return None
            if chess.Move.from_uci(best_uci) not in board.legal_moves:
                logger.debug("Tablebase API suggested illegal move %s for %s", best_uci, fen)
                return None
            return {"wdl": int(best_entry.get("wdl", 0)),
                    "dtz": int(best_entry.get("dtz") or 0),
                    "best_move": best_uci,
                    "source": "lichess-api"}
        except Exception as e:
            logger.debug("Tablebase API parse failed: %s", e)
            return None

    def probe(self, board: chess.Board) -> dict | None:
        """Local Syzygy first, then API. Returns dict or None."""
        hit = self.probe_local(board)
        if hit is not None:
            logger.info("Tablebase hit (%s): wdl=%s dtz=%s move=%s",
                        hit["source"], hit["wdl"], hit["dtz"], hit["best_move"])
            return hit
        hit = self.probe_api(board)
        if hit is not None:
            logger.info("Tablebase hit (%s): wdl=%s dtz=%s move=%s",
                        hit["source"], hit["wdl"], hit["dtz"], hit["best_move"])
        return hit

    def close(self):
        try:
            if self._tb is not None:
                self._tb.close()
        except Exception:
            pass
        finally:
            self._tb = None
