# Solution document

Section 20 of the brief lists fifteen items the documentation must contain. This file
covers each one in order, and links out where the detail lives.

---

## 1. Problem statement

Digital learning platforms serve pre-recorded lectures or text chatbots. Neither adapts.
A recorded lecture cannot tell that you did not understand minute four; a chatbot answers
whatever you asked and has no plan. Both leave the learner to work out what they should be
learning, in what order, and whether they have actually understood it.

The task: build an AI teacher that takes uploaded material or a topic, plans a lesson,
delivers it as video with a speaking avatar, questions the learner mid-lesson, evaluates the
answers, identifies what went wrong, and changes its approach — in the learner's language,
inside their available time.

## 2. Solution overview

A pipeline with an explicit pedagogical controller at its centre.

Material is ingested and indexed. A planner produces an ordered lesson with per-concept time
allocations. A controller then walks the lesson, calling separate agents to explain,
visualise, question, grade and remediate. Every transition — continue, question, re-teach,
jump back, stop — is decided from a live mastery model, not by the language model.

The lesson is rendered to video: a subject-appropriate board, natural narration, an animated
presenter, and burned subtitles. Afterwards the learner gets a report, and their per-concept
mastery is written to long-term memory so the next lesson starts where this one ended.

**The design principle**: the LLM writes the words; the controller decides the teaching.
That is what makes the behaviour inspectable, testable, and different from a chatbot.

## 3. Key features

- Ingests PDF, DOCX, DOC, PPTX, PPT, EPUB, HTML, RTF, TXT, MD
- Teaches from a topic alone, with no uploaded material
- Dependency-ordered lesson plans with per-concept time budgets
- Bayesian Knowledge Tracing mastery model per concept
- Eight explanation strategies; a failed strategy is never reused
- Eleven misconception types, each with a distinct corrective move
- Nine subject-aware visual renderers with an inspectable choice trace
- Video generation: board, voice, avatar, subtitles, chapter markers
- 20+ teaching languages, switchable mid-lesson without losing progress
- Typed *or* spoken answers
- Post-generation grounding verification
- Long-term learner memory, auto notes and flashcards, multi-day learning paths
- Runs entirely offline with no API keys

## 4. System architecture

See [ARCHITECTURE.md](ARCHITECTURE.md). In brief:

```
ingestion → RAG → planner → controller ⇄ agents → media → API → React
                                ↑
                          mastery model
```

The controller is the only component that decides what happens next. Agents are stateless
and single-purpose. Media generation is a background job. Everything external is behind an
adapter with an offline fallback.

## 5. AI/ML models used

| Role | Default | Alternatives |
|---|---|---|
| Reasoning and language | Claude (Anthropic) | OpenAI, Ollama (local), offline stub |
| Embeddings | `intfloat/multilingual-e5-small` | OpenAI embeddings, hashed n-grams |
| Speech-to-text | `faster-whisper` (local) | OpenAI Whisper, Groq |
| Text-to-speech | edge-tts | ElevenLabs, gTTS, espeak-ng |
| Avatar | procedural (local) | D-ID, HeyGen, SadTalker |

The multilingual embedding model is a deliberate choice, not a default: it places Hindi and
English in one vector space, which is what allows a Hindi question to retrieve from an
English textbook.

## 6. RAG implementation

1. **Load** — per-format loaders emit blocks carrying a heading trail. Paragraphs opening
   with a heading are split, and unstyled paragraphs that look like headings are promoted,
   since most real documents carry no style metadata.
2. **Chunk** — sentence-packed to ~320 tokens with 60-token overlap. Chunks never cross a
   heading. Tables and code are atomic. Token estimation is script-aware.
3. **Index** — dense vectors plus BM25, persisted per document.
4. **Retrieve** — reciprocal rank fusion of both rankings, optionally scoped to a heading.
5. **Verify** — generated narration is checked sentence-by-sentence against the retrieved
   chunks; unsupported sentences are reported to the UI.

Hybrid retrieval is used because textbook questions are full of exact tokens embeddings blur
— "Ohm's Law", "Article 21", "Figure 4.3". Step 5 exists because retrieval alone does not
prevent hallucination; the model can ignore its context.

## 7. Prompt and agent architecture

Eleven single-responsibility agents in `backend/core/prompts.py`: profiler, planner,
explainer, visual director, questioner, evaluator, remediator, assessor, reporter, path
builder, follow-up handler.

Each has its own output contract and is independently testable. A single "be a teacher"
prompt would be impossible to debug — when a lesson comes out badly you need to know whether
the plan, the explanation or the grading was at fault.

