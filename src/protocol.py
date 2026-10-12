
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union

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
    eval_str: str = ""
    clock: str = ""
    is_blunder: bool = False

@dataclass(frozen=True)
class MsgMultiMove:
    """Bulk move list (e.g., initial sync)."""
    sans: list[str]
    kind: Literal["M_MOVE"] = "M_MOVE"
    evals: list[str] | None = None
    clocks: list[str] | None = None
    blunders: list[bool] | None = None

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
class MsgPV:
    """Principal-variation / threat line for the GUI (engine analysis)."""
    pv_text: str  # e.g. "1. Nf3 (+0.42)  2. c4 (#3)"
    threat_san: str = ""  # opponent threat after null move ("" = none/unknown)
    kind: Literal["PV"] = "PV"

@dataclass(frozen=True)
class MsgTelemetry:
    """Live engine and system telemetry."""
    nps: int = 0
    depth: int = 0
    time_per_move_ms: float = 0.0
    connection_health: str = "Connected"
    last_error: str = ""
    kind: Literal["TELEMETRY"] = "TELEMETRY"

@dataclass(frozen=True)
class MsgError:
    """Error / warning to show in GUI."""
    code: str  # e.g. "ERR_BOARD", "ERR_COLOR", "ERR_MOVES", "ERR_GAMEOVER", "ERR_TIMEOUT", "ERR_DISCONNECT", "ERR_STALE", "ERR_ENGINE", "ERR_EXE", "ERR_PERM"
    detail: str = ""
    kind: Literal["ERROR"] = "ERROR"

BotToGuiMessage = Union[MsgStart, MsgRestart, MsgSingleMove, MsgMultiMove, MsgEval, MsgPV, MsgTelemetry, MsgError]
GuiToBotMessage = Union[MsgDelete]
PipeMessage = Union[BotToGuiMessage, GuiToBotMessage, str]  # str for legacy fallback


# ---------- Queue: Bot -> Overlay ----------

@dataclass(frozen=True)
class OverlayArrows:
    """Arrow polygons for overlay: list of ((x1,y1),(x2,y2)). Empty list clears."""
    arrows: list[Any]  # List[Tuple[Tuple[int,int], Tuple[int,int]]]
    kind: Literal["ARROWS"] = "ARROWS"

@dataclass(frozen=True)
class OverlayEval:
    """Eval bar update."""
    eval_value: float  # centipawns / 100 or mate value
    eval_type: str     # "cp" or "mate"
    board_position: dict[str, Any] | None = None  # {x,y,width,height}
    is_white: bool | None = None
    kind: Literal["OVERLAY_EVAL"] = "OVERLAY_EVAL"

@dataclass(frozen=True)
class OverlayLines:
    """Extra PV arrows alongside the best-move arrow.

    ``lines``: list of {uci, san, score} dicts (see EngineService.top_moves_to_pv).
    The overlay resolves UCIs to pixels via the board position it already has;
    the bot therefore sends logical UCIs, not screen coords.
    """
    lines: list[dict[str, Any]]
    threat_uci: str = ""
    kind: Literal["LINES"] = "LINES"

@dataclass(frozen=True)
class OverlayConfig:
    """Config update for overlay process: opacity, side ('left'|'right'), visibility."""
    opacity: float | None = None
    side: str | None = None
    visible: bool | None = None
    kind: Literal["OVERLAY_CONFIG"] = "OVERLAY_CONFIG"

@dataclass(frozen=True)
class OverlayCalibration:
    """Config update for overlay calibration mode / test targets."""
    active: bool = True
    board_position: dict[str, Any] | None = None
    is_white: bool | None = None
    kind: Literal["OVERLAY_CALIBRATION"] = "OVERLAY_CALIBRATION"

@dataclass(frozen=True)
class OverlayClear:
    """Clear overlay arrows."""
    kind: Literal["CLEAR"] = "CLEAR"

OverlayMessage = Union[OverlayArrows, OverlayEval, OverlayLines, OverlayConfig, OverlayCalibration, OverlayClear, list[Any], dict[str, Any]]  # legacy list/dict fallback


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
    if isinstance(msg, MsgPV):
        return f"PV|{msg.pv_text}|{msg.threat_san}"
    if isinstance(msg, MsgTelemetry):
        return f"TELEMETRY|{msg.nps}|{msg.depth}|{msg.time_per_move_ms}|{msg.connection_health}|{msg.last_error}"
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
    if s.startswith("PV|"):
        parts = s.split("|")
        if len(parts) >= 2:
            return MsgPV(pv_text=parts[1], threat_san=parts[2] if len(parts) > 2 else "")
    if s.startswith("TELEMETRY|"):
        parts = s.split("|")
        if len(parts) >= 6:
            try:
                return MsgTelemetry(nps=int(parts[1]), depth=int(parts[2]), time_per_move_ms=float(parts[3]), connection_health=parts[4], last_error=parts[5])
            except Exception:
                pass
    if s.startswith("ERR_"):
        # ERR_CODE|detail or ERR_CODE
        if "|" in s:
            code, detail = s.split("|", 1)
            return MsgError(code=code, detail=detail)
        return MsgError(code=s)
    return s  # unknown, keep as str


def is_typed_pipe_msg(obj: Any) -> bool:
    return isinstance(obj, (MsgStart, MsgRestart, MsgSingleMove, MsgMultiMove, MsgEval, MsgPV, MsgError, MsgDelete))


def overlay_to_legacy(msg: OverlayMessage) -> Any:
    """Convert typed overlay msg to legacy form (list or dict) for queue."""
    if isinstance(msg, OverlayArrows):
        return msg.arrows
    if isinstance(msg, OverlayClear):
        return []
    if isinstance(msg, OverlayEval):
        d: dict[str, Any] = {"eval": msg.eval_value, "eval_type": msg.eval_type}
        if msg.board_position is not None:
            d["board_position"] = msg.board_position
        if msg.is_white is not None:
            d["is_white"] = msg.is_white
        return d
    if isinstance(msg, OverlayLines):
        return {"pv_lines": [dict(entry) for entry in msg.lines], "threat_uci": msg.threat_uci}
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
    if isinstance(obj, dict) and "pv_lines" in obj:
        return OverlayLines(lines=list(obj.get("pv_lines") or []),
                            threat_uci=obj.get("threat_uci") or "")
    if isinstance(obj, dict) and "calibration_active" in obj:
        return OverlayCalibration(
            active=bool(obj.get("calibration_active", True)),
            board_position=obj.get("board_position"),
            is_white=obj.get("is_white"),
        )
    if isinstance(obj, (OverlayArrows, OverlayEval, OverlayLines, OverlayConfig, OverlayCalibration, OverlayClear)):
        return obj
    return obj
