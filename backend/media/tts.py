"""Text-to-speech.

Voice selection is per-language, not global: the same lesson delivered in Hindi
and English must not use an English voice reading Devanagari. Edge TTS is the
default because its Indian-language voices are good and free, which matters for
a demo that has to run on a judge's laptop.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from backend.config import settings

log = logging.getLogger(__name__)

#: language code -> (edge voice, elevenlabs voice id env fallback)
VOICE_MAP = {
    "en": "en-IN-NeerjaNeural",
    "en-US": "en-US-AriaNeural",
    "en-GB": "en-GB-SoniaNeural",
    "hi": "hi-IN-SwaraNeural",
    "hinglish": "hi-IN-SwaraNeural",  # handles Latin-script Hindi acceptably
    "bn": "bn-IN-TanishaaNeural",
    "ta": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "mr": "mr-IN-AarohiNeural",
    "gu": "gu-IN-DhwaniNeural",
    "kn": "kn-IN-SapnaNeural",
    "ml": "ml-IN-SobhanaNeural",
    "pa": "pa-IN-OjasNeural",
    "ur": "ur-IN-GulNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "ar": "ar-EG-SalmaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "id": "id-ID-GadisNeural",
}


@dataclass
class Speech:
    audio_path: Path
    duration_s: float
    word_timings: list[dict]   # [{"word": str, "start": float, "end": float}]
    voice: str
    provider: str


def voice_for(lang: str) -> str:
    return VOICE_MAP.get(lang) or VOICE_MAP.get(lang.split("-")[0].lower()) or settings.tts.default_voice


def synthesise(text: str, *, lang: str, out_path: Path) -> Speech:
    """Speak `text`, returning audio plus word timings for subtitles."""
    text = clean_for_speech(text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provider = settings.tts.provider.lower()

    for attempt in (provider, "gtts", "espeak", "silent"):
        try:
            fn = {
                "edge": _edge, "elevenlabs": _elevenlabs, "gtts": _gtts,
                "espeak": _espeak, "silent": _silent,
            }.get(attempt)
            if fn is None:
                continue
            speech = fn(text, lang, out_path)
            if attempt != provider:
                log.warning("TTS provider '%s' unavailable; used '%s'.", provider, attempt)
            return speech
        except Exception as exc:  # noqa: BLE001 - fall through the chain
            log.warning("TTS via '%s' failed: %s", attempt, exc)
    raise RuntimeError("all TTS providers failed")


def clean_for_speech(text: str) -> str:
    """Strip anything a TTS engine would read aloud badly."""
    text = re.sub(r"\[c\d+\]", "", text)                    # citation markers
    text = re.sub(r"[*_#`>]+", "", text)                     # markdown
    text = re.sub(r"^\s*[-•●]\s*", "", text, flags=re.M)     # bullet glyphs
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------- providers

def _edge(text: str, lang: str, out_path: Path) -> Speech:
    import edge_tts

    voice = voice_for(lang)
    mp3 = out_path.with_suffix(".mp3")
    timings: list[dict] = []

    async def run() -> None:
        rate = f"{int((settings.tts.speed - 1) * 100):+d}%"
        comm = edge_tts.Communicate(text, voice, rate=rate)
        with open(mp3, "wb") as fh:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    timings.append({
                        "word": chunk["text"],
                        "start": round(start, 3),
                        "end": round(start + chunk["duration"] / 1e7, 3),
                    })

    asyncio.run(run())
    wav = _to_wav(mp3, out_path)
    return Speech(wav, _duration(wav), timings or _estimate_timings(text, _duration(wav)), voice, "edge")


def _elevenlabs(text: str, lang: str, out_path: Path) -> Speech:
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.tts.api_key)
    voice_id = settings.tts.default_voice
    mp3 = out_path.with_suffix(".mp3")
    audio = client.text_to_speech.convert(
        voice_id=voice_id, model_id="eleven_multilingual_v2", text=text
    )
    with open(mp3, "wb") as fh:
        for chunk in audio:
            fh.write(chunk)
    wav = _to_wav(mp3, out_path)
    dur = _duration(wav)
    return Speech(wav, dur, _estimate_timings(text, dur), voice_id, "elevenlabs")


def _gtts(text: str, lang: str, out_path: Path) -> Speech:
    from gtts import gTTS

    code = "hi" if lang == "hinglish" else lang.split("-")[0]
    mp3 = out_path.with_suffix(".mp3")
    gTTS(text=text, lang=code).save(str(mp3))
    wav = _to_wav(mp3, out_path)
    dur = _duration(wav)
    return Speech(wav, dur, _estimate_timings(text, dur), code, "gtts")


def _espeak(text: str, lang: str, out_path: Path) -> Speech:
    code = {"hinglish": "hi"}.get(lang, lang.split("-")[0])
    subprocess.run(
        ["espeak-ng", "-v", code, "-s", str(int(150 * settings.tts.speed)), "-w", str(out_path), text],
        check=True, capture_output=True,
    )
    dur = _duration(out_path)
    return Speech(out_path, dur, _estimate_timings(text, dur), code, "espeak")


def _silent(text: str, lang: str, out_path: Path) -> Speech:
    """Last resort: silence of the right length, so video assembly still works."""
    from backend.agents.pedagogy import SPEAKING_RATE_WPM

    wpm = SPEAKING_RATE_WPM.get(lang.split("-")[0], SPEAKING_RATE_WPM["default"])
    dur = max(2.0, len(text.split()) / wpm * 60)
    rate = 24000
    with wave.open(str(out_path), "w") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\x00\x00" * int(rate * dur))
    return Speech(out_path, dur, _estimate_timings(text, dur), "none", "silent")


# ---------------------------------------------------------------- helpers

def _to_wav(src: Path, dst: Path) -> Path:
    dst = dst.with_suffix(".wav")
    subprocess.run(
        [settings.video.ffmpeg_bin, "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "24000", "-ac", "1", str(dst)],
        check=True, capture_output=True,
    )
    src.unlink(missing_ok=True)
    return dst


def _duration(path: Path) -> float:
    out = subprocess.run(
        [settings.video.ffmpeg_bin.replace("ffmpeg", "ffprobe"), "-v", "error",
         "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _estimate_timings(text: str, duration: float) -> list[dict]:
    """Even distribution weighted by word length — good enough for subtitles
    when the provider gives no word boundaries."""
    words = text.split()
    if not words or duration <= 0:
        return []
    weights = [max(len(w), 2) for w in words]
    total = sum(weights)
    timings, t = [], 0.0
    for w, weight in zip(words, weights):
        span = duration * weight / total
        timings.append({"word": w, "start": round(t, 3), "end": round(t + span, 3)})
        t += span
    return timings
