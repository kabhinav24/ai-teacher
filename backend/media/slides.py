"""Render visual specs to board frames (PNG).

Everything is drawn with matplotlib in a headless process so the same code path
works on a laptop, in Docker and in CI. The board styling is deliberate: a dark
slate board with chalk-toned text, because white slides with black text wash out
behind a picture-in-picture avatar.
"""
from __future__ import annotations

import logging
import re
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from backend.config import settings  # noqa: E402

log = logging.getLogger(__name__)

BOARD = "#16202B"
CHALK = "#F2F4F1"
MUTED = "#9BB0C0"
ACCENT = "#F2D648"
CORRECTION = "#E4694F"
VERIFIED = "#63C2A0"

# Devanagari and other Indic scripts need a font that has the glyphs; without
# this every Hindi lesson renders as tofu boxes.
FONT_CANDIDATES = [
    "Noto Sans Devanagari", "Nirmala UI", "Mangal", "Lohit Devanagari",
    "Noto Sans", "DejaVu Sans",
]


def _configure_fonts() -> None:
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = [f for f in FONT_CANDIDATES if f in available]
    if not chosen:
        chosen = ["DejaVu Sans"]
        log.warning("No Indic-capable font found. Install fonts-noto for non-Latin lessons.")
    plt.rcParams["font.family"] = chosen
    plt.rcParams["axes.unicode_minus"] = False


_configure_fonts()


def render(spec_bundle: dict, *, title: str, out_path: Path, footer: str = "",
           reserve_right: float = 0.0) -> Path:
    """Render one board frame. Never raises — a failed visual falls back to text.

    `reserve_right` keeps a fraction of the canvas free on the right so the
    presenter inset never lands on top of the diagram. The compositor passes
    the avatar's actual width, so the board and the video layout can't drift
    out of sync.
    """
    renderer = spec_bundle.get("renderer", "bullets")
    spec = spec_bundle.get("spec", {}) or {}

    w, h = settings.video.width / 100, settings.video.height / 100
    fig = plt.figure(figsize=(w, h), dpi=100, facecolor=BOARD)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BOARD)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    _draw_frame(ax, title, footer)
    body_w = max(0.34, 0.86 - max(0.0, reserve_right))
    body = fig.add_axes([0.07, 0.14, body_w, 0.68])
    body.set_facecolor(BOARD)

    try:
        _DISPATCH.get(renderer, _bullets)(body, spec)
    except Exception as exc:  # noqa: BLE001 - a broken visual must not kill the lesson
        log.warning("Renderer '%s' failed (%s); falling back to text.", renderer, exc)
        body.clear()
        body.set_facecolor(BOARD)
        _bullets(body, {"points": _fallback_points(spec)})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor=BOARD, dpi=100)
    plt.close(fig)
    return out_path


def _draw_frame(ax, title: str, footer: str) -> None:
    ax.add_patch(mpatches.Rectangle((0.04, 0.88), 0.92, 0.004, color=ACCENT, transform=ax.transAxes))
    ax.text(0.04, 0.905, textwrap.shorten(title or "", 78, placeholder="…"),
            color=CHALK, fontsize=25, fontweight="600", va="bottom", transform=ax.transAxes)
    if footer:
        ax.text(0.04, 0.035, footer, color=MUTED, fontsize=11, va="bottom", transform=ax.transAxes)


# ---------------------------------------------------------------- renderers

def _bullets(ax, spec: dict) -> None:
    points = spec.get("points") or []
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    y = 0.92
    for p in points[:6]:
        wrapped = textwrap.wrap(str(p), 62) or [""]
        ax.plot([0.015], [y - 0.012], marker="s", markersize=6, color=ACCENT)
        for j, line in enumerate(wrapped[:3]):
            ax.text(0.055, y - j * 0.075, line, color=CHALK, fontsize=19, va="top")
        y -= 0.075 * min(len(wrapped), 3) + 0.075


