# Troubleshooting Wiki

> **Start here for every symptom.** 90 % of issues are Chrome/driver mismatches, board DOM location, or display scaling. Collect `logs/chess-x.log` before filing — see §9.

---

## Quick triage

| Symptom | Next section |
|---------|-------------|
| Chrome won't open / closes instantly | §1 Chrome & ChromeDriver |
| `Can't find board / color / moves` or `health_check FAIL` | §2 DOM breakages |
| Overlay missing, behind windows, black rectangle | §3 Overlay & Wayland |
| Clicks land on wrong squares, promotion misses | §4 DPI & Multi-monitor |
| Hotkeys `1/2/3` dead on Linux | §5 Linux input / Wayland |
| Bot played wrong move, material/eval frozen | §6 Game-state resync |
| Bot stalls, `ERR_TIMEOUT` / `ERR_STALE` | §7 Engine & performance |
| Settings lost after restart | §8 Config persistence |

---

## 1. Chrome & ChromeDriver variations

### 1.1 `Can't find Chrome` / `Failed to start Chrome`

**Cause:** Chrome not installed, mismatched driver, stale `chromedriver.exe`, or another instance already running.

**Fix:**

1. Install Google Chrome (not Chromium) from `https://www.google.com/chrome/` and verify `chrome://version`.
2. Close **all** Chrome windows (check Task Manager → `chrome.exe`). On Linux: `pkill chrome`.
3. Delete the driver cache and retry:
   ```powershell
   # Windows
   Remove-Item -Recurse -Force "$env:USERPROFILE\.wdm"
   # Linux
   rm -rf ~/.wdm
   # then click OPEN BROWSER again
   ```
4. If `webdriver-manager` picks a wrong cache file (`third_party_notices.chromedriver`), the app now self-heals via path search in `gui.py:1099` + `browser_session.py`. But if it still fails, install driver manually:
   ```bash
   # match Chrome major version at chrome://version
   # download from https://chromedriver.chromium.org/ or https://googlechromelabs.github.io/chrome-for-testing/
   # place next to src/gui.py or add to PATH
   ```
5. Linux headless servers need:
   ```bash
   sudo apt-get install -y google-chrome-stable libnss3 libatk-bridge2.0-0 libxss1 libasound2
   ```

**Screenshot — Chrome closed unexpectedly** (placeholder — capture Task Manager + `logs/chess-x.log` line `Selenium Manager also failed`):

> `logs/chess-x.log:1145` prints both `webdriver-manager` and `Selenium Manager` errors. Paste them in the issue.

### 1.2 Chrome opens but shows `data:,` blank page

- Check antivirus: Windows Defender / AVG may quarantine `chromedriver.exe`. Whitelist the project folder.
- On macOS gatekeeper: `xattr -d com.apple.quarantine stockfish` and `chromedriver`.
- Linux snap Chrome (`/snap/bin/chromium`) needs `CHROME_BIN` override:
  ```bash
  export CHROME_BIN=/snap/bin/chromium
  python src/gui.py
  ```

### 1.3 Chrome closes when GUI closes

Expected — `GUI._cleanup:509` calls `BrowserSessionManager.close()` via `atexit` + `WM_DELETE_WINDOW`. To keep Chrome open for debugging, set `GUI._cleaned = True` temporarily (dev only).

---

## 2. DOM breakages — `ERR_BOARD / ERR_MOVES / ERR_COLOR`

The bot uses **ordered fallback selector chains** (`src/grabbers/grabber.py:152`). When chess.com or lichess ships a new deploy, the primary selector can silently stop matching.

### How to diagnose

1. Click **Stop**, open DevTools (F12) → Console:
   ```js
   // Chess.com — should return an element, not null
   document.querySelector('wc-chess-board')       // primary
   document.querySelector('#board-single')        // fallback
   document.querySelector('.board')               // broad

   // Lichess — check which of the rotating tags exists today
   document.querySelector('cg-container')
   document.querySelector('.a1t')                 // stable active-move class
   document.querySelector('rm6, l4x, i5d, aPp')   // obfuscated container candidates
   ```
