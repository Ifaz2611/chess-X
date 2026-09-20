# TODO — Chess-X / PawnBit Improvement Backlog

> Prioritized roadmap for future development. Each item has **Priority** (P0 critical → P3 nice-to-have), **Effort** (S/M/L), and **Area** tags. Check items off as they ship.

Legend: `P0` = blocking / high-value, `P1` = important, `P2` = useful, `P3` = polish. `S` < 1 day, `M` 1–3 days, `L` > 3 days / research.

---

## 1. Stability & Bug Fixes (P0)

- [x] **Fix brittle XPath selectors** — chess.com & lichess DOM changes break `get_board()` / `get_move_list()` frequently. Replace hard XPaths with resilient CSS selectors + fallback chains + `WebDriverWait` (`selenium.webdriver.support.ui`). Add selector health-check on startup. `P0` `M` `grabber`
- [x] **Handle stale / detached DOM** — `StaleElementReferenceException` not caught in `chesscom_grabber.py:get_move_list` and `lichess_grabber.py`. Wrap board & move reads in retry with exponential backoff. `P0` `S` `grabber`
- [x] **Robust new-game detection** — `data-processed` + `moves_list` reset is race-prone (rematch, abort, takeback). Reconcile board FEN via `chess.Board` vs DOM each poll; clear overlay queue on reset. `P0` `M` `engine`
- [x] **Fix Linux permissions / keyboard hook** — document + auto-detect `input` group / `sudo` need; fallback to `pynput` if `keyboard` fails. Handle Wayland (`PyAutoGUI` + `overlay` unsupported). `P0` `S` `platform`
- [x] **Graceful shutdown & zombie processes** — `StockfishBot` + overlay can linger after GUI close (`on_close_listener`). Ensure `atexit` + `signal` handlers kill children, close Pipes, `stockfish` subprocess, and `chrome.quit()`. `P0` `S` `core`
- [x] **Surface engine & Selenium errors** — `stockfish_bot.py:354` swallows exceptions with `print`. Pipe them to GUI `messagebox` + log file. Add `ERR_TIMEOUT`, `ERR_DISCONNECT` codes. `P0` `S` `core`
- [x] **Promotion edge cases** — underpromotion UI (`n/r/b`) relies on `move[2]+str(int(move[3])-N)` which breaks for black promotions. Test all 4 promotion types for both colors. `P0` `S` `engine`
- [x] **Pinned dependency updates** — `selenium 4.9.1` (2023), `chess 1.10.0`, `stockfish 3.28.0` are EOL. Bump to latest, adapt breaking changes (`Service`, `desired_capabilities` removed, `attach_to_session` patch fragile). `P0` `M` `deps`

-=======================================

## 2. Anti-Detection & Humanization (P1 — use responsibly, for vs-bot / analysis only)

- [ ] **Randomized delays** — replace fixed `mouse_latency` + `Slow Mover` with configurable jitter: thinking time `depth ± rand`, move delay `N(μ,σ)`, inter-move pause. Expose profile presets (Bullet / Blitz / Rapid). `P1` `M` `humanize`
- [ ] **Human-like mouse curves** — replace `pyautogui.moveTo`/`dragTo` straight lines with Bézier / wind-mouse trajectories, variable speed, overshoot correction, and occasional micro-misclick + correction. `P1` `M` `humanize`
- [ ] **Strength & blunder profiles** — add ELO-target slider that maps to `Skill + Depth + blunder% + centipawn threshold`. Optionally use `stockfish.get_top_moves(3)` and pick sub-optimal with weighted chance. `P1` `M` `engine`
- [ ] **Time management awareness** — read clock DOM, scale `Slow Mover` / `depth` / `get_best_move_time(ms)` based on remaining time. Avoid flagging in bullet. `P1` `M` `grabber/engine`
- [ ] **Premove & premove-like queuing** — detect opponent premove window; optionally delay bot move to avoid inhuman reaction (<100ms). `P2` `S` `engine`

## 3. Feature Parity (P1)

