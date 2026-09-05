# Evaluation mapping

Where each requirement and each scored criterion is implemented, and how to verify it
yourself. Everything below runs offline with `LLM_PROVIDER=echo`.

## Mandatory requirements (§17)

| # | Requirement | Implementation | Verify |
|---|---|---|---|
| 1 | Learn from uploaded material | `backend/ingestion/`, `backend/rag/` | `POST /api/documents`, then `GET /api/documents/{id}/search?q=...` |
| 2 | Topic-based teaching | `agents/planner.py` — `doc_id` is optional throughout | `python scripts/demo.py --topic "Ohm's Law"` |
| 3 | AI-generated lesson structure | `planner.plan_lesson` + `_topological_order` | demo prints the ordered plan with per-concept seconds |
| 4 | Personalised teaching | `LEVEL_GUIDE`/`DEPTH_GUIDE` in `agents/teacher.py`; `LEVEL_PRIORS` in `pedagogy.py` | `--level beginner` vs `--level advanced` |
| 5 | Human-like teaching interaction | `agents/controller.py` phase machine | `make demo-offline` — see the decision log |
| 6 | Video presentation | `services/video_service.py`, `media/compositor.py` | `python scripts/demo.py --video` |
| 7 | AI voice | `media/tts.py` | produced `lesson.mp4` has an audio track |
| 8 | Human-like avatar | `media/avatar.py` | picture-in-picture presenter in the video |
| 9 | Multilingual | `tts.VOICE_MAP`, `LANGUAGE_RULE`, `controller.switch_language` | `--language hi`; `POST /api/lessons/{id}/language` |
| 10 | Questioning and assessment | `agents/assessment.py` | demo asks mid-lesson, then a final quiz |
| 11 | Adaptive response | `controller.submit_answer` | answer wrong → re-teach with a new strategy |
| 12 | Working prototype | `backend/main.py`, `frontend/` | `make api && make web` |

## Scored criteria (§19)

### Human-like teaching and adaptation — 20

The heaviest-weighted criterion, so it gets the most machinery.

- Full loop implemented as an explicit state machine, not prompt-improvised:
  `Understand → Plan → Explain → Demonstrate → Question → Evaluate → Adapt → Continue`
- Mastery tracked per concept with Bayesian Knowledge Tracing (`pedagogy.observe`)
- Eight explanation strategies on a level-ordered ladder; a strategy that already failed for
  this student is never reused (`pedagogy.next_strategy`)
- Eleven misconception types, each mapped to a *different* corrective move
  (`MISCONCEPTION_PLAYBOOK`)
- Prerequisite gaps cause a real jump backwards, not a canned apology
- The teacher gives up after two failed re-teaches and flags the concept for revision rather
  than looping
- Every decision is logged with its reason and surfaced in the UI

Verify: `make demo-offline` and read the trailing decision log.

### AI/ML and LLM implementation — 15

- Eleven single-responsibility agents, each with its own prompt and output contract
  (`core/prompts.py`)
- Provider-agnostic LLM adapter: Anthropic, OpenAI, Ollama, offline stub (`core/llm.py`)
- Tolerant JSON extraction with a one-shot repair loop that feeds bad output back
- Deterministic grading paths for MCQ, numeric and non-answers, so a model error can't
  corrupt the mastery estimate
- Structural repair of model output (`planner._normalise_plan`, `assessment._normalise_question`)

### RAG and knowledge grounding — 15

- Structure-aware chunking; chunks never cross a heading; tables and code are atomic
- Hybrid retrieval: BM25 + dense vectors fused by reciprocal rank
- Heading-scoped filtering, so "teach me Chapter 4" is a real constraint
- Multilingual embeddings, so a Hindi query retrieves from an English textbook
- **Grounding verified after generation** — every narration is checked sentence-by-sentence
  against its retrieved chunks, and unsupported sentences are reported to the UI and the
  video footer

Verify: `tests/test_rag.py`, and `GET /api/documents/{id}/search`.

### AI teaching video generation — 15

- Per-segment: rendered board → narration audio → animated avatar → composite → concatenate
- Board is the primary surface; the avatar is an inset, and board layout reserves its corner
- Subtitles from real TTS word boundaries; `chapters.json` for concept-level seeking
- Nine subject-aware renderers, chosen by a two-stage inspectable process

### Multilingual capability — 10

- 20+ languages mapped to voices, including 11 Indian languages
- Teaching language independent of source language, both directions
- Mid-lesson switching preserves plan and mastery
- Language-specific speaking rates; technical terms glossed rather than translated away
- Hinglish handled as genuine code-mixing, not transliterated formal Hindi

### Voice and AI avatar — 10

- Four TTS providers with automatic fallback down to a silent track of correct length
- Four avatar providers; the offline one is driven by the real audio RMS envelope

### Innovation and originality — 5

The parts I'd point at: grounding verification as a first-class output rather than an
assumption; the strategy ladder that makes re-explanation genuinely different; misconception
tags mapped to distinct corrective moves; the decision log that makes the pedagogy auditable;
and a system that runs completely offline.

### User experience and interface — 5

- Live mastery meters, colour-coded by state
- The decision log is shown to the student — the teacher explains its own reasoning
- Grounding percentage shown per segment
- Interrupt box that doesn't lose lesson position
- Keyboard focus styles, reduced-motion support, responsive to mobile

### Documentation and technical presentation — 5

`README.md`, `docs/ARCHITECTURE.md`, `docs/SETUP.md`, `docs/LIMITATIONS.md`, this file,
plus inline commentary explaining *why* rather than *what*.

## Task 1 and Task 2

**Task 1 — AI Teaching Video:** upload or topic → plan → adapt to level and time → narrate
with avatar and voice → subject-appropriate visuals → multilingual → `lesson.mp4`.

**Task 2 — Interactive and Adaptive:** mid-lesson questions, response evaluation, gap
identification, re-explanation on failure, difficulty adjustment from running accuracy,
follow-up questions with lesson context preserved, final assessment, personalised report
with next steps.

## Fastest way to evaluate this

```bash
pip install -r requirements.txt
make demo-offline      # full adaptive lesson + decision log, no keys
make test              # 47 tests
python scripts/demo.py --topic "Ohm's Law" --minutes 3 --video   # produces lesson.mp4
```
