# Platform & Distribution — Chess-X P2

This document covers the **Platform & Distribution** work (`TODO.md §5`): macOS support, packaged executables, Wayland abstraction, and Firefox/Edge webdriver support.

## Supported Platforms

| OS | Display | Browser | Input | Overlay | Notes |
|---|---|---|---|---|---|
| Windows 10/11 | DWM | Chrome / Firefox / Edge | PyAutoGUI | PyQt6 (WA_Translucent) | Primary CI |
| Linux X11 | Xorg / XWayland | Chrome / Firefox / Edge | PyAutoGUI → pynput → xdotool | PyQt6 opaque-window | Recommended |
| Linux Wayland | Wayland (GNOME/KDE) | Chrome (--ozone-platform-hint=x11) / Firefox | pynput → ydotool → xdotool | PyQt6 Tool + `QT_QPA_PLATFORM=xcb` fallback, layer-shell if available | See Wayland section |
| macOS 12+ | Quartz / Retina | Chrome / Firefox / Edge | PyAutoGUI (retina-aware) → pynput | PyQt6 + `WA_MacAlwaysShowToolWindow`, DPR scaling | See macOS section |

## Browser Factory (`src/browser_factory.py`)

Abstracts webdriver creation:

```python
from browser_factory import create_driver, normalize_browser
driver = create_driver("firefox")          # geckodriver via webdriver-manager → Selenium Manager fallback
driver = create_driver("edge", headless=False)
driver = create_driver("chrome")
```

- Honors `CHESSX_BROWSER` env and `config.json:browser` (`chrome|firefox|edge`).
- Binaries overridden via `CHROME_BIN`, `FIREFOX_BIN`, `EDGE_BIN`.
- Uses `webdriver-manager` when installed, otherwise Selenium Manager.
- On macOS logs quarantine hint: `xattr -d com.apple.quarantine <path>`.
- Attach via `attach_to_session_generic(executor_url, session_id, browser)` or `utilities.attach_to_session(..., browser=...)`.

GUI exposes a radio row (Chrome / Firefox / Edge) in the **Platform** card; selection is persisted (`config_store` v3) and syncs `CHESSX_BROWSER` for the bot process and grabbers.

## Input Abstraction (`src/input_backend.py`)

`MoveExecutor` no longer calls `pyautogui` directly. It uses a backend selected by:

```
Wayland: pynput → ydotool → xdotool → pyautogui → dummy
macOS:   pyautogui → pynput → dummy
X11/Win: pyautogui → pynput → xdotool → dummy
```

Force via `CHESSX_INPUT_BACKEND=pyautogui|pynput|xdotool|ydotool`.

- **PyAutoGUIBackend**: default on X11/Windows. Disabled on pure Wayland.
- **PynputBackend**: `pynput.mouse.Controller`, works on Wayland and macOS. Supports `moveTo` with interpolated duration.
- **XdotoolBackend**: wraps `xdotool mousemove/mousedown/mouseup` (X11/XWayland).
- **YdotoolBackend**: wraps `ydotool mousemove --absolute` / `click 0x110` (native Wayland, needs `ydotoold` daemon).
- **DummyBackend**: records moves for tests/headless.

```python
from input_backend import get_input_backend
backend = get_input_backend()        # auto
backend = get_input_backend("pynput")
backend.moveTo(300, 400)
backend.dragTo(350, 400)
backend.click(button="left")
```

## macOS Support (`src/platform_info.py`, `src/overlay.py`)

- **Detection**: `is_macos()`, `is_retina()` via `QGuiApplication.devicePixelRatio()`, `check_macos_permissions()` (AXIsProcessTrusted + Screen Recording hint), `check_macos_chromedriver()` (quarantine).
- **DPR / Retina**: `get_device_pixel_ratio()` and `get_scaling_factor()` unify Qt DPR, env overrides (`CHESSX_SCALE_FACTOR`, `GDK_SCALE`), Windows DPI, and Tk scaling. `MoveExecutor` logs scaling but keeps logical coords; `OverlayScreen` scales incoming arrows by DPR so they line up on Retina.
- **Overlay**: sets `WA_MacAlwaysShowToolWindow` + `Tool` flag, logs permission guidance.
- **Testing**: `python tools/build.py --check` prints platform diagnostics. On macOS run:
  ```bash
  xattr -d com.apple.quarantine $(find ~/.wdm -name "chromedriver" -o -name "geckodriver")
  # Grant permissions: System Settings → Privacy & Security → Accessibility + Screen Recording → Terminal / Python
  python3 src/gui.py
  ```

