"""Turn lesson segments into a finished teaching video.

Runs as a background job because a 20-minute lesson takes minutes to render.
Progress is written to a JSON status file the frontend polls, so the UI can show
real progress rather than a spinner.
"""
from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

from backend.config import settings
from backend.media import avatar as avatar_mod
from backend.media import compositor, slides, tts

log = logging.getLogger(__name__)


class VideoJob:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.root = settings.media_dir / session_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.status_path = self.root / "status.json"

    # ------------------------------------------------------------- status
    def set_status(self, state: str, *, progress: float = 0.0, message: str = "", **extra) -> None:
        payload = {"state": state, "progress": round(progress, 3), "message": message, **extra}
        self.status_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def get_status(self) -> dict:
        if not self.status_path.exists():
            return {"state": "not_started", "progress": 0.0, "message": ""}
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"state": "unknown", "progress": 0.0, "message": ""}

    # -------------------------------------------------------------- build
    def build(self, segments: list[dict], *, lang: str, title: str) -> dict:
        """segments: [{kind, concept_key, narration, board_title, board_points,
        visual, callout}]"""
        try:
            return self._build(segments, lang=lang, title=title)
        except Exception as exc:  # noqa: BLE001 - report failure to the UI, never crash the worker
            log.exception("Video build failed for %s", self.session_id)
            self.set_status("failed", message=str(exc), traceback=traceback.format_exc()[-1500:])
            raise

    def _build(self, segments: list[dict], *, lang: str, title: str) -> dict:
        total = max(len(segments), 1)
        built: list[compositor.Segment] = []
        clip_paths: list[Path] = []

        self.set_status("running", progress=0.02, message="Preparing lesson segments")

        for i, seg in enumerate(segments):
            share = i / total
            narration = (seg.get("narration") or "").strip()
            if not narration:
                continue

            # 1. board frame
            self.set_status("running", progress=share + 0.02, message=f"Drawing the board for segment {i+1} of {total}")
            board_path = self.root / f"board_{i:03d}.png"
            footer = " · ".join(str(x) for x in (seg.get("citations") or [])[:3])
            slides.render(
                seg.get("visual") or {"renderer": "bullets", "spec": {"points": seg.get("board_points", [])}},
                title=seg.get("board_title") or title,
                out_path=board_path,
                footer=f"source: {footer}" if footer else "",
                # Keep the presenter's corner clear of diagram content.
                reserve_right=settings.video.avatar_scale + 0.05,
            )

            # 2. narration audio
            self.set_status("running", progress=share + 0.05, message=f"Recording narration for segment {i+1}")
            speech = tts.synthesise(narration, lang=lang, out_path=self.root / f"audio_{i:03d}.wav")

            # 3. talking head
            self.set_status("running", progress=share + 0.10, message=f"Animating the teacher for segment {i+1}")
            clip = avatar_mod.generate(speech.audio_path, out_path=self.root / f"avatar_{i:03d}.mp4", lang=lang)

            # 4. composite
            self.set_status("running", progress=share + 0.14, message=f"Composing segment {i+1}")
            cseg = compositor.Segment(
                index=i, kind=seg.get("kind", "concept"), concept_key=seg.get("concept_key"),
                narration=narration, board_path=board_path, audio_path=speech.audio_path,
                avatar_path=clip.video_path, duration_s=speech.duration_s,
                word_timings=speech.word_timings, title=seg.get("board_title") or title,
            )
            out = compositor.compose_segment(cseg, self.root / f"segment_{i:03d}.mp4")
            built.append(cseg)
            clip_paths.append(out)

        if not clip_paths:
            raise RuntimeError("no segments produced any narration")

        self.set_status("running", progress=0.92, message="Stitching the lesson together")
        final = compositor.concat(clip_paths, self.root / "lesson.mp4")

        # Full-lesson subtitle track with cumulative offsets.
        cues: list[dict] = []
        offset = 0.0
        for seg in built:
            for wt in seg.word_timings:
                cues.append({"word": wt["word"], "start": wt["start"] + offset, "end": wt["end"] + offset})
            offset += seg.duration_s
        compositor.write_srt(cues, "", self.root / "lesson.srt")
        compositor.write_chapters(built, self.root / "chapters.json")

        duration = round(sum(s.duration_s for s in built), 2)
        result = {
            "video_url": f"/media/{self.session_id}/lesson.mp4",
            "subtitles_url": f"/media/{self.session_id}/lesson.srt",
            "chapters_url": f"/media/{self.session_id}/chapters.json",
            "duration_s": duration,
            "segments": len(built),
            "language": lang,
            "tts_provider": settings.tts.provider,
            "avatar_provider": settings.avatar.provider,
        }
        self.set_status("complete", progress=1.0, message="Lesson video ready", **result)
        _cleanup(self.root)
        return result


def _cleanup(root: Path) -> None:
    """Remove per-segment intermediates, keep the final artefacts."""
    for pattern in ("board_*.png", "audio_*.wav", "avatar_*.mp4", "segment_*.srt", "segment_*.mp4"):
        for f in root.glob(pattern):
            f.unlink(missing_ok=True)
