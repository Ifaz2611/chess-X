# Changelog

All notable changes to **Chess-X** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Stockfish is not bundled** — download it from https://stockfishchess.org/download/.

---

## [Unreleased]

### Added
- `docs/ARCHITECTURE.md` — Mermaid pipeline, process/sequence/class diagrams, module map, IPC and resilience patterns.
- `docs/GRABBER_GUIDE.md` — step-by-step Grabber authoring guide (contract, selectors, example site, tests, GUI wiring).
- `docs/TROUBLESHOOTING.md` — expanded wiki: Chrome/Driver variations, DOM breakages, Wayland/overlay, DPI & multi-monitor, Linux input, FEN resync, error codes (`ERR_*`), log collection.
- `CHANGELOG.md` with semver history.

### Changed
- Docs index in `README.md` (links to `docs/`).

---

## [2.1.0] - 2026-09-22

### Added
- P1 accuracy model in `engine_service.py` / `stockfish_bot.py:186` — centipawn-loss buckets (`best/excellent/good/inaccuracy/mistake/blunder`) and lichess-style `accuracy_from_losses` (`exp(-avg/550)`), replacing exact-UCI-match.
- Example config `src/config-example.json` referenced in `GETSTART.md`.
- Offline fixtures `tests/fixtures/chesscom.html`, `lichess.html` for integration harness (`TODO 6`).

### Changed
- Engine resources auto-managed: `Hash` 512 MB + `Threads` `os.cpu_count()//2` removed from GUI-config (`stockfish_bot.py:50`); legacy `memory`/`cpu_threads` keys migrated away in `config_store.py:55`.
- Promotion handling fixed for all 4 pieces × both colours (`stockfish_bot.make_move:368`).
- Move-timing scaled by `Slow Mover` / depth (`get_best_move_time 50–300 ms`, `stockfish_bot.py:569`) for bullet/blitz responsiveness.
- GUI polish: light theme (`BG_BASE #F4F1EA`), `clam` Treeview, `_button` contrast fix for `SELECT STOCKFISH` (was white-on-light), radio highlight sync, eval bar init race fix, `Export PGN` unique filenames.
- `BrowserSessionManager` fixes for `webdriver-manager` cache path `third_party_notices.chromedriver` → `chromedriver.exe` search (`gui.py:1099`).
- Overlay always-on-top under Wayland warning + `QT_QPA_PLATFORM=xcb` hint (`gui.py:33`).

### Fixed
- Stale board after rematch / takeback: full FEN reconcile with three cases (0 moves, takeback ↓, same-length mismatch) plus cp-loss trimming (`stockfish_bot.py:738`).
- `is_white` and `get_move_list` stale/DOM race — multi-attempt retry with exponential backoff across both grabbers.
- `find_element` fallback warning on last-resort selector (`grabber.py:125`).
- `get_move_list` fresh board now returns `[]` (not `None`) to avoid spurious `ERR_MOVES` (`chesscom_grabber.py:255`, `lichess_grabber.py:314`).
- GUI emergency `KILL` now hard-kills via `os._exit` after window destroy (`gui.py:618`).

### Security
- Documented `SECURITY.md` (replaces inline disclaimer).

---

## [2.0.0] - 2026-09-10

### Added
- Typed IPC `src/protocol.py` — dataclasses `MsgStart/Restart/SingleMove/MultiMove/Eval/Error` on Pipe and `OverlayArrows/Eval/Clear` on Queue; `legacy_to_bot_msg` / `legacy_to_overlay_msg` for gradual migration (`stockfish_bot.py:94`, `overlay.py:68`).
- `src/config_store.py` v2 — `Config` dataclass, `migrate(v1→v2)`, `validate`/`clamp`, atomic `mkstemp+rename` persistence, `candidate_paths` (`src/config.json` → `config.json` → `~/.chess-x.json`).
- Extracted modules: `src/browser_session.py`, `src/hotkeys.py`, `src/board_sync.py`, `src/engine_service.py`, `src/move_executor.py`, `src/game_loop.py`, `src/controls.py`, `src/eval_panel.py`, `src/analysis.py`, `src/puzzle_trainer.py`, `src/local_board.py` (Phase 2–5 extractions).
- `src/utilities.py:154` — `retry_on_stale`, `wait_for_any_element`, `find_element_with_fallback`, `attach_to_session` compatible Selenium 4.9→4.49, `is_wayland`, `check_linux_input_permissions`, `get_keyboard_handler` with `pynput` fallback, `RotatingFileHandler` 5 MB×3 (`logs/chess-x.log`).
- CI `.github/workflows/ci.yml` — Windows + Linux × Python 3.10–3.12, `pytest-cov`, `ruff`, `mypy`, `compileall`, webdriver cache.

### Changed
- `StockfishBot` split from monolithic `gui.py` into facaded `BoardSync`/`EngineService`/`MoveExecutor` delegates (`stockfish_bot.py:71`).
- GUI debounced autosave (400 ms) and topmost/input-permission checks.

### Fixed
- Overlay queue draining on new game (`OverlayClear` via protocol).
- `health_check` logging selector that actually matched.

---

## [1.2.0] - 2026-08-28

### Added
- Lichess resilient JS SAN extraction for obfuscated tags (`rm6/l4x` randomisation) — `_js_extract_moves` + `get_normal_move_list_elem` JS fallback (`lichess_grabber.py:152`).
- `LichessGrabber.make_mouseless_move` via `lichess.socket.ws.send` with retry (`lichess_grabber.py:516`).
- PGN export unique-filename logic (`gui.py:1398` — `match_YYYYMMDD_HHMMSS_ms_counter.pgn` never overwrites).
- `GETSTART.md` 5-minute quick-start (first-game 6 clicks, Stockfish download table, Wayland tip).

### Changed
- `ChesscomGrabber` broadened `BOARD_SELECTORS` / `MOVE_LIST_SELECTORS` with ranked fallbacks and SVG fallback for `is_white` (`chesscom_grabber.py:15`).
- README expanded with How-It-Works Mermaid, Features matrix, Stockfish params, Overlay & Evaluation deep-dive.

### Fixed
- `select_stockfish` now topmost-aware (dialog no longer hidden), validates via `uciok` (rejects `.py`/`.txt` picks) and refuses to persist invalid path (`gui.py:1858`).

---

## [1.1.0] - 2026-08-15

### Added
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.
- Linux input-group check plus `pynput` hotkey fallback (`utilities.get_keyboard_handler:104`).

### Changed
- GUI layout to card-based sections (Platform / Controls / Modes / Stockfish / Engine binary / Move History / Eval) with `clam` theme.

### Fixed
- Windows path handling for driver cache traversal in `gui._open_browser_worker`.

---

## [1.0.0] - 2026-08-01

### Added
- Initial public release: `src/gui.py` (Tkinter), `src/stockfish_bot.py` (Selenium + python-chess + Stockfish), `src/overlay.py` (PyQt6 arrow + eval bar), grabbers for `chess.com` + `lichess.org`, `run.bat`.

[Unreleased]: https://github.com/Ifaz2611/chess-X/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/Ifaz2611/chess-X/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/Ifaz2611/chess-X/compare/v1.2.0...v2.0.0
[1.2.0]: https://github.com/Ifaz2611/chess-X/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Ifaz2611/chess-X/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Ifaz2611/chess-X/releases/tag/v1.0.0
