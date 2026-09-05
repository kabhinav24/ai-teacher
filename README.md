# AI Teacher

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

> Replace `OWNER/REPO` in the badge URLs above with your GitHub path after pushing.

An AI educator that reads your textbook, plans a lesson, teaches it as a video with a
speaking avatar, stops to question you, works out *why* a wrong answer was wrong, and
changes its approach based on what you got wrong.

Built for the Round 2 technical assessment, *AI Teacher: Build a Human-Like AI Educator
That Teaches Through Video*.

The whole thing runs offline with no API keys — see [Running with nothing](#running-with-nothing-installed).

---

## The one thing that makes this not a chatbot

A chatbot picks its next message by looking at your last message. This picks it by looking
at a model of what you know.

Every concept carries a live mastery estimate updated by [Bayesian Knowledge
Tracing](backend/agents/pedagogy.py). The [controller](backend/agents/controller.py) reads
that state and decides the next move — continue, stop and ask, re-teach differently, jump
back to a prerequisite, or give up and flag it for revision. The LLM writes the words; it
does not decide the pedagogy.

Because the decision is code, the system can always say why it did something:

```
[explain   ] explain concept              ← strategy=everyday_analogy, depth=explain
[check     ] stop and ask                 ← checkpoint on 'Ohm's Law' at difficulty=medium
[check     ] re-teach this concept        ← misconception=inverse_relationship, mastery=0.33
[remediate ] re-teach with a new approach ← strategy=visual_walkthrough (attempt 1/2)
[remediate ] re-teach with a new approach ← strategy=worked_example (attempt 2/2)
[check     ] move on and flag for revision← already re-taught 2 times; parking it for the report
```

That trace is real output from `make demo-offline`. It is also shown live in the UI, so a
judge can see the reasoning rather than take it on trust.

### Re-explanations are actually different

The commonest failure of an "adaptive" tutor is re-explaining by rewording. Each concept
tracks which of eight explanation strategies have already been tried, and the next attempt
is drawn from a ladder ordered by learner level:

| Level | Ladder |
|---|---|
| Beginner | analogy → visual → worked example → simplest language → contrast → story |
| Intermediate | definition → worked example → visual → contrast → analogy → first principles |
| Advanced | first principles → definition → contrast → worked example → visual → analogy |

A strategy that has already failed for you is never reused.

### Wrong answers are diagnosed, not just marked

The evaluator classifies every wrong answer into one of eleven misconception types, and
each maps to a *different* corrective move:

| Misconception | What the teacher does instead |
|---|---|
| `inverse_relationship` | Walk one variable to an extreme so the direction becomes obvious |
| `formula_misapplied` | Show a case where it holds and a near-identical one where it doesn't |
| `procedural_slip` | Don't re-teach — the concept is fine. Point at the step and move on |
| `prerequisite_gap` | Stop, jump back, teach the missing prerequisite, return |
| `language_barrier` | Re-ask in simpler wording before concluding anything about understanding |
| `no_attempt` | Don't grade it at all. Hint, and ask a smaller version |

`procedural_slip` and `no_attempt` matter more than they look. A student who understands
the idea but slipped in arithmetic should not sit through the concept again, and "I don't
know" is not evidence that they don't know — treating it as a wrong answer corrupts the
mastery estimate. Both are handled before any grading happens.

---

## What it does

| Requirement | Where it lives |
|---|---|
| Learn from uploaded material | `backend/ingestion/`, `backend/rag/` |
| Teach a topic with no material | `backend/agents/planner.py` |
| Generate a lesson structure | `backend/agents/planner.py` |
| Personalise to level, goal, style | `backend/agents/planner.py`, `backend/core/prompts.py` |
| Human-like teaching loop | `backend/agents/controller.py` |
| Teaching video | `backend/services/video_service.py`, `backend/media/` |
| AI voice | `backend/media/tts.py` |
| Talking avatar | `backend/media/avatar.py` |
| Multilingual, switchable mid-lesson | `tts.py` voice map, `controller.switch_language` |
| Questioning and assessment | `backend/agents/assessment.py` |
| Adaptive response to performance | `backend/agents/pedagogy.py`, `controller.py` |
| Working application | `backend/main.py`, `frontend/` |

Beyond the mandatory list: spoken answers via speech-to-text, long-term learner memory
across sessions, automatic notes and flashcards, multi-day study planner, learning paths,
grounding verification, and a decision log.

A per-item walkthrough of everything §20 of the brief asks documentation to cover is in
[docs/SOLUTION.md](docs/SOLUTION.md).

---

## Architecture

```
 Upload / topic
      │
      ▼
┌──────────────┐   loaders → structure-aware chunker → hybrid index
│  Ingestion   │   PDF DOCX PPTX EPUB HTML TXT
└──────┬───────┘
       ▼
┌──────────────┐   BM25 + dense vectors, fused by reciprocal rank
│     RAG      │   heading-scoped ("teach me Chapter 4" is a real filter)
└──────┬───────┘
       ▼
┌──────────────────────────────────────────────────────────────┐
│  Agents        profiler → planner → explainer → visual        │
│                director → questioner → evaluator → reporter   │
└──────┬───────────────────────────────────────────────────────┘
       ▼
┌──────────────┐   BKT mastery model + phase machine + time budget
│  Controller  │   decides: continue / ask / re-teach / jump back / stop
└──────┬───────┘
       ▼
┌──────────────┐   board (matplotlib) + TTS + avatar → ffmpeg
│    Media     │   → lesson.mp4 + lesson.srt + chapters.json
└──────┬───────┘
       ▼
   FastAPI  ←→  React
```

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Retrieval

Hybrid, because textbook questions are full of exact tokens that embeddings blur — "Ohm's
Law", "Article 21", "Figure 4.3", `O(n log n)`. BM25 catches those; dense vectors catch
paraphrase and cross-language. The two rankings are fused with reciprocal rank rather than
a tuned score threshold.

Chunks never span a heading boundary and carry their heading trail into the embedded text,
which is what makes chapter-scoped requests work:

```
Chapter 4: Electricity > 4.1 Electric Current
Chapter 4: Electricity > 4.2 Potential Difference
Chapter 4: Electricity > 4.3 Ohm's Law
Chapter 4: Electricity > 4.4 Resistors in Series and Parallel
```

### Grounding is verified, not assumed

Retrieval does not stop hallucination — the model can ignore the context it was handed.
So every generated narration is checked back against its retrieved chunks sentence by
sentence, and unsupported sentences are reported:

```
grounded text     score=1.00  passed=True   unsupported=0
invented text     score=0.00  passed=False  unsupported=1
```

Questions, asides and second-person address are excluded from the check — a teacher saying
"now look at the screen" is not a factual claim. The score is surfaced in the UI and burned
into the video footer as a citation, so grounding is visible instead of claimed.

### Subject-aware visuals

Two stages. A deterministic prior from the subject and the narration text produces a
shortlist; the LLM then chooses only from that shortlist. This stops every concept in every
subject collapsing into a bullet list, and the choice is inspectable:

```
mathematics  Quadratic Formula   → equation   signals: equation(+10)
physics      Ohm's Law           → diagram    signals: equation(+5), diagram(+5)
history      Indian Independence → timeline   signals: timeline(+7)
programming  Recursion           → code       signals: code(+5), flow(+5)
biology      Photosynthesis      → flow       signals: flow(+8)
```

`POST /api/debug/visual-choice` returns the full scoring trace for any concept. Renderers:
equation, plot, diagram, flow, timeline, table, code, map, bullets — bullets only when
nothing else fits.

### Video

Board first, presenter second. The brief says a talking head in front of generated text
isn't enough, so the board is the primary surface: a rendered visual per segment, with the
avatar as a picture-in-picture inset that the board layout explicitly reserves space for.

Output is `lesson.mp4` (H.264 720p), `lesson.srt`, and `chapters.json` for concept-level
seeking.

### Time budgeting

The requested duration reshapes the lesson rather than truncating it. Short sessions cut
breadth and assessment before they cut explanation; long ones add checkpoints and a real
assessment; anything over a couple of hours becomes a study plan instead of one sitting.

| Requested | Mode | Behaviour |
|---|---|---|
| ≤ 7 min | micro | 2 concepts, 1 check, no formal assessment, 74% of time on explanation |
| ≤ 30 min | standard | 3–6 concepts, checkpoints, short assessment |
| ≤ 120 min | deep | 5–10 concepts, more checks, full assessment |
| > 2 hr | multiday | scheduled learning path across days |

If the lesson runs long mid-session, remaining concepts are compressed rather than the
assessment dropped — a lesson that never checks understanding is the failure mode the brief
calls out.

### Multilingual

Teaching language is independent of source language, so an English textbook can be taught
in Hindi and vice versa. Switching mid-lesson preserves the plan and every mastery estimate
— only delivery changes. Technical terms keep their standard form with a gloss on first use
(`प्रतिरोध (resistance)`), because students are examined in the textbook's terminology.

Hinglish is treated as its own target: natural code-mixed speech in Latin script, not formal
Hindi transliterated. Voices are mapped per language; narration speaking rate is
language-specific, since Devanagari is slower per word than English at the same perceived pace.

---

## Running with nothing installed

Every external service has an offline fallback, so the full pipeline runs with no keys and
no network:

```bash
pip install -r requirements.txt
make demo-offline
```

Core install is small and quick. Trained multilingual embeddings are optional and live in
`requirements-embeddings.txt`, because they pull PyTorch — without them retrieval falls back
to hashed n-grams, which works but has no cross-lingual signal.

That plans a lesson, teaches it, asks a question, mis-answers it, re-teaches with a
different strategy, assesses, and prints the report and the decision log.

| Service | Default | Offline fallback |
|---|---|---|
| LLM | Anthropic | `echo` — deterministic schema-valid stubs |
| Embeddings | multilingual-e5-small | hashed character n-grams |
| TTS | edge-tts (free, no key) | gTTS → espeak → silent track |
| Avatar | D-ID / HeyGen | procedural talking head driven by the audio envelope |

The procedural avatar isn't a static image: mouth openness and head sway are driven by the
RMS envelope of the actual narration, so lip movement tracks speech without a GPU or a key.

## Running properly

```bash
cp .env.example .env        # add LLM_API_KEY
make install
make api                    # http://localhost:8000/docs
make web                    # http://localhost:5173
```

Or `docker compose up --build` — the image includes ffmpeg and the Noto/Indic fonts, without
which Hindi and Tamil boards render as empty boxes.

Setup detail, including OCR for scanned textbooks: [docs/SETUP.md](docs/SETUP.md).
Publishing and CI setup: [docs/GITHUB.md](docs/GITHUB.md).

## Tests

```bash
make test        # 47 tests, no network, no API key
```

They cover the parts where being wrong is invisible: that partial credit is weaker evidence
than a wrong answer, that a re-explanation never reuses a failed strategy, that a concept
the teacher gives up on reaches the report instead of looping, that chunks never span a
heading, that invented text fails the grounding check, and that the answer key never
appears in an API response.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | What it proves |
|---|---|
| **test** | 47 tests on Python 3.11 and 3.12, with coverage |
| **lint** | Ruff, advisory only — style never blocks a green pipeline |
| **pipeline** | Teaches a real lesson from `samples/`, renders the video, and `ffprobe`s the output to confirm it's genuinely h264 with a non-zero duration, subtitles and chapter markers |
| **frontend** | Vite production build |
| **docker** | Builds the image, boots it, polls `/health` and `/api/config`, then pushes to GHCR on `main` |

No secrets are required — CI runs entirely on the offline providers. The **pipeline** job
uploads the generated `lesson.mp4` as a downloadable artifact, so every commit produces a
watchable lesson.

`.github/workflows/demo.yml` is manual (`workflow_dispatch`): pick a topic, language, level
and duration, and it renders a real lesson with real TTS. Add an `LLM_API_KEY` repository
secret to use a live model; without one it falls back to the stub provider rather than
failing. Use this to produce your submission demo video.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/documents` | Upload and index material |
| `GET /api/documents/{id}/search` | Inspect retrieval directly |
| `POST /api/lessons` | Understand the request, plan the lesson |
| `POST /api/lessons/{id}/step` | Advance to the teacher's next action |
| `POST /api/lessons/{id}/answer` | Submit an answer; teacher grades and adapts |
| `POST /api/lessons/{id}/answer/voice` | Answer out loud; transcribed then graded identically |
| `POST /api/lessons/{id}/ask` | Interrupt without losing lesson position |
| `POST /api/lessons/{id}/language` | Switch teaching language mid-lesson |
| `POST /api/lessons/{id}/video` | Render the teaching video (background job) |
| `GET /api/students/{id}/progress` | Long-term progress and recurring misconceptions |
| `POST /api/paths` | Multi-day learning path |
| `POST /api/debug/visual-choice` | Why a renderer was chosen |
| `GET /api/config` | Which providers are actually live |

## Third-party services

Disclosed as the brief requires, and also at runtime via `GET /api/config`.

**Required:** FastAPI, SQLAlchemy, NumPy, Matplotlib, pypdf, python-docx, python-pptx,
BeautifulSoup, EbookLib, ffmpeg.
**Model providers (pick one):** Anthropic Claude, OpenAI, or Ollama for local models.
**Embeddings:** sentence-transformers `intfloat/multilingual-e5-small`, or OpenAI.
**Voice:** edge-tts (default, free), gTTS, espeak-ng, or ElevenLabs.
**Speech-to-text:** faster-whisper (default, local), OpenAI Whisper, or Groq.
**Avatar:** D-ID, HeyGen, SadTalker (local), or the built-in procedural renderer.

No paid service is required to run or evaluate the project.

## Known limitations

Written up honestly in [docs/LIMITATIONS.md](docs/LIMITATIONS.md). The short version:
scanned PDFs need an OCR pass first; hosted avatar providers need a publicly reachable media
URL; video rendering is minutes-per-lesson, not real time; and the misconception classifier
is only as good as the LLM behind it, which is why `procedural_slip` and `no_attempt` are
handled by rules instead.

## Licence

MIT — see [LICENSE](LICENSE).
