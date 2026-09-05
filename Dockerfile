FROM python:3.12-slim

# ffmpeg composes the video; the Noto fonts are what stop Hindi, Tamil and
# Bengali boards rendering as empty boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-noto-core \
      fonts-noto-cjk \
      fonts-indic \
      espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1 AIT_DATA_DIR=/app/data
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
