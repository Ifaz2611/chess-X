# TODO — Chess-X Remaining Work

> Only **pending** tasks are listed. Completed stability, persistence, accuracy, logging, tests and CI work has been removed. Priority `P0`→`P3`, Effort `S` (<1d) `M` (1–3d) `L` (>3d).

---

## 1. GUI & Overlay (P1-P2)

- [ ] **Modern look & theme** — migrate to `PyQt6`/`CustomTkinter` single-window UI, add system tray / light-dark toggle. `P2` `M` `gui`
- [x] **Move list polish** — show clock, eval per move, highlight blunders (`??`), copy FEN/PGN with headers `[Event]` `[Result]`. `P2` `S` `gui`
- [x] **Overlay toggle & opacity** — hotkey hide/show arrow & eval bar, opacity slider, snap to either side of board. `P2` `S` `overlay`
- [ ] **Multi-monitor & DPI awareness** — fix `get_top_left_corner()` for per-monitor scaling, fractional DPI, non-maximized Chrome. Calibration helper. `P1` `M` `overlay`
- [x] **Status & telemetry panel** — NPS, depth reached, time per move, connection health, last error tail. `P2` `S` `gui`


## 2. Code Quality, Testing & DevOps (P1-P2)

- [x] **Integration harness** — fixtures with static HTML snapshots of chess.com / lichess boards; CI runs grabber parsing without live browser. `P1` `M` `testing`
- [ ] **Type hints & docstrings** — annotate public methods `Grabber`, `StockfishBot`, `OverlayScreen` with Google-style docstrings. `P2` `M` `quality`
- [ ] **Refactor process model** — replace `multiprocess` fork + `attach_to_session` hack with `threading`/`concurrent.futures` + BiDi; remove `WebDriver.execute` monkey-patch. `P2` `L` `core`
- [ ] **Security review** — audit `execute_script` injection (`make_mouseless_move`), file-path handling, `keyboard` privilege surface. `P1` `S` `security`

---

> **Contributing:** Pick any unchecked item, open an Issue referencing its line, and submit a PR. See `README.md#contributing` and `GETSTART.md`.
