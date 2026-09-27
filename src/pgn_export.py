from __future__ import annotations
import datetime
import os
import chess
from utilities import get_logger
logger = get_logger("pgn_export")



NAG_GOOD = 1       # !
NAG_MISTAKE = 2    # ?
NAG_BRILLIANT = 3  # !!
NAG_BLUNDER = 4    # ??
NAG_INTERESTING = 5  # !?
NAG_DUBIOUS = 6    # ?!



BUCKET_TO_NAG = {
    "best": None,
    "excellent": None,
    "good": None,
    "inaccuracy": NAG_DUBIOUS,
    "mistake": NAG_MISTAKE,
    "blunder": NAG_BLUNDER,
}

NAG_TO_GLYPH = {1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!"}


def bucket_to_nag(bucket: str | None) -> int | None:
    if not bucket:
        return None
    return BUCKET_TO_NAG.get(str(bucket).strip().lower())


def board_result(board: chess.Board) -> str:
    """Result marker for headers/movetext: '1-0' / '0-1' / '1/2-1/2' / '*'."""
    try:
        if board.is_checkmate():
            return "0-1" if board.turn == chess.WHITE else "1-0"
        if (board.is_stalemate() or board.is_insufficient_material()
                or board.is_seventyfive_moves() or board.is_fivefold_repetition()):
            return "1/2-1/2"
    except Exception:
        pass
    return "*"


def default_headers(site: str = "chess.com", white: str = "Me", black: str = "Opponent",
                     result: str = "*", time_control: str = "-",
                     event: str = "Casual Game", date: str | None = None,
                     extra: dict | None = None) -> dict:
    headers = {
        "Event": event,
        "Site": site,
        "Date": date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y.%m.%d"),
        "White": white,
        "Black": black,
        "Result": result,
        "TimeControl": time_control,
    }
    if extra:
        headers.update({k: str(v) for k, v in extra.items()})
    return headers


def build_movetext(sans: list[str], nags: list[int | None] | None = None,
                   result: str = "*") -> str:



    nags = list(nags) if nags else [None] * len(sans)
    # pad/truncate defensively
    if len(nags) < len(sans):
        nags = nags + [None] * (len(sans) - len(nags))
    parts: list[str] = []
    for i in range(0, len(sans), 2):
        parts.append(f"{i // 2 + 1}.")
        parts.append(sans[i])
        if nags[i]:
            parts.append(f"${int(nags[i])}")
        if i + 1 < len(sans):
            parts.append(sans[i + 1])
            if nags[i + 1]:
                parts.append(f"${int(nags[i + 1])}")
    if result and result != "*":
        parts.append(result)
    elif result == "*":
        parts.append("*")
    return " ".join(parts)


def build_pgn(sans: list[str], headers: dict | None = None,
              nags: list[int | None] | None = None, result: str = "*") -> str:


    headers = dict(headers) if headers else default_headers(result=result)
    headers.setdefault("Result", result)
    lines = [f'[{k} "{v}"]' for k, v in headers.items()]
    lines.append("")
    lines.append(build_movetext(sans, nags=nags, result=headers.get("Result", result)))
    lines.append("")
    return "\n".join(lines)


def sans_from_board(board: chess.Board) -> list[str]:


    try:
        replay = board.root()
    except Exception:
        replay = chess.Board()
    out: list[str] = []
    for move in board.move_stack:
        try:
            out.append(replay.san(move))
            replay.push(move)
        except Exception as e:
            logger.debug("sans_from_board failed at %s: %s", move.uci(), e)
            out.append(move.uci())
    return out


def _loss_to_bucket(loss: int) -> str:


    try:
        value = int(loss)
    except Exception:
        value = 0
    if value <= 10:
        return "best"
    if value <= 50:
        return "excellent"
    if value <= 100:
        return "good"
    if value <= 200:
        return "inaccuracy"
    if value <= 400:
        return "mistake"
    return "blunder"


def nags_from_losses(white_losses: list[int], black_losses: list[int]) -> list[int | None]:


    nags: list[int | None] = []
    for i in range(max(len(white_losses), len(black_losses))):
        if i < len(white_losses):
            nags.append(bucket_to_nag(_loss_to_bucket(white_losses[i])))
        if i < len(black_losses):
            nags.append(bucket_to_nag(_loss_to_bucket(black_losses[i])))
    return nags


def unique_pgn_path(directory: str | os.PathLike, stem: str = "match") -> str:


    directory = str(directory)
    os.makedirs(directory, exist_ok=True)
    now = datetime.datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    ms = now.strftime("%f")[:3]
    n = 0
    while True:
        n += 1
        path = os.path.join(directory, f"{stem}_{ts}_{ms}_{n:02d}.pgn")
        if not os.path.exists(path):
            return path


def save_pgn(path: str | os.PathLike, sans: list[str], headers: dict | None = None,
             nags: list[int | None] | None = None, result: str = "*") -> str:
    text = build_pgn(sans, headers=headers, nags=nags, result=result)
    path = str(path)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    logger.info("PGN saved to %s (%d moves)", path, len(sans))
    return path


def auto_save_game(directory: str | os.PathLike, sans: list[str],
                   headers: dict | None = None,
                   nags: list[int | None] | None = None,
                   result: str = "*") -> str | None:


    try:
        if not sans:
            return None
        path = unique_pgn_path(directory)
        return save_pgn(path, sans, headers=headers, nags=nags, result=result)
    except Exception as e:
        logger.warning("PGN auto-save failed: %s", e)
        return None
