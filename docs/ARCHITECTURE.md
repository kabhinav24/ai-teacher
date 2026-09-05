# Architecture

## Why agents are split

Every stage is a separate LLM call with its own prompt and its own output contract:
profiler, planner, explainer, visual director, questioner, evaluator, remediator, assessor,
reporter, path builder, follow-up handler.

One "be a teacher" mega-prompt would be shorter to write and impossible to debug. When a
lesson comes out badly you need to know whether the plan was wrong, the explanation was
wrong, or the grading was wrong. Separate calls make each stage independently testable and
independently repairable, and let cheap stages use a cheaper model.

## The teaching loop

```
        ┌──────────────────────────────────────────────┐
        ▼                                              │
  INTRO ──► EXPLAIN ──► CHECK ──► [correct] ──────► ADVANCE
              ▲            │                            │
              │            └─[wrong]─► REMEDIATE ───────┤
              │                             │           │
              │                    [2 failures]         │
              │                             ▼           │
              └────[prerequisite gap]──  FLAG FOR ──────┤
                                          REVISION      │
                                                        ▼
                                     ASSESS ──► REPORT ──► DONE
```

Transitions are decided in `controller.submit_answer` from the mastery model, never by the
LLM. Each one appends to `decision_log` with a human-readable reason.

## Mastery model

Standard Bayesian Knowledge Tracing with level-dependent priors:

```
correct:    P(known|obs) = P·(1-slip) / (P·(1-slip) + (1-P)·guess)
incorrect:  P(known|obs) = P·slip     / (P·slip     + (1-P)·(1-guess))
then:       P(known)     = posterior + (1 - posterior)·transit
```

| Level | p_init | p_transit |
|---|---|---|
| Beginner | 0.12 | 0.30 |
| Intermediate | 0.30 | 0.38 |
| Advanced | 0.50 | 0.45 |

Mastery threshold 0.80; struggling below 0.35 or two consecutive wrong answers. Partially
correct answers are applied at half weight — one fuzzy answer shouldn't collapse the
estimate. Explaining a concept raises it too, at half the learning rate of a correct answer,
because teaching is weaker evidence than demonstration.

The final score is an *evidence-weighted* average: concepts actually questioned dominate,
concepts only explained contribute at reduced weight. Without that, a lesson that ran out of
time to test everything would report the student as having failed the untested concepts.

## Retrieval

1. **Load** — format-specific loaders emit blocks with a heading trail. Paragraphs whose
   first line is a heading are split, since textbooks rarely leave a blank line after
   "4.3 Ohm's Law".
2. **Chunk** — sentence-packed to ~320 tokens with 60-token overlap. Chunks never cross a
   heading. Tables and code blocks are atomic. Token estimation is script-aware, because
   Devanagari runs about twice as dense per character as Latin.
3. **Index** — dense vectors plus a BM25 index, persisted per document.
4. **Retrieve** — both rankings fused by reciprocal rank (`w/(60+rank_dense) +
   (1-w)/(60+rank_lex)`), optionally filtered to a heading scope.
5. **Verify** — generated narration checked sentence-by-sentence against retrieved chunks.

## Video pipeline

Per segment: render the board → synthesise narration → animate the avatar → composite →
concatenate. Board rendering reserves the presenter's corner so diagrams are never covered,
and the board and video layouts read the same `avatar_scale` so they can't drift apart.

Subtitles come from real TTS word boundaries when the provider supplies them (edge-tts
does) and from a length-weighted estimate otherwise. `original_size` is passed to libass —
without it, margins and font sizes are interpreted against a 384×288 script canvas rather
than the actual frame.

Rendering runs as a background job writing progress to a status file the frontend polls.

## Persistence

`ConceptRecord` carries mastery across sessions, blended 35% history / 65% new observation.
The planner seeds a new lesson from it, so the second lesson on a topic starts where the
first ended and known misconceptions are pre-loaded.

## Failure handling

Nothing in the media path is allowed to kill a lesson. A renderer that throws falls back to
text; a TTS provider that fails walks a chain down to a silent track of correct length; an
avatar provider that fails falls back to the procedural head; malformed LLM JSON is repaired
with one retry that feeds the bad output back for correction.
