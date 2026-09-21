"""Typed dataclass messages for IPC (Pipe / Queue).

Replaces ad-hoc string protocol like "S_MOVEe4", "EVAL|...", "ERR_BOARD".
All messages are pickle-able and can be sent via multiprocess.Pipe / Queue.

Backward compatibility: helpers to convert to/from legacy string form for
gradual migration and for logging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union, List, Dict, Any, Optional
import queue  # for type hints

# ---------- Pipe: Bot -> GUI ----------

@dataclass(frozen=True)
class MsgStart:
    """Bot signals GUI to clear board and set RUNNING."""
    kind: Literal["START"] = "START"

@dataclass(frozen=True)
class MsgRestart:
    """Bot signals GUI that a new game / takeback restart is needed. GUI should send MsgDelete."""
    kind: Literal["RESTART"] = "RESTART"

@dataclass(frozen=True)
class MsgDelete:
    """GUI -> Bot: acknowledge RESTART and tell bot to continue."""
    kind: Literal["DELETE"] = "DELETE"

@dataclass(frozen=True)
class MsgSingleMove:
    """Single SAN move played (bot or opponent)."""
    san: str
    kind: Literal["S_MOVE"] = "S_MOVE"

@dataclass(frozen=True)
class MsgMultiMove:
    """Bulk move list (e.g., initial sync)."""
    sans: List[str]
    kind: Literal["M_MOVE"] = "M_MOVE"

@dataclass(frozen=True)
class MsgEval:
    """Evaluation update for GUI labels + overlay bar."""
    eval_str: str  # "+0.42" or "M3"
    wdl_str: str   # "45.0/30.0/25.0"
    material_str: str  # "+3"
    bot_acc: str   # "92.3%"
    opponent_acc: str
    kind: Literal["EVAL"] = "EVAL"

@dataclass(frozen=True)
class MsgError:
    """Error / warning to show in GUI."""
    code: str  # e.g. "ERR_BOARD", "ERR_COLOR", "ERR_MOVES", "ERR_GAMEOVER", "ERR_TIMEOUT", "ERR_DISCONNECT", "ERR_STALE", "ERR_ENGINE", "ERR_EXE", "ERR_PERM"
    detail: str = ""
    kind: Literal["ERROR"] = "ERROR"

BotToGuiMessage = Union[MsgStart, MsgRestart, MsgSingleMove, MsgMultiMove, MsgEval, MsgError]
GuiToBotMessage = Union[MsgDelete]
PipeMessage = Union[BotToGuiMessage, GuiToBotMessage, str]  # str for legacy fallback


# ---------- Queue: Bot -> Overlay ----------

@dataclass(frozen=True)
class OverlayArrows:
    """Arrow polygons for overlay: list of ((x1,y1),(x2,y2)). Empty list clears."""
    arrows: List[Any]  # List[Tuple[Tuple[int,int], Tuple[int,int]]]
    kind: Literal["ARROWS"] = "ARROWS"

@dataclass(frozen=True)
class OverlayEval:
    """Eval bar update."""
    eval_value: float  # centipawns / 100 or mate value
    eval_type: str     # "cp" or "mate"
    board_position: Optional[Dict[str, Any]] = None  # {x,y,width,height}
    is_white: Optional[bool] = None
    kind: Literal["OVERLAY_EVAL"] = "OVERLAY_EVAL"

@dataclass(frozen=True)
class OverlayClear:
    """Clear overlay arrows."""
    kind: Literal["CLEAR"] = "CLEAR"

OverlayMessage = Union[OverlayArrows, OverlayEval, OverlayClear, List[Any], Dict[str, Any]]  # legacy list/dict fallback


# ---------- Serialization for legacy string protocol ----------

def bot_msg_to_legacy(msg: BotToGuiMessage) -> str:
    """Convert typed message to legacy string (for logging / transitional)."""
    if isinstance(msg, MsgStart):
        return "START"
    if isinstance(msg, MsgRestart):
        return "RESTART"
    if isinstance(msg, MsgSingleMove):
        return f"S_MOVE{msg.san}"
    if isinstance(msg, MsgMultiMove):
        return "M_MOVE" + ",".join(msg.sans)
    if isinstance(msg, MsgEval):
        return f"EVAL|{msg.eval_str}|{msg.wdl_str}|{msg.material_str}|{msg.bot_acc}|{msg.opponent_acc}"
    if isinstance(msg, MsgError):
        if msg.detail:
            return f"{msg.code}|{msg.detail}" if "|" not in msg.code else f"{msg.code}{msg.detail}"
        return msg.code
    return str(msg)


def legacy_to_bot_msg(s: str) -> BotToGuiMessage | str:
    """Parse legacy string to typed message; returns original str if unknown."""
    if not isinstance(s, str):
        return s  # already typed
    if s == "START":
        return MsgStart()
    if s.startswith("RESTART"):
        return MsgRestart()
    if s.startswith("S_MOVE"):
        return MsgSingleMove(san=s[6:])
    if s.startswith("M_MOVE"):
        payload = s[6:]
        sans = [m for m in payload.split(",") if m]
        return MsgMultiMove(sans=sans)
    if s.startswith("EVAL|"):
        parts = s.split("|")
        if len(parts) >= 6:
            return MsgEval(eval_str=parts[1], wdl_str=parts[2], material_str=parts[3], bot_acc=parts[4], opponent_acc=parts[5])
        return s
    if s.startswith("ERR_"):
        # ERR_CODE|detail or ERR_CODE
        if "|" in s:
            code, detail = s.split("|", 1)
            return MsgError(code=code, detail=detail)
        return MsgError(code=s)
    return s  # unknown, keep as str


def is_typed_pipe_msg(obj: Any) -> bool:
    return isinstance(obj, (MsgStart, MsgRestart, MsgSingleMove, MsgMultiMove, MsgEval, MsgError, MsgDelete))


def overlay_to_legacy(msg: OverlayMessage) -> Any:
    """Convert typed overlay msg to legacy form (list or dict) for queue."""
    if isinstance(msg, OverlayArrows):
        return msg.arrows
    if isinstance(msg, OverlayClear):
        return []
    if isinstance(msg, OverlayEval):
        d: Dict[str, Any] = {"eval": msg.eval_value, "eval_type": msg.eval_type}
        if msg.board_position is not None:
            d["board_position"] = msg.board_position
        if msg.is_white is not None:
            d["is_white"] = msg.is_white
        return d
    return msg  # already legacy


def legacy_to_overlay_msg(obj: Any) -> OverlayMessage:
    if isinstance(obj, list):
        # list of arrows or empty clear
        if len(obj) == 0:
            return OverlayClear()
        return OverlayArrows(arrows=obj)
    if isinstance(obj, dict) and "eval" in obj:
        return OverlayEval(
            eval_value=obj.get("eval", 0.0),
            eval_type=obj.get("eval_type", "cp"),
            board_position=obj.get("board_position"),
            is_white=obj.get("is_white"),
        )
    if isinstance(obj, (OverlayArrows, OverlayEval, OverlayClear)):
        return obj
    return obj
