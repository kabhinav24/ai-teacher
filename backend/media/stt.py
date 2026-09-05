"""Speech-to-text, so a student can answer out loud instead of typing.

This matters more for this project than it looks. A Class 8 student answering a
physics question in Hindi types slowly and self-censors; speaking is how they'd
actually answer a teacher. The evaluator already grades on the idea rather than
the wording, so transcription noise costs very little.

Providers, in order of preference:
  faster_whisper  local, no key, good multilingual coverage including Indic
  openai          hosted Whisper
  groq            hosted Whisper, fast
  disabled        typing only
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.config import settings

log = logging.getLogger(__name__)

#: Whisper uses ISO-639-1. "hinglish" is code-mixed, and Whisper handles it far
#: better as Hindi than as English — it keeps the Devanagari-origin words rather
#: than transcribing them as English near-homophones.
WHISPER_LANG = {"hinglish": "hi"}


@dataclass
class Transcript:
    text: str
    language: str
    provider: str
    confidence: float | None = None


class STTUnavailable(RuntimeError):
    pass


def transcribe(audio_path: Path, *, lang: str = "en") -> Transcript:
    """Transcribe a student's spoken answer."""
    provider = getattr(settings, "stt", None)
    provider = (provider.provider if provider else "faster_whisper").lower()
    code = WHISPER_LANG.get(lang, lang.split("-")[0].lower())

    if provider == "disabled":
        raise STTUnavailable("Voice answers are turned off (STT_PROVIDER=disabled).")

    wav = _to_wav(audio_path)
    for attempt in (provider, "faster_whisper"):
        fn = {"faster_whisper": _faster_whisper, "openai": _openai, "groq": _groq}.get(attempt)
        if fn is None:
            continue
        try:
            result = fn(wav, code)
            if attempt != provider:
                log.warning("STT provider '%s' unavailable; used '%s'.", provider, attempt)
            return result
        except Exception as exc:  # noqa: BLE001 - walk the chain
            log.warning("STT via '%s' failed: %s", attempt, exc)

    raise STTUnavailable(
        "No speech-to-text backend is available. Install faster-whisper "
        "(`pip install faster-whisper`) or set STT_PROVIDER=openai with an API key."
    )


def _faster_whisper(wav: Path, lang: str) -> Transcript:

    model_name = settings.stt.model or "small"
    model = _cached_model(model_name)
    segments, info = model.transcribe(str(wav), language=lang or None, vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    return Transcript(
        text=text,
        language=getattr(info, "language", lang),
        provider="faster_whisper",
        confidence=getattr(info, "language_probability", None),
    )


_model_cache: dict[str, object] = {}


def _cached_model(name: str):
    """Whisper takes seconds to load, so keep it resident between answers."""
    if name not in _model_cache:
        from faster_whisper import WhisperModel

        _model_cache[name] = WhisperModel(name, device="auto", compute_type="int8")
    return _model_cache[name]


def _openai(wav: Path, lang: str) -> Transcript:
    from openai import OpenAI

    client = OpenAI(api_key=settings.stt.api_key or None)
    with open(wav, "rb") as fh:
        resp = client.audio.transcriptions.create(
            model=settings.stt.model or "whisper-1", file=fh, language=lang or None
        )
    return Transcript(text=resp.text.strip(), language=lang, provider="openai")


def _groq(wav: Path, lang: str) -> Transcript:
    import httpx

    with open(wav, "rb") as fh:
        r = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.stt.api_key}"},
            files={"file": (wav.name, fh, "audio/wav")},
            data={"model": settings.stt.model or "whisper-large-v3-turbo", "language": lang},
            timeout=120,
        )
    r.raise_for_status()
    return Transcript(text=r.json().get("text", "").strip(), language=lang, provider="groq")


def _to_wav(src: Path) -> Path:
    """Browsers record WebM/Opus; Whisper wants 16 kHz mono PCM."""
    if src.suffix.lower() == ".wav":
        return src
    dst = src.with_suffix(".stt.wav")
    proc = subprocess.run(
        [settings.video.ffmpeg_bin, "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", str(dst)],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"could not decode the recording: {proc.stderr.decode(errors='ignore')[-300:]}")
    return dst