def _equation(ax, spec: dict) -> None:
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    lines = [str(x) for x in (spec.get("lines") or [])][:6]
    highlight = spec.get("highlight")
    n = max(len(lines), 1)
    for i, line in enumerate(lines):
        y = 0.88 - i * (0.78 / n)
        colour = ACCENT if highlight == i else CHALK
        ax.text(0.5, y, _to_mathtext(line), color=colour, fontsize=30 if highlight == i else 26,
                ha="center", va="center")
    if spec.get("caption"):
        ax.text(0.5, 0.03, str(spec["caption"]), color=MUTED, fontsize=14, ha="center")


def _plot(ax, spec: dict) -> None:
    ax.set_facecolor(BOARD)
    for s in ax.spines.values():
        s.set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=12)
    ax.grid(True, color="#2A3A49", linewidth=0.8)

    kind = spec.get("kind", "function")
    series = spec.get("series") or []

    if kind == "function" and spec.get("expression"):
        lo, hi = (spec.get("x_range") or [-10, 10])[:2]
        xs = np.linspace(float(lo), float(hi), 600)
        ys = _safe_eval(str(spec["expression"]), xs)
        ax.plot(xs, ys, color=ACCENT, linewidth=2.6)
    elif kind == "bar" and series:
        for i, s in enumerate(series):
            labels = [str(x) for x in s.get("x", [])]
            ax.bar(labels, [float(v) for v in s.get("y", [])],
                   color=[ACCENT, VERIFIED, CORRECTION][i % 3], label=s.get("label"))
    else:
        for i, s in enumerate(series):
            colour = [ACCENT, VERIFIED, CORRECTION, "#7FB2E5"][i % 4]
            style = "o" if kind == "scatter" else "-"
            ax.plot([float(v) for v in s.get("x", [])], [float(v) for v in s.get("y", [])],
                    style, color=colour, linewidth=2.4, markersize=7, label=s.get("label"))

    ax.set_xlabel(spec.get("x_label", ""), color=CHALK, fontsize=14)
    ax.set_ylabel(spec.get("y_label", ""), color=CHALK, fontsize=14)
    for a in spec.get("annotations", []) or []:
        try:
            ax.annotate(str(a.get("text", "")), (float(a["x"]), float(a["y"])),
                        color=CHALK, fontsize=12,
                        arrowprops={"arrowstyle": "->", "color": MUTED})
        except (KeyError, TypeError, ValueError):
            continue
    if any(s.get("label") for s in series):
        leg = ax.legend(facecolor=BOARD, edgecolor=MUTED, labelcolor=CHALK, fontsize=12)
        leg.get_frame().set_alpha(0.9)


def _diagram(ax, spec: dict) -> None:
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    aspect = _aspect(ax)
    raw = spec.get("nodes", [])[:12]
    nodes = {str(n.get("id")): (float(n.get("x", 0.5)), float(n.get("y", 0.5))) for n in raw}

    # Edges first so nodes paint over the line ends; shrink keeps the arrow
    # outside the node instead of striking through its label.
    for e in spec.get("edges", []) or []:
        a, b = nodes.get(str(e.get("from"))), nodes.get(str(e.get("to")))
        if not a or not b:
            continue
        ax.annotate("", xy=b, xytext=a,
                    arrowprops={"arrowstyle": "-|>", "color": MUTED, "linewidth": 2,
                                "shrinkA": 58, "shrinkB": 58})
        if e.get("label"):
            ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.055, str(e["label"]),
                    color=ACCENT, fontsize=15, ha="center", fontweight="600")

    for n in raw:
        x, y = float(n.get("x", 0.5)), float(n.get("y", 0.5))
        shape = n.get("shape", "box")
        # Round shapes have less usable width than boxes, so wrap tighter.
        label = "\n".join(textwrap.wrap(str(n.get("label", "")), 10 if shape in {"circle", "source"} else 15)[:3])
        if shape in {"circle", "source"}:
            r = 0.15 if shape == "circle" else 0.13
            ax.add_patch(mpatches.Ellipse((x, y), r / aspect * 2, r * 2,
                                          facecolor="#22303E" if shape == "circle" else BOARD,
                                          edgecolor=ACCENT, linewidth=2.2))
        else:
            ax.add_patch(mpatches.FancyBboxPatch((x - 0.105, y - 0.075), 0.21, 0.15,
                                                 boxstyle="round,pad=0.012",
                                                 facecolor="#22303E", edgecolor=ACCENT, linewidth=2))
        ax.text(x, y, label, color=CHALK, fontsize=15, ha="center", va="center")

    if spec.get("caption"):
        ax.text(0.5, 0.02, str(spec["caption"]), color=MUTED, fontsize=14, ha="center")


