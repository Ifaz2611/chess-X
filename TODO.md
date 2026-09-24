# TODO — Chess-X Remaining Work

> Only **pending** tasks are listed. Completed stability, persistence, accuracy, logging, tests and CI work has been removed. Priority `P0`→`P3`, Effort `S` (<1d) `M` (1–3d) `L` (>3d).

---

## 1. Anti-Detection & Humanization (P1 — educational / vs-bot only)

- [ ] **Randomized delays** — replace fixed `mouse_latency` + `Slow Mover` with jitter: thinking time `depth ± rand`, move delay `N(μ,σ)`, inter-move pause. Presets: Bullet / Blitz / Rapid. `P1` `M` `humanize`
- [ ] **Human-like mouse curves** — Bézier / wind-mouse `pyautogui` trajectories, variable speed, overshoot + micro-misclick correction. `P1` `M` `humanize`
- [ ] **Strength & blunder profiles** — ELO slider → `Skill + Depth + blunder% + cp threshold`. Use `get_top_moves(3)` weighted pick. `P1` `M` `engine`
- [ ] **Time management awareness** — read clock DOM, scale `Slow Mover` / `depth` / `get_best_move_time(ms)` by remaining time. `P1` `M` `grabber/engine`
- [ ] **Premove queuing** — detect opponent premove window, delay bot move to avoid <100ms reactions. `P2` `S` `engine`

## 2. Feature Parity (P1)

- [ ] **Chess.com mouseless mode** — investigate `chess.com` WebSocket/live API for background moves (parity with `lichess_grabber.py:189`). `P1` `L` `feature`
- [ ] **Chess.com puzzles & non-stop** — implement `is_game_puzzles`, `click_puzzle_next`, `click_game_next` for chess.com. `P1` `M` `feature`
- [ ] **Chess.com next-game loop** — mirror Lichess `New opponent` flow for chess.com rematch/new game. `P1` `S` `feature`
- [ ] **Draw / resign / abort handling** — detect offer buttons and result banners (`1-0`, `1/2-1/2`, `0-1`, `*`) beyond `score_pattern`. `P1` `S` `feature`
- [ ] **Takeback / undo support** — re-sync `board` when move count decreases. `P2` `S` `feature`
- [ ] **Variant support** — Chess960 / 3-check / King of the Hill (board orientation + validation). `P3` `M` `feature`

## 3. GUI & Overlay (P1-P2)

- [ ] **Modern look & theme** — migrate to `PyQt6`/`CustomTkinter` single-window UI, add system tray / light-dark toggle. `P2` `M` `gui`
- [ ] **Move list polish** — show clock, eval per move, highlight blunders (`??`), copy FEN/PGN with headers `[Event]` `[Result]`. `P2` `S` `gui`
- [ ] **Overlay toggle & opacity** — hotkey hide/show arrow & eval bar, opacity slider, snap to either side of board. `P2` `S` `overlay`
- [ ] **Multi-monitor & DPI awareness** — fix `get_top_left_corner()` for per-monitor scaling, fractional DPI, non-maximized Chrome. Calibration helper. `P1` `M` `overlay`
- [ ] **Status & telemetry panel** — NPS, depth reached, time per move, connection health, last error tail. `P2` `S` `gui`

## 4. Engine & Analysis (P1-P2)

- [ ] **Opening book** — optional polyglot `book.bin` for first N moves. `P2` `S` `engine`
- [ ] **Endgame tablebase probing** — local Syzygy or API for ≤7 pieces. `P2` `M` `engine`
- [ ] **PGN headers & export polish** — include `[Site]` `[Date]` `[White]` `[Black]` `[Result]` `[TimeControl]`, SAN `+/#` + `NAG`, auto-save per game. `P2` `S` `engine`
- [ ] **Threat & line display** — show PV arrows/text via `get_top_moves` in overlay/GUI. `P2` `M` `engine/overlay`
- [ ] **Self-play / analysis mode** — run without browser: load FEN/PGN, step through with eval bar. `P3` `M` `feature`


## 5. Code Quality, Testing & DevOps (P1-P2)

- [ ] **Integration harness** — fixtures with static HTML snapshots of chess.com / lichess boards; CI runs grabber parsing without live browser. `P1` `M` `testing`
- [ ] **Type hints & docstrings** — annotate public methods `Grabber`, `StockfishBot`, `OverlayScreen` with Google-style docstrings. `P2` `M` `quality`
- [ ] **Refactor process model** — replace `multiprocess` fork + `attach_to_session` hack with `threading`/`concurrent.futures` + BiDi; remove `WebDriver.execute` monkey-patch. `P2` `L` `core`
- [ ] **Security review** — audit `execute_script` injection (`make_mouseless_move`), file-path handling, `keyboard` privilege surface. `P1` `S` `security`

---

## Suggested Next Milestones

| Milestone | Scope | Outcome |
|---|---|---|
| **v0.3 — Humanize** | Anti-detection `P1` + parity `P1` | Human-like play, chess.com parity |
| **v0.4 — Polish** | GUI `P1/P2` + engine `P1` | Theme polish, overlay controls, PGN headers |
| **v0.5 — Ship** | Platform `P2` + DevOps `P1` | Docker, packaged exe, docs |

> **Contributing:** Pick any unchecked item, open an Issue referencing its line, and submit a PR. See `README.md#contributing` and `GETSTART.md`.
