"""Tolerant JSON extraction for LLM output."""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Pull the first complete JSON value out of a model reply.

    Handles: bare JSON, fenced JSON, and JSON preceded/followed by prose.
    """
    if not text or not text.strip():
        raise ValueError("empty model reply")

    candidates: list[str] = []
    fenced = _FENCE.findall(text)
    candidates.extend(f.strip() for f in fenced)
    candidates.append(text.strip())

    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
        sliced = _balanced_slice(cand)
        if sliced:
            try:
                return json.loads(sliced)
            except json.JSONDecodeError:
                continue
    raise ValueError("no parsable JSON found in model reply")


def _balanced_slice(text: str) -> str | None:
    """Return the first balanced {...} or [...] region, ignoring braces in strings."""
    start = None
    opener = closer = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start, opener = i, ch
            closer = "}" if ch == "{" else "]"
            break
    if start is None:
        return None

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def coerce_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def get_path(obj: Any, path: str, default: Any = None) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