def _flow(ax, spec: dict) -> None:
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    steps = spec.get("steps", [])[:7]
    if not steps:
        return
    horizontal = len(steps) <= 4
    positions = {}
    for i, s in enumerate(steps):
        if horizontal:
            x, y = (i + 0.5) / len(steps), 0.5
        else:
            x, y = 0.5, 0.92 - i * (0.84 / max(len(steps) - 1, 1))
        positions[str(s.get("id", i))] = (x, y)
        colour = {"start": VERIFIED, "end": VERIFIED, "decision": ACCENT}.get(s.get("kind"), "#7FB2E5")
        label = "\n".join(textwrap.wrap(str(s.get("label", "")), 20 if horizontal else 44)[:3])
        wbox = 0.9 / len(steps) - 0.03 if horizontal else 0.52
        ax.add_patch(mpatches.FancyBboxPatch((x - wbox / 2, y - 0.062), wbox, 0.124,
                                             boxstyle="round,pad=0.012",
                                             facecolor="#22303E", edgecolor=colour, linewidth=2))
        ax.text(x, y, label, color=CHALK, fontsize=13, ha="center", va="center")

    for e in spec.get("edges", []) or []:
        a, b = positions.get(str(e.get("from"))), positions.get(str(e.get("to")))
        if not a or not b:
            continue
        ax.annotate("", xy=b, xytext=a, arrowprops={"arrowstyle": "-|>", "color": MUTED, "linewidth": 2,
                                                    "shrinkA": 34, "shrinkB": 34})
        if e.get("label"):
            ax.text((a[0] + b[0]) / 2 + 0.03, (a[1] + b[1]) / 2, str(e["label"]), color=ACCENT, fontsize=11)


def _timeline(ax, spec: dict) -> None:
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    events = spec.get("events", [])[:7]
    if not events:
        return
    ax.plot([0.06, 0.94], [0.5, 0.5], color=MUTED, linewidth=2.5)
    for i, e in enumerate(events):
        x = 0.06 + (i + 0.5) * (0.88 / len(events))
        ax.plot([x], [0.5], marker="o", markersize=13, color=ACCENT)
        above = i % 2 == 0
        y = 0.62 if above else 0.38
        ax.text(x, y, str(e.get("when", "")), color=ACCENT, fontsize=16, ha="center",
                va="bottom" if above else "top", fontweight="600")
        label = "\n".join(textwrap.wrap(str(e.get("label", "")), 20)[:3])
        ax.text(x, y + (0.07 if above else -0.07), label, color=CHALK, fontsize=12,
                ha="center", va="bottom" if above else "top")


