import logging
import math
import os
import platform
import sys
import threading

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QGuiApplication, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import QApplication, QWidget

try:
    from utilities import get_logger
    logger = get_logger("overlay")
except Exception:
    logger = logging.getLogger("overlay")

# Platform helpers (lazy to avoid import cycles)
def _is_wayland():
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))

def _is_macos():
    return platform.system() == "Darwin"

def _get_dpr():
    try:
        # Use primary screen DPR
        screens = QGuiApplication.screens()
        if screens:
            return float(screens[0].devicePixelRatio())
    except Exception:
        pass
    return 1.0


class OverlayScreen(QWidget):
    def __init__(self, stockfish_queue):
        super().__init__()
        self.stockfish_queue = stockfish_queue

        # Multi-monitor & DPI-aware virtual geometry setup
        screens = QGuiApplication.screens()
        self._virtual_origin = (0, 0)
        self.screen = screens[0] if screens else None
        if screens:
            primary = QGuiApplication.primaryScreen() or screens[0]
            v_geom = primary.virtualGeometry() if hasattr(primary, "virtualGeometry") else None
            if v_geom and not v_geom.isEmpty():
                self.setGeometry(v_geom)
                self._virtual_origin = (v_geom.x(), v_geom.y())
                logger.info("Overlay virtual desktop: %dx%d origin=(%d,%d) screens=%d",
                            v_geom.width(), v_geom.height(), v_geom.x(), v_geom.y(), len(screens))
            else:
                s_geom = primary.geometry()
                self.setGeometry(s_geom)
                self._virtual_origin = (s_geom.x(), s_geom.y())
        else:
            self.setFixedWidth(1920)
            self.setFixedHeight(1080)
            self._virtual_origin = (0, 0)

        # Transparent + click-through + always-on-top
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        # Wayland layer-shell: try to use LayerShell if available (via env hint)
        # We cannot directly use wlr-layer-shell without extra lib, but we can set
        # Tool flag which works better on Wayland compositors (GNOME/KDE) as overlay
        if _is_wayland():
            # On Wayland, FramelessWindowHint+WindowStaysOnTopHint may not produce
            # always-on-top without xdg-layer-shell. Fall back to Tool + bypass compositor hint
            flags |= Qt.WindowType.Tool
            # Hint to Qt to use wayland platform if not already
            # Check if layer-shell plugin available via env
            if os.environ.get("CHESSX_WAYLAND_LAYER_SHELL") == "1":
                try:
                    # If python-layer-shell is installed, we could use it – probe
                    import importlib.util
                    if importlib.util.find_spec("layershell") is not None:
                        logger.info("layer-shell available, using it for overlay")
                except Exception:
                    pass
            logger.warning("Wayland detected – overlay uses Tool flag; for best results use: QT_QPA_PLATFORM=xcb or install layer-shell-qt. If overlay is invisible, export QT_QPA_PLATFORM=xcb.")
            # Also try to enable compositor bypass
            self.setAttribute(Qt.WidgetAttribute.WA_X11DoNotAcceptFocus, True)

        if _is_macos():
            # macOS specific: ensure overlay appears over fullscreen Chrome
            # WA_MacAlwaysShowToolWindow keeps tool windows visible
            try:
                self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
            except Exception:
                pass
            # On macOS, WindowStaysOnTopHint + Tool may be needed for space handling
            flags |= Qt.WindowType.Tool
            logger.info("macOS overlay: using Tool + WA_MacAlwaysShowToolWindow for visibility over Chrome. Grant Screen Recording if overlay is invisible.")

        self.setWindowFlags(flags)

        # Opacity / visibility controls (P2 overlay toggle)
        self._opacity = 1.0  # 0.0-1.0
        self._visible = True  # hotkey toggle
        self.eval_bar_side = "left"  # "left" or "right" side of board

        # A list of QPolygon objects containing the points of the arrows
        self.arrows = []

        # PV / threat lines (engine analysis): raw UCIs resolved at paint time
        # so they track the latest board_position / orientation.
        self._pv_ucis: list[str] = []
        self._threat_uci: str = ""
        
        # Evaluation bar properties
        self.eval_bar_visible = False
        self.eval_value = 0.0
        self.eval_type = "cp"  # "cp" for centipawns or "mate" for mate
        self.eval_text = "0.00"
        self.is_white = True  # Default assumption
        
        # Board position, will be updated
        self.board_position = None
        
        # Evaluation bar dimensions
        self.eval_bar_width = 40
        self.eval_bar_height = 400
        self.eval_bar_x = 20  # Default x position
        self.eval_bar_y = (self.height() - self.eval_bar_height) // 2  # Default y position
        self.eval_bar_margin = 15  # Margin between board and eval bar

        # Calibration helper mode
        self.calibration_active = False
        self._dpr = _get_dpr() if self.screen else 1.0

        # Start the message queue thread (daemon so overlay can exit cleanly)
        self.message_queue_thread = threading.Thread(target=self.message_queue_thread_fn, daemon=True)
        self.message_queue_thread.start()

    # -- opacity / toggle helpers --
    def set_opacity(self, opacity: float):
        """Set overlay opacity 0.0-1.0."""
        self._opacity = max(0.0, min(1.0, float(opacity)))
        self.setWindowOpacity(self._opacity)
        self.update()

    def set_visibility(self, visible: bool):
        """Explicitly show or hide overlay."""
        self._visible = bool(visible)
        self.setVisible(self._visible)
        if self._visible:
            self.update()

    def toggle_visibility(self):
        """Toggle overlay hidden/shown (for hotkey)."""
        self._visible = not self._visible
        self.setVisible(self._visible)
        if self._visible:
            self.update()
        logger.info("Overlay visibility toggled: %s", self._visible)

    def set_eval_bar_side(self, side: str):
        """Set eval bar side ('left' or 'right')."""
        self.eval_bar_side = "right" if str(side).lower() == "right" else "left"
        self.update_eval_bar_position()
        self.update()

    def message_queue_thread_fn(self):
        """
        This thread is used to receive messages from the stockfish message queue
        and update the arrows
        """

        while True:
            message = self.stockfish_queue.get()
            # Check for opacity/toggle commands (dict with keys)
            try:
                if isinstance(message, dict):
                    if "overlay_opacity" in message:
                        self.set_opacity(float(message["overlay_opacity"]))
                    if "eval_bar_side" in message:
                        self.set_eval_bar_side(message["eval_bar_side"])
                    if "overlay_visible" in message:
                        self.set_visibility(bool(message["overlay_visible"]))
                    if message.get("overlay_toggle"):
                        self.toggle_visibility()
                    if "overlay_opacity" in message or "eval_bar_side" in message or "overlay_visible" in message or message.get("overlay_toggle"):
                        continue
            except Exception:
                pass
            # Typed overlay messages first (no behavior change, just new types)
            try:
                import protocol as proto  # lazy to avoid circular import at top
                if isinstance(message, proto.OverlayConfig):
                    if message.opacity is not None:
                        self.set_opacity(message.opacity)
                    if message.side is not None:
                        self.set_eval_bar_side(message.side)
                    if message.visible is not None:
                        self.set_visibility(message.visible)
                    continue
                if isinstance(message, proto.OverlayArrows):
                    self.set_arrows(message.arrows)
                    continue
                if isinstance(message, proto.OverlayClear):
                    self.set_arrows([])
                    self.set_pv_lines([], "")
                    continue
                if isinstance(message, proto.OverlayEval):
                    if message.board_position is not None:
                        self.board_position = message.board_position
                        self.update_eval_bar_position()
                    if message.is_white is not None:
                        self.is_white = message.is_white
                    self.update_eval_bar(message.eval_value, message.eval_type)
                    continue
                if isinstance(message, proto.OverlayLines):
                    ucis = [entry.get("uci", "") for entry in (message.lines or []) if isinstance(entry, dict)]
                    self.set_pv_lines([u for u in ucis if u], message.threat_uci or "")
                    continue
                if isinstance(message, proto.OverlayCalibration):
                    self.calibration_active = bool(message.active)
                    if message.board_position is not None:
                        self.board_position = message.board_position
                        self.update_eval_bar_position()
                    if message.is_white is not None:
                        self.is_white = message.is_white
                    self.update()
                    continue
            except Exception:
                pass
            if isinstance(message, list):
                # Arrow data (legacy)
                self.set_arrows(message)
            elif isinstance(message, dict) and "pv_lines" in message:
                # PV / threat lines (legacy dict form of OverlayLines)
                try:
                    ucis = [entry.get("uci", "") for entry in (message.get("pv_lines") or []) if isinstance(entry, dict)]
                    self.set_pv_lines([u for u in ucis if u], message.get("threat_uci") or "")
                except Exception:
                    pass
            elif isinstance(message, dict) and "calibration_active" in message:
                self.calibration_active = bool(message.get("calibration_active", True))
                if "board_position" in message:
                    self.board_position = message["board_position"]
                    self.update_eval_bar_position()
                if "is_white" in message:
                    self.is_white = message["is_white"]
                self.update()
            elif isinstance(message, dict) and "eval" in message:
                # Evaluation data (legacy)
                eval_value = message["eval"]
                eval_type = message.get("eval_type", "cp")
                
                # Update board position if provided
                if "board_position" in message:
                    self.board_position = message["board_position"]
                    self.update_eval_bar_position()
                
                # Update bot color if provided
                if "is_white" in message:
                    self.is_white = message["is_white"]
                
                self.update_eval_bar(eval_value, eval_type)
    
    def update_eval_bar_position(self):
        """
        Update the evaluation bar position based on the chess board position,
        translated into local widget coordinates for multi-monitor setups.
        """
        if not self.board_position:
            return
            
        vx, vy = getattr(self, "_virtual_origin", (0, 0))
        # Position the eval bar to either left or right of the board with margin
        if getattr(self, "eval_bar_side", "left") == "right":
            self.eval_bar_x = int(self.board_position['x'] + self.board_position['width'] + self.eval_bar_margin - vx)
        else:
            self.eval_bar_x = int(self.board_position['x'] - self.eval_bar_width - self.eval_bar_margin - vx)
        
        # Make the eval bar the exact same height as the board
        self.eval_bar_height = int(self.board_position['height'])
        self.eval_bar_y = int(self.board_position['y'] - vy)
            
    def update_eval_bar(self, eval_value, eval_type="cp"):
        """
        Update the evaluation bar with a new value
        Args:
            eval_value: The evaluation value (float for centipawns, int for mate)
            eval_type: "cp" for centipawns or "mate" for mate
        """
        self.eval_bar_visible = True
        self.eval_type = eval_type
        self.eval_value = eval_value
        
        # Format text display
        if eval_type == "cp":
            self.eval_text = f"{eval_value:.2f}"
        else:  # mate
            self.eval_text = f"M{eval_value}"
        
        self.update()

    def set_arrows(self, arrows):
        """
        Set arrows to be drawn on screen, translated by virtual desktop origin.
        """
        self.arrows = []
        vx, vy = getattr(self, "_virtual_origin", (0, 0))
        for arrow in arrows:
            try:
                s = arrow[0]
                e = arrow[1]
                p1 = QPoint(int(s[0] - vx), int(s[1] - vy))
                p2 = QPoint(int(e[0] - vx), int(e[1] - vy))
                poly = self.get_arrow_polygon(p1, p2)
                self.arrows.append(poly)
            except Exception as ex:
                logger.debug("set_arrows conversion error: %s", ex)
        self.update()

    # -- PV / threat lines (engine analysis) --

    def set_pv_lines(self, ucis: list, threat_uci: str = ""):
        """Store PV + threat UCIs; resolved to pixels at paint time."""
        try:
            self._pv_ucis = [u for u in (ucis or []) if isinstance(u, str) and len(u) >= 4]
        except Exception:
            self._pv_ucis = []
        self._threat_uci = threat_uci if isinstance(threat_uci, str) else ""
        self.update()

    def _square_center(self, sq: str):
        """Pixel center of a square like 'e4' using board_position/is_white in widget coords."""
        if not self.board_position:
            return None
        try:
            f = ord(sq[0]) - ord("a")
            r = int(sq[1]) - 1
            if not (0 <= f <= 7 and 0 <= r <= 7):
                return None
            sq_w = self.board_position["width"] / 8.0
            sq_h = self.board_position["height"] / 8.0
            col = f if self.is_white else 7 - f
            row = (7 - r) if self.is_white else r
            vx, vy = getattr(self, "_virtual_origin", (0, 0))
            x = (self.board_position["x"] + col * sq_w + sq_w / 2) - vx
            y = (self.board_position["y"] + row * sq_h + sq_h / 2) - vy
            return QPoint(int(x), int(y))
        except Exception:
            return None

    def _uci_polygon(self, uci: str, height: int = 18):
        try:
            if not uci or len(uci) < 4:
                return None
            start = self._square_center(uci[0:2])
            end = self._square_center(uci[2:4])
            if start is None or end is None:
                return None
            return self.get_arrow_polygon(start, end, arrow_height=height)
        except Exception as e:
            logger.debug("_uci_polygon error: %s", e)
            return None

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)

        # Respect opacity for arrows as well
        painter.setPen(QPen(Qt.GlobalColor.red, 1, Qt.PenStyle.NoPen))
        painter.setBrush(QBrush(QColor(255, 0, 0, 122), Qt.BrushStyle.SolidPattern))
        for arrow in self.arrows:
            painter.drawPolygon(arrow)

        # Draw PV lines (thinner, distinct colors, skip best-move duplicate)
        pv_colors = [QColor(80, 140, 255, 150), QColor(80, 200, 120, 150),
                     QColor(180, 130, 255, 150), QColor(255, 200, 80, 150)]
        try:
            for i, uci in enumerate(getattr(self, "_pv_ucis", []) or []):
                poly = self._uci_polygon(uci, height=16)
                if poly is None:
                    continue
                painter.setBrush(QBrush(pv_colors[i % len(pv_colors)], Qt.BrushStyle.SolidPattern))
                painter.drawPolygon(poly)
            # Threat arrow (opponent reply after null move) in orange
            threat = getattr(self, "_threat_uci", "") or ""
            if threat:
                poly = self._uci_polygon(threat, height=16)
                if poly is not None:
                    painter.setBrush(QBrush(QColor(255, 140, 0, 170), Qt.BrushStyle.SolidPattern))
                    painter.drawPolygon(poly)
        except Exception as e:
            logger.debug("PV paint error: %s", e)

        if self.eval_bar_visible:
            self.draw_eval_bar(painter)

        # Draw calibration alignment pattern if active
        if getattr(self, "calibration_active", False) and self.board_position:
            try:
                self.draw_calibration_targets(painter)
            except Exception as e:
                logger.debug("calibration paint error: %s", e)

        painter.end()

    def draw_calibration_targets(self, painter: QPainter):
        """Draw high-visibility alignment crosshairs and markers for calibration."""
        if not self.board_position:
            return
        vx, vy = getattr(self, "_virtual_origin", (0, 0))
        bx = self.board_position["x"] - vx
        by = self.board_position["y"] - vy
        bw = self.board_position["width"]
        bh = self.board_position["height"]

        # Outer board boundary (dashed cyan)
        painter.setPen(QPen(QColor(0, 220, 255, 220), 2, Qt.PenStyle.DashLine))
        painter.setBrush(QBrush(QColor(0, 220, 255, 25)))
        painter.drawRect(QRect(int(bx), int(by), int(bw), int(bh)))

        # Crosshair helper
        def draw_crosshair(cx, cy, color, size=18, label=""):
            painter.setPen(QPen(color, 2))
            painter.drawLine(int(cx - size), int(cy), int(cx + size), int(cy))
            painter.drawLine(int(cx), int(cy - size), int(cx), int(cy + size))
            if label:
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.setPen(QPen(color, 1))
                painter.drawText(int(cx + 6), int(cy - 6), label)

        # 4 Corners
        draw_crosshair(bx, by, QColor(255, 60, 60, 255), label="TL (a8/h1)")
        draw_crosshair(bx + bw, by, QColor(60, 160, 255, 255), label="TR")
        draw_crosshair(bx, by + bh, QColor(60, 160, 255, 255), label="BL")
        draw_crosshair(bx + bw, by + bh, QColor(60, 160, 255, 255), label="BR")

        # Square centers e2, e4
        e2 = self._square_center("e2")
        if e2:
            draw_crosshair(e2.x(), e2.y(), QColor(60, 255, 80, 255), label="e2")
        e4 = self._square_center("e4")
        if e4:
            draw_crosshair(e4.x(), e4.y(), QColor(60, 255, 80, 255), label="e4")

    def draw_eval_bar(self, painter):
        """
        Draw the evaluation bar on the screen
        Args:
            painter: QPainter object
        """
        # Bar border
        border_rect = QRect(
            self.eval_bar_x - 2, 
            self.eval_bar_y - 2, 
            self.eval_bar_width + 4, 
            self.eval_bar_height + 4
        )
        painter.setPen(QPen(QColor(40, 40, 40), 2))
        painter.setBrush(QBrush(QColor(40, 40, 40, 180)))
        painter.drawRect(border_rect)
        
        # The eval value is already from the player's perspective
        # (positive = advantage for the player, negative = advantage for opponent)
        if self.eval_type == "cp":
            value = max(min(float(self.eval_value), 10.0), -10.0)
            player_advantage = 1.0 / (1.0 + math.exp(-value * 0.5))  # Sigmoid function
        else:  # mate
            mate_value = int(self.eval_value)
            if mate_value > 0:  # Mate for player
                player_advantage = 1.0  # Maximum advantage - full bar
            else:  # Mate for opponent
                player_advantage = 0.0  # Minimum advantage - no bar
        
        # Set colors based on player's color
        if self.is_white:
            bottom_color = QColor(235, 235, 235, 220)  # White
            top_color = QColor(30, 30, 30, 220)       # Black
        else:
            bottom_color = QColor(30, 30, 30, 220)    # Black
            top_color = QColor(235, 235, 235, 220)    # White
        
        # Calculate section heights
        player_height = int(self.eval_bar_height * player_advantage)
        opponent_height = self.eval_bar_height - player_height
        
        # Draw opponent's section (top)
        opponent_rect = QRect(
            self.eval_bar_x, 
            self.eval_bar_y, 
            self.eval_bar_width, 
            opponent_height
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(top_color))
        painter.drawRect(opponent_rect)
        
        # Draw player's section (bottom)
        player_rect = QRect(
            self.eval_bar_x, 
            self.eval_bar_y + opponent_height, 
            self.eval_bar_width, 
            player_height
        )
        painter.setBrush(QBrush(bottom_color))
        painter.drawRect(player_rect)
        
        center_y = self.eval_bar_y + (self.eval_bar_height // 2)
        painter.setPen(QPen(QColor(100, 100, 100, 150), 1))
        painter.drawLine(
            self.eval_bar_x, 
            center_y, 
            self.eval_bar_x + self.eval_bar_width, 
            center_y
        )
        
        painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        
        display_text = self.eval_text
        if self.eval_type == "cp" and float(self.eval_value) > 0:
            display_text = "+" + display_text
            
        text_rect = QRect(
            self.eval_bar_x, 
            self.eval_bar_y + self.eval_bar_height - 20, 
            self.eval_bar_width, 
            20
        )
        
        painter.setBrush(QBrush(QColor(60, 60, 60, 180)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(text_rect)
        
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, display_text)

    def get_arrow_polygon(self, start_point, end_point, arrow_height=25):
        """
        This function is used to get the polygon for the arrow
        Args:
            start_point: The start point of the arrow
            end_point: The end point of the arrow
            arrow_height: Width scale of the arrowhead/shaft (25 = best move,
                smaller for secondary PV lines)
        Returns:
            A QPolygon object containing the points of the arrow
        """

        try:
            dx, dy = start_point.x() - end_point.x(), start_point.y() - end_point.y()

            # Normalize the vector
            leng = math.sqrt(dx ** 2 + dy ** 2)
            if leng == 0:
                return QPolygon([start_point, end_point])
            norm_x, norm_y = dx / leng, dy / leng

            # Get the perpendicular vector
            perp_x = -norm_y
            perp_y = norm_x
            left_x = end_point.x() + arrow_height * norm_x * 1.5 + arrow_height * perp_x
            left_y = end_point.y() + arrow_height * norm_y * 1.5 + arrow_height * perp_y

            right_x = end_point.x() + arrow_height * norm_x * 1.5 - arrow_height * perp_x
            right_y = end_point.y() + arrow_height * norm_y * 1.5 - arrow_height * perp_y

            point2 = QPoint(int(left_x), int(left_y))
            point3 = QPoint(int(right_x), int(right_y))

            mid_point1 = QPoint(int((2 / 5) * point2.x() + (3 / 5) * point3.x()), int((2 / 5) * point2.y() + (3 / 5) * point3.y()))
            mid_point2 = QPoint(int((3 / 5) * point2.x() + (2 / 5) * point3.x()), int((3 / 5) * point2.y() + (2 / 5) * point3.y()))

            start_left = QPoint(int(start_point.x() + (arrow_height / 5) * perp_x), int(start_point.y() + (arrow_height / 5) * perp_y))
            start_right = QPoint(int(start_point.x() - (arrow_height / 5) * perp_x), int(start_point.y() - (arrow_height / 5) * perp_y))

            return QPolygon([end_point, point2, mid_point1, start_right, start_left, mid_point2, point3])
        except Exception as e:
            logger.debug("get_arrow_polygon error: %s", e, exc_info=True)
            return QPolygon([start_point, end_point])


def run(stockfish_queue):
    """
    This function is used to run the overlay
    Args:
        stockfish_queue: The message queue used to communicate with the stockfish thread
    Returns:
        None
    """

    # Wayland: allow fallback to xcb if native wayland fails for overlay
    if _is_wayland() and os.environ.get("QT_QPA_PLATFORM") is None:
        # Don't force, but log hint; user can set QT_QPA_PLATFORM=xcb externally
        logger.info("Wayland overlay: QT_QPA_PLATFORM not set, Qt will use wayland. If overlay invisible, run with QT_QPA_PLATFORM=xcb python src/gui.py")
    # macOS: check permissions before showing overlay
    if _is_macos():
        try:
            from platform_info import check_macos_permissions
            ok, msg = check_macos_permissions()
            if not ok:
                logger.warning("macOS permissions: %s", msg)
        except Exception:
            pass

    app = QApplication(sys.argv)
    overlay = OverlayScreen(stockfish_queue)
    overlay.show()
    app.exec()
