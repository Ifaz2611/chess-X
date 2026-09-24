import math
import sys
import os
import threading
import logging
import platform
from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QGuiApplication, QPolygon, QFont
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

        # Platform-aware window setup
        self.screen = QGuiApplication.screens()[0] if QGuiApplication.screens() else None
        if self.screen is not None:
            # Handle macOS retina: size is in device pixels, but coords from move_executor are logical
            dpr = _get_dpr()
            w = self.screen.size().width()
            h = self.screen.size().height()
            # On macOS retina, Qt reports size in device pixels; we keep as is for overlay canvas
            self.setFixedWidth(w)
            self.setFixedHeight(h)
            logger.info("Overlay screen %dx%d DPR=%s Wayland=%s macOS=%s", w, h, dpr, _is_wayland(), _is_macos())
        else:
            # Fallback if no screen yet (offscreen tests)
            self.setFixedWidth(1920)
            self.setFixedHeight(1080)

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

        # A list of QPolygon objects containing the points of the arrows
        self.arrows = []
        
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

        # Retina-aware scaling for arrow coords (if DPR != 1, incoming coords are logical, overlay is device)
        # We store DPR and scale incoming arrow coords to device pixels for correct placement
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

    def toggle_visibility(self):
        """Toggle overlay hidden/shown (for hotkey)."""
        self._visible = not self._visible
        self.setVisible(self._visible)
        if self._visible:
            self.update()
        logger.info("Overlay visibility toggled: %s", self._visible)

    def message_queue_thread_fn(self):
        """
        This thread is used to receive messages from the stockfish message queue
        and update the arrows
        """

        while True:
            message = self.stockfish_queue.get()
            # Check for opacity/toggle commands (dict with keys)
            try:
                if isinstance(message, dict) and "overlay_opacity" in message:
                    self.set_opacity(float(message["overlay_opacity"]))
                    continue
                if isinstance(message, dict) and message.get("overlay_toggle"):
                    self.toggle_visibility()
                    continue
            except Exception:
                pass
            # Typed overlay messages first (no behavior change, just new types)
            try:
                import protocol as proto  # lazy to avoid circular import at top
                if isinstance(message, proto.OverlayArrows):
                    self.set_arrows(message.arrows)
                    continue
                if isinstance(message, proto.OverlayClear):
                    self.set_arrows([])
                    continue
                if isinstance(message, proto.OverlayEval):
                    if message.board_position is not None:
                        self.board_position = message.board_position
                        self.update_eval_bar_position()
                    if message.is_white is not None:
                        self.is_white = message.is_white
                    self.update_eval_bar(message.eval_value, message.eval_type)
                    continue
            except Exception:
                pass
            if isinstance(message, list):
                # Arrow data (legacy)
                self.set_arrows(message)
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
        Update the evaluation bar position based on the chess board position
        """
        if not self.board_position:
            return
            
        # Position the eval bar to the left of the board with a small margin
        self.eval_bar_x = self.board_position['x'] - self.eval_bar_width - self.eval_bar_margin
        
        # Make the eval bar the exact same height as the board
        self.eval_bar_height = self.board_position['height']
        self.eval_bar_y = self.board_position['y']
            
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
        This function is used to set the arrows to be drawn on the screen
        Args:
            arrows: A list of tuples containing the start and end position of the arrows
            in the form of ((start_point, end_point), (start_point, end_point))
        Returns:
            None
        """

        self.arrows = []
        for arrow in arrows:
            try:
                dpr = getattr(self, "_dpr", 1.0) or 1.0
                if dpr != 1.0:
                    # Scale logical -> device
                    s = arrow[0]
                    e = arrow[1]
                    arrow = ((int(s[0]*dpr), int(s[1]*dpr)), (int(e[0]*dpr), int(e[1]*dpr)))
            except Exception:
                pass
            poly = self.get_arrow_polygon(
                QPoint(arrow[0][0], arrow[0][1]),
                QPoint(arrow[1][0], arrow[1][1])
            )
            self.arrows.append(poly)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        
        # Respect opacity for arrows as well
        # Draw arrows
        painter.setPen(QPen(Qt.GlobalColor.red, 1, Qt.PenStyle.NoPen))
        painter.setBrush(QBrush(QColor(255, 0, 0, 122), Qt.BrushStyle.SolidPattern))
        for arrow in self.arrows:
            painter.drawPolygon(arrow)
        
        # Draw evaluation bar if visible
        if self.eval_bar_visible:
            self.draw_eval_bar(painter)
        
        painter.end()
    
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
        
        # Draw the center line
        center_y = self.eval_bar_y + (self.eval_bar_height // 2)
        painter.setPen(QPen(QColor(100, 100, 100, 150), 1))
        painter.drawLine(
            self.eval_bar_x, 
            center_y, 
            self.eval_bar_x + self.eval_bar_width, 
            center_y
        )
        
        # Draw evaluation text
        painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        
        # Format the display text
        display_text = self.eval_text
        if self.eval_type == "cp" and float(self.eval_value) > 0:
            display_text = "+" + display_text
            
        # Position text at the bottom
        text_rect = QRect(
            self.eval_bar_x, 
            self.eval_bar_y + self.eval_bar_height - 20, 
            self.eval_bar_width, 
            20
        )
        
        # Draw text background
        painter.setBrush(QBrush(QColor(60, 60, 60, 180)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(text_rect)
        
        # Draw text
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, display_text)

    def get_arrow_polygon(self, start_point, end_point):
        """
        This function is used to get the polygon for the arrow
        Args:
            start_point: The start point of the arrow
            end_point: The end point of the arrow
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

            arrow_height = 25
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
