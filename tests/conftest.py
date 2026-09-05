"""Force the offline providers so the suite needs no API key and no network."""
import os

os.environ.setdefault("LLM_PROVIDER", "echo")
os.environ.setdefault("EMBEDDING_PROVIDER", "hashing")
os.environ.setdefault("TTS_PROVIDER", "silent")
os.environ.setdefault("AVATAR_PROVIDER", "static")
