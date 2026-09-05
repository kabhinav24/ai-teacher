"""Talking-head generation.

Three tiers, chosen by AVATAR_PROVIDER:
  did / heygen   hosted photoreal avatars — best quality, needs an API key
  sadtalker      local model, no key, needs a GPU to be quick
  static         procedural fallback: a portrait animated by the audio envelope

The `static` tier is not a placeholder image — it drives mouth openness and
head sway from the actual RMS envelope of the narration, so lip movement tracks
speech. It keeps the demo working offline when a key or GPU is unavailable.
"""
from __future__ import annotations

import logging
import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from backend.config import settings

log = logging.getLogger(__name__)


@dataclass
class AvatarClip:
    video_path: Path
    provider: str
    has_alpha: bool = False


def generate(audio_path: Path, *, out_path: Path, lang: str) -> AvatarClip:
    provider = settings.avatar.provider.lower()
    try:
        if provider == "did":
            return _did(audio_path, out_path)
        if provider == "heygen":
            return _heygen(audio_path, out_path)
        if provider == "sadtalker":
            return _sadtalker(audio_path, out_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Avatar provider '%s' failed (%s); using procedural avatar.", provider, exc)
    return _procedural(audio_path, out_path)


def _did(audio_path: Path, out_path: Path) -> AvatarClip:
    """D-ID talks API. Requires the audio to be reachable by URL, so this path
    assumes MEDIA_PUBLIC_BASE is set to a public/tunnelled media root."""
    import os
    import time

    import httpx

    base = os.getenv("MEDIA_PUBLIC_BASE", "").rstrip("/")
    if not base:
        raise RuntimeError("Set MEDIA_PUBLIC_BASE so D-ID can fetch the audio")
    rel = audio_path.relative_to(settings.media_dir)
    headers = {"Authorization": f"Basic {settings.avatar.api_key}", "Content-Type": "application/json"}
    payload = {
        "source_url": settings.avatar.portrait_path if settings.avatar.portrait_path.startswith("http")
        else os.getenv("AVATAR_PORTRAIT_URL", ""),
        "script": {"type": "audio", "audio_url": f"{base}/{rel.as_posix()}"},
        "config": {"stitch": True},
    }
    with httpx.Client(timeout=180) as client:
        created = client.post("https://api.d-id.com/talks", json=payload, headers=headers)
        created.raise_for_status()
        talk_id = created.json()["id"]
        for _ in range(90):
            time.sleep(2)
            status = client.get(f"https://api.d-id.com/talks/{talk_id}", headers=headers).json()
            if status.get("status") == "done":
                video = client.get(status["result_url"]).content
                out_path.write_bytes(video)
                return AvatarClip(out_path, "did")
            if status.get("status") == "error":
                raise RuntimeError(status.get("error"))
    raise TimeoutError("D-ID render timed out")


def _heygen(audio_path: Path, out_path: Path) -> AvatarClip:
    import os
    import time

    import httpx

    base = os.getenv("MEDIA_PUBLIC_BASE", "").rstrip("/")
    if not base:
        raise RuntimeError("Set MEDIA_PUBLIC_BASE so HeyGen can fetch the audio")
    rel = audio_path.relative_to(settings.media_dir)
    headers = {"X-Api-Key": settings.avatar.api_key, "Content-Type": "application/json"}
    payload = {
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": settings.avatar.presenter_id, "avatar_style": "normal"},
            "voice": {"type": "audio", "audio_url": f"{base}/{rel.as_posix()}"},
        }],
        "dimension": {"width": 720, "height": 720},
    }
    with httpx.Client(timeout=300) as client:
        r = client.post("https://api.heygen.com/v2/video/generate", json=payload, headers=headers)
        r.raise_for_status()
        vid = r.json()["data"]["video_id"]
        for _ in range(120):
            time.sleep(3)
            s = client.get(f"https://api.heygen.com/v1/video_status.get?video_id={vid}", headers=headers).json()
            data = s.get("data", {})
            if data.get("status") == "completed":
                out_path.write_bytes(client.get(data["video_url"]).content)
                return AvatarClip(out_path, "heygen")
            if data.get("status") == "failed":
                raise RuntimeError(data.get("error"))
    raise TimeoutError("HeyGen render timed out")


def _sadtalker(audio_path: Path, out_path: Path) -> AvatarClip:
    """Local lip-sync via a SadTalker checkout. Set SADTALKER_DIR."""
    import os

    root = os.getenv("SADTALKER_DIR")
    if not root:
        raise RuntimeError("Set SADTALKER_DIR to a SadTalker checkout")
    subprocess.run(
        ["python", "inference.py",
         "--driven_audio", str(audio_path),
         "--source_image", settings.avatar.portrait_path,
         "--result_dir", str(out_path.parent),
         "--still", "--preprocess", "full", "--enhancer", "gfpgan"],
        cwd=root, check=True, capture_output=True,
    )
    produced = sorted(out_path.parent.glob("**/*.mp4"), key=lambda p: p.stat().st_mtime)
    if not produced:
        raise RuntimeError("SadTalker produced no output")
    produced[-1].rename(out_path)
    return AvatarClip(out_path, "sadtalker")


#: Distinct mouth-openness steps in the pose bank. Twelve is past the point
#: where more is visible at inset size.
MOUTH_STEPS = 12
#: Pixel size the avatar is drawn at. It gets scaled down to roughly
#: `avatar_scale * video_width` in the composite, so rendering larger is waste.
POSE_PX = 384