- [ ] **Chess.com mouseless mode** — investigate `chess.com` WebSocket / `live` API for background moves (parity with `lichess_grabber.py:189`). `P1` `L` `feature`
- [ ] **Chess.com puzzles & non-stop** — implement `is_game_puzzles`, `click_puzzle_next`, `click_game_next` for chess.com (currently no-ops). `P1` `M` `feature`
- [ ] **Chess.com next-game loop** — mirror lichess `New opponent` flow for chess.com rematch / new game. `P1` `S` `feature`
- [ ] **Draw / resign / abort handling** — detect offer buttons and game-result banners (`1-0`, `1/2-1/2`, `0-1`, `*`) beyond current `score_pattern` + `is_game_over` modal check. `P1` `S` `feature`
- [ ] **Takeback / undo support** — re-sync `board` when move count decreases. `P2` `S` `feature`
- [ ] **Variant support** — Chess960 / 3-check / King of the Hill (lichess) — board orientation + move validation. `P3` `M` `feature`

## 4. GUI & Overlay Modernization (P1)

- [x] **Persist config** — save/load Stockfish path, site, params, checkboxes to `config.json` / `QSettings`; restore on launch. `P1` `S` `gui` — implemented: `gui.py:_load_config/_save_config/_apply_config_values` with autosave traces, clamping, and `src/config-example.json` template.
- [x] **Input validation** — clamp `Slow Mover`, `Hash`, `Threads` with live error hints; disable `Start` until valid. Prevent `Threads > os.cpu_count()`. `P1` `S` `gui` — implemented: `gui.py:_validate_inputs/_setup_validation_traces` with red borders, warnings, and Start toggle.
- [ ] **Modern look & theme** — replace `clam` with `ttk` dark/light theme or migrate to `PyQt6`/`CustomTkinter` single-window UI (merge Tkinter + overlay). Add system tray. `P2` `M` `gui`
- [ ] **Move list improvements** — show clock, eval per move, highlight blunders (`??`), copy FEN/PGN with headers (`[Event]`, `[Result]`). `P2` `S` `gui`
- [ ] **Overlay toggle & opacity** — hotkey to hide/show arrow & eval bar; slider for opacity; snap eval bar to either side of board. `P2` `S` `overlay`
- [ ] **Multi-monitor & DPI awareness** — fix `get_top_left_corner()` for per-monitor scaling, fractional DPI, and Chrome window not maximized. Add calibration helper. `P1` `M` `overlay`
- [ ] **Status & telemetry panel** — NPS, depth reached, time per move, connection health, last error log tail. `P2` `S` `gui`

## 5. Engine & Analysis (P1)

- [ ] **Opening book** — optional polyglot `book.bin` for first N moves before engaging Stockfish. `P2` `S` `engine`
- [ ] **Endgame tablebase probing** — local Syzygy or API for perfect play ≤7 pieces. `P2` `M` `engine`
- [x] **Better accuracy model** — replace exact-UCI-match (`white_moves == white_best_moves`) with `centipawn loss` buckets (as lichess does: `>300 brilliant` etc.). Use `stockfish.get_evaluation()` delta. `P1` `M` `engine` — implemented: `stockfish_bot.py:_eval_to_white_cp/_cp_loss_to_bucket/_accuracy_from_losses/_record_cp_loss` with exponential decay accuracy and takeback handling.
- [ ] **PGN headers & export polish** — include `[Site]`, `[Date]`, `[White]`, `[Black]`, `[Result]`, `[TimeControl]`; SAN with `+/#` and `NAG`. Offer auto-save per game. `P2` `S` `engine`
- [ ] **Threat & line display** — show PV (principal variation) arrow(s) or text in overlay/GUI (`get_top_moves`). `P2` `M` `engine/overlay`
- [ ] **Self-play / analysis mode** — run without browser: load FEN/PGN, step through with eval bar. `P3` `M` `feature`

## 6. Platform & Distribution (P2)

- [ ] **macOS support** — test & document ChromeDriver + `PyAutoGUI` + overlay on macOS (retina scaling, permissions). `P2` `M` `platform`
- [ ] **Headless / Docker** — `Dockerfile` + `docker-compose` with headless Chrome, Xvfb, and noVNC for server use. `P2` `M` `platform`
- [ ] **Packaged executable** — `PyInstaller` / `briefcase` one-file build for Windows & Linux; include Stockfish or downloader. `P2` `M` `packaging`
- [ ] **Wayland support** — replace `PyAutoGUI` with `pynput`/`xdotool`/`ydotool` abstraction; make overlay work under Wayland (`layer-shell`). `P2` `L` `platform`
- [ ] **Firefox / Edge support** — abstract `webdriver` creation; allow `geckodriver` / `msedgedriver`. `P3` `S` `platform`

