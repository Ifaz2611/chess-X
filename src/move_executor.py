from __future__ import annotations

import time

from utilities import char_to_num, get_logger

logger = get_logger("move_executor")


class MoveExecutor:
    def __init__(self, grabber, is_white: bool | None, mouse_latency: float, input_backend=None):
        self.grabber = grabber
        self.is_white = is_white
        self.mouse_latency = mouse_latency
        # Allow injection for tests; otherwise auto-detect
        if input_backend is not None:
            self._backend = input_backend
        else:
            try:
                from input_backend import get_input_backend
                self._backend = get_input_backend()
            except Exception as e:
                logger.debug("get_input_backend failed, falling back to pyautogui directly: %s", e)
                self._backend = None
        # DPI scaling factor (for logging / future correction)
        try:
            from platform_info import get_scaling_factor
            self._scale = get_scaling_factor()
            if self._scale != 1.0:
                logger.info("Display scaling factor: %s", self._scale)
        except Exception:
            self._scale = 1.0

    def _get_backend(self):
        if self._backend is not None:
            return self._backend
        # Fallback shim around pyautogui
        try:
            import pyautogui  # type: ignore
            class _Shim:
                def moveTo(self, x, y, duration=0.0):
                    return pyautogui.moveTo(x, y, duration=duration)
                def dragTo(self, x, y, duration=0.0, button="left"):
                    return pyautogui.dragTo(x, y, duration=duration, button=button)
                def click(self, x=None, y=None, button="left"):
                    if x is not None and y is not None:
                        return pyautogui.click(x=x, y=y, button=button)
                    return pyautogui.click(button=button)
            return _Shim()
        except Exception as e:
            logger.warning("No input backend available and pyautogui import failed: %s", e)
            # Dummy no-op
            class _Dummy:
                def moveTo(self, *a, **kw): pass
                def dragTo(self, *a, **kw): pass
                def click(self, *a, **kw): pass
            return _Dummy()

    def move_to_screen_pos(self, move: str):
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
        # On macOS retina, Qt overlay uses device pixels but pyautogui uses logical points.
        # We keep logical coords for input; overlay scales its rendering by DPR.
        # If scaling factor !=1.0 and backend is not retina-aware, log but don't mutate
        # (mutating would break X11). Future: apply correction via platform_info.apply_retina_correction
        return x, y

    def get_move_pos(self, move: str):
        start_pos_x, start_pos_y = self.move_to_screen_pos(move[0:2])
        end_pos_x, end_pos_y = self.move_to_screen_pos(move[2:4])
        return (start_pos_x, start_pos_y), (end_pos_x, end_pos_y)

    def make_move(self, move: str):
        backend = self._get_backend()
        start_pos, end_pos = self.get_move_pos(move)
        try:
            backend.moveTo(start_pos[0], start_pos[1])
        except Exception as e:
            logger.debug("backend moveTo failed, trying pyautogui fallback: %s", e)
            try:
                import pyautogui
                pyautogui.moveTo(start_pos[0], start_pos[1])
            except Exception:
                pass
        time.sleep(self.mouse_latency)
        try:
            backend.dragTo(end_pos[0], end_pos[1])
        except Exception as e:
            logger.debug("backend dragTo failed, trying pyautogui fallback: %s", e)
            try:
                import pyautogui
                pyautogui.dragTo(end_pos[0], end_pos[1])
            except Exception:
                pass
        if len(move) == 5:
            time.sleep(0.04)
            promo = move[4].lower()
            target_file = move[2]
            target_rank = int(move[3])
            is_white_promo = target_rank == 8
            promo_offsets = {"q": 0, "n": 1, "r": 2, "b": 3}
            offset = promo_offsets.get(promo, 0)
            if promo == "q":
                promo_rank = target_rank
            else:
                if is_white_promo:
                    promo_rank = target_rank - offset
                else:
                    promo_rank = target_rank + offset
            promo_rank = max(1, min(8, promo_rank))
            promo_square = f"{target_file}{promo_rank}"
            try:
                end_pos_x, end_pos_y = self.move_to_screen_pos(promo_square)
                backend.moveTo(x=end_pos_x, y=end_pos_y)
                backend.click(button='left')
                logger.debug("Promotion click %s -> %s (promo %s) via %s", move, promo_square, promo, getattr(backend, 'name', 'unknown'))
            except Exception as e:
                logger.warning("Promotion click failed for %s: %s", move, e)
                # try pyautogui fallback
                try:
                    import pyautogui
                    end_pos_x, end_pos_y = self.move_to_screen_pos(promo_square)
                    pyautogui.moveTo(x=end_pos_x, y=end_pos_y)
                    pyautogui.click(button='left')
                except Exception:
                    pass
