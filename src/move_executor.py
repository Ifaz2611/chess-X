"""MoveExecutor – square -> screen mapping extracted from StockfishBot.

No behavior change: same board-flip compensation, promotion handling.
"""
from __future__ import annotations

import time

from utilities import char_to_num, get_logger

logger = get_logger("move_executor")


class MoveExecutor:
    def __init__(self, grabber, is_white: bool | None, mouse_latency: float):
        self.grabber = grabber
        self.is_white = is_white
        self.mouse_latency = mouse_latency

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
        return x, y

    def get_move_pos(self, move: str):
        start_pos_x, start_pos_y = self.move_to_screen_pos(move[0:2])
        end_pos_x, end_pos_y = self.move_to_screen_pos(move[2:4])
        return (start_pos_x, start_pos_y), (end_pos_x, end_pos_y)

    def make_move(self, move: str):
        import pyautogui
        start_pos, end_pos = self.get_move_pos(move)
        pyautogui.moveTo(start_pos[0], start_pos[1])
        time.sleep(self.mouse_latency)
        pyautogui.dragTo(end_pos[0], end_pos[1])
        if len(move) == 5:
            time.sleep(0.15)
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
                pyautogui.moveTo(x=end_pos_x, y=end_pos_y)
                pyautogui.click(button='left')
                logger.debug("Promotion click %s -> %s (promo %s)", move, promo_square, promo)
            except Exception as e:
                logger.warning("Promotion click failed for %s: %s", move, e)