## 7. Code Quality, Testing & DevOps (P1)

- [x] **Automated tests** — `pytest` unit tests for `utilities.char_to_num`, `move_to_screen_pos`, `calculate_material_advantage`, grabber parsers (mock DOM), and PGN export. `P1` `M` `testing` — implemented: `tests/test_utilities.py`, `tests/test_stockfish_bot.py`, `tests/test_grabbers.py`, `tests/test_config.py` (24 tests).
- [ ] **Integration harness** — fixtures with static HTML snapshots of chess.com / lichess boards; CI runs grabber parsing without live browser. `P1` `M` `testing`
- [x] **Lint & format** — add `ruff` + `black` + `mypy` + `pre-commit` hooks; fix `sourcery skip` debt and bare `except:` in `stockfish_bot.py:377`. `P1` `S` `quality` — implemented: `pyproject.toml` (ruff/black/mypy), `.pre-commit-config.yaml`.
- [x] **Logging** — structured `logging` to `logs/chess-x.log` with rotation; replace `print(e)` in `overlay.py:283` & `stockfish_bot.py:355`. Add `--verbose` flag. `P1` `S` `quality` — implemented: `utilities.py:get_logger` now uses `RotatingFileHandler` (5 MB ×3) and `CHES_X_VERBOSE`/`--verbose`.
- [x] **CI pipeline** — GitHub Actions: `install → lint → test → build` on push; cache `webdriver`. `P1` `S` `devops` — implemented: `.github/workflows/ci.yml` matrix 3.10–3.12.
- [ ] **Type hints & docstrings** — annotate public methods (`Grabber`, `StockfishBot`, `OverlayScreen`); add Google-style docstrings. `P2` `M` `quality`
- [ ] **Refactor process model** — replace `multiprocess` fork + `attach_to_session` hack with `threading` or `concurrent.futures` + `selenium` BiDi where possible; remove `WebDriver.execute` monkey-patch. `P2` `L` `core`
- [x] **Dependency hygiene** — `requirements.txt` → `pyproject.toml` (`hatch`/`poetry`), pin with `pip-tools`, add `dependabot`. `P2` `S` `deps` — partially implemented: added `pyproject.toml` with `project` metadata and `optional-dependencies.dev`.
- [ ] **Security review** — audit `execute_script` injection (`make_mouseless_move`), file-path handling, and `keyboard` privilege surface. `P1` `S` `security`

## 8. Documentation (P2)

- [ ] **Architecture diagram** — Mermaid / draw.io version of the pipeline in `docs/ARCHITECTURE.md`. `P2` `S` `docs`
- [ ] **Grabber authoring guide** — how to add a new site (implement `Grabber` ABC, selectors, tests). `P2` `S` `docs`
- [ ] **Troubleshooting wiki** — expand FAQ with screenshots, Wayland/Chrome variations, and known DOM breakages. `P2` `S` `docs`
- [ ] **Changelog** — `CHANGELOG.md` with semver tags. `P3` `S` `docs`

## 9. Nice-to-Have / Future Exploration (P3)

- [ ] **Voice / chat control** — "start / stop / hint" via voice or chat command.
- [ ] **Mobile (Android) via ADB** — drive `lichess` mobile app through `uiautomator2`.
- [ ] **Cloud engine option** — fallback to Lichess Cloud Eval / chess.com analysis API when local Stockfish unavailable.
- [ ] **Game database** — SQLite history with searchable PGN, eval graphs, and accuracy trends.
- [ ] **Auto-update** — check GitHub releases + self-update Stockfish/ChromeDriver.

---

## Suggested Milestones

| Milestone | Scope | Outcome |
|---|---|---|
| **v0.2 — Solidify** | All `P0` | No crashes on DOM change, clean shutdown, updated deps, tests green |
| **v0.3 — Humanize** | Anti-detection `P1` + parity `P1` | Configurable human-like play, chess.com feature parity |
| **v0.4 — Polish** | GUI `P1/P2` + engine `P1` | Persisted config, better PGN/accuracy, overlay controls |
| **v0.5 — Ship** | Platform `P2` + DevOps `P1` | Docker, packaged exe, CI, docs |

---

> **Contributing:** Pick any unchecked item, open an Issue referencing its line, and submit a PR. For `P0`/`P1` items please include a short test or repro note. See `README.md#contributing`.
