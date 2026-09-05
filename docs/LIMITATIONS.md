# Known limitations

Written plainly, because a submission that hides its edges is harder to trust than one that
names them.

## Ingestion

- **Scanned PDFs need OCR first.** Ingestion detects a missing text layer and says so rather
  than silently indexing nothing. `scripts/ocr.py` handles it.
- **Heading detection is heuristic.** It handles "Chapter 4", "4.3 Ohm's Law", Title Case and
  ALL CAPS, and Devanagari chapter markers. A textbook that signals structure only through
  font size in a PDF will produce a flatter outline.
- **Equations in PDFs extract as mangled text.** Retrieval still works on the surrounding
  prose; the lesson's own equations are generated rather than lifted from the page.
- **No image extraction.** Figures in the source are not pulled out and re-shown; visuals are
  generated from the concept instead.

## Teaching

- **Misconception classification is only as good as the LLM.** That is exactly why
  `no_attempt`, MCQ correctness and numeric answers are graded by rules first — the cases
  where a model error would corrupt the mastery estimate never reach the model.
- **BKT assumes concepts are independent.** They aren't. Prerequisite links are used for
  ordering and gap detection, but the probability update itself doesn't propagate across
  them.
- **BKT parameters are hand-set, not fitted.** Fitting them needs real learner data, which
  a hackathon build doesn't have.
- **Time budgets are estimates.** Duration is predicted from word counts and speaking rate,
  then compared against the real TTS output. A student who pauses to think will overrun.

## Against the brief specifically

Two things §9 of the brief asks for that this does not do:

- **No real images.** §9 lists "relevant images" among the video's components. Every visual
  here is *generated* — diagrams, plots, equations, timelines, code — and nothing sources or
  generates photographs. That was a deliberate trade (generated visuals are grounded in the
  concept and never licence-encumbered), but it is a gap against the wording.
- **No figure extraction from source material.** Diagrams already in an uploaded textbook
  are not pulled out and re-shown. The system draws its own instead.

Also worth naming: **learning paths are API-only**. `POST /api/paths` builds and schedules a
multi-day plan and it is covered by tests, but the React UI does not surface it yet — you can
only reach it through the API or `/docs`. Same for the progress dashboard
(`GET /students/{id}/progress`) and auto-generated notes and flashcards
(`GET /lessons/{id}/notes`): all implemented and working server-side, none in the UI.

**Teaching is turn-based, not real-time conversational.** §18 lists real-time conversation as
an advanced feature. Voice answers work, but the loop is still ask → answer → grade rather
than continuous duplex speech.

## Media

- **Rendering is minutes, not real time.** A 20-minute lesson takes several minutes to
  render on CPU. It's a background job with progress reporting, not a live stream.
- **The procedural avatar is stylised, not photoreal.** It's driven by the real audio
  envelope so lip movement tracks speech, but it is a fallback for running without a key or
  a GPU. Use D-ID or HeyGen for a photoreal presenter.
- **Hosted avatar providers need a public media URL.** They fetch the audio over HTTP, so
  localhost needs a tunnel.
- **Speech-to-text quality varies by language.** faster-whisper is strong on English and
  reasonable on Hindi, weaker on lower-resource Indian languages. The evaluator grades on
  the idea rather than the wording, which absorbs some transcription noise, but a badly
  mangled answer can still be misgraded — which is why typing is always available.
- **Devanagari conjuncts in matplotlib.** Complex ligatures need a font with proper shaping;
  the Docker image installs `fonts-indic` for this. Bare matplotlib does not do full Indic
  shaping, so some conjuncts render imperfectly on the board. Narration and subtitles are
  unaffected.

## Multilingual

- **Hinglish TTS uses a Hindi voice on Latin script.** Acceptable, not ideal — English words
  in the mix get a Hindi accent.
- **Low-resource languages degrade further.** The embedding model covers ~100 languages, but
  retrieval quality drops for languages thin in its training data.

## Scale

- **SQLite and in-process session state.** Fine for a demo and a single worker. Multiple
  workers need Postgres and a shared cache; session state is already fully serialised to the
  database, so the change is a swap rather than a rewrite.
- **No authentication.** Student IDs are unguarded. Do not deploy publicly as-is.
