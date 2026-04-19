from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def graph_name(obj: dict[str, Any]) -> str:
    return str(obj.get("displayName") or obj.get("name") or obj.get("title") or "Untitled")


def safe_filename(value: str, max_length: int = 120) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^\w\s.-]", "", ascii_value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_length].rstrip(" .") or "untitled"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def style_dict(style: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not style:
        return result
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, raw_value = part.split(":", 1)
        key = key.strip().lower()
        raw_value = raw_value.strip()
        if key:
            result[key] = raw_value
    return result


def style_string(parts: dict[str, str]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in parts.items())


def css_px(value: str | None) -> float | None:
    if not value:
        return None
    match = re.match(r"(-?\d+(?:\.\d+)?)px$", value.strip().lower())
    if not match:
        return None
    return float(match.group(1))

