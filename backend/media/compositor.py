"""Assemble the lesson video.

Per segment: board frame (background) + avatar clip (picture-in-picture) +
narration audio + burned subtitles. Segments are then concatenated. The avatar
sits in the lower-right at 28% width, which keeps the board readable — the brief
explicitly says a talking head in front of text is not enough, so the board is
the primary surface and the avatar is the presenter.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import settings

log = logging.getLogger(__name__)


@dataclass
class Segment:
    index: int
    kind: str                    # intro | concept | question | remediation | recap
    concept_key: str | None
    narration: str
    board_path: Path
    audio_path: Path
    avatar_path: Path | None
    duration_s: float
    word_timings: list[dict] = field(default_factory=list)
    title: str = ""


def compose_segment(seg: Segment, out_path: Path) -> Path:
    """Board + avatar PiP + audio + subtitles for one segment."""
    v = settings.video
    out_path.parent.mkdir(parents=True, exist_ok=True)

    srt = out_path.with_suffix(".srt")
    write_srt(seg.word_timings, seg.narration, srt, offset=0.0)

    cmd = [v.ffmpeg_bin, "-y", "-loglevel", "error",
           "-loop", "1", "-framerate", str(v.fps), "-t", f"{seg.duration_s:.3f}", "-i", str(seg.board_path)]
    if seg.avatar_path and seg.avatar_path.exists():
        cmd += ["-i", str(seg.avatar_path)]
    cmd += ["-i", str(seg.audio_path)]

    pip_w = int(v.width * v.avatar_scale)
    margin = int(v.width * 0.025)
    # Captions occupy the bottom band, so the presenter sits above it.
    pip_bottom = margin + (int(v.height * 0.13) if v.burn_subtitles else 0)
    filters = [f"[0:v]scale={v.width}:{v.height},setsar=1[bg]"]

    if seg.avatar_path and seg.avatar_path.exists():
        filters.append(f"[1:v]scale={pip_w}:-1,setsar=1[av]")
        filters.append(f"[bg][av]overlay=W-w-{margin}:H-h-{pip_bottom}:shortest=0[comp]")
        last = "comp"
        audio_idx = 2
    else:
        last = "bg"
        audio_idx = 1

    if v.burn_subtitles and srt.exists():
        style = "FontSize=16,PrimaryColour=&H00F1F4F2,BackColour=&HA0161F2B,BorderStyle=4,MarginV=22,Outline=0"
        escaped = str(srt).replace("\\", "/").replace(":", r"\:")
        # original_size pins libass to the real frame size; without it margins
        # and font sizes are interpreted against a 384x288 script canvas.
        filters.append(
            f"[{last}]subtitles='{escaped}':original_size={v.width}x{v.height}:"
            f"force_style='{style}'[out]"
        )
        last = "out"

    cmd += ["-filter_complex", ";".join(filters),
            "-map", f"[{last}]", "-map", f"{audio_idx}:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", str(out_path)]

    _run(cmd)
    return out_path


def concat(segment_paths: list[Path], out_path: Path) -> Path:
    """Concatenate segments. Re-encodes because segment sources differ."""
    if not segment_paths:
        raise ValueError("no segments to concatenate")
    if len(segment_paths) == 1:
        segment_paths[0].replace(out_path)
        return out_path

    listing = out_path.parent / "segments.txt"
    listing.write_text("\n".join(f"file '{p.resolve()}'" for p in segment_paths), encoding="utf-8")
    _run([settings.video.ffmpeg_bin, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(listing), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(out_path)])
    listing.unlink(missing_ok=True)
    return out_path


def write_srt(word_timings: list[dict], narration: str, out_path: Path, *, offset: float = 0.0,
              max_chars: int = 52) -> Path:
    """Group word timings into readable caption lines.

    Captions are also the accessibility deliverable and the multilingual
    evidence — they show the lesson really was generated in the target language.
    """
    cues: list[tuple[float, float, str]] = []
    if word_timings:
        line, start = [], None
        for wt in word_timings:
            if start is None:
                start = wt["start"]
            line.append(wt["word"])
            text = " ".join(line)
            if len(text) >= max_chars or wt["word"].endswith((".", "?", "!", "।")):
                cues.append((start + offset, wt["end"] + offset, text))
                line, start = [], None
        if line and start is not None:
            cues.append((start + offset, word_timings[-1]["end"] + offset, " ".join(line)))
    elif narration:
        cues.append((offset, offset + 4.0, narration[:max_chars]))

    lines = []
    for i, (start, end, text) in enumerate(cues, start=1):
        lines += [str(i), f"{_ts(start)} --> {_ts(max(end, start + 0.6))}", text, ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_chapters(segments: list[Segment], out_path: Path) -> Path:
    """Chapter markers so a student can jump to a concept in the finished video."""
    chapters, t = [], 0.0
    for seg in segments:
        chapters.append({
            "index": seg.index, "kind": seg.kind, "concept_key": seg.concept_key,
            "title": seg.title or seg.kind, "start_s": round(t, 2),
            "end_s": round(t + seg.duration_s, 2),
        })
        t += seg.duration_s
    out_path.write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-800:]}")