def _procedural(audio_path: Path, out_path: Path) -> AvatarClip:
    """A stylised teacher whose mouth and head follow the audio envelope.

    Rendering every frame through matplotlib is far too slow — a five-minute
    lesson is over seven thousand frames. Instead the distinct poses are drawn
    once into a small bank of RGB arrays, and the video is assembled by piping
    raw frames straight into ffmpeg. Head sway becomes an integer pixel shift
    of the cached array rather than a re-draw.
    """
    fps = settings.video.fps
    envelope, duration = _audio_envelope(audio_path, fps)
    bank = _pose_bank()

    cmd = [
        settings.video.ffmpeg_bin, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{POSE_PX}x{POSE_PX}",
        "-framerate", str(fps), "-i", "-",
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    try:
        for i, level in enumerate(envelope):
            step = int(round(float(np.clip(level, 0.0, 1.0)) * (MOUTH_STEPS - 1)))
            blink = (i % (fps * 4)) < 2
            frame = bank[(step, blink)]
            sway_px = int(round(4 * math.sin(i / (fps * 0.8))))
            if sway_px:
                frame = np.roll(frame, sway_px, axis=1)
            proc.stdin.write(frame.tobytes())
    finally:
        # Closing stdin is what tells ffmpeg the stream is finished. Don't call
        # communicate() afterwards — it tries to flush an already-closed pipe.
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        proc.wait()
        err = proc.stderr.read() if proc.stderr else b""
        if proc.stderr:
            proc.stderr.close()

    if proc.returncode != 0:
        raise RuntimeError(f"avatar encode failed: {err.decode(errors='ignore')[-500:]}")
    return AvatarClip(out_path, "static")


def _pose_bank() -> dict[tuple[int, bool], np.ndarray]:
    """Draw every distinct pose once and keep it as an RGB array."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    # Background matches the board exactly so the inset reads as part of the
    # scene rather than a pasted-on rectangle.
    skin, hair, cloth, bg = "#E8B98C", "#2A2118", "#2F5D62", "#16202B"
    bank: dict[tuple[int, bool], np.ndarray] = {}
    inches = POSE_PX / 100

    for step in range(MOUTH_STEPS):
        level = step / (MOUTH_STEPS - 1)
        for blink in (False, True):
            fig = plt.figure(figsize=(inches, inches), dpi=100, facecolor=bg)
            ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
            ax.set_facecolor(bg)
            cx, cy = 0.5, 0.60

            ax.add_patch(mpatches.Circle((0.5, 0.52), 0.47, facecolor="#1B2733",
                                         edgecolor="#F2D648", linewidth=2.5))            # portrait ring
            ax.add_patch(mpatches.Ellipse((0.5, 0.13), 0.62, 0.34, facecolor=cloth))     # shoulders
            ax.add_patch(mpatches.Ellipse((cx, cy - 0.20), 0.13, 0.14, facecolor=skin))  # neck
            ax.add_patch(mpatches.Ellipse((cx, cy), 0.30, 0.36, facecolor=skin))         # head
            ax.add_patch(mpatches.Wedge((cx, cy + 0.06), 0.163, 8, 172, width=0.055, facecolor=hair))

            eye_h = 0.006 if blink else 0.022
            for dx in (-0.062, 0.062):
                ax.add_patch(mpatches.Ellipse((cx + dx, cy + 0.045), 0.042, eye_h, facecolor="#FFFFFF"))
                if not blink:
                    ax.add_patch(mpatches.Circle((cx + dx, cy + 0.045), 0.011, facecolor="#22303E"))
            ax.plot([cx - 0.012, cx, cx + 0.008], [cy + 0.02, cy - 0.03, cy - 0.035],
                    color="#C9945F", linewidth=2)

            mouth_h = 0.012 + 0.075 * level      # driven by the speech envelope
            mouth_w = 0.085 + 0.035 * level
            ax.add_patch(mpatches.Ellipse((cx, cy - 0.115), mouth_w, mouth_h, facecolor="#8C3B3B"))
            if mouth_h > 0.05:
                ax.add_patch(mpatches.Ellipse((cx, cy - 0.098), mouth_w * 0.75, mouth_h * 0.28,
                                              facecolor="#FFFFFF"))

            fig.canvas.draw()
            rgba = np.asarray(fig.canvas.buffer_rgba())
            bank[(step, blink)] = np.ascontiguousarray(rgba[:, :, :3])
            plt.close(fig)

    return bank


def _audio_envelope(audio_path: Path, fps: int) -> tuple[np.ndarray, float]:
    """Per-frame loudness in 0-1, smoothed so the mouth doesn't flicker."""
    try:
        with wave.open(str(audio_path)) as wf:
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001
        return np.zeros(fps * 3), 3.0

    duration = len(samples) / rate if rate else 0.0
    n_frames = max(1, int(round(duration * fps)))
    per = max(1, len(samples) // n_frames)

    # Pad to a whole number of frames, then reshape so the RMS is one vectorised
    # operation rather than a Python loop over every frame.
    padded = np.pad(samples[: n_frames * per], (0, max(0, n_frames * per - len(samples))))
    rms = np.sqrt(np.mean(np.square(padded.reshape(n_frames, per)), axis=1))

    peak = float(rms.max())
    if peak > 0:
        rms = rms / peak
    else:
        # Silent track (the offline TTS fallback): give the mouth a gentle
        # idle motion so the avatar doesn't freeze mid-frame.
        rms = 0.25 + 0.15 * np.sin(np.arange(n_frames) / 3.0)

    kernel = np.ones(3) / 3
    return np.convolve(rms, kernel, mode="same"), duration