Shared prompt fragments (`GROUNDING_RULE`, `LANGUAGE_RULE`) are composed in, so citation and
language policy cannot drift between agents. All JSON output passes through tolerant
extraction with a one-shot repair loop, then structural normalisation that repairs what the
model got wrong rather than failing the lesson.

## 8. Personalisation approach

Three layers:

- **Stated** — level, language, time, goal, style, parsed from free text by the profiler.
- **Observed** — the mastery model updates from every answer; difficulty tracks running
  accuracy; explanation strategy adapts to what has already failed.
- **Remembered** — `ConceptRecord` persists mastery across sessions, blended 35% history /
  65% new. The planner seeds new lessons from it and pre-loads known misconceptions.

Level changes real behaviour, not just vocabulary: BKT priors, the strategy ladder order,
and depth guidance all differ by level.

## 9. Assessment methodology

Checkpoints are placed where students usually go wrong, not at fixed intervals. Questions
must require application rather than recall, and must have a wrong answer a student holding
the named misconception would actually choose.

Grading runs rules first, model second. MCQ selection, numeric tolerance and non-answers are
handled deterministically — those are exactly the cases where a model error would corrupt
the mastery estimate. Everything else goes to the evaluator, which returns a verdict *and* a
misconception tag.

Final assessment is weighted towards weak concepts but always includes one the student
handled well. The report is evidence-weighted: concepts actually tested dominate the score.

## 10. Multilingual implementation

Teaching language is independent of source language, in both directions. Language switching
mid-lesson preserves the plan and every mastery estimate.

Technical terms keep their standard form with a first-use gloss (`प्रतिरोध (resistance)`),
because students are examined in the textbook's terminology. Hinglish is treated as genuine
code-mixing, not transliterated formal Hindi. Speaking rates are language-specific, since
Devanagari is slower per word than English at the same perceived pace. Voices are mapped per
language, and Whisper receives `hi` for Hinglish input because it transcribes code-mixed
speech better that way.

## 11. Voice implementation

`backend/media/tts.py`. Four providers behind one interface, with an automatic fallback
chain ending in a silent track of correct length so video assembly never fails.

edge-tts is the default: free, no key, good Indian-language voices, and it returns real word
boundaries — which is what makes properly timed subtitles possible. Providers without word
boundaries fall back to a length-weighted estimate.

Input is cleaned before synthesis: citation markers, markdown and bullet glyphs are stripped,
since a TTS engine will happily read them aloud.

## 12. Avatar and video generation approach

Per segment: render the board → synthesise narration → animate the presenter → composite →
concatenate.

The board is the primary surface and the avatar an inset, because the brief is explicit that
a talking head in front of text is not sufficient. Board layout reserves the presenter's
corner, and both read the same `avatar_scale` so they cannot drift apart.

The offline avatar is procedural but not static: mouth openness and head sway are driven by
the RMS envelope of the real narration. Distinct poses are drawn once into a bank of RGB
arrays and frames are piped straight to ffmpeg, which renders at 12–17× realtime.

Output: `lesson.mp4` (H.264 720p), `lesson.srt`, `chapters.json`.

## 13. APIs and third-party services

Disclosed here as required, and at runtime via `GET /api/config`.

**Required**: FastAPI, Uvicorn, Pydantic, SQLAlchemy, NumPy, Matplotlib, pypdf, python-docx,
python-pptx, BeautifulSoup, EbookLib, httpx, ffmpeg.
**Optional (system)**: LibreOffice for legacy `.doc`/`.ppt`, Tesseract/OCRmyPDF for scanned
PDFs, Noto/Indic fonts for non-Latin boards.
**Model providers**: Anthropic Claude, OpenAI, or Ollama.
**Embeddings**: sentence-transformers, or OpenAI.
**Speech**: faster-whisper, OpenAI Whisper, Groq; edge-tts, gTTS, espeak-ng, ElevenLabs.
**Avatar**: D-ID, HeyGen, SadTalker, or the built-in renderer.
**Frontend**: React, Vite.

No paid service is required to run or evaluate the project.

## 14. Setup instructions

See [SETUP.md](SETUP.md). Shortest path:

```bash
pip install -r requirements.txt
make demo-offline        # full adaptive lesson, no keys, no network
```

## 15. Deployment instructions

See [SETUP.md](SETUP.md#deploying) and [GITHUB.md](GITHUB.md).

`docker compose up --build` runs both services. The image bundles ffmpeg and Indic fonts.
Video rendering is CPU-bound, so allow at least 2 vCPU and mount `data/` on a real volume —
it holds the database, indexes and rendered video. For production, move rendering to a worker
queue; `VideoJob` is already a self-contained unit of work with its own status reporting.

## 16. Known limitations

See [LIMITATIONS.md](LIMITATIONS.md), which is deliberately specific about what does not work
and why.