2. Check `logs/chess-x.log` line `Health check PASS|FAIL: {board:..., board_selector:"By=..."}`
   - If `board_selector` is the **last entry** in `BOARD_SELECTORS` (`grabber.py:125` warning), DOM has changed — update chain.
   - If `ERR_MOVES` but board exists: `get_move_list` returned `None` vs `[]`. Fresh board must return `[]`, not `None` (`chesscom_grabber.py:255`).

### Site-specific variations

**Chess.com**
- Board tags drift: `wc-chess-board` → `chess-board` → `div.board`. Keep all three plus `div[class*='board']` XPath as last resort (`chesscom_grabber.py:15`).
- Move list container renames: `.play-controller-scrollable` vs `.mode-swap-move-list-wrapper-component`. Global fallback searches `div.node[data-node]` anywhere (`chesscom_grabber.py:200`).
- Colour detection via `wc-chess-board svg` coordinates (`chesscom_grabber.py:66`) flips when board is `flipped` class — keep class fallback.

**Lichess**
- Tag obfuscation: `rm6/l4x/kwdb` are **randomised per deploy** (`lichess_grabber.py:28`). The primary fix is JS extraction `_js_extract_moves:152` which scans `.a1t` parent + SAN regex fallback — never rely solely on tag names.
- Puzzles move list uses separate selectors `PUZZLE_MOVE_LIST_SELECTORS:35`.
- Mouseless `lichess.socket.ws.send` can throw `WebDriverException` if not on a live game URL — retry once (`make_mouseless_move:516`).

### When to file a bug

Paste: exact page URL (e.g., `https://www.chess.com/live`, `https://lichess.org/training`), selector that was `null`, `health_check` line, Chrome version, and a saved HTML snapshot (`Right-click → Save As`) as `tests/fixtures/` for CI.

### Self-test without browser

```bash
pytest tests/test_grabbers.py -q --cov=src
pytest tests/test_board_sync.py -q
```

---

## 3. Overlay — PyQt6 transparent window

The overlay is a click-through frameless `QWidget` (`src/overlay.py:16`) spanning the primary screen.

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Overlay **behind** board | X11 compositor or `WindowStaysOnTopHint` ignored | Keep browser **maximized, not fullscreen (F11)**. On Linux try `export QT_QPA_PLATFORM=xcb` before launch. |
| Overlay invisible / black rectangle | `QGuiApplication.screens()[0]` picks wrong GPU or Wayland layer-shell missing | See §3.2 Wayland. On NVIDIA try `__GL_GSYNC_ALLOWED=0 python src/gui.py`. |
| Arrow offset from board | Browser not at `(screenX, outerWidth-innerWidth)` or zoom ≠ 100 % | Set Chrome zoom **100 %** (`Ctrl+0`), disable `Use custom chrome` extensions that shift board. |
| Overlay freezes after Stop | Queue not drained | `stockfish_bot._clear_overlay_queue_safe:927` sends `OverlayClear`, GUI `_overlay_queue` cancelled on stop. Click **Stop → Start** to force `RESET+START`. |
| `libEGL.so.1` / `xcb` error | Missing Qt system libs on Linux | `sudo apt-get install -y libegl1 libgl1 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0` |

### 3.1 Correct alignment — expected look

```
[ eval bar  ][     chess board 8x8      ]
  40px wide, margin 15px, height = board height
  bottom = your colour, top = opponent (is_white flip)
  centre line = 0.00; bar extends from centre (see overlay.py:211)
  label at bottom shows +1.23 / M3 / M-2
  arrow: 25 px head, semi-transparent red (255,0,0,122)
```

Board position is sampled per evaluation via `get_top_left_corner:37` (JS `screenX/Y`) + `board_elem.location/size`. If mismatch persists, print `board_position` from `logs/chess-x.log` line `overlay_data`.

### 3.2 Wayland vs Xorg (Linux)

