# Setup

## Requirements

- Python 3.11+
- Node 18+ (frontend only)
- ffmpeg on PATH
- Noto / Indic fonts for non-Latin lessons

```bash
# Debian / Ubuntu
sudo apt install ffmpeg fonts-noto-core fonts-indic espeak-ng
# macOS
brew install ffmpeg && brew install --cask font-noto-sans font-noto-sans-devanagari
```

Without the fonts, Hindi and Tamil boards render as empty boxes. The renderer logs a warning
when no Indic-capable font is found.

## Install

```bash
git clone <your-repo-url> ai-teacher && cd ai-teacher
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt      # core + pytest + ruff
pip install -r requirements-embeddings.txt   # optional: real multilingual RAG (~2GB, PyTorch)
cp .env.example .env          # add LLM_API_KEY
cd frontend && npm install && cd ..
```

## Run

```bash
make api    # backend on :8000, docs at /docs
make web    # frontend on :5173
```

Or `docker compose up --build`, which includes ffmpeg and fonts.

## Verify without any API key

```bash
make demo-offline
make test
```

## Scanned PDFs

If a PDF has no extractable text layer, ingestion says so rather than indexing an empty
document. Run OCR first:

```bash
sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-tam ocrmypdf
python scripts/ocr.py textbook.pdf --lang eng+hin
```

## Provider notes

**edge-tts** is the default voice: free, no key, good `hi-IN`, `ta-IN`, `bn-IN` voices, and
it returns real word boundaries so subtitles are properly timed.

**D-ID / HeyGen** fetch the narration audio over HTTP, so they need a publicly reachable
media root. Set `MEDIA_PUBLIC_BASE` to a tunnel (ngrok, cloudflared) or a deployed host.

**Ollama** for a fully local stack:

```bash
ollama pull qwen2.5:7b
LLM_PROVIDER=ollama LLM_MODEL=qwen2.5:7b LLM_BASE_URL=http://localhost:11434 make api
```

## Deploying

The backend is a standard ASGI app: `uvicorn backend.main:app --host 0.0.0.0 --port 8000`.
Video rendering is CPU-heavy, so give it at least 2 vCPU, and mount `data/` on a real
volume — it holds the SQLite database, the vector indexes and the rendered videos.

For anything beyond a demo, move video rendering onto a worker queue; `VideoJob` is already
a self-contained unit of work with its own status reporting.
