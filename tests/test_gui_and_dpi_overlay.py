import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_grabber_viewport_metrics_maximized():
    """Verify get_top_left_corner properly calculates maximized window offsets."""
    from grabbers.chesscom_grabber import ChesscomGrabber
    with patch("grabbers.grabber.attach_to_session") as mock_attach:
        fake_driver = MagicMock()
        mock_attach.return_value = fake_driver
        grabber = ChesscomGrabber("http://fake", "sess")

    # In maximized Chrome on a 1920x1080 screen:
    # outerWidth == innerWidth (borders hidden), top chrome = 80px (title/tabs/URL), scroll = 0
    fake_driver.execute_script.return_value = {
        "screenX": 0,
        "screenY": 0,
        "outerWidth": 1920,
        "outerHeight": 1080,
        "innerWidth": 1920,
        "innerHeight": 1000,
        "scrollX": 0,
        "scrollY": 0,
        "devicePixelRatio": 1.0,
        "isMaximized": True,
        "borderLeft": 0.0,
        "borderBottom": 0.0,
        "topChrome": 80.0,
    }

    x, y = grabber.get_top_left_corner()
    assert x == 0.0
    assert y == 80.0


def test_grabber_viewport_metrics_non_maximized():
    """Verify get_top_left_corner subtracts bottom border from top chrome in non-maximized mode."""
    from grabbers.chesscom_grabber import ChesscomGrabber
    with patch("grabbers.grabber.attach_to_session") as mock_attach:
        fake_driver = MagicMock()
        mock_attach.return_value = fake_driver
        grabber = ChesscomGrabber("http://fake", "sess")

    # In non-maximized Chrome:
    # window at (100, 150)
    # outerWidth: 1016, innerWidth: 1000 -> horizDiff: 16 -> borderLeft = 8.0, borderBottom = 8.0
    # outerHeight: 896, innerHeight: 800 -> vertDiff: 96 -> topChrome = 96 - 8 = 88.0
    fake_driver.execute_script.return_value = {
        "screenX": 100,
        "screenY": 150,
        "outerWidth": 1016,
        "outerHeight": 896,
        "innerWidth": 1000,
        "innerHeight": 800,
        "scrollX": 10,
        "scrollY": 5,
        "devicePixelRatio": 1.0,
        "isMaximized": False,
        "borderLeft": 8.0,
        "borderBottom": 8.0,
        "topChrome": 88.0,
    }

    x, y = grabber.get_top_left_corner()
    # x = screenX (100) + borderLeft (8) - scrollX (10) = 98.0
    # y = screenY (150) + topChrome (88) - scrollY (5) = 233.0
    assert x == 98.0
    assert y == 233.0


def test_grabber_multi_monitor_and_fractional_dpi():
    """Verify get_top_left_corner handles secondary monitor coordinates and fractional DPI."""
    from grabbers.chesscom_grabber import ChesscomGrabber
    with patch("grabbers.grabber.attach_to_session") as mock_attach:
        fake_driver = MagicMock()
        mock_attach.return_value = fake_driver
        grabber = ChesscomGrabber("http://fake", "sess")

    # Secondary monitor placed to the right at screenX = 1920, with 125% DPI (DPR 1.25)
    # fractional borders: outerWidth: 1015, innerWidth: 1000 -> borderLeft = 7.5
    fake_driver.execute_script.return_value = {
        "screenX": 1920,
        "screenY": 50,
        "outerWidth": 1015,
        "outerHeight": 890,
        "innerWidth": 1000,
        "innerHeight": 800,
        "scrollX": 0,
        "scrollY": 0,
        "devicePixelRatio": 1.25,
        "isMaximized": False,
        "borderLeft": 7.5,
        "borderBottom": 7.5,
        "topChrome": 82.5,
    }

    x, y = grabber.get_top_left_corner()
    assert x == 1927.5
    assert y == 132.5


def test_grabber_calibration_offset():
    """Verify custom calibration offset dx, dy modifies corner coordinates."""
    from grabbers.chesscom_grabber import ChesscomGrabber
    with patch("grabbers.grabber.attach_to_session") as mock_attach:
        fake_driver = MagicMock()
        mock_attach.return_value = fake_driver
        grabber = ChesscomGrabber("http://fake", "sess")

    fake_driver.execute_script.return_value = {
        "screenX": 100,
        "screenY": 100,
        "borderLeft": 0.0,
        "topChrome": 80.0,
        "scrollX": 0,
        "scrollY": 0,
    }

    grabber.set_calibration_offset(4.5, -2.5)
    assert grabber.get_calibration_offset() == (4.5, -2.5)

    x, y = grabber.get_top_left_corner()
    assert x == 104.5
    assert y == 177.5


def test_grabber_fallback_on_error():
    """Verify fallback to (0, 0) if execute_script fails on disconnect."""
    from selenium.common import WebDriverException

    from grabbers.chesscom_grabber import ChesscomGrabber
    with patch("grabbers.grabber.attach_to_session") as mock_attach:
        fake_driver = MagicMock()
        fake_driver.execute_script.side_effect = WebDriverException("Session closed")
        mock_attach.return_value = fake_driver
        grabber = ChesscomGrabber("http://fake", "sess")

    x, y = grabber.get_top_left_corner()
    assert x == 0
    assert y == 0