## Wayland Support (`src/input_backend.py`, `src/overlay.py`, `src/platform_info.py`)

- **Detection**: `is_wayland()` checks `XDG_SESSION_TYPE`/`WAYLAND_DISPLAY`.
- **Input**: auto-selects pynput/ydotool/xdotool; logs warning if PyAutoGUI attempted on Wayland.
- **Overlay**: detects Wayland, adds `Tool` flag, sets `WA_X11DoNotAcceptFocus`, and warns that `QT_QPA_PLATFORM=xcb` forces XWayland where overlay is reliable. If `CHESSX_WAYLAND_LAYER_SHELL=1` and `python-layer-shell` available, hints layer-shell usage. Pure Wayland without layer-shell may have invisible overlay; run:
  ```bash
  QT_QPA_PLATFORM=xcb python src/gui.py
  # or for native Wayland with layer-shell:
  pip install pynput
  sudo apt install wl-clipboard ydotool  # if using ydotool
  systemctl enable --now ydotool  # if needed
  ```
- **Chrome args**: Wayland adds `--ozone-platform-hint=x11` to force XWayland for automation stability (configurable).

## Packaging

### PyInstaller (one-file for Windows/Linux, .app for macOS)

```bash
pip install pyinstaller
pyinstaller chess-x.spec              # onefile → dist/chess-x(.exe)
pyinstaller --onedir chess-x.spec     # faster startup
python tools/build.py --pyinstaller
python tools/build.py --pyinstaller --onedir
```

- Spec bundles `src/assets`, hidden imports for `selenium`, `PyQt6`, `multiprocess`, `stockfish`, etc.
- Console hidden (`console=False`), icon from `pawn_32x32.png`, macOS bundle with `NSHighResolutionCapable`, ScreenCapture/AppleEvents usage strings.
- Verify: `dist/chess-x --help` or run; check `logs/chess-x.log`.

### Briefcase (cross-platform app bundle)

```bash
pip install briefcase
python tools/build.py --briefcase
briefcase create && briefcase build && briefcase run
```

`pyproject.toml:[tool.briefcase]` defines bundle `com.chessx`, per-platform `requires` (macOS `pyobjc`, Linux `pynput`), icons, and `info` plist.

### CI

`.github/workflows/ci.yml` runs ruff/mypy/pytest on Windows+Linux×3.10-3.12 plus packaging validation (`python -m compileall src`, PyInstaller compile check). Add macOS to matrix when needed:

```yaml
matrix:
  os: [ubuntu-latest, windows-latest, macos-latest]
```

## Config Migration

`config_store.py` v3 adds `browser`. Old configs (`version=1|2`) migrate automatically:

```python
from config_store import load_config
cfg = load_config()      # browser defaults to "chrome" if missing
print(cfg.browser)       # chrome|firefox|edge
```

## Troubleshooting

| Issue | Fix |
|---|---|
| `xattr: No such file` / driver quarantine on macOS | `xattr -d com.apple.quarantine $(find ~/.wdm -name "*driver*")`; install Chrome for correct arch (Intel vs Apple Silicon) |
| Overlay invisible on Wayland | `QT_QPA_PLATFORM=xcb python src/gui.py`; install `pynput`; try `CHESSX_WAYLAND_LAYER_SHELL=1` |
| Clicks land off squares (HiDPI) | Check `get_scaling_factor` log; disable OS scaling or set `CHESSX_SCALE_FACTOR=1.0` |
| Firefox/Edge driver not found | `pip install webdriver-manager` or rely on Selenium Manager; set `FIREFOX_BIN`/`EDGE_BIN` if custom location |
| `No input backend` | Install `pynput` (`pip install pynput`), on Wayland `xdotool`/`ydotool` |
