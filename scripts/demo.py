#!/usr/bin/env python3
"""End-to-end CLI demo. Runs the whole loop without the frontend.

    python scripts/demo.py --topic "Ohm's Law" --minutes 5 --language hi
    python scripts/demo.py --file samples/physics.pdf --section "Chapter 4" --video

With LLM_PROVIDER=echo it runs offline, which is what CI uses.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents.controller import TeachingSession  # noqa: E402
from backend.agents.planner import build_profile, plan_lesson  # noqa: E402
from backend.ingestion import pipeline  # noqa: E402
from backend.services.video_service import VideoJob  # noqa: E402

BAR = "─" * 68


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="Ohm's Law")
    ap.add_argument("--file")
    ap.add_argument("--section")
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--level", default="beginner")
    ap.add_argument("--language", default="en")
    ap.add_argument("--video", action="store_true", help="also render the teaching video")
    ap.add_argument("--answers", nargs="*", default=["it increases", "it decreases"],
                    help="scripted student answers, used in order")
    args = ap.parse_args()

    doc_id = None
    if args.file:
        print(f"Indexing {args.file} …")
        meta = pipeline.ingest(args.file)
        doc_id = meta["doc_id"]
        print(f"  {meta['chunks']} chunks, language={meta['language']}, pages={meta.get('pages')}")

    request = (f"I am a {args.level}. Teach me {args.section or args.topic} in {args.minutes:g} minutes. "
               f"Explain it in {args.language} with simple examples. Ask me questions during the lesson "
               f"and test me at the end.")
    print(f"\n{BAR}\nSTUDENT REQUEST\n{request}\n{BAR}")

    profile = build_profile(request)
    print(f"PROFILE  level={profile['level']}  lang={profile['teaching_language']}  minutes={profile['minutes']}")

    plan = plan_lesson(profile, doc_id=doc_id, section=args.section)
    print(f"\nLESSON PLAN — {plan['title']}  [{plan['subject']}]")
    for c in plan["concepts"]:
        mark = "?" if c["checkpoint"] else " "
        print(f"  {c['order']+1}. {c['name']:<38} {c['seconds']:>5.0f}s  {c['depth']:<8} {mark}")
    if plan.get("gaps"):
        print(f"  gaps not covered by the material: {plan['gaps']}")

    session = TeachingSession(plan, profile, doc_id=doc_id)
    answers = list(args.answers)
    guard = 0

    while session.phase.value != "done" and guard < 60:
        guard += 1
        result = session.step()
        kind = result.get("type")

        if kind in {"explanation", "remediation"}:
            seg = result["segment"]
            tag = "RE-TEACH" if kind == "remediation" else "TEACH"
            print(f"\n[{tag}] {seg.get('board_title','')}  ({seg.get('strategy','')})")
            print(f"  {seg.get('narration','')[:320]}…")
            vis = seg.get("visual") or {}
            if vis:
                print(f"  visual: {vis.get('renderer')} — {vis.get('reason','')}")
            g = seg.get("grounding")
            if g:
                print(f"  grounding: {g['score']:.0%} supported, citations={g['citations']}")

        if result.get("question"):
            q = result["question"]
            print(f"\n[ASK] {q['prompt']}")
            for o in q.get("options", []):
                print(f"       {o['id']}) {o['text']}")
            reply = answers.pop(0) if answers else "I am not sure"
            print(f"[STUDENT] {reply}")
            fb = session.submit_answer(reply)
            r = fb["result"]
            print(f"[GRADE] {r['verdict']}  misconception={r.get('misconception')}  ({r.get('graded_by')})")
            print(f"        {r.get('feedback','')}")

        if kind == "assessment_question":
            reply = answers.pop(0) if answers else "not sure"
            print(f"\n[QUIZ] {result['question']['prompt']}\n[STUDENT] {reply}")
            session.submit_answer(reply)

        if kind == "report":
            rep = result["report"]
            print(f"\n{BAR}\nLEARNING REPORT — score {rep.get('score')}%")
            print(f"  {rep.get('headline','')}")
            for w in rep.get("weak_areas", []):
                print(f"  needs work: {w.get('concept')} — {w.get('fix')}")
            nxt = rep.get("next_topic") or {}
            print(f"  next: {nxt.get('name')} ({nxt.get('reason','')})")

    print(f"\n{BAR}\nWHY THE TEACHER DID WHAT IT DID")
    for d in session.decision_log:
        print(f"  [{d['phase']:<11}] {d['decision']:<34} ← {d['because']}")

    if args.video:
        print(f"\n{BAR}\nRendering video …")
        segments = [
            {"kind": t.kind, "concept_key": t.concept_key, "narration": t.payload.get("narration", ""),
             "board_title": t.payload.get("board_title", ""), "board_points": t.payload.get("board_points", []),
             "visual": t.payload.get("visual"), "citations": t.payload.get("citations", [])}
            for t in session.turns if t.kind in {"intro", "explanation", "remediation"}
        ]
        out = VideoJob("cli-demo").build(segments, lang=profile["teaching_language"], title=plan["title"])
        print(f"  {out['video_url']}  ({out['duration_s']}s, {out['segments']} segments)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
