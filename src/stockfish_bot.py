import multiprocess
from stockfish import Stockfish
import pyautogui
import time
import sys
import os
import chess
import re
import queue
import signal
import atexit
import traceback
import logging
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException, TimeoutException

from grabbers.chesscom_grabber import ChesscomGrabber
from grabbers.lichess_grabber import LichessGrabber
from utilities import char_to_num, get_logger, get_keyboard_handler
import protocol as proto

logger = get_logger("stockfish_bot")

# Try to get resilient keyboard handler
_kb, _kb_backend = get_keyboard_handler()
if _kb is None:
    import keyboard as _kb_fallback
    _kb = _kb_fallback
    _kb_backend = "keyboard"


class StockfishBot(multiprocess.Process):
    def __init__(self, chrome_url, chrome_session_id, website, pipe, overlay_queue, stockfish_path, enable_manual_mode, enable_mouseless_mode, enable_non_stop_puzzles, enable_non_stop_matches, mouse_latency, slow_mover, skill_level, stockfish_depth, memory=None, cpu_threads=None, browser: str | None = None, engine_options: dict | None = None):
        multiprocess.Process.__init__(self)
        self.chrome_url = chrome_url
        self.chrome_session_id = chrome_session_id
        self.website = website
        self.pipe = pipe
        self.overlay_queue = overlay_queue
        self.stockfish_path = stockfish_path
        self.enable_manual_mode = enable_manual_mode
        self.enable_mouseless_mode = enable_mouseless_mode
        self.enable_non_stop_puzzles = enable_non_stop_puzzles
        self.enable_non_stop_matches = enable_non_stop_matches
        # Clamp: a stale 5s+ config must never stall every move (see MoveExecutor).
        try:
            _lat = float(mouse_latency)
        except Exception:
            _lat = 0.0
        if _lat < 0.0:
            _lat = 0.0
        if _lat > 0.5:
            logger.warning("mouse_latency %.2fs clamped to 0.50s", _lat)
            _lat = 0.5
        self.mouse_latency = _lat
        self.slow_mover = slow_mover
        self.skill_level = skill_level
        self.stockfish_depth = stockfish_depth
        self.grabber = None
        # Auto-managed engine resources — no longer exposed in GUI
        self.memory = int(memory) if memory is not None else 512
        if cpu_threads is not None:
            self.cpu_threads = int(cpu_threads)
        else:
            try:
                self.cpu_threads = max(1, int((os.cpu_count() or 2) // 2))
            except Exception:
                self.cpu_threads = 1
        # Browser for webdriver (chrome/firefox/edge)
        raw_browser = browser if browser is not None else os.environ.get("CHESSX_BROWSER", "chrome")
        try:
            from browser_factory import normalize_browser as _nb
            self.browser = _nb(raw_browser)
        except Exception:
            self.browser = str(raw_browser).lower() if raw_browser else "chrome"
        self.is_white = None
        self._stockfish = None
        self._shutdown = False
        # Engine & Analysis options (all optional – defaults preserve behavior)
        _opts = dict(engine_options) if isinstance(engine_options, dict) else {}
        self.opening_book_path = str(_opts.get("opening_book_path", "") or "")
        try:
            self.book_max_plies = max(0, min(200, int(_opts.get("book_max_plies", 30))))
        except Exception:
            self.book_max_plies = 30
        self.syzygy_path = str(_opts.get("syzygy_path", "") or "")
        self.tablebase_api = bool(_opts.get("tablebase_api", True))
        self.show_pv_lines = bool(_opts.get("show_pv_lines", False))
        try:
            self.pv_lines_count = max(1, min(5, int(_opts.get("pv_lines_count", 3))))
        except Exception:
            self.pv_lines_count = 3
        self.auto_save_pgn = bool(_opts.get("auto_save_pgn", False))
        self.pgn_games_dir = str(_opts.get("pgn_games_dir", "games") or "games")
        # P1: Better accuracy model – centipawn loss tracking
        self.white_cp_losses = []
        self.black_cp_losses = []
        self._prev_eval_cp = None  # white-centric cp before last move
        self.white_best_moves = []
        self.black_best_moves = []
        # Extracted services (no behavior change, delegated)
        try:
            from board_sync import BoardSync as _BS
            from engine_service import EngineService as _ES
            from move_executor import MoveExecutor as _ME
            from game_loop import GameLoopState as _GL
            self._board_sync = _BS()
            self._engine_service = _ES(stockfish_path, stockfish_depth, self.cpu_threads, self.memory, slow_mover, skill_level,
                                       opening_book_path=self.opening_book_path, book_max_plies=self.book_max_plies,
                                       syzygy_path=self.syzygy_path or None, tablebase_api=self.tablebase_api)
            # keep cp losses synced
            self._engine_service.white_cp_losses = self.white_cp_losses
            self._engine_service.black_cp_losses = self.black_cp_losses
            self._engine_service._prev_eval_cp = self._prev_eval_cp
            self._move_executor_cls = _ME
            self._move_executor = None  # created after grabber
        except Exception as e:
            logger.debug("Failed to init extracted services: %s", e)
            self._board_sync = None
            self._engine_service = None
            self._move_executor_cls = None
            self._move_executor = None

    # --- helpers ---

    def _safe_send(self, msg):
        """Send typed or legacy string message via pipe (pickle)."""
        try:
            if self.pipe and not self.pipe.closed:
                self.pipe.send(msg)
        except (BrokenPipeError, OSError, EOFError) as e:
            preview = msg[:20] if isinstance(msg, str) else type(msg).__name__
            logger.debug("pipe send failed (%s): %s", preview, e)

    def _send_typed(self, typed_msg):
        """Convenience: send typed dataclass."""
        self._safe_send(typed_msg)

    def _clear_overlay_queue(self):
        try:
            while True:
                try:
                    self.overlay_queue.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
        except Exception:
            pass

    def _setup_signal_handlers(self):
        def _handler(signum, frame):
            logger.info("StockfishBot received signal %s, shutting down", signum)
            self._cleanup()
            sys.exit(0)
        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except Exception:
            pass
        atexit.register(self._cleanup)

    def _cleanup(self):
        if self._shutdown:
            return
        self._shutdown = True
        logger.info("StockfishBot cleanup")
        try:
            self._clear_overlay_queue()
        except Exception:
            pass
        try:
            if self._stockfish:
                # stockfish wrapper holds subprocess; try to quit
                try:
                    self._stockfish._stockfish.terminate()
                except Exception:
                    pass
                try:
                    self._stockfish._stockfish.kill()
                except Exception:
                    pass
        except Exception as e:
            logger.debug("stockfish cleanup error: %s", e)
        # NOTE: Do NOT quit chrome here – the GUI owns the browser session.
        # The grabber's chrome is an attached session; quitting would kill the browser for the GUI.
        try:
            if self.pipe and not self.pipe.closed:
                self.pipe.close()
        except Exception:
            pass

    def _reconcile_board_fen(self, board, move_list, variant: str | None = None):
        """Delegate to BoardSync (extracted). Variant-aware."""
        if variant is None:
            try:
                if hasattr(self.grabber, "detect_variant"):
                    variant = self.grabber.detect_variant()
            except Exception:
                variant = None
        if getattr(self, "_board_sync", None) is not None:
            try:
                return self._board_sync.reconcile_board_fen(board, move_list, variant=variant)
            except TypeError:
                # Back-compat with older BoardSync signature
                return self._board_sync.reconcile_board_fen(board, move_list)
            except Exception as e:
                logger.debug("BoardSync delegate failed: %s", e)
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

    # ── P1: Better accuracy model helpers (centipawn loss) ─────────
    def _eval_to_white_cp(self, eval_data):
        if getattr(self, "_engine_service", None) is not None:
            try:
                return self._engine_service.eval_to_white_cp(eval_data)
            except Exception:
                pass
        """Convert stockfish eval dict to white-centric centipawns (mate => ±10000)."""
        if not eval_data or not isinstance(eval_data, dict):
            return 0
        try:
            t = eval_data.get("type", "cp")
            v = int(eval_data.get("value", 0))
            if t == "cp":
                return v
            # mate: positive = white mates in v, negative = black mates
            if v > 0:
                return 10000 - (v * 10)
            elif v < 0:
                return -10000 - (v * 10)  # v negative => -10000 + |v|*10
            else:
                return 0
        except Exception:
            return 0

    def _cp_loss_to_bucket(self, loss):
        if getattr(self, "_engine_service", None) is not None:
            try:
                return self._engine_service.cp_loss_to_bucket(loss)
            except Exception:
                pass
        """Classify centipawn loss into lichess-like buckets."""
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

    def _accuracy_from_losses(self, losses):
        if getattr(self, "_engine_service", None) is not None:
            try:
                return self._engine_service.accuracy_from_losses(losses)
            except Exception:
                pass
        """Compute accuracy % from list of cp losses. Uses exponential decay similar to lichess."""
        if not losses:
            return "-"
        try:
            # Lichess-inspired: accuracy = 103.93 - 7.3 * avg_loss^0.15 ? simplified to weighted harmonic.
            # We use: acc = 100 * exp(-avg_loss / 600) blended with linear to keep intuitive.
            # Clamp and ensure 0–100.
            avg = sum(losses) / len(losses)
            # exponential component
            import math
            # avg 0 => 100, avg 50 => ~92, avg 150 => ~78, avg 350 => ~56
            acc = 100 * math.exp(-avg / 550)
            # boost slightly for very low avg
            if avg < 20:
                acc = min(100, acc + (20 - avg) * 0.05)
            acc = max(0, min(100, acc))
            return f"{acc:.1f}%"
        except Exception:
            return "-"

    def _record_cp_loss(self, stockfish, board_before_turn_is_white):
        if getattr(self, "_engine_service", None) is not None:
            # keep engine_service sync with bot state
            try:
                self._engine_service.white_cp_losses = self.white_cp_losses
                self._engine_service.black_cp_losses = self.black_cp_losses
                self._engine_service._prev_eval_cp = self._prev_eval_cp
                self._engine_service.record_cp_loss(stockfish, board_before_turn_is_white)
                # sync back
                self.white_cp_losses = self._engine_service.white_cp_losses
                self.black_cp_losses = self._engine_service.black_cp_losses
                self._prev_eval_cp = self._engine_service._prev_eval_cp
                # Cache the fresh eval for send_eval_data reuse (avoids a
                # second back-to-back get_evaluation UCI round-trip).
                try:
                    self._last_eval_data = stockfish.get_evaluation()
                    self._last_eval_ts = time.time()
                except Exception:
                    pass
                return
            except Exception:
                pass
        """Call after a move has been pushed and stockfish position updated. Compares prev cp to current."""
        try:
            cur_eval = stockfish.get_evaluation()
            try:
                self._last_eval_data = cur_eval
                self._last_eval_ts = time.time()
            except Exception:
                pass
            if not cur_eval or "value" not in cur_eval:
                return
            cur_cp = self._eval_to_white_cp(cur_eval)
            if self._prev_eval_cp is None:
                self._prev_eval_cp = cur_cp
                return
            prev = self._prev_eval_cp
            # mover is opposite of board.turn? Not reliable; use board_before param.
            # loss from mover perspective: white mover => prev - cur, black mover => cur - prev
            if board_before_turn_is_white:
                loss = prev - cur_cp
            else:
                loss = cur_cp - prev
            # negative loss means move improved eval => treat as 0
            loss = max(0, int(loss))
            if board_before_turn_is_white:
                self.white_cp_losses.append(loss)
            else:
                self.black_cp_losses.append(loss)
            logger.debug("cp loss %s: prev %d cur %d => loss %d (%s)", "white" if board_before_turn_is_white else "black", prev, cur_cp, loss, self._cp_loss_to_bucket(loss))
            self._prev_eval_cp = cur_cp
        except Exception as e:
            logger.debug("_record_cp_loss error: %s", e)
            try:
                # still update prev to current to avoid drift
                cur_eval = stockfish.get_evaluation()
                self._prev_eval_cp = self._eval_to_white_cp(cur_eval)
            except Exception:
                pass

    def _reset_accuracy_state(self, stockfish):
        if getattr(self, "_engine_service", None) is not None:
            try:
                self._engine_service.reset_accuracy_state(stockfish)
                self.white_cp_losses = self._engine_service.white_cp_losses
                self.black_cp_losses = self._engine_service.black_cp_losses
                self._prev_eval_cp = self._engine_service._prev_eval_cp
                return
            except Exception:
                pass
        """Clear cp loss history and re-seed prev eval (call on new game)."""
        self.white_cp_losses = []
        self.black_cp_losses = []
        try:
            ev = stockfish.get_evaluation()
            self._prev_eval_cp = self._eval_to_white_cp(ev)
        except Exception:
            self._prev_eval_cp = 0

    def move_to_screen_pos(self, move):
        if getattr(self, "_move_executor", None) is not None:
            try:
                return self._move_executor.move_to_screen_pos(move)
            except Exception:
                pass
        canvas_x_offset, canvas_y_offset = self.grabber.get_top_left_corner()
        board_elem = self.grabber.get_board()
        if board_elem is None:
            raise RuntimeError("Board element is None")
        board_x = canvas_x_offset + board_elem.location["x"]
        board_y = canvas_y_offset + board_elem.location["y"]
        square_size = board_elem.size['width'] / 8
        if self.is_white:
            x = board_x + square_size * (char_to_num(move[0]) - 1) + square_size / 2
            y = board_y + square_size * (8 - int(move[1])) + square_size / 2
        else:
            x = board_x + square_size * (8 - char_to_num(move[0])) + square_size / 2
            y = board_y + square_size * (int(move[1]) - 1) + square_size / 2
        return x, y

    def get_move_pos(self, move):
        if getattr(self, "_move_executor", None) is not None:
            try:
                return self._move_executor.get_move_pos(move)
            except Exception:
                pass
        start_pos_x, start_pos_y = self.move_to_screen_pos(move[0:2])
        end_pos_x, end_pos_y = self.move_to_screen_pos(move[2:4])
        return (start_pos_x, start_pos_y), (end_pos_x, end_pos_y)

    def make_move(self, move):
        if getattr(self, "_move_executor", None) is not None:
            try:
                return self._move_executor.make_move(move)
            except Exception:
                pass
        start_pos, end_pos = self.get_move_pos(move)
        try:
            pyautogui.moveTo(start_pos[0], start_pos[1], duration=0.0)
        except TypeError:
            pyautogui.moveTo(start_pos[0], start_pos[1])
        if self.mouse_latency > 0:
            time.sleep(self.mouse_latency)
        try:
            pyautogui.dragTo(end_pos[0], end_pos[1], duration=0.0)
        except TypeError:
            pyautogui.dragTo(end_pos[0], end_pos[1])
        # Promotion: handle all 4 piece types for both colors correctly
        if len(move) == 5:
            time.sleep(0.04)
            promo = move[4].lower()
            # Chess.com / lichess promotion UI: pieces appear offset from target square.
            # Direction depends on promotion rank: white promotes on rank 8 (pieces go downward),
            # black promotes on rank 1 (pieces go upward). We calculate offset generically.
            # Ordering in UI (top to bottom): queen, knight, rook, bishop – but we use exact squares.
            # Simpler: click offset squares relative to promotion target.
            # Map promo -> offset: q=0, n=1, r=2, b=3 for white; inverted for black.
            # Our previous code missed queen and used wrong direction for black.
            target_file = move[2]
            target_rank = int(move[3])
            # Determine if white promotion (pawn moved to rank 8) vs black (rank 1)
            is_white_promo = target_rank == 8
            # Offsets: queen at target, then one square away, etc. Direction: white -> decreasing rank, black -> increasing
            promo_offsets = {"q": 0, "n": 1, "r": 2, "b": 3}
            offset = promo_offsets.get(promo, 0)
            if promo == "q":
                # Queen is the promotion square itself – often need extra click on promotion piece chooser
                # On many UIs, queen auto-promotion is default; but we still ensure click is on target
                promo_rank = target_rank
            else:
                if is_white_promo:
                    promo_rank = target_rank - offset
                else:
                    promo_rank = target_rank + offset
            # Clamp to board
            promo_rank = max(1, min(8, promo_rank))
            promo_square = f"{target_file}{promo_rank}"
            try:
                end_pos_x, end_pos_y = self.move_to_screen_pos(promo_square)
                pyautogui.moveTo(x=end_pos_x, y=end_pos_y)
                pyautogui.click(button='left')
                logger.debug("Promotion click %s -> %s (promo %s)", move, promo_square, promo)
            except Exception as e:
                logger.warning("Promotion click failed for %s: %s", move, e)

    def wait_for_gui_to_delete(self):
        # Poll with timeout to avoid hanging forever if GUI dies
        start = time.time()
        while True:
            if self._shutdown:
                return
            try:
                if self.pipe.poll(0.1):
                    msg = self.pipe.recv()
                    # typed or legacy
                    if msg == "DELETE" or isinstance(msg, proto.MsgDelete) or (hasattr(msg, "kind") and getattr(msg, "kind") == "DELETE"):
                        return
                    # also handle legacy helper if str
                    try:
                        if isinstance(msg, str) and msg == "DELETE":
                            return
                    except Exception:
                        pass
                else:
                    # timeout check – if GUI closed pipe, exit
                    if time.time() - start > 30:
                        logger.warning("wait_for_gui_to_delete timed out")
                        return
            except (EOFError, OSError, BrokenPipeError):
                return
            except Exception as e:
                logger.debug("wait_for_gui_to_delete error: %s", e)
                time.sleep(0.1)

    def go_to_next_puzzle(self):
        """Advance to next puzzle (both sites). Returns True if clicked."""
        clicked = False
        try:
            clicked = bool(self.grabber.click_puzzle_next())
        except Exception as e:
            logger.warning("go_to_next_puzzle failed: %s", e)
        self._safe_send(proto.MsgRestart())
        self.wait_for_gui_to_delete()
        return clicked

    def find_new_online_match(self):
        """Next-game loop: rematch/new-opponent for lichess AND chess.com.

        Mirrors the Lichess 'New opponent' flow for chess.com rematch/new-game
        buttons. Waits briefly for the end-of-game modal, clicks via the
        grabber's resilient selector chain, resets cached moves, and hands
        control back to the GUI via RESTART/DELETE.
        Returns True if a button was clicked.
        """
        time.sleep(2)
        clicked = False
        try:
            clicked = bool(self.grabber.click_game_next())
            if not clicked:
                logger.info("find_new_online_match: no next-game button for %s (modal may not be up yet)",
                            getattr(self, "website", "?"))
                # One retry after a short wait – modal can animate in late
                time.sleep(1.5)
                try:
                    clicked = bool(self.grabber.click_game_next())
                except Exception as e:
                    logger.debug("find_new_online_match retry failed: %s", e)
        except Exception as e:
            logger.warning("find_new_online_match failed: %s", e)
        try:
            self.grabber.reset_moves_list()
        except Exception:
            pass
        self._safe_send(proto.MsgRestart())
        self.wait_for_gui_to_delete()
        return clicked

    # --- Engine & Analysis helpers (book / tablebase / PV / autosave) ---

    def _choose_book_move(self, board) -> str | None:
        """Polyglot book move (UCI) for the first N plies, or None."""
        if not getattr(self, "opening_book_path", ""):
            return None
        try:
            svc = getattr(self, "_engine_service", None)
            if svc is None:
                from engine_service import EngineService as _ES
                svc = _ES(self.stockfish_path, self.stockfish_depth, self.cpu_threads,
                          self.memory, self.slow_mover, self.skill_level,
                          opening_book_path=self.opening_book_path,
                          book_max_plies=self.book_max_plies)
                self._engine_service = svc
            return svc.get_book_move(board)
        except Exception as e:
            logger.debug("_choose_book_move failed: %s", e)
            return None

    def _choose_tablebase_move(self, board) -> str | None:
        """Tablebase best move (UCI) for <=7-piece endgames, or None."""
        try:
            svc = getattr(self, "_engine_service", None)
            if svc is None:
                return None
            hit = svc.probe_tablebase(board)
            if hit and hit.get("best_move") and hit["best_move"] in [m.uci() for m in board.legal_moves]:
                logger.info("Tablebase move %s (%s wdl=%s dtz=%s)",
                            hit["best_move"], hit.get("source"), hit.get("wdl"), hit.get("dtz"))
                return hit["best_move"]
        except Exception as e:
            logger.debug("_choose_tablebase_move failed: %s", e)
        return None

    def _maybe_send_pv(self, board, stockfish, best_uci: str | None = None):
        """Send PV text (GUI) + PV/threat arrows (overlay) when enabled."""
        if not getattr(self, "show_pv_lines", False):
            return
        try:
            svc = getattr(self, "_engine_service", None)
            if svc is None:
                return
            stockfish.set_position([m.uci() for m in board.move_stack])
            top = svc.get_top_moves(stockfish, num_moves=self.pv_lines_count)
            if not top:
                return
            pvs = svc.top_moves_to_pv(board, top, max_lines=self.pv_lines_count)
            if not pvs:
                return
            pv_text = svc.format_pv_text(pvs)
            threat_uci = None
            threat_san = ""
            try:
                threat_uci = svc.threat_move(board, stockfish)
                if threat_uci:
                    threat_san = board.copy().san(chess.Move.from_uci(threat_uci))
            except Exception:
                threat_uci, threat_san = None, ""
            self._safe_send(proto.MsgPV(pv_text=pv_text, threat_san=threat_san or ""))
            try:
                # Skip the best-move line (index 0) – the main arrow already shows it
                extra = [{"uci": p["uci"], "san": p["san"]} for p in pvs[1:]]
                self.overlay_queue.put(proto.OverlayLines(lines=extra, threat_uci=threat_uci or ""))
            except Exception as e:
                logger.debug("PV overlay put failed: %s", e)
        except Exception as e:
            logger.debug("_maybe_send_pv failed: %s", e)

    def _maybe_autosave_game(self, move_list, board):
        """Auto-save finished game PGN (headers + NAGs) when enabled."""
        if not getattr(self, "auto_save_pgn", False):
            return None
        try:
            from grabbers.grabber import Grabber as _G
            from pgn_export import auto_save_game, board_result, default_headers, nags_from_losses
            sans = [m for m in (move_list or []) if _G.normalize_result_text(m) is None]
            if not sans:
                return None
            result = board_result(board)
            site = "lichess.org" if getattr(self, "website", "") == "lichess" else "chess.com"
            if getattr(self, "is_white", None):
                white, black = "Me", "Opponent"
            elif self.is_white is False:
                white, black = "Opponent", "Me"
            else:
                white, black = "White", "Black"
            headers = default_headers(site=site, white=white, black=black,
                                      result=result, event="Online Game")
            nags = nags_from_losses(getattr(self, "white_cp_losses", []) or [],
                                    getattr(self, "black_cp_losses", []) or [])
            path = auto_save_game(getattr(self, "pgn_games_dir", "games"), sans,
                                  headers=headers, nags=nags, result=result)
            if path:
                logger.info("Auto-saved game PGN to %s", path)
            return path
        except Exception as e:
            logger.debug("_maybe_autosave_game failed: %s", e)
            return None

    def run(self):
        self._setup_signal_handlers()
        # Browser for attach (chrome/firefox/edge) – via constructor or env
        _browser = getattr(self, "browser", None)
        if _browser is None:
            try:
                import os as _os
                _browser = _os.environ.get("CHESSX_BROWSER", "chrome")
            except Exception:
                _browser = "chrome"
        # Also store back for MoveExecutor fallback
        self.browser = _browser
        if self.website == "chesscom":
            self.grabber = ChesscomGrabber(self.chrome_url, self.chrome_session_id, browser=_browser)
        else:
            self.grabber = LichessGrabber(self.chrome_url, self.chrome_session_id, browser=_browser)
        self.grabber.reset_moves_list()
        # Health check on startup
        try:
            ok, details = self.grabber.health_check()
            if not ok:
                logger.warning("Health check failed: %s", details)
                # don't abort – board may appear shortly after; retry update_board
        except Exception as e:
            logger.debug("health_check error: %s", e)

        parameters = {
            "Threads": self.cpu_threads,
            "Hash": self.memory,
            "Ponder": "false",
            "Slow Mover": self.slow_mover,
            "Skill Level": self.skill_level
        }
        try:
            stockfish = Stockfish(path=self.stockfish_path, depth=self.stockfish_depth, parameters=parameters)
            self._stockfish = stockfish
        except PermissionError:
            self._safe_send(proto.MsgError(code="ERR_PERM"))
            logger.error("Stockfish PermissionError: %s", self.stockfish_path)
            return
        except OSError as e:
            self._safe_send(proto.MsgError(code="ERR_EXE"))
            logger.error("Stockfish OSError: %s %s", self.stockfish_path, e)
            return
        except Exception as e:
            self._safe_send(proto.MsgError(code="ERR_EXE"))
            logger.error("Stockfish init failed: %s", e)
            return

        try:
            # Board with retry (fast)
            for attempt in range(3):
                self.grabber.update_board_elem()
                if self.grabber.get_board() is not None:
                    break
                time.sleep(0.2 * (attempt+1))
            if self.grabber.get_board() is None:
                self._safe_send(proto.MsgError(code="ERR_BOARD"))
                logger.error("Board not found after retries")
                return

            # Color with retry
            for attempt in range(3):
                self.is_white = self.grabber.is_white()
                if self.is_white is not None:
                    break
                time.sleep(0.15)
            if self.is_white is None:
                self._safe_send(proto.MsgError(code="ERR_COLOR"))
                logger.error("Could not determine player color")
                return
            # Init MoveExecutor now that is_white known (extracted)
            try:
                if getattr(self, "_move_executor_cls", None) is not None:
                    self._move_executor = self._move_executor_cls(self.grabber, self.is_white, self.mouse_latency)
            except Exception as e:
                logger.debug("MoveExecutor init failed: %s", e)

            move_list = None
            for attempt in range(3):
                move_list = self.grabber.get_move_list()
                if move_list is not None:
                    break
                time.sleep(0.15)
            if move_list is None:
                self._safe_send(proto.MsgError(code="ERR_MOVES"))
                logger.error("Move list not found")
                return

            score_pattern = r"([0-9]+)\-([0-9]+)"
            if len(move_list) > 0 and re.match(score_pattern, move_list[-1]):
                self._safe_send(proto.MsgError(code="ERR_GAMEOVER"))
                return
            # Draw / resign / abort handling: result banners beyond score_pattern
            try:
                _res = self.grabber.get_game_result() if hasattr(self.grabber, "get_game_result") else None
                if _res is not None:
                    logger.info("Game already has result %s at startup – aborting", _res)
                    self._safe_send(proto.MsgError(code="ERR_GAMEOVER", detail=str(_res)))
                    return
                if self.grabber.is_game_over():
                    logger.info("Grabber reports game over at startup")
                    self._safe_send(proto.MsgError(code="ERR_GAMEOVER"))
                    return
            except Exception as e:
                logger.debug("startup game-over check failed: %s", e)

            # Variant support: detect Chess960 / 3-check / KotH for validation
            variant = "standard"
            try:
                if hasattr(self.grabber, "detect_variant"):
                    variant = self.grabber.detect_variant() or "standard"
            except Exception:
                variant = "standard"
            try:
                from board_sync import BoardSync as _BS2
                if _BS2.normalize_variant(variant) != "standard":
                    logger.info("Detected variant: %s", variant)
                    if not _BS2.is_variant_supported(variant):
                        logger.warning("Variant %r not engine-supported – playing with standard rules", variant)
            except Exception:
                pass

            try:
                from board_sync import BoardSync as _BSB
                board = _BSB.create_board(variant=variant)
            except Exception:
                board = chess.Board()
            for mv in move_list:
                # Skip stray result markers that scrapers may have missed
                try:
                    from grabbers.grabber import Grabber as _G
                    if _G.normalize_result_text(mv) is not None:
                        logger.debug("Skipping result marker in initial list: %r", mv)
                        continue
                except Exception:
                    pass
                try:
                    board.push_san(mv)
                except Exception as e:
                    logger.warning("push_san failed for %s: %s", mv, e)

            move_list_uci = [m.uci() for m in board.move_stack]
            stockfish.set_position(move_list_uci)
            # P1: seed previous eval for cp-loss accuracy
            self._reset_accuracy_state(stockfish)

            white_moves = []
            white_best_moves = []
            black_moves = []
            black_best_moves = []

            self.send_eval_data(stockfish, board)
            self._safe_send(proto.MsgStart())
            if len(move_list) > 0:
                self._safe_send(proto.MsgMultiMove(sans=move_list))

            while True:
                if self._shutdown:
                    return
                if (self.is_white and board.turn == chess.WHITE) or (not self.is_white and board.turn == chess.BLACK):
                    move = None
                    move_source = "engine"
                    # 1) Opening book (first N plies, instant + human-like)
                    try:
                        book_move = self._choose_book_move(board)
                        if book_move:
                            move = book_move
                            move_source = "book"
                    except Exception as e:
                        logger.debug("book lookup failed: %s", e)
                    # 2) Endgame tablebase (<=7 pieces, exact)
                    if not move:
                        try:
                            tb_move = self._choose_tablebase_move(board)
                            if tb_move:
                                move = tb_move
                                move_source = "tablebase"
                        except Exception as e:
                            logger.debug("tablebase lookup failed: %s", e)
                    # 3) Engine search (time-limited for bullet/blitz)
                    if not move:
                        try:
                            # FAST: time-limited search keeps bullet/blitz responsive
                            # Depth 20 blocking can take >1s; movetime 150-300ms is much faster
                            # Scale by slow_mover so high slow_mover still gets a bit more time
                            if self.slow_mover < 60:
                                _mt = 100
                            elif self.slow_mover < 100:
                                _mt = 150
                            elif self.stockfish_depth >= 18:
                                _mt = 300
                            else:
                                _mt = 200
                            try:
                                _t_start = time.perf_counter()
                                move = stockfish.get_best_move_time(_mt)
                                self._last_move_calc_ms = (time.perf_counter() - _t_start) * 1000
                            except Exception:
                                _t_start = time.perf_counter()
                                move = stockfish.get_best_move()
                                self._last_move_calc_ms = (time.perf_counter() - _t_start) * 1000
                        except Exception as e:
                            logger.error("get_best_move failed: %s", e)
                            self._safe_send(proto.MsgError(code="ERR_TIMEOUT"))
                            time.sleep(0.2)
                            continue
                    else:
                        logger.info("Playing %s move %s", move_source, move)
                        # Keep wrapper position in sync (book/tablebase skip search)
                        try:
                            stockfish.set_position([m.uci() for m in board.move_stack])
                        except Exception as e:
                            logger.debug("position sync after %s move failed: %s", move_source, e)

                    if not move:
                        logger.warning("get_best_move returned None/empty")
                        time.sleep(0.1)
                        continue

                    # PV / threat lines for overlay + GUI (gated by show_pv_lines)
                    try:
                        self._maybe_send_pv(board, stockfish, best_uci=move)
                    except Exception as e:
                        logger.debug("PV send failed: %s", e)

                    if board.turn == chess.WHITE:
                        white_best_moves.append(move)
                    else:
                        black_best_moves.append(move)

                    self_moved = False
                    if self.enable_manual_mode:
                        try:
                            move_start_pos, move_end_pos = self.get_move_pos(move)
                            self.overlay_queue.put(proto.OverlayArrows(arrows=[((int(move_start_pos[0]), int(move_start_pos[1])), (int(move_end_pos[0]), int(move_end_pos[1])))]))
                        except Exception as e:
                            logger.debug("overlay put failed: %s", e)
                        # Poll for manual trigger or opponent move with stale resilience
                        poll_start = time.time()
                        while True:
                            if self._shutdown:
                                return
                            try:
                                if _kb.is_pressed("3"):
                                    break
                            except Exception:
                                pass
                            # Check if opponent moved (race-safe with retry)
                            try:
                                cur = self.grabber.get_move_list()
                            except Exception:
                                cur = None
                            if cur is not None and len(cur) != len(move_list):
                                # Opponent moved while in manual mode
                                self_moved = True
                                move_list = cur
                                try:
                                    move_san = move_list[-1]
                                    move = board.parse_san(move_san).uci()
                                    if board.turn == chess.WHITE:
                                        white_moves.append(move)
                                    else:
                                        black_moves.append(move)
                                    board.push_uci(move)
                                    stockfish.make_moves_from_current_position([move])
                                except Exception as e:
                                    logger.warning("manual mode parse/push failed: %s", e)
                                    # resync via FEN
                                    board, _ = self._reconcile_board_fen(board, move_list)
                                    stockfish.set_position([m.uci() for m in board.move_stack])
                                break
                            time.sleep(0.02)
                            # Avoid indefinite spin – timeout handled by normal loop
                            if time.time() - poll_start > 300:
                                break

                    if not self_moved:
                        try:
                            move_san = board.san(chess.Move(chess.parse_square(move[0:2]), chess.parse_square(move[2:4])))
                            # For promotions, include promo piece
                            if len(move) == 5:
                                # san already handles promotion if using Move.from_uci
                                try:
                                    move_san = board.san(chess.Move.from_uci(move))
                                except Exception:
                                    pass
                        except Exception:
                            move_san = move
                        mover_is_white = (board.turn == chess.WHITE)
                        if board.turn == chess.WHITE:
                            white_moves.append(move)
                        else:
                            black_moves.append(move)
                        try:
                            board.push_uci(move)
                        except Exception as e:
                            logger.error("push_uci failed for %s: %s", move, e)
                            self._safe_send(proto.MsgError(code="ERR_TIMEOUT"))
                            continue
                        try:
                            stockfish.make_moves_from_current_position([move])
                        except Exception as e:
                            logger.warning("make_moves_from_current_position failed: %s", e)
                            stockfish.set_position([m.uci() for m in board.move_stack])
                        # P1: record centipawn loss for accuracy model
                        self._record_cp_loss(stockfish, mover_is_white)
                        move_list.append(move_san)
                        if self.enable_mouseless_mode and not self.grabber.is_game_puzzles():
                            try:
                                # ply count AFTER our move (1-based), parity with lichess `a` param
                                _ply = len(move_list)
                                _ok = self.grabber.make_mouseless_move(move, _ply)
                                if _ok is False:
                                    # Grabber signals unsupported – fall back to mouse
                                    logger.info("Mouseless returned False, falling back to mouse for %s", move)
                                    self.make_move(move)
                            except NameError as e:
                                logger.error("mouseless move bookkeeping failed: %s", e)
                                try:
                                    self.make_move(move)
                                except Exception as e2:
                                    logger.error("make_move fallback failed: %s", e2)
                                    self._safe_send(proto.MsgError(code="ERR_DISCONNECT"))
                            except Exception as e:
                                logger.warning("mouseless move failed (%s), falling back to mouse: %s", move, e)
                                try:
                                    self.make_move(move)
                                except Exception as e2:
                                    logger.error("make_move fallback failed: %s", e2)
                                    self._safe_send(proto.MsgError(code="ERR_DISCONNECT"))
                        else:
                            try:
                                self.make_move(move)
                            except Exception as e:
                                logger.error("make_move failed: %s", e)
                                self._safe_send(proto.MsgError(code="ERR_DISCONNECT"))

                    self._clear_overlay_queue_safe()
                    self.send_eval_data(stockfish, board, white_moves, white_best_moves, black_moves, black_best_moves)
                    _w_clk, _b_clk = ("", "")
                    try:
                        if hasattr(self.grabber, "get_clocks"):
                            _w_clk, _b_clk = self.grabber.get_clocks()
                    except Exception:
                        pass
                    _losses = self.white_cp_losses if self.is_white else self.black_cp_losses
                    _is_blunder = bool(_losses and _losses[-1] >= 300)
                    self._safe_send(proto.MsgSingleMove(
                        san=move_san,
                        eval_str=getattr(self, "_last_eval_str", ""),
                        clock=_w_clk if self.is_white else _b_clk,
                        is_blunder=_is_blunder
                    ))

                    if board.is_game_over():
                        _why = "checkmate" if board.is_checkmate() else (
                            "stalemate" if board.is_stalemate() else "draw/claim")
                        logger.info("Board reports game over (%s)", _why)
                        self._maybe_autosave_game(move_list, board)
                        if self.enable_non_stop_puzzles and self.grabber.is_game_puzzles():
                            self.go_to_next_puzzle()
                        elif self.enable_non_stop_matches and not self.enable_non_stop_puzzles:
                            self.find_new_online_match()
                        return
                    # Also honor site result banners (resign/abort/timeout are not
                    # visible in python-chess until the result marker is scraped)
                    try:
                        if self.grabber.is_game_over():
                            logger.info("Site reports game over after our move")
                            self._maybe_autosave_game(move_list, board)
                            if self.enable_non_stop_puzzles and self.grabber.is_game_puzzles():
                                self.go_to_next_puzzle()
                            elif self.enable_non_stop_matches and not self.enable_non_stop_puzzles:
                                self.find_new_online_match()
                            return
                    except Exception:
                        pass
                    time.sleep(0.02)

                # Wait for opponent
                previous_move_list = move_list.copy()
                consecutive_errors = 0
                consecutive_invalid = 0
                while True:
                    if self._shutdown:
                        return
                    try:
                        if self.grabber.is_game_over():
                            self._maybe_autosave_game(move_list, board)
                            if self.enable_non_stop_puzzles and self.grabber.is_game_puzzles():
                                self.go_to_next_puzzle()
                            elif self.enable_non_stop_matches and not self.enable_non_stop_puzzles:
                                self.find_new_online_match()
                            return
                    except (StaleElementReferenceException, WebDriverException) as e:
                        logger.debug("is_game_over stale: %s", e)
                        time.sleep(0.08)
                        continue

                    try:
                        new_move_list = self.grabber.get_move_list()
                    except (StaleElementReferenceException, WebDriverException) as e:
                        consecutive_errors += 1
                        logger.debug("get_move_list error in opponent wait: %s", e)
                        if consecutive_errors > 5:
                            self._safe_send(proto.MsgError(code="ERR_DISCONNECT"))
                            time.sleep(0.2)
                            consecutive_errors = 0
                        time.sleep(0.12)
                        continue

                    if new_move_list is None:
                        consecutive_errors += 1
                        if consecutive_errors > 3:
                            logger.warning("get_move_list returned None repeatedly – disconnect?")
                            self._safe_send(proto.MsgError(code="ERR_DISCONNECT"))
                            time.sleep(0.2)
                        time.sleep(0.12)
                        continue
                    consecutive_errors = 0

                    # --- Robust new-game detection via FEN reconciliation ---
                    # Case 1: new game reset to 0 moves
                    if len(new_move_list) == 0 and len(move_list) > 0:
                        logger.info("New game detected (0 moves)")
                        self._handle_new_game(board, stockfish)
                        self.white_cp_losses = []
                        self.black_cp_losses = []
                        self._reset_accuracy_state(stockfish)
                        move_list = []
                        white_moves = []
                        white_best_moves = []
                        black_moves = []
                        black_best_moves = []
                        previous_move_list = []
                        break

                    # Case 2: takeback / undo / abort – move count decreased but not zero.
                    # Re-sync board when move count decreases (takeback/undo support).
                    if len(new_move_list) < len(previous_move_list) and len(new_move_list) != 0:
                        logger.info("Takeback/abort detected: %d -> %d", len(previous_move_list), len(new_move_list))
                        # Reconcile board (variant-aware)
                        try:
                            from board_sync import BoardSync as _BST
                            new_board = _BST.create_board(variant=variant)
                            for san in new_move_list:
                                new_board.push_san(san)
                            board = new_board
                            stockfish.set_position([m.uci() for m in board.move_stack])
                        except Exception as e:
                            logger.warning("Takeback resync failed: %s", e)
                            board, _ = self._reconcile_board_fen(board, new_move_list)
                            stockfish.set_position([m.uci() for m in board.move_stack])
                        # Trim per-move + accuracy history via shared helper
                        try:
                            from board_sync import BoardSync as _BST2
                            total = len(new_move_list)
                            (white_moves, white_best_moves, black_moves, black_best_moves,
                             _wl, _bl) = _BST2.trim_history_to_ply(
                                white_moves, white_best_moves, black_moves, black_best_moves,
                                self.white_cp_losses, self.black_cp_losses, total)
                            self.white_cp_losses, self.black_cp_losses = _wl, _bl
                        except Exception:
                            total = len(new_move_list)
                            white_moves = white_moves[: (total+1)//2]
                            white_best_moves = white_best_moves[: (total+1)//2]
                            black_moves = black_moves[: total//2]
                            black_best_moves = black_best_moves[: total//2]
                            # trim cp losses as well
                            self.white_cp_losses = self.white_cp_losses[: (total+1)//2]
                            self.black_cp_losses = self.black_cp_losses[: total//2]
                        try:
                            self._prev_eval_cp = self._eval_to_white_cp(stockfish.get_evaluation())
                        except Exception:
                            pass
                        move_list = new_move_list
                        self._clear_overlay_queue_safe()
                        self._safe_send(proto.MsgRestart())
                        self.wait_for_gui_to_delete()
                        self.send_eval_data(stockfish, board)
                        self._safe_send(proto.MsgStart())
                        if len(move_list) > 0:
                            self._safe_send(proto.MsgMultiMove(sans=move_list))
                        break

                    # Case 3: FEN mismatch when same move count but different moves (e.g., undo+ different line)
                    if len(new_move_list) == len(previous_move_list) and new_move_list != previous_move_list:
                        # Validate BEFORE treating as takeback: a corrupt DOM
                        # snapshot (phantom/variation node, same length) must not
                        # trigger a GUI restart storm / hot spin. Just ignore it
                        # and wait for the DOM to stabilize.
                        try:
                            from board_sync import BoardSync as _BSF
                            new_board = _BSF.create_board(variant=variant)
                            for san in new_move_list:
                                new_board.push_san(san)
                        except Exception as e:
                            consecutive_invalid += 1
                            # Rate-limit: log at most every ~25 hits, sleep to
                            # avoid the 20/sec busy loop seen in the wild.
                            try:
                                _n = getattr(self, "_takeback_noise", 0) + 1
                                self._takeback_noise = _n
                            except Exception:
                                _n = 1
                            if _n == 1 or _n % 25 == 0:
                                logger.warning("Ignoring corrupt same-length move snapshot %r (%s) [%d]", new_move_list, e, _n)
                            time.sleep(0.15)
                            continue
                        try:
                            self._takeback_noise = 0
                            board = new_board
                            stockfish.set_position([m.uci() for m in board.move_stack])
                            move_list = new_move_list
                            self._safe_send(proto.MsgRestart())
                            self.wait_for_gui_to_delete()
                            self.send_eval_data(stockfish, board)
                            self._safe_send(proto.MsgStart())
                            self._safe_send(proto.MsgMultiMove(sans=move_list))
                            break
                        except Exception as e:
                            logger.debug("FEN mismatch handling error: %s", e)
                            time.sleep(0.1)
                            continue

                    # Normal: opponent made a move
                    if len(new_move_list) > len(previous_move_list):
                        # Validate FEN before accepting (variant-aware). Also
                        # detect trailing result markers (1-0 etc.) = game end.
                        try:
                            from grabbers.grabber import Grabber as _GV
                            if _GV.normalize_result_text(new_move_list[-1]) is not None:
                                logger.info("Result marker %r detected – game over", new_move_list[-1])
                                move_list = [m for m in new_move_list if _GV.normalize_result_text(m) is None]
                                self._maybe_autosave_game(move_list, board)
                                if self.enable_non_stop_puzzles and self.grabber.is_game_puzzles():
                                    self.go_to_next_puzzle()
                                elif self.enable_non_stop_matches and not self.enable_non_stop_puzzles:
                                    self.find_new_online_match()
                                return
                        except SystemExit:
                            raise
                        except Exception:
                            pass
                        try:
                            from board_sync import BoardSync as _BSV
                            tmp = _BSV.create_board(variant=variant)
                            for san in new_move_list:
                                tmp.push_san(san)
                            consecutive_invalid = 0
                            move_list = new_move_list
                            break
                        except Exception as e:
                            # Corrupt snapshot (phantom node / mid-list dup as seen
                            # in the wild: e6-after-e5, Re1-on-move-2, ...).
                            # Do NOT accept it – keep the old move_list and keep
                            # polling for the DOM to stabilize. Accepting it
                            # poisons the board and deadlocks the bot ("stops").
                            consecutive_invalid += 1
                            if consecutive_invalid == 1 or consecutive_invalid % 10 == 0:
                                logger.warning("Invalid SAN in new_move_list (ignored, kept %d moves): %s %s",
                                               len(move_list), new_move_list, e)
                            if consecutive_invalid >= 15:
                                # DOM persistently corrupt – force grabber cache
                                # reset so the next poll rebuilds from scratch.
                                logger.warning("Move list corrupt %dx in a row – forcing grabber resync",
                                               consecutive_invalid)
                                try:
                                    self.grabber.reset_moves_list()
                                except Exception:
                                    pass
                                consecutive_invalid = 0
                            time.sleep(0.15)
                            continue

                    # 80ms poll: full move-list scrape costs ~100ms+ of
                    # WebDriver round-trips; tighter polling only queues up
                    # and makes detection *slower*, not faster.
                    time.sleep(0.08)

                # New-game / takeback / same-length resync paths break out with
                # the board already matching move_list (len <= previous len):
                # nothing incremental to apply – go around the loop.
                if len(move_list) <= len(previous_move_list):
                    continue
                # Apply only the NEW suffix moves (robust to multi-move jumps
                # after lag), each validated individually.
                try:
                    new_suffix = move_list[len(previous_move_list):] if len(move_list) >= len(previous_move_list) else [move_list[-1]]
                    if not new_suffix:
                        continue
                    last_mover_is_white = None
                    for move in new_suffix:
                        prev_board = board.copy()
                        mover_is_white = (prev_board.turn == chess.WHITE)
                        board.push_san(move)
                        move_uci = prev_board.parse_san(move).uci()
                        if prev_board.turn == chess.WHITE:
                            white_moves.append(move_uci)
                        else:
                            black_moves.append(move_uci)
                        last_mover_is_white = mover_is_white
                        # Keep engine in step per move (cheap, keeps accuracy right)
                        try:
                            stockfish.make_moves_from_current_position([str(board.peek())])
                        except Exception as e:
                            logger.debug("make_moves_from_current_position (opponent) failed: %s", e)
                            stockfish.set_position([m.uci() for m in board.move_stack])
                        self._record_cp_loss(stockfish, mover_is_white)
                    # NOTE: no extra engine search here – the old
                    # get_best_move_time(50) purely for fallback accuracy
                    # cost 50ms+ per opponent move and contended with the
                    # real search. cp-loss accuracy (above) supersedes it.
                    self.send_eval_data(stockfish, board, white_moves, white_best_moves, black_moves, black_best_moves)
                    _w_clk, _b_clk = ("", "")
                    try:
                        if hasattr(self.grabber, "get_clocks"):
                            _w_clk, _b_clk = self.grabber.get_clocks()
                    except Exception:
                        pass
                    _mover_is_w = bool(last_mover_is_white) if last_mover_is_white is not None else (not self.is_white)
                    _losses = self.white_cp_losses if _mover_is_w else self.black_cp_losses
                    _is_blunder = bool(_losses and _losses[-1] >= 300)
                    self._safe_send(proto.MsgSingleMove(
                        san=new_suffix[-1],
                        eval_str=getattr(self, "_last_eval_str", ""),
                        clock=_w_clk if _mover_is_w else _b_clk,
                        is_blunder=_is_blunder
                    ))
                except Exception as e:
                    logger.warning("Failed to apply opponent move %s: %s", move_list[-1] if move_list else "?", e)
                    # Try FEN resync
                    try:
                        nb = chess.Board()
                        for san in move_list:
                            nb.push_san(san)
                        board = nb
                        stockfish.set_position([m.uci() for m in board.move_stack])
                    except Exception:
                        pass

                if board.is_game_over():
                    logger.info("Board reports game over after opponent move")
                    self._maybe_autosave_game(move_list, board)
                    if self.enable_non_stop_puzzles and self.grabber.is_game_puzzles():
                        self.go_to_next_puzzle()
                    elif self.enable_non_stop_matches and not self.enable_non_stop_puzzles:
                        self.find_new_online_match()
                    return
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Unhandled exception in StockfishBot.run: %s\n%s", e, tb)
            # Map exception types to new error codes
            if isinstance(e, TimeoutException):
                self._safe_send(proto.MsgError(code="ERR_TIMEOUT"))
            elif isinstance(e, WebDriverException):
                self._safe_send(proto.MsgError(code="ERR_DISCONNECT"))
            elif isinstance(e, StaleElementReferenceException):
                self._safe_send(proto.MsgError(code="ERR_STALE"))
            else:
                # Generic engine error – include info via ERR_ENGINE
                self._safe_send(proto.MsgError(code="ERR_ENGINE", detail=str(e)[:200]))
            # Also try to log to file and print for backwards compat
            exc_type, exc_obj, exc_tb = sys.exc_info()
            if exc_tb:
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                logger.error("Exception %s %s:%s", exc_type, fname, exc_tb.tb_lineno)
            # Don't swallow silently – ensure GUI knows
            try:
                time.sleep(0.2)
            except Exception:
                pass
        finally:
            self._cleanup()

    def _handle_new_game(self, board, stockfish):
        try:
            board.reset()
            stockfish.set_position([])
        except Exception as e:
            logger.debug("_handle_new_game reset error: %s", e)
        self.grabber.reset_moves_list()
        self._clear_overlay_queue_safe()
        # reset accuracy state
        try:
            self._reset_accuracy_state(stockfish)
        except Exception:
            self.white_cp_losses = []
            self.black_cp_losses = []
        try:
            self.is_white = self.grabber.is_white()
            if getattr(self, "_move_executor", None) is not None:
                self._move_executor.is_white = self.is_white
        except Exception:
            pass
        self._safe_send(proto.MsgRestart())
        self.wait_for_gui_to_delete()
        self.send_eval_data(stockfish, board)
        self._safe_send(proto.MsgStart())

    def _clear_overlay_queue_safe(self):
        try:
            self.overlay_queue.put(proto.OverlayClear())
        except Exception:
            pass
        # Also drain if needed
        # We put empty list to clear arrows; eval bar cleared via next eval

    def send_eval_data(self, stockfish, board, white_moves=None, white_best_moves=None, black_moves=None, black_best_moves=None):
        try:
            # Reuse the eval just fetched by _record_cp_loss (<1.5s old) –
            # saves one blocking UCI round-trip per move.
            eval_data = None
            try:
                _ts = getattr(self, "_last_eval_ts", 0) or 0
                _cached = getattr(self, "_last_eval_data", None)
                if isinstance(_cached, dict) and "value" in _cached and (time.time() - _ts) < 1.5:
                    eval_data = _cached
            except Exception:
                eval_data = None
            if eval_data is None:
                try:
                    eval_data = stockfish.get_evaluation()
                except Exception as e:
                    logger.debug("get_evaluation failed: %s", e)
                    eval_data = None
            else:
                logger.debug("send_eval_data: reusing cached eval")
            try:
                self._last_eval_data = eval_data
                self._last_eval_ts = time.time()
            except Exception:
                pass
            # stockfish 5.2.0 can return {} or None if position not yet evaluated / game over
            if not eval_data or not isinstance(eval_data, dict) or "type" not in eval_data or "value" not in eval_data:
                logger.debug("get_evaluation returned empty/invalid: %r – using 0", eval_data)
                eval_data = {"type": "cp", "value": 0}
            eval_type = eval_data['type']
            eval_value = eval_data['value']
            # Ensure int
            try:
                eval_value = int(eval_value)
            except Exception:
                eval_value = 0
            player_perspective_eval_value = eval_value
            if not self.is_white:
                player_perspective_eval_value = -eval_value
            # WDL is display-only – cache for 2s to avoid a second blocking
            # UCI call on every move (it cost ~50-150ms per update).
            wdl_stats = None
            try:
                _wts = getattr(self, "_last_wdl_ts", 0) or 0
                _wc = getattr(self, "_last_wdl", None)
                if isinstance(_wc, (list, tuple)) and len(_wc) == 3 and (time.time() - _wts) < 2.0:
                    wdl_stats = list(_wc)
                    logger.debug("send_eval_data: reusing cached WDL")
            except Exception:
                wdl_stats = None
            if wdl_stats is None:
                try:
                    wdl_stats = stockfish.get_wdl_stats()
                except Exception as e:
                    logger.debug("get_wdl_stats exception: %s", e)
                    wdl_stats = [0, 0, 0]
                try:
                    self._last_wdl = list(wdl_stats) if isinstance(wdl_stats, (list, tuple)) else [0, 0, 0]
                    self._last_wdl_ts = time.time()
                except Exception:
                    pass
            # get_wdl_stats returns None when game is over (mate) – handle gracefully
            if wdl_stats is None:
                logger.debug("get_wdl_stats returned None (game over) – using 0/0/0")
                wdl_stats = [0, 0, 0]
            # Ensure list of 3 ints
            if not isinstance(wdl_stats, (list, tuple)) or len(wdl_stats) != 3:
                logger.debug("wdl_stats invalid %r – using 0/0/0", wdl_stats)
                wdl_stats = [0, 0, 0]
            try:
                wdl_stats = [int(x) if x is not None else 0 for x in wdl_stats]
            except Exception:
                wdl_stats = [0, 0, 0]
            material = self.calculate_material_advantage(board)
            # P1: Better accuracy model – centipawn loss buckets (replaces exact-UCI-match)
            # Prefer cp-loss based accuracy if losses are available; fallback to exact match for backwards compat
            white_accuracy = "-"
            black_accuracy = "-"
            try:
                # Use internal cp loss lists if populated
                if getattr(self, "white_cp_losses", None) and len(self.white_cp_losses) > 0:
                    white_accuracy = self._accuracy_from_losses(self.white_cp_losses)
                elif white_moves and white_best_moves and len(white_moves) > 0 and len(white_moves) == len(white_best_moves):
                    matches = sum(1 for a, b in zip(white_moves, white_best_moves) if a == b)
                    white_accuracy = f"{matches / len(white_moves) * 100:.1f}%"
            except Exception as e:
                logger.debug("white accuracy calc error: %s", e)
            try:
                if getattr(self, "black_cp_losses", None) and len(self.black_cp_losses) > 0:
                    black_accuracy = self._accuracy_from_losses(self.black_cp_losses)
                elif black_moves and black_best_moves and len(black_moves) > 0 and len(black_moves) == len(black_best_moves):
                    matches = sum(1 for a, b in zip(black_moves, black_best_moves) if a == b)
                    black_accuracy = f"{matches / len(black_moves) * 100:.1f}%"
            except Exception as e:
                logger.debug("black accuracy calc error: %s", e)
            if eval_type == "cp":
                eval_str = f"{player_perspective_eval_value/100:.2f}"
                eval_value_decimal = player_perspective_eval_value/100
            else:
                eval_str = f"M{player_perspective_eval_value}"
                eval_value_decimal = player_perspective_eval_value
            total = sum(wdl_stats)
            if total > 0:
                is_bot_turn = (self.is_white and board.turn == chess.WHITE) or (not self.is_white and board.turn == chess.BLACK)
                if is_bot_turn:
                    win_pct = wdl_stats[0] / total * 100
                    draw_pct = wdl_stats[1] / total * 100
                    loss_pct = wdl_stats[2] / total * 100
                else:
                    win_pct = wdl_stats[2] / total * 100
                    draw_pct = wdl_stats[1] / total * 100
                    loss_pct = wdl_stats[0] / total * 100
                wdl_str = f"{win_pct:.1f}/{draw_pct:.1f}/{loss_pct:.1f}"
            else:
                wdl_str = "?/?/?"
            bot_accuracy = white_accuracy if self.is_white else black_accuracy
            opponent_accuracy = black_accuracy if self.is_white else white_accuracy
            self._last_eval_str = eval_str
            self._safe_send(proto.MsgEval(eval_str=eval_str, wdl_str=wdl_str, material_str=material, bot_acc=bot_accuracy, opponent_acc=opponent_accuracy))
            self._safe_send(proto.MsgTelemetry(
                nps=getattr(self, "_last_nps", 850000),
                depth=int(self.stockfish_depth),
                time_per_move_ms=float(getattr(self, "_last_move_calc_ms", 120.0)),
                connection_health="Healthy",
                last_error="",
            ))
            overlay_data = {
                "eval": eval_value_decimal,
                "eval_type": eval_type
            }
            board_elem = self.grabber.get_board()
            if board_elem:
                try:
                    canvas_x_offset, canvas_y_offset = self.grabber.get_top_left_corner()
                    overlay_data["board_position"] = {
                        'x': canvas_x_offset + board_elem.location['x'],
                        'y': canvas_y_offset + board_elem.location['y'],
                        'width': board_elem.size['width'],
                        'height': board_elem.size['height']
                    }
                except Exception as e:
                    logger.debug("overlay board_position error: %s", e)
            overlay_data["is_white"] = self.is_white
            try:
                self.overlay_queue.put(proto.OverlayEval(eval_value=eval_value_decimal, eval_type=eval_type, board_position=overlay_data.get("board_position"), is_white=overlay_data.get("is_white")))
            except Exception as e:
                logger.debug("overlay queue put error: %s", e)
        except Exception as e:
            logger.warning("Error sending evaluation: %s", e)

    def calculate_material_advantage(self, board):
        if getattr(self, "_engine_service", None) is not None:
            try:
                return self._engine_service.calculate_material_advantage(board)
            except Exception:
                pass
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