- **Detection:** `utilities.is_wayland:68` checks `XDG_SESSION_TYPE=wayland` / `WAYLAND_DISPLAY`.
- **PyAutoGUI & overlay on Wayland:** `moveTo/dragTo` and `QT_QPA_PLATFORM` are X11-only. Symptoms: clicks go to `(0,0)` or overlay never appears.
- **Fix — switch to Xorg:** On Gear icon at login → **Ubuntu on Xorg** (or KDE X11). Verify with `echo $XDG_SESSION_TYPE` → `x11`.
- **If you must stay on Wayland:**
  ```bash
  export QT_QPA_PLATFORM=xcb
  export GDK_BACKEND=x11
  python src/gui.py
  # alternative automation backends planned: pynput/xdotool/ydotool + layer-shell (see TODO.md:45)
  ```

### Screenshots (add your own captures in PRs)

- `[placeholder] Overlay correct: eval bar flush left of board, arrow from e2 to e4`
- `[placeholder] Overlay wrong: gap between bar and board → check DPI 100 %`
- `[placeholder] Wayland error: black overlay window`

---

## 4. DPI, Scaling & Multi-monitor

| Symptom | Cause | Fix |
|---------|-------|-----|
| Clicks land 1–2 squares off | Windows display **Scale 125 %** or Chrome zoom ≠ 100 % | Settings → Display → **Scale 100 %** (log out). Chrome `Ctrl+0`. Keep board **fully visible** (no scroll). |
| Only primary monitor works | `get_top_left_corner` uses `window.screenX` from primary screen (`grabber.py:41`) | Move Chrome to **primary monitor**, maximize. Multi-monitor DPI fix tracked in `TODO.md:29`. |
| Fractional scaling (Linux 150 %/200 %) | `outerWidth-innerWidth` half-pixel | Run `gsettings set org.gnome.mutter experimental-features "[]"` (Wayland) or switch to X11, use integer scale. |
| Board partially clipped | Board `location` underflows on non-maximized window | Maximize Chrome; don't use DevTools docked side-by-side. |

**Calibration tip:** In `src/utilities.py` log `board_elem.location` + `canvas_x_offset` and compare to a screenshot crosshair. For local fix, patch `move_executor.move_to_screen_pos` temporarily with `+ dx, dy` offset.

---

## 5. Keyboard hotkeys — Linux `input` group

- **Hotkeys:** `1` Start · `2` Stop · `3` (hold) Manual execute · `Esc/F12` Kill (`README.md:391`).
- **Backend:** `utilities.get_keyboard_handler:104` tries `keyboard` then `pynput`.
- **`keyboard` needs raw input:** On Linux, without `input` group or `sudo`, you'll see `get_keyboard_handler` fallback or `check_linux_input_permissions:73` warning.
- **Fix (recommended):**
  ```bash
  sudo usermod -aG input $USER
  # log out and back in, then groups → should list input
  python src/gui.py
  ```
- **Alternative:** `sudo venv/bin/python src/gui.py`
- **On Wayland:** even `sudo` may not deliver global `keyboard` hooks — use `pynput` backend (auto-fallback) or switch to X11 as in §3.2.

---

## 6. Game-state resync — takeback / abort / new game

Symptoms: bot replays old moves, hangs after undo, PGN diverges from board.

**What the bot does correctly:**
- Validates every candidate `new_move_list` as SAN-parseable via `chess.Board.push_san` (`stockfish_bot:814`).
- Detects **new game** (`0 moves`, `741`), **takeback** (`len(new) < len(prev)`, `755`), and **FEN mismatch** (`same length but different SAN`, `791`) and calls `_reconcile_board_fen` / `_reset_accuracy_state` + trims `white_cp_losses` etc.
- Re-seeds engine with `stockfish.set_position([m.uci() for m in board.move_stack])`.

**If you see stale board after rematch:**
1. Click **Stop → Start** (forces `MsgRestart` + `MsgDelete` handshake + `RESET+START`).
2. Ensure the move-list selectors didn't match a **detached** container — check `health_check` fallback warning.
3. On cross-time-control rematch (bullet→rapid), lichess reuses DOM node — `lichess_grabber._js_extract_moves` handles via parent of `.a1t`.

