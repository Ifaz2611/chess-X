from __future__ import annotations

import contextlib
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import protocol as proto
from utilities import get_logger

logger = get_logger("calibration")


def get_screen_diagnostics() -> list[dict[str, Any]]:
    """Return list of detected screen information (geometry, DPR, primary)."""
    results = []
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    for idx, screen in enumerate(screens):
        geom = screen.geometry()
        v_geom = screen.virtualGeometry() if hasattr(screen, "virtualGeometry") else geom
        dpr = screen.devicePixelRatio()
        results.append({
            "index": idx,
            "name": screen.name(),
            "width": geom.width(),
            "height": geom.height(),
            "x": geom.x(),
            "y": geom.y(),
            "dpr": dpr,
            "is_primary": (screen == primary),
            "virtual_geometry": (v_geom.x(), v_geom.y(), v_geom.width(), v_geom.height()),
        })
    return results


def calculate_auto_calibration(metrics: dict[str, Any]) -> tuple[float, float]:
    """Calculate recommended offset correction given viewport metrics."""
    if not isinstance(metrics, dict):
        return 0.0, 0.0
    # In non-maximized Chrome, if border bottom is not zero, offset difference is already accounted for
    # Any residual adjustment can be calculated here
    return 0.0, 0.0


class CalibrationHelperDialog(QDialog):
    """Interactive multi-monitor and DPI calibration helper dialog."""

    calibration_changed = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None, gui_controller=None):
        super().__init__(parent)
        self.gui = gui_controller
        self.setWindowTitle("Chess-X · Multi-Monitor & DPI Calibration Helper")
        self.resize(580, 520)
        self.setMinimumSize(500, 420)

        # Stored initial offsets
        self._initial_x = 0.0
        self._initial_y = 0.0
        self._load_current_offsets()

        self._build_ui()
        self.refresh_diagnostics()

    def _load_current_offsets(self):
        try:
            from config_store import load_config
            cfg = load_config()
            self._initial_x = float(getattr(cfg, "calibration_offset_x", 0.0) or 0.0)
            self._initial_y = float(getattr(cfg, "calibration_offset_y", 0.0) or 0.0)
        except Exception:
            self._initial_x = 0.0
            self._initial_y = 0.0

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # Header
        header = QLabel("Multi-Monitor & DPI Calibration Helper")
        header.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        layout.addWidget(header)

        desc = QLabel(
            "Fine-tune coordinate alignment for fractional scaling (125%/150%), "
            "multi-monitor offsets, and non-maximized browser windows."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(desc)

        # Diagnostics Group
        diag_group = QGroupBox("Detected Display & Browser Diagnostics")
        diag_layout = QGridLayout(diag_group)
        diag_layout.setSpacing(8)

        self.lbl_screens = QLabel("Detecting screens...")
        self.lbl_screens.setWordWrap(True)
        self.lbl_browser_pos = QLabel("Browser: Not connected")
        self.lbl_viewport = QLabel("Viewport: —")
        self.lbl_offsets = QLabel("Computed Top-Left: —")

        diag_layout.addWidget(QLabel("<b>Monitors:</b>"), 0, 0)
        diag_layout.addWidget(self.lbl_screens, 0, 1)

        diag_layout.addWidget(QLabel("<b>Browser Window:</b>"), 1, 0)
        diag_layout.addWidget(self.lbl_browser_pos, 1, 1)

        diag_layout.addWidget(QLabel("<b>Window Geometry:</b>"), 2, 0)
        diag_layout.addWidget(self.lbl_viewport, 2, 1)

        diag_layout.addWidget(QLabel("<b>Top-Left Offset:</b>"), 3, 0)
        diag_layout.addWidget(self.lbl_offsets, 3, 1)

        layout.addWidget(diag_group)

        # Calibration Controls Group
        adj_group = QGroupBox("Calibration Adjustments (Pixels)")
        adj_layout = QGridLayout(adj_group)
        adj_layout.setSpacing(10)

        adj_layout.addWidget(QLabel("Offset X (dx):"), 0, 0)
        self.spin_x = QDoubleSpinBox()
        self.spin_x.setRange(-500.0, 500.0)
        self.spin_x.setSingleStep(1.0)
        self.spin_x.setDecimals(1)
        self.spin_x.setValue(self._initial_x)
        self.spin_x.valueChanged.connect(self._on_values_changed)
        adj_layout.addWidget(self.spin_x, 0, 1)

        adj_layout.addWidget(QLabel("Offset Y (dy):"), 1, 0)
        self.spin_y = QDoubleSpinBox()
        self.spin_y.setRange(-500.0, 500.0)
        self.spin_y.setSingleStep(1.0)
        self.spin_y.setDecimals(1)
        self.spin_y.setValue(self._initial_y)
        self.spin_y.valueChanged.connect(self._on_values_changed)
        adj_layout.addWidget(self.spin_y, 1, 1)

        # Nudge button row
        nudge_layout = QHBoxLayout()
        btn_left = QPushButton("◀ -1px X")
        btn_left.clicked.connect(lambda: self.spin_x.setValue(self.spin_x.value() - 1.0))
        btn_right = QPushButton("▶ +1px X")
        btn_right.clicked.connect(lambda: self.spin_x.setValue(self.spin_x.value() + 1.0))
        btn_up = QPushButton("▲ -1px Y")
        btn_up.clicked.connect(lambda: self.spin_y.setValue(self.spin_y.value() - 1.0))
        btn_down = QPushButton("▼ +1px Y")
        btn_down.clicked.connect(lambda: self.spin_y.setValue(self.spin_y.value() + 1.0))
        btn_reset = QPushButton("Reset (0,0)")
        btn_reset.clicked.connect(self._reset_to_zero)

        for b in (btn_left, btn_right, btn_up, btn_down, btn_reset):
            nudge_layout.addWidget(b)
        adj_layout.addLayout(nudge_layout, 2, 0, 1, 2)

        # Crosshair preview toggle
        self.chk_crosshair = QCheckBox("Show alignment crosshairs on overlay (tests pixel-perfect board match)")
        self.chk_crosshair.toggled.connect(self._toggle_crosshairs)
        adj_layout.addWidget(self.chk_crosshair, 3, 0, 1, 2)

        layout.addWidget(adj_group)

        # Action bar
        btn_bar = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh Info")
        self.btn_refresh.clicked.connect(self.refresh_diagnostics)
        btn_bar.addWidget(self.btn_refresh)

        btn_bar.addStretch()

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #2E7D5B; font-weight: bold;")
        btn_bar.addWidget(self.lbl_status)

        self.btn_save = QPushButton("Save & Apply")
        self.btn_save.clicked.connect(self.save_and_apply)
        btn_bar.addWidget(self.btn_save)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        btn_bar.addWidget(self.btn_close)

        layout.addLayout(btn_bar)

    def refresh_diagnostics(self):
        """Query screens and browser for diagnostics display."""
        # 1. Screen info
        screens = get_screen_diagnostics()
        screen_lines = []
        for s in screens:
            prim = " [Primary]" if s["is_primary"] else ""
            screen_lines.append(f"{s['name']}: {s['width']}x{s['height']} @ {int(s['dpr']*100)}% scale{prim}")
        self.lbl_screens.setText(" | ".join(screen_lines) if screen_lines else "None detected")

        # 2. Browser & Viewport info
        if self.gui and getattr(self.gui, "chrome", None):
            try:
                # Try getting metrics via grabber if active
                grabber = getattr(self.gui, "grabber", None)
                metrics = None
                if grabber and hasattr(grabber, "get_viewport_metrics"):
                    metrics = grabber.get_viewport_metrics()
                elif hasattr(self.gui.chrome, "execute_script"):
                    # Quick inline query
                    metrics = self.gui.chrome.execute_script(
                        "return {screenX: window.screenX, screenY: window.screenY, "
                        "outerWidth: window.outerWidth, outerHeight: window.outerHeight, "
                        "innerWidth: window.innerWidth, innerHeight: window.innerHeight, "
                        "devicePixelRatio: window.devicePixelRatio, "
                        "scrollX: window.scrollX, scrollY: window.scrollY};"
                    )

                if metrics and isinstance(metrics, dict):
                    sx = metrics.get("screenX", 0)
                    sy = metrics.get("screenY", 0)
                    dpr = metrics.get("devicePixelRatio", 1.0)
                    ow = metrics.get("outerWidth", 0)
                    oh = metrics.get("outerHeight", 0)
                    iw = metrics.get("innerWidth", 0)
                    ih = metrics.get("innerHeight", 0)
                    self.lbl_browser_pos.setText(f"Screen pos: ({sx}, {sy}) | DPR: {dpr}")
                    self.lbl_viewport.setText(f"Outer: {ow}x{oh} | Inner: {iw}x{ih} (borders: {ow-iw}px horiz, {oh-ih}px vert)")
                    
                    if grabber and hasattr(grabber, "get_top_left_corner"):
                        cx, cy = grabber.get_top_left_corner()
                        self.lbl_offsets.setText(f"canvas_x: {cx:.1f}, canvas_y: {cy:.1f}")
                    return
            except Exception as e:
                logger.debug("Diagnostics browser query error: %s", e)

        self.lbl_browser_pos.setText("Browser window: Not open or inactive")
        self.lbl_viewport.setText("Geometry: Launch browser to inspect live metrics")
        self.lbl_offsets.setText(f"Active offsets: dx={self.spin_x.value():.1f}, dy={self.spin_y.value():.1f}")

    def _on_values_changed(self):
        dx = float(self.spin_x.value())
        dy = float(self.spin_y.value())
        self.calibration_changed.emit(dx, dy)
        if self.gui and hasattr(self.gui, "grabber") and self.gui.grabber:
            with contextlib.suppress(Exception):
                self.gui.grabber.set_calibration_offset(dx, dy)
        if self.chk_crosshair.isChecked():
            self._send_crosshair_update(True)

    def _reset_to_zero(self):
        self.spin_x.setValue(0.0)
        self.spin_y.setValue(0.0)

    def _toggle_crosshairs(self, checked: bool):
        self._send_crosshair_update(checked)

    def _send_crosshair_update(self, active: bool):
        if not self.gui:
            return
        q = getattr(self.gui, "_overlay_queue", None)
        if q is None:
            return
        try:
            # Send OverlayCalibration message
            msg = proto.OverlayCalibration(active=active)
            q.put(msg)
        except Exception as e:
            logger.debug("Send overlay calibration message failed: %s", e)

    def save_and_apply(self):
        """Save calibrated offsets to config.json and apply to grabber."""
        dx = float(self.spin_x.value())
        dy = float(self.spin_y.value())
        try:
            from config_store import load_config, save_config
            cfg = load_config()
            cfg_dict = cfg.to_dict()
            cfg_dict["calibration_offset_x"] = dx
            cfg_dict["calibration_offset_y"] = dy
            save_config(cfg_dict)

            if self.gui and hasattr(self.gui, "grabber") and self.gui.grabber:
                self.gui.grabber.set_calibration_offset(dx, dy)

            self.lbl_status.setText("✓ Offsets saved to config!")
            logger.info("Saved calibration offsets: dx=%s, dy=%s", dx, dy)
        except Exception as e:
            logger.error("Failed to save calibration: %s", e)
            self.lbl_status.setText(f"Error: {e}")

    def closeEvent(self, event):
        # Disable crosshairs on close
        self._send_crosshair_update(False)
        super().closeEvent(event)
