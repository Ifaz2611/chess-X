# GETSTART — Chess-X Beginner Guide

Get up and running in **5 minutes**. No coding needed.

> **Educational use only** — use vs bots, puzzles, or local analysis. Cheating in rated games will get you banned.

---

## 1. What You Need

- **Google Chrome** (latest) — [download](https://www.google.com/chrome/)
- **Python 3.10+** — [download](https://www.python.org/downloads/) — tick **“Add to PATH”** during install
- **Stockfish engine** — [download](https://stockfishchess.org/download/)

| OS | File to download |
|---|---|
| Windows | `stockfish-windows-x86-64-avx2.zip` (contains `stockfish-windows-x86-64-avx2.exe`) |
| Linux | `stockfish` via `sudo apt install stockfish` or the Linux zip |
| macOS | `stockfish-mac` zip (untested) |

Unzip Stockfish to a folder you will remember, e.g. `C:\stockfish\` or `~/stockfish/`.

On **Linux** make it executable:
```bash
chmod +x stockfish
```

---

## 2. Install Chess-X

```bash
# 1. Clone or download ZIP and open terminal in the folder
git clone https://github.com/Ifaz2611/chess-X
cd chess-X

# 2. Create virtual environment
# Windows:
python -m venv venv
# Linux / macOS:
python3 -m venv venv

# 3. Install dependencies
# Windows:
venv\Scripts\pip install -r requirements.txt
# Linux / macOS:
venv/bin/pip install -r requirements.txt
```

No extra build step. `PyQt6`, `selenium`, `chess`, `stockfish` wrapper, etc. all come from `requirements.txt`.

---

## 3. Launch

```bash
# Windows:
venv\Scripts\python src\gui.py
# Linux / macOS:
venv/bin/python src/gui.py
# Windows quick double-click:
# double-click run.bat
```

You should see a light beige window titled **CHESS-X · Stockfish Bot** (1120×760). All text is black for readability.

If you see `libEGL.so.1` or blank window on Linux, run:
```bash
sudo apt-get install -y libegl1 libgl1 libxkbcommon-x11-0
export QT_QPA_PLATFORM=offscreen  # for headless tests only
```

---

## 4. First Game — 6 Clicks

1. **Select Stockfish** → navigate to the binary you unzipped (`stockfish*.exe` or `stockfish`).  
   Label turns **green** when valid; red means wrong file. Pick the `.exe`, not a `.py`.

2. **Choose Platform** — `Chess.com` or `Lichess.org` radio at the top left. It highlights orange when selected.

3. **Open Browser** → click **↗ OPEN BROWSER** (teal). A Chrome window opens to chess.com or lichess.org. **Log in** there if you want.

4. **Go to a game**:  
   - Chess.com: `Play → Quick pairing → vs Computer` or `Puzzles`  
   - Lichess: `Play → Create a game → vs Stockfish` or `Puzzles → Training`

5. **Start** → click **▶ START ENGINE** or press **`1`**. Status pill top-right turns **green RUNNING**.  
   You will see:  
   - Red arrow on the board (overlay) = best move  
   - Left vertical eval bar + `+1.23` / `M3` in the GUI

6. **Stop/Pause** → click **■ STOP** or press **`2`**.  
   **Manual Mode** (checkbox): bot shows arrow but waits for you to hold **`3`** to execute.

---

## 5. GUI Quick Reference

| Area | What it does |
|---|---|
| **Platform** | Pick site *before* opening browser |
| **Controls** → `OPEN BROWSER` | Launches managed Chrome (auto-downloads driver) |
| **Controls** → `START ENGINE` | Enabled only after browser open *and* valid Stockfish path |
| **Modes** | `Manual`, `Mouseless` (Lichess only), `Non-stop puzzles/matches` |
| **Stockfish** | `Slow Mover 10–1000` (lower = faster), `Skill 0–20`, `Depth 1–20`. Hash/Threads are now auto-managed. |
| **Engine binary** | Shows current path; `SELECT STOCKFISH`; `Keep window on top` |
| **Move History** | `# / White / Black` — auto-scrolls |
| **Export PGN** | Saves `match_YYYYMMDD_HHMMSS_*.pgn` — never overwrites, picks next free name |
| **Engine Evaluation** | Big number `+0.42` / `M1`, bar, `WDL`, `MATERIAL`, `BOT ACC` / `OPP ACC` |
| **Emergency** | Footer `KILL BOT + BROWSER` or `Esc` / `F12` / `Ctrl+Q` — hard-kills everything |

**Hotkeys (global, even when Chrome focused):**

- `1` = Start, `2` = Stop, `3` (hold) = make move in Manual Mode, `Esc`/`F12` = Kill

On **Linux** if hotkeys fail: `sudo usermod -aG input $USER` then log out/in, or run with `sudo venv/bin/python src/gui.py`.

---

## 6. Common Fixes (30 seconds each)

| Problem | Fix |
|---|---|
| `Stockfish not found / not valid` | Re-click `SELECT STOCKFISH`, pick the **binary** (1–5 MB .exe/ELF), not a script. `file stockfish` should say ELF/executable. |
| `Can't find Chrome` | Install Chrome from google.com/chrome. Delete `~/.wdm` folder and retry. |
| `Can't find board / color / moves` | Make sure Chrome is on a **live board page** (not homepage). Reload page, then `Stop → Start`. |
| Overlay missing | Keep Chrome maximized, 100% display scaling. Try `QT_QPA_PLATFORM=xcb` on Linux. |
| Clicks miss squares | Set Windows `Display Settings → Scale = 100%`, keep board fully visible, one monitor. |
| Chrome closes instantly | Close all Chrome windows, delete `~/.wdm`, click `OPEN BROWSER` again. |
| Moves stop after a few | Click `Stop` then `Start` — board DOM changed; new FEN reconnection handles most cases. Check `logs/chess-x.log`. |

Logs live in `logs/chess-x.log` (rotates 5 MB ×3). Run with `CHES_X_VERBOSE=1` for debug.

---

## 7. Config Is Auto-Saved

Every checkbox/slider is saved 400 ms after you change it to `src/config.json`. Next launch restores everything — no need to re-pick Stockfish or site. Delete `src/config.json` to reset.

Example `src/config-example.json`:
```json
{
  "stockfish_path": "C:/path/to/stockfish.exe",
  "website": "chesscom",
  "enable_manual_mode": false,
  "slow_mover": 100,
  "skill_level": 20,
  "stockfish_depth": 15
}
```

---

## 8. Next Steps

- Read `README.md#troubleshooting` for site-specific details
- See `TODO.md` for what's still being built (humanization, Docker, headless)
- Open an Issue if you find a DOM breakage — include OS, Python version, Chrome version, and last lines of `logs/chess-x.log`

**Have fun and play fair!** Use this against bots or for post-game analysis — not against humans in rated play.
