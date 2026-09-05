"""Central configuration.

Every external service is optional and selected by env var, so the project runs
end-to-end on a laptop with no paid API keys (using local/offline fallbacks) and
scales up to hosted providers for the judged demo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("AIT_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
MEDIA_DIR = DATA_DIR / "media"
INDEX_DIR = DATA_DIR / "index"

for _d in (DATA_DIR, UPLOAD_DIR, MEDIA_DIR, INDEX_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _flag(key: str, default: bool = False) -> bool:
    raw = _env(key)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass
class LLMConfig:
    # one of: anthropic | openai | ollama | echo
    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "anthropic"))
    model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-sonnet-4-6"))
    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY") or _env("ANTHROPIC_API_KEY") or _env("OPENAI_API_KEY"))
    base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    max_tokens: int = field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "4096")))
    temperature: float = field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.4")))


@dataclass
class EmbeddingConfig:
    # one of: sentence_transformers | openai | hashing
    provider: str = field(default_factory=lambda: _env("EMBEDDING_PROVIDER", "sentence_transformers"))
    model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "intfloat/multilingual-e5-small"))
    dim: int = field(default_factory=lambda: int(_env("EMBEDDING_DIM", "384")))
    api_key: str = field(default_factory=lambda: _env("EMBEDDING_API_KEY") or _env("OPENAI_API_KEY"))


@dataclass
class TTSConfig:
    # one of: edge | elevenlabs | gtts | espeak | silent
    provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "edge"))
    api_key: str = field(default_factory=lambda: _env("TTS_API_KEY") or _env("ELEVENLABS_API_KEY"))
    default_voice: str = field(default_factory=lambda: _env("TTS_DEFAULT_VOICE", "en-IN-NeerjaNeural"))
    speed: float = field(default_factory=lambda: float(_env("TTS_SPEED", "1.0")))


@dataclass
class STTConfig:
    # one of: faster_whisper | openai | groq | disabled
    provider: str = field(default_factory=lambda: _env("STT_PROVIDER", "faster_whisper"))
    model: str = field(default_factory=lambda: _env("STT_MODEL", "small"))
    api_key: str = field(default_factory=lambda: _env("STT_API_KEY") or _env("OPENAI_API_KEY"))


@dataclass
class AvatarConfig:
    # one of: did | heygen | sadtalker | static
    provider: str = field(default_factory=lambda: _env("AVATAR_PROVIDER", "static"))
    api_key: str = field(default_factory=lambda: _env("AVATAR_API_KEY"))
    portrait_path: str = field(default_factory=lambda: _env("AVATAR_PORTRAIT", str(BASE_DIR / "backend" / "media" / "assets" / "teacher.png")))
    presenter_id: str = field(default_factory=lambda: _env("AVATAR_PRESENTER_ID"))


@dataclass
class VideoConfig:
    width: int = field(default_factory=lambda: int(_env("VIDEO_WIDTH", "1280")))
    height: int = field(default_factory=lambda: int(_env("VIDEO_HEIGHT", "720")))
    fps: int = field(default_factory=lambda: int(_env("VIDEO_FPS", "24")))
    avatar_scale: float = field(default_factory=lambda: float(_env("VIDEO_AVATAR_SCALE", "0.20")))
    burn_subtitles: bool = field(default_factory=lambda: _flag("VIDEO_BURN_SUBTITLES", True))
    ffmpeg_bin: str = field(default_factory=lambda: _env("FFMPEG_BIN", "ffmpeg"))


@dataclass
class RAGConfig:
    chunk_tokens: int = field(default_factory=lambda: int(_env("RAG_CHUNK_TOKENS", "320")))
    chunk_overlap: int = field(default_factory=lambda: int(_env("RAG_CHUNK_OVERLAP", "60")))
    top_k: int = field(default_factory=lambda: int(_env("RAG_TOP_K", "8")))
    dense_weight: float = field(default_factory=lambda: float(_env("RAG_DENSE_WEIGHT", "0.65")))
    min_grounding_score: float = field(default_factory=lambda: float(_env("RAG_MIN_GROUNDING", "0.35")))


@dataclass
class Settings:
    app_name: str = "AI Teacher"
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{DATA_DIR / 'ai_teacher.db'}"))
    cors_origins: list[str] = field(default_factory=lambda: [o for o in _env("CORS_ORIGINS", "http://localhost:5173").split(",") if o])
    llm: LLMConfig = field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    avatar: AvatarConfig = field(default_factory=AvatarConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)

    upload_dir: Path = UPLOAD_DIR
    media_dir: Path = MEDIA_DIR
    index_dir: Path = INDEX_DIR
    max_upload_mb: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_MB", "80")))


settings = Settings()
