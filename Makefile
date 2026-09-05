.PHONY: install install-full api web test lint demo demo-offline video clean docker

# Core install: fast, and everything works via the fallback providers.
install:
	python -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm install

# Adds trained multilingual embeddings (pulls PyTorch, ~2GB).
install-full: install
	.venv/bin/pip install -r requirements-embeddings.txt

api:
	uvicorn backend.main:app --reload --port 8000

web:
	cd frontend && npm run dev

test:
	LLM_PROVIDER=echo EMBEDDING_PROVIDER=hashing TTS_PROVIDER=silent pytest tests/

lint:
	ruff check backend tests scripts

lint-fix:
	ruff check --fix backend tests scripts

# Full lesson in the terminal, no API keys, no network.
demo-offline:
	LLM_PROVIDER=echo EMBEDDING_PROVIDER=hashing python scripts/demo.py --minutes 5

# Real lesson with whatever providers your .env selects.
demo:
	python scripts/demo.py --topic "Ohm's Law" --minutes 5 --language hi

video:
	python scripts/demo.py --topic "Ohm's Law" --minutes 3 --video

clean:
	rm -rf data/media/* data/index/* __pycache__ .pytest_cache

docker:
	docker compose up --build