def test_protocol_overlay_calibration():
    """Verify protocol.py supports OverlayCalibration typed and legacy messages."""
    import protocol as proto

    msg = proto.OverlayCalibration(active=True, board_position={"x": 10, "y": 20, "width": 400, "height": 400})
    assert msg.active is True
    assert msg.kind == "OVERLAY_CALIBRATION"

    # Legacy conversion
    legacy = {"calibration_active": True, "board_position": {"x": 100, "y": 100}}
    converted = proto.legacy_to_overlay_msg(legacy)
    assert isinstance(converted, proto.OverlayCalibration)
    assert converted.active is True
    assert converted.board_position["x"] == 100


def test_config_store_theme_and_calibration():
    """Verify config_store validates and persists theme and calibration offsets."""
    from config_store import Config, validate

    data = {
        "theme": "light",
        "calibration_offset_x": 12.5,
        "calibration_offset_y": -8.0,
    }
    validated = validate(data)
    assert validated["theme"] == "light"
    assert validated["calibration_offset_x"] == 12.5
    assert validated["calibration_offset_y"] == -8.0

    cfg = Config.from_dict(data)
    assert cfg.theme == "light"
    assert cfg.calibration_offset_x == 12.5
    assert cfg.calibration_offset_y == -8.0


def test_overlay_virtual_geometry_translation(monkeypatch):
    """Verify OverlayScreen translates coordinates by virtual desktop origin."""
    import queue

    from PyQt6.QtWidgets import QApplication

    import overlay

    _ = QApplication.instance() or QApplication(["test"])
    q = queue.Queue()
    oscreen = overlay.OverlayScreen(q)

    # Simulate multi-monitor setup where virtual desktop origin is at x=-1920, y=0
    oscreen._virtual_origin = (-1920, 0)
    oscreen.board_position = {"x": 100, "y": 200, "width": 800, "height": 800}

    # Eval bar position on left
    oscreen.eval_bar_side = "left"
    oscreen.update_eval_bar_position()
    # x should be: board_x (100) - width (40) - margin (15) - (-1920) = 45 + 1920 = 1965
    assert oscreen.eval_bar_x == 1965
    assert oscreen.eval_bar_y == 200

    # Square center
    e4 = oscreen._square_center("e4")
    assert e4 is not None
    # x should be shifted by -(-1920) = +1920
    assert e4.x() > 1920


def test_calibration_helper_dialog_and_diagnostics():
    """Verify CalibrationHelperDialog constructs, displays diagnostics, and emits signal."""
    from PyQt6.QtWidgets import QApplication

    from calibration_helper import CalibrationHelperDialog, get_screen_diagnostics

    _ = QApplication.instance() or QApplication(["test"])
    diags = get_screen_diagnostics()
    assert isinstance(diags, list)
    assert len(diags) >= 1

    dlg = CalibrationHelperDialog()
    assert dlg.spin_x.value() == 0.0 or isinstance(dlg.spin_x.value(), float)

    changed = []
    dlg.calibration_changed.connect(lambda x, y: changed.append((x, y)))
    dlg.spin_x.setValue(5.0)
    dlg.spin_y.setValue(-3.0)

    assert (5.0, -3.0) in changed
    dlg.close()


def test_gui_modern_theme_and_system_tray():
    """Verify PyQt6 GUI window builds, handles themes, move table, and system tray."""
    from PyQt6.QtWidgets import QApplication

    from gui import GUI

    _ = QApplication.instance() or QApplication(["test"])
    gui = GUI()

    # Theme switching
    gui.set_theme("dark")
    assert gui.current_theme == "dark"
    assert "🌙 Dark" in gui.btn_theme.text()

    gui.toggle_theme()
    assert gui.current_theme == "light"
    assert "☀️ Light" in gui.btn_theme.text()

    # System tray existence
    assert hasattr(gui, "tray_icon")

    # Move table insertion with blunder highlighting
    gui.clear_tree()
    gui.match_moves.append("e4")
    gui.insert_move("e4")
    assert gui.tree.rowCount() == 1
    assert gui.tree.item(0, 1).text() == "e4"

    gui.match_moves.append("e5??")
    gui.insert_move("e5??", is_blunder=True)
    assert gui.tree.rowCount() == 1
    assert gui.tree.item(0, 2).text() == "e5??"

    # Eval and telemetry updates
    gui.update_evaluation_display("+1.25", "45/35/20", "+2", "95.0%", "82.0%")
    assert gui.eval_text.text() == "+1.25"
    assert gui.material_text.text() == "+2"

    gui.update_telemetry(nps=1500000, depth=18, time_per_move_ms=85.5, connection_health="Connected")
    assert "1,500,000" in gui.nps_text.text()
    assert gui.depth_reached_text.text() == "18"

    # Clean shutdown
    gui._cleanup()
    gui.close()