---

## 7. Engine & `ERR_TIMEOUT / ERR_STALE / ERR_DISCONNECT`

| Code | Meaning | Fix |
|------|---------|-----|
| `ERR_PERM` | Stockfish not executable | Linux `chmod +x stockfish`, Windows confirm `file stockfish` is `ELF`/`PE` |
| `ERR_EXE` | Bad Stockfish path | Re-pick binary via **Select Stockfish**, not the `.py` wrapper. Try `stockfish uci` → must print `uciok`. |
| `ERR_BOARD` | Board not found after 3 retries | DOM breakage — §2. Also confirm you're on a **live game URL**, not lobby. |
| `ERR_COLOR` | Board orientation undecided after 3 retries | Flipped/board hidden. Scroll board into view, disable board themes with custom CSS. |
| `ERR_MOVES` | Move list not found after 3 retries | Same as ERR_BOARD; check `health_check` move_selector. Lichess puzzle uses different container — `is_game_puzzles` switches. |
| `ERR_TIMEOUT` | `get_best_move` didn't return within expected `mt` | High CPU / Depth 20 on weak machine. Lower `Depth 10–12`, `Slow Mover 50–80`, close other apps. |
| `ERR_DISCONNECT` | Chrome session lost / `invalid session id` | Chrome closed externally; `browser_checker_thread:742` already resets button. Re-open browser. |
| `ERR_STALE` | Repeated `StaleElementReferenceException` | Page reloaded mid-poll (rematch). Bot auto-retries with backoff; if persistent, update selectors. |
| `ERR_ENGINE` | Generic stockfish wrapper exception | Paste `logs/chess-x.log` traceback (detail truncated to 200 chars on pipe). |

**High CPU / slow moves:** `Depth 18–20` + `Slow Mover 500+` can stall. Use `Slow Mover 80`, `Skill 8–12`, `Depth 10–12` for blitz-like latency. Hash/Threads are auto (`512 MB + nproc/2`).

**Logs location:** `logs/chess-x.log` (`RotatingFileHandler` 5 MB × 3). Debug with `CHES_X_VERBOSE=1 python src/gui.py` or add `--verbose`.

---

## 8. Config not persisting

Config lives at `src/config.json` (`config_store.default_config_path:242`). Falls back to `config.json` then `~/.chess-x.json` (`candidate_paths:171`).

- Every slider/checkbox auto-saves **400 ms** after change (`gui._schedule_save:1595`) via atomic `mkstemp+rename`.
- Old keys (`memory`, `cpu_threads`, `stockfish` v1) are **migrated + dropped** (`migrate:95`).
- Validate & clamp on load (`validate:120`) — e.g., `slow_mover` clamped `10–1000`. File validates on missing `version` too.
- Reset: delete `src/config.json` (backs up as untracked).

Check `logs/chess-x.log` line `Loaded config from src/config.json (v2)` or `Saved config to ...`.

---

## 9. Filing a useful issue

Include **all** of:

- OS, Python (`python --version`), Chrome (`chrome://version`), Stockfish (`stockfish --help` / wrapper version), platform (X11/Wayland via `echo $XDG_SESSION_TYPE`).
- Exact page URL and action (e.g., `lichess.org/training → puzzle 42 → after Continue`).
- Last 100 lines of `logs/chess-x.log` (scrub cookie lines).
- Screenshots: board + overlay alignment, DevTools element for `wc-chess-board` / `cg-container`.
- If DOM-related: saved HTML (`Ctrl+S` → `SingleFile`) attached as `tests/fixtures/yoursite.html`.

Template:

```
OS:
Python:
Chrome:
Stockfish:
Steps:
Expected:
Actual:
Logs (tail):
Selectors checked (DevTools):
```

---

## Further reading

- Architecture: `docs/ARCHITECTURE.md`
- Grabber authoring: `docs/GRABBER_GUIDE.md`
- Changelog & known regressions: `CHANGELOG.md`
- Contributor workflow: `CONTRIBUTING.md`
