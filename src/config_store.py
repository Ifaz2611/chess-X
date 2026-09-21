"""Single source of truth for Chess-X configuration.

Versioned schema + validation + migration. Used by GUI and tests.

Schema v2 (current):
  version: int = 2
  stockfish_path: str = ""
  website: str = "chesscom"  # chesscom | lichess
  enable_manual_mode: bool = False
  enable_mouseless_mode: bool = False
  enable_non_stop_puzzles: int = 0
  enable_non_stop_matches: int = 0
  enable_bongcloud: int = 0
  mouse_latency: float = 0.0
  slow_mover: int = 100
  skill_level: int = 20
  stockfish_depth: int = 15
  enable_topmost: int = 1

Migration from v1 (no version field, may contain memory/cpu_threads/stockfish):
  - drop memory/cpu_threads
  - rename stockfish -> stockfish_path
  - add version=2
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from utilities import get_logger

logger = get_logger("config_store")

CONFIG_VERSION = 2

DEFAULTS: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "stockfish_path": "",
    "website": "chesscom",
    "enable_manual_mode": False,
    "enable_mouseless_mode": False,
    "enable_non_stop_puzzles": 0,
    "enable_non_stop_matches": 0,
    "enable_bongcloud": 0,
    "mouse_latency": 0.0,
    "slow_mover": 100,
    "skill_level": 20,
    "stockfish_depth": 15,
    "enable_topmost": 1,
}

# Fields that are not part of current schema but were present in old configs
LEGACY_KEYS = {"memory", "cpu_threads", "stockfish"}


@dataclass
class Config:
    version: int = CONFIG_VERSION
    stockfish_path: str = ""
    website: str = "chesscom"
    enable_manual_mode: bool = False
    enable_mouseless_mode: bool = False
    enable_non_stop_puzzles: int = 0
    enable_non_stop_matches: int = 0
    enable_bongcloud: int = 0
    mouse_latency: float = 0.0
    slow_mover: int = 100
    skill_level: int = 20
    stockfish_depth: int = 15
    enable_topmost: int = 1

    @classmethod
    def defaults(cls) -> Config:
        return cls(**{k: v for k, v in DEFAULTS.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["version"] = CONFIG_VERSION
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Create Config from raw dict, applying migration + validation."""
        if not isinstance(data, dict):
            logger.warning("Config from_dict: expected dict, got %r", type(data))
            return cls.defaults()
        migrated = migrate(data)
        validated = validate(migrated)
        # keep only known fields
        kw = {k: validated[k] for k in cls.__dataclass_fields__ if k in validated}
        return cls(**kw)


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate old config dicts to current schema."""
    out = dict(data)  # shallow copy
    # v1 had no version
    if "version" not in out:
        logger.info("Migrating config from v1 (no version) to v%d", CONFIG_VERSION)
        out["version"] = 1
    ver = out.get("version", 1)
    # rename legacy stockfish -> stockfish_path
    if "stockfish" in out and not out.get("stockfish_path"):
        out["stockfish_path"] = out.pop("stockfish") or ""
    # drop legacy keys
    for k in list(out.keys()):
        if k in LEGACY_KEYS:
            out.pop(k, None)
    # ensure defaults for missing keys
    for k, v in DEFAULTS.items():
        if k not in out:
            out[k] = v
    out["version"] = CONFIG_VERSION
    if ver != CONFIG_VERSION:
        logger.info("Config migrated v%d -> v%d", ver, CONFIG_VERSION)
    return out


def validate(data: dict[str, Any]) -> dict[str, Any]:
    """Clamp / coerce config values, return new dict."""
    out = dict(DEFAULTS)
    out.update({k: v for k, v in data.items() if k in DEFAULTS})
    # stockfish_path: string, empty ok — clear if file vanished (matches legacy GUI behavior)
    try:
        sp = out.get("stockfish_path", "")
        out["stockfish_path"] = str(sp).strip() if sp else ""
        if out["stockfish_path"] and not os.path.exists(out["stockfish_path"]):
            logger.warning("Saved stockfish path not found on disk: %s", out["stockfish_path"])
            out["stockfish_path"] = ""
    except Exception:
        out["stockfish_path"] = ""
    # website: only chesscom or lichess
    try:
        w = str(out.get("website", "chesscom")).lower()
        out["website"] = "lichess" if w in ("lichess", "lichess.org") else "chesscom"
    except Exception:
        out["website"] = "chesscom"
    # booleans
    for k in ("enable_manual_mode", "enable_mouseless_mode"):
        try:
            out[k] = bool(out[k])
        except Exception:
            out[k] = bool(DEFAULTS[k])
    for k in ("enable_non_stop_puzzles", "enable_non_stop_matches", "enable_bongcloud", "enable_topmost"):
        try:
            out[k] = 1 if int(out[k]) else 0
        except Exception:
            out[k] = int(DEFAULTS[k])
    # numerics with clamp
    try:
        out["mouse_latency"] = max(0.0, min(15.0, float(out["mouse_latency"])))
    except Exception:
        out["mouse_latency"] = float(DEFAULTS["mouse_latency"])
    try:
        out["slow_mover"] = max(10, min(1000, int(out["slow_mover"])))
    except Exception:
        out["slow_mover"] = int(DEFAULTS["slow_mover"])
    try:
        out["skill_level"] = max(0, min(20, int(out["skill_level"])))
    except Exception:
        out["skill_level"] = int(DEFAULTS["skill_level"])
    try:
        out["stockfish_depth"] = max(1, min(20, int(out["stockfish_depth"])))
    except Exception:
        out["stockfish_depth"] = int(DEFAULTS["stockfish_depth"])
    out["version"] = CONFIG_VERSION
    return out


def candidate_paths(explicit: str | os.PathLike | None = None) -> list[Path]:
    if explicit is not None:
        return [Path(explicit)]
    return [
        Path("src/config.json"),
        Path("config.json"),
        Path.home() / ".chess-x.json",
    ]


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from first existing candidate (or explicit path). Returns defaults if missing."""
    paths = candidate_paths(path)
    for p in paths:
        try:
            if p.exists():
                with p.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    logger.warning("Config %s is not a dict, using defaults", p)
                    continue
                cfg = Config.from_dict(data)
                # validate stockfish_path quick check: if exists and looks invalid, warn but keep
                logger.info("Loaded config from %s (v%s)", p, cfg.version)
                return cfg
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("load_config %s failed: %s", p, e)
            continue
        except Exception as e:
            logger.debug("load_config %s unexpected: %s", p, e, exc_info=True)
            continue
    return Config.defaults()


def save_config(cfg: Config | dict[str, Any], path: str | os.PathLike | None = None) -> Path:
    """Persist config atomically (write temp + rename). Returns path written."""
    if isinstance(cfg, dict):
        cfg_obj = Config.from_dict(cfg)
    elif isinstance(cfg, Config):
        cfg_obj = cfg
    else:
        raise TypeError(f"save_config expects Config or dict, got {type(cfg)}")
    # validate before saving
    validated = validate(cfg_obj.to_dict())
    cfg_obj = Config.from_dict(validated)
    target = Path(path) if path is not None else Path("src/config.json")
    # ensure parent exists
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # atomic write
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(cfg_obj.to_dict(), fh, indent=2)
        os.replace(tmp_path, target)
        logger.debug("Saved config to %s", target)
    except Exception as e:
        logger.error("save_config failed for %s: %s", target, e, exc_info=True)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        # fallback non-atomic
        with target.open("w", encoding="utf-8") as fh:
            json.dump(cfg_obj.to_dict(), fh, indent=2)
    return target


def default_config_path() -> Path:
    return Path("src/config.json")