def _table(ax, spec: dict) -> None:
    ax.axis("off")
    cols = [str(c) for c in (spec.get("columns") or [])]
    rows = [[ "\n".join(textwrap.wrap(str(c), 24)[:3]) for c in r] for r in (spec.get("rows") or [])][:7]
    if not cols or not rows:
        return
    rows = [r + [""] * (len(cols) - len(r)) for r in rows]
    table = ax.table(cellText=rows, colLabels=cols, loc="center", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(14)
    table.scale(1, 2.4)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor(MUTED)
        cell.set_linewidth(0.8)
        if row == 0:
            cell.set_facecolor("#22303E")
            cell.set_text_props(color=ACCENT, fontweight="600")
        else:
            cell.set_facecolor(BOARD)
            cell.set_text_props(color=CHALK)


def _code(ax, spec: dict) -> None:
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    lines = str(spec.get("code", "")).split("\n")[:16]
    highlight = set(spec.get("highlight_lines") or [])
    ax.add_patch(mpatches.FancyBboxPatch((0.0, 0.30), 1.0, 0.68, boxstyle="round,pad=0.01",
                                         facecolor="#111C26", edgecolor="#2A3A49"))
    for i, line in enumerate(lines):
        y = 0.94 - i * (0.62 / max(len(lines), 1))
        hot = (i + 1) in highlight
        if hot:
            ax.add_patch(mpatches.Rectangle((0.01, y - 0.016), 0.98, 0.034, facecolor="#2C3A26"))
        ax.text(0.02, y, f"{i+1:>2}", color=MUTED, fontsize=11, family="monospace", va="center")
        ax.text(0.06, y, line[:76], color=ACCENT if hot else CHALK, fontsize=13,
                family="monospace", va="center")
    if spec.get("output"):
        ax.text(0.0, 0.23, "Output", color=MUTED, fontsize=12)
        out = "\n".join(str(spec["output"]).split("\n")[:3])
        ax.text(0.0, 0.17, out, color=VERIFIED, fontsize=13, family="monospace", va="top")


def _map(ax, spec: dict) -> None:
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    regions = spec.get("regions", [])[:6]
    for i, r in enumerate(regions):
        col, row = i % 2, i // 2
        x, y = 0.06 + col * 0.48, 0.80 - row * 0.30
        ax.add_patch(mpatches.FancyBboxPatch((x, y - 0.11), 0.42, 0.20, boxstyle="round,pad=0.012",
                                             facecolor="#22303E", edgecolor=VERIFIED, linewidth=1.8))
        ax.text(x + 0.02, y + 0.045, str(r.get("name", ""))[:28], color=CHALK, fontsize=16, va="center")
        note = "\n".join(textwrap.wrap(str(r.get("note", "")), 34)[:2])
        ax.text(x + 0.02, y - 0.03, note, color=MUTED, fontsize=12, va="center")
    if spec.get("caption"):
        ax.text(0.5, 0.02, str(spec["caption"]), color=MUTED, fontsize=13, ha="center")


_DISPATCH = {
    "bullets": _bullets, "equation": _equation, "plot": _plot, "diagram": _diagram,
    "flow": _flow, "timeline": _timeline, "table": _table, "code": _code, "map": _map,
}


# ---------------------------------------------------------------- helpers

def _aspect(ax) -> float:
    """Width/height ratio of the axes in pixels, so 0-1 data coordinates can be
    corrected to draw visually round circles."""
    bbox = ax.get_window_extent()
    return (bbox.width / bbox.height) if bbox.height else 1.0


_MATH_SAFE = re.compile(r"^[\sA-Za-z0-9_+\-*/^=().,<>|\[\]]+$")


def _to_mathtext(line: str) -> str:
    """Wrap in mathtext only when it will parse; otherwise show it literally."""
    s = line.strip().strip("$")
    if not s or not _MATH_SAFE.match(s):
        return line
    s = s.replace("*", r"\times ")
    return f"${s}$"


def _safe_eval(expression: str, xs: np.ndarray) -> np.ndarray:
    """Evaluate a plot expression with no builtins in scope."""
    allowed = {
        "x": xs, "sin": np.sin, "cos": np.cos, "tan": np.tan, "exp": np.exp,
        "log": np.log, "log10": np.log10, "sqrt": np.sqrt, "abs": np.abs,
        "pi": np.pi, "e": np.e, "power": np.power, "maximum": np.maximum,
        "minimum": np.minimum, "where": np.where,
    }
    expr = expression.replace("^", "**")
    if not re.match(r"^[\sxA-Za-z0-9_+\-*/().,]+$", expr):
        raise ValueError(f"unsafe plot expression: {expression!r}")
    with np.errstate(divide="ignore", invalid="ignore"):
        result = eval(expr, {"__builtins__": {}}, allowed)  # noqa: S307 - whitelisted namespace
    return np.asarray(result, dtype=float) * np.ones_like(xs)


def _fallback_points(spec: dict) -> list[str]:
    for key in ("points", "lines", "steps", "events", "regions"):
        vals = spec.get(key)
        if isinstance(vals, list) and vals:
            return [str(v.get("label", v)) if isinstance(v, dict) else str(v) for v in vals][:5]
    return ["(visual unavailable)"]
