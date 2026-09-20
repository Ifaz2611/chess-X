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

logger = get_logger("stockfish_bot")

# Try to get resilient keyboard handler
_kb, _kb_backend = get_keyboard_handler()
if _kb is None:
    import keyboard as _kb_fallback
    _kb = _kb_fallback
    _kb_backend = "keyboard"


class StockfishBot(multiprocess.Process):
    def __init__(self, chrome_url, chrome_session_id, website, pipe, overlay_queue, stockfish_path, enable_manual_mode, enable_mouseless_mode, enable_non_stop_puzzles, enable_non_stop_matches, mouse_latency, bongcloud, slow_mover, skill_level, stockfish_depth, memory, cpu_threads):
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
        self.mouse_latency = mouse_latency
        self.bongcloud = bongcloud
        self.slow_mover = slow_mover
        self.skill_level = skill_level
        self.stockfish_depth = stockfish_depth
        self.grabber = None
        self.memory = memory
        self.cpu_threads = cpu_threads
        self.is_white = None
        self._stockfish = None
        self._shutdown = False
        # P1: Better accuracy model – centipawn loss tracking
        self.white_cp_losses = []
        self.black_cp_losses = []
        self._prev_eval_cp = None  # white-centric cp before last move
        self.white_best_moves = []
        self.black_best_moves = []

    # --- helpers ---

    def _safe_send(self, msg):
        try:
            if self.pipe and not self.pipe.closed:
                self.pipe.send(msg)
        except (BrokenPipeError, OSError, EOFError) as e:
            logger.debug("pipe send failed (%s): %s", msg[:20] if isinstance(msg, str) else msg, e)

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

    def _reconcile_board_fen(self, board, move_list):
        """Reconcile internal board with DOM move list via FEN. Handles takeback/undo.
        Returns (new_board, needs_reset:bool)"""
        try:
            expected = chess.Board()
            for san in move_list:
                expected.push_san(san)
            if expected.fen() == board.fen():
                return board, False
            # If move count decreased -> takeback
            if len(move_list) < len(list(board.move_stack)):
                logger.info("Takeback/abort detected: DOM moves %d < internal %d – resyncing", len(move_list), len(list(board.move_stack)))
                return expected, True
            # If DOM has fewer moves but not zero and fen differs -> mismatch, resync
            # Check if DOM is prefix of board (e.g., after takeback)
            try:
                # Try to see if move_list is prefix
                tmp = chess.Board()
                for san in move_list:
                    tmp.push_san(san)
                # If no exception, use tmp as new board
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
        """Call after a move has been pushed and stockfish position updated. Compares prev cp to current."""
        try:
            cur_eval = stockfish.get_evaluation()
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
        """Clear cp loss history and re-seed prev eval (call on new game)."""
        self.white_cp_losses = []
        self.black_cp_losses = []
        try:
            ev = stockfish.get_evaluation()
            self._prev_eval_cp = self._eval_to_white_cp(ev)
        except Exception:
            self._prev_eval_cp = 0

    def move_to_screen_pos(self, move):
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
        start_pos_x, start_pos_y = self.move_to_screen_pos(move[0:2])
        end_pos_x, end_pos_y = self.move_to_screen_pos(move[2:4])
        return (start_pos_x, start_pos_y), (end_pos_x, end_pos_y)

    def make_move(self, move):
        start_pos, end_pos = self.get_move_pos(move)
        pyautogui.moveTo(start_pos[0], start_pos[1])
        time.sleep(self.mouse_latency)
        pyautogui.dragTo(end_pos[0], end_pos[1])
        # Promotion: handle all 4 piece types for both colors correctly
        if len(move) == 5:
            time.sleep(0.15)
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
                    if msg == "DELETE":
                        return
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
        try:
            self.grabber.click_puzzle_next()
        except Exception as e:
            logger.warning("go_to_next_puzzle failed: %s", e)
        self._safe_send("RESTART")
        self.wait_for_gui_to_delete()

    def find_new_online_match(self):
        time.sleep(2)
        try:
            self.grabber.click_game_next()
        except Exception as e:
            logger.warning("find_new_online_match failed: %s", e)
        self._safe_send("RESTART")
        self.wait_for_gui_to_delete()

    def run(self):
        self._setup_signal_handlers()
        if self.website == "chesscom":
            self.grabber = ChesscomGrabber(self.chrome_url, self.chrome_session_id)
        else:
            self.grabber = LichessGrabber(self.chrome_url, self.chrome_session_id)
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
            "Ponder": "true",
            "Slow Mover": self.slow_mover,
            "Skill Level": self.skill_level
        }
        try:
            stockfish = Stockfish(path=self.stockfish_path, depth=self.stockfish_depth, parameters=parameters)
            self._stockfish = stockfish
        except PermissionError:
            self._safe_send("ERR_PERM")
            logger.error("Stockfish PermissionError: %s", self.stockfish_path)
            return
        except OSError as e:
            self._safe_send("ERR_EXE")
            logger.error("Stockfish OSError: %s %s", self.stockfish_path, e)
            return
        except Exception as e:
            self._safe_send("ERR_EXE")
            logger.error("Stockfish init failed: %s", e)
            return

        try:
            # Board with retry
            for attempt in range(3):
                self.grabber.update_board_elem()
                if self.grabber.get_board() is not None:
                    break
                time.sleep(0.5 * (attempt+1))
            if self.grabber.get_board() is None:
                self._safe_send("ERR_BOARD")
                logger.error("Board not found after retries")
                return

            # Color with retry
            for attempt in range(3):
                self.is_white = self.grabber.is_white()
                if self.is_white is not None:
                    break
                time.sleep(0.4)
            if self.is_white is None:
                self._safe_send("ERR_COLOR")
                logger.error("Could not determine player color")
                return

            move_list = None
            for attempt in range(3):
                move_list = self.grabber.get_move_list()
                if move_list is not None:
                    break
                time.sleep(0.4)
            if move_list is None:
                self._safe_send("ERR_MOVES")
                logger.error("Move list not found")
                return

            score_pattern = r"([0-9]+)\-([0-9]+)"
            if len(move_list) > 0 and re.match(score_pattern, move_list[-1]):
                self._safe_send("ERR_GAMEOVER")
                return

            board = chess.Board()
            for mv in move_list:
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
            self._safe_send("START")
            if len(move_list) > 0:
                self._safe_send("M_MOVE" + ",".join(move_list))

            while True:
                if self._shutdown:
                    return
                if (self.is_white and board.turn == chess.WHITE) or (not self.is_white and board.turn == chess.BLACK):
                    move = None
                    move_count = len(board.move_stack)
                    if self.bongcloud and move_count <= 3:
                        if move_count == 0:
                            move = "e2e3"
                        elif move_count == 1:
                            move = "e7e6"
                        elif move_count == 2:
                            move = "e1e2"
                        elif move_count == 3:
                            move = "e8e7"
                        if move and not board.is_legal(chess.Move.from_uci(move)):
                            move = stockfish.get_best_move()
                    else:
                        try:
                            move = stockfish.get_best_move()
                        except Exception as e:
                            logger.error("get_best_move failed: %s", e)
                            self._safe_send("ERR_TIMEOUT")
                            time.sleep(0.5)
                            continue

                    if not move:
                        logger.warning("get_best_move returned None/empty")
                        time.sleep(0.3)
                        continue

                    if board.turn == chess.WHITE:
                        white_best_moves.append(move)
                    else:
                        black_best_moves.append(move)

                    self_moved = False
                    if self.enable_manual_mode:
                        try:
                            move_start_pos, move_end_pos = self.get_move_pos(move)
                            self.overlay_queue.put([
                                ((int(move_start_pos[0]), int(move_start_pos[1])), (int(move_end_pos[0]), int(move_end_pos[1]))),
                            ])
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
                            time.sleep(0.05)
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
                            self._safe_send("ERR_TIMEOUT")
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
                                self.grabber.make_mouseless_move(move, move_count + 1)
                            except Exception as e:
                                logger.warning("mouseless move failed: %s", e)
                                self._safe_send("ERR_DISCONNECT")
                        else:
                            try:
                                self.make_move(move)
                            except Exception as e:
                                logger.error("make_move failed: %s", e)
                                self._safe_send("ERR_DISCONNECT")

                    self._clear_overlay_queue_safe()
                    self.send_eval_data(stockfish, board, white_moves, white_best_moves, black_moves, black_best_moves)
                    self._safe_send("S_MOVE" + move_san)

                    if board.is_checkmate():
                        if self.enable_non_stop_puzzles and self.grabber.is_game_puzzles():
                            self.go_to_next_puzzle()
                        elif self.enable_non_stop_matches and not self.enable_non_stop_puzzles:
                            self.find_new_online_match()
                        return
                    time.sleep(0.1)

                # Wait for opponent
                previous_move_list = move_list.copy()
                consecutive_errors = 0
                while True:
                    if self._shutdown:
                        return
                    try:
                        if self.grabber.is_game_over():
                            if self.enable_non_stop_puzzles and self.grabber.is_game_puzzles():
                                self.go_to_next_puzzle()
                            elif self.enable_non_stop_matches and not self.enable_non_stop_puzzles:
                                self.find_new_online_match()
                            return
                    except (StaleElementReferenceException, WebDriverException) as e:
                        logger.debug("is_game_over stale: %s", e)
                        time.sleep(0.2)
                        continue

                    try:
                        new_move_list = self.grabber.get_move_list()
                    except (StaleElementReferenceException, WebDriverException) as e:
                        consecutive_errors += 1
                        logger.debug("get_move_list error in opponent wait: %s", e)
                        if consecutive_errors > 5:
                            self._safe_send("ERR_DISCONNECT")
                            time.sleep(0.5)
                            consecutive_errors = 0
                        time.sleep(0.3)
                        continue

                    if new_move_list is None:
                        consecutive_errors += 1
                        if consecutive_errors > 3:
                            logger.warning("get_move_list returned None repeatedly – disconnect?")
                            self._safe_send("ERR_DISCONNECT")
                            time.sleep(0.5)
                        time.sleep(0.3)
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

                    # Case 2: takeback / abort – move count decreased but not zero
                    if len(new_move_list) < len(previous_move_list) and len(new_move_list) != 0:
                        logger.info("Takeback/abort detected: %d -> %d", len(previous_move_list), len(new_move_list))
                        # Reconcile board
                        new_board = chess.Board()
                        try:
                            for san in new_move_list:
                                new_board.push_san(san)
                            board = new_board
                            stockfish.set_position([m.uci() for m in board.move_stack])
                        except Exception as e:
                            logger.warning("Takeback resync failed: %s", e)
                            board, _ = self._reconcile_board_fen(board, new_move_list)
                            stockfish.set_position([m.uci() for m in board.move_stack])
                        # Trim accuracy lists
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
                        self._safe_send("RESTART")
                        self.wait_for_gui_to_delete()
                        self.send_eval_data(stockfish, board)
                        self._safe_send("START")
                        if len(move_list) > 0:
                            self._safe_send("M_MOVE" + ",".join(move_list))
                        break

                    # Case 3: FEN mismatch when same move count but different moves (e.g., undo+ different line)
                    if len(new_move_list) == len(previous_move_list) and new_move_list != previous_move_list:
                        logger.info("Move list changed without length increase – possible takeback with new move")
                        new_board = chess.Board()
                        try:
                            for san in new_move_list:
                                new_board.push_san(san)
                            board = new_board
                            stockfish.set_position([m.uci() for m in board.move_stack])
                            move_list = new_move_list
                            self._safe_send("RESTART")
                            self.wait_for_gui_to_delete()
                            self.send_eval_data(stockfish, board)
                            self._safe_send("START")
                            self._safe_send("M_MOVE" + ",".join(move_list))
                            break
                        except Exception as e:
                            logger.debug("FEN mismatch handling error: %s", e)

                    # Normal: opponent made a move
                    if len(new_move_list) > len(previous_move_list):
                        # Validate FEN before accepting
                        try:
                            tmp = chess.Board()
                            for san in new_move_list:
                                tmp.push_san(san)
                            move_list = new_move_list
                            break
                        except Exception as e:
                            logger.warning("Invalid SAN in new_move_list: %s %s", new_move_list, e)
                            # Try to resync by pushing last move only
                            move_list = new_move_list
                            break

                    time.sleep(0.15)

                # Apply opponent move to board
                try:
                    move = move_list[-1]
                    prev_board = board.copy()
                    mover_is_white = (prev_board.turn == chess.WHITE)
                    board.push_san(move)
                    move_uci = prev_board.parse_san(move).uci()
                    if prev_board.turn == chess.WHITE:
                        white_moves.append(move_uci)
                    else:
                        black_moves.append(move_uci)
                    try:
                        best_move = stockfish.get_best_move_time(300)
                    except Exception as e:
                        logger.debug("get_best_move_time failed: %s", e)
                        best_move = None
                    if best_move:
                        if prev_board.turn == chess.WHITE:
                            white_best_moves.append(best_move)
                        else:
                            black_best_moves.append(best_move)
                    try:
                        stockfish.make_moves_from_current_position([str(board.peek())])
                    except Exception as e:
                        logger.debug("make_moves_from_current_position (opponent) failed: %s", e)
                        stockfish.set_position([m.uci() for m in board.move_stack])
                    # P1: record cp loss for opponent move
                    self._record_cp_loss(stockfish, mover_is_white)
                    self.send_eval_data(stockfish, board, white_moves, white_best_moves, black_moves, black_best_moves)
                    self._safe_send("S_MOVE" + move)
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

                if board.is_checkmate():
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
                self._safe_send("ERR_TIMEOUT")
            elif isinstance(e, WebDriverException):
                self._safe_send("ERR_DISCONNECT")
            elif isinstance(e, StaleElementReferenceException):
                self._safe_send("ERR_STALE")
            else:
                # Generic engine error – include info via ERR_ENGINE
                self._safe_send("ERR_ENGINE|" + str(e)[:200])
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
        except Exception:
            pass
        self._safe_send("RESTART")
        self.wait_for_gui_to_delete()
        self.send_eval_data(stockfish, board)
        self._safe_send("START")

    def _clear_overlay_queue_safe(self):
        try:
            self.overlay_queue.put([])
        except Exception:
            pass
        # Also drain if needed
        # We put empty list to clear arrows; eval bar cleared via next eval

    def send_eval_data(self, stockfish, board, white_moves=None, white_best_moves=None, black_moves=None, black_best_moves=None):
        try:
            try:
                eval_data = stockfish.get_evaluation()
            except Exception as e:
                logger.debug("get_evaluation failed: %s", e)
                eval_data = None
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
            try:
                wdl_stats = stockfish.get_wdl_stats()
            except Exception as e:
                logger.debug("get_wdl_stats exception: %s", e)
                wdl_stats = [0, 0, 0]
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
            data = f"EVAL|{eval_str}|{wdl_str}|{material}|{bot_accuracy}|{opponent_accuracy}"
            self._safe_send(data)
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
                self.overlay_queue.put(overlay_data)
            except Exception as e:
                logger.debug("overlay queue put error: %s", e)
        except Exception as e:
            logger.warning("Error sending evaluation: %s", e)

    def calculate_material_advantage(self, board):
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
