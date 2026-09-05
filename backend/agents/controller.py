"""The teaching loop.

Understand -> Plan -> Explain -> Demonstrate -> Question -> Evaluate -> Adapt -> Continue

Every transition is a decision this class makes from the mastery model, not
something the LLM improvises. That means the behaviour is inspectable: given a
session's state you can say exactly why the teacher re-explained instead of
moving on, and `decision_log` records that reason for the UI and the report.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from backend.agents import assessment, teacher
from backend.agents.pedagogy import (
    LEVEL_PRIORS,
    ConceptState,
    Level,
    Phase,
    apply_teaching,
    budget,
    difficulty_for,
    observe,
    overall_mastery,
    readiness_score,
)
from backend.agents.visuals import choose_visual

log = logging.getLogger(__name__)

MAX_REMEDIATIONS_PER_CONCEPT = 2
MAX_RETRIES_PER_QUESTION = 1


@dataclass
class Turn:
    """One thing that happens in the lesson, in order."""

    index: int
    kind: str                 # intro | explanation | remediation | question | feedback | assessment | report
    concept_key: str | None
    payload: dict
    at: float = field(default_factory=time.time)


class TeachingSession:
    """Holds all lesson state. Serialisable so it survives a page reload."""

    def __init__(self, plan: dict, profile: dict, *, doc_id: str | None = None) -> None:
        self.plan = plan
        self.profile = profile
        self.doc_id = doc_id
        self.level = Level(profile.get("level", "beginner"))
        self.params = LEVEL_PRIORS[self.level]
        self.time_plan = budget(float(profile.get("minutes", 20)))

        self.states: dict[str, ConceptState] = {
            c["key"]: ConceptState(key=c["key"], name=c["name"], p_known=self.params.p_init)
            for c in plan["concepts"]
        }
        self.phase = Phase.INTRO
        self.cursor = 0                     # index into plan["concepts"]
        self.turns: list[Turn] = []
        self.decision_log: list[dict] = []
        self.pending_question: dict | None = None
        self.question_attempts = 0
        self.remediations: dict[str, int] = {}
        self.asked: dict[str, list[str]] = {}
        self.assessment_queue: list[dict] = []
        self.assessment_results: list[dict] = []
        self.elapsed_s = 0.0
        self.report: dict | None = None
        self.revisit_queue: list[str] = []   # jump back to these during the lesson
        self.flagged_for_revision: list[str] = []   # give up in-lesson, carry into the report

    # ------------------------------------------------------------- helpers
    @property
    def current_concept(self) -> dict | None:
        if 0 <= self.cursor < len(self.plan["concepts"]):
            return self.plan["concepts"][self.cursor]
        return None

    def _log(self, decision: str, because: str, **extra: Any) -> None:
        entry = {"turn": len(self.turns), "phase": self.phase.value, "decision": decision, "because": because, **extra}
        self.decision_log.append(entry)
        log.info("[%s] %s — %s", self.phase.value, decision, because)

    def _record(self, kind: str, concept_key: str | None, payload: dict) -> Turn:
        turn = Turn(index=len(self.turns), kind=kind, concept_key=concept_key, payload=payload)
        self.turns.append(turn)
        return turn

    def _time_left(self) -> float:
        return max(0.0, self.time_plan.total_seconds - self.elapsed_s)

    # --------------------------------------------------------------- steps
    def step(self) -> dict:
        """Advance the lesson to the next teacher action.

        Returns one of: explanation, question, remediation, assessment_question,
        report, done. The caller (API or CLI) renders it and, if it needs a
        student response, calls `submit_answer` before stepping again.
        """
        if self.pending_question:
            return {"type": "awaiting_answer", "question": _public_question(self.pending_question)}

        if self.phase is Phase.INTRO:
            return self._do_intro()
        if self.phase in (Phase.EXPLAIN, Phase.DEMONSTRATE):
            return self._do_explain()
        if self.phase is Phase.CHECK:
            return self._do_check()
        if self.phase is Phase.REMEDIATE:
            return self._do_remediate()
        if self.phase is Phase.ASSESS:
            return self._do_assess()
        if self.phase is Phase.REPORT:
            return self._do_report()
        return {"type": "done"}

    def _do_intro(self) -> dict:
        concepts = [c["name"] for c in self.plan["concepts"]]
        payload = {
            "title": self.plan["title"],
            "summary": self.plan.get("summary", ""),
            "roadmap": concepts,
            "minutes": self.profile.get("minutes"),
            "language": self.profile.get("language_label"),
            "narration": self._intro_narration(concepts),
            "board_title": self.plan["title"],
            "board_points": concepts[:6],
            "visual": {"renderer": "flow", "spec": {
                "steps": [{"id": f"s{i}", "label": n, "kind": "start" if i == 0 else "end" if i == len(concepts) - 1 else "process"}
                          for i, n in enumerate(concepts[:6])],
                "edges": [{"from": f"s{i}", "to": f"s{i+1}"} for i in range(min(len(concepts), 6) - 1)],
            }},
            "target_seconds": self.time_plan.intro_s,
        }
        self._log("open the lesson", f"{len(concepts)} concepts planned for {self.profile.get('minutes')} minutes")
        self.phase = Phase.EXPLAIN
        self._record("intro", None, payload)
        self.elapsed_s += self.time_plan.intro_s
        return {"type": "explanation", "segment": payload}

    def _do_explain(self) -> dict:
        concept = self.current_concept
        if concept is None:
            self.phase = Phase.ASSESS
            return self.step()

        state = self.states[concept["key"]]

        # Time pressure: if we are running out, compress what's left rather than
        # dropping the assessment. A lesson that never checks understanding is
        # the failure mode the brief calls out.
        remaining_concepts = len(self.plan["concepts"]) - self.cursor
        reserve = self.time_plan.assessment_s + self.time_plan.recap_s
        if self._time_left() < reserve and remaining_concepts > 1:
            self._log("compress remaining concepts", f"{self._time_left():.0f}s left, {remaining_concepts} concepts to go")
            for c in self.plan["concepts"][self.cursor:]:
                c["seconds"] = max(25.0, c["seconds"] * 0.55)
                if c.get("depth") == "master":
                    c["depth"] = "explain"

        previous = self.plan["concepts"][self.cursor - 1]["name"] if self.cursor else None
        segment = teacher.explain_concept(
            concept, profile=self.profile, state=state, doc_id=self.doc_id, previous_concept=previous
        )
        segment["visual"] = choose_visual(
            concept, segment.get("narration", ""), subject=self.plan.get("subject", "general"), profile=self.profile
        )
        apply_teaching(state, self.params)
        self.elapsed_s += float(concept.get("seconds", 60))
        self._record("explanation", concept["key"], segment)
        self._log("explain concept", f"strategy={segment['strategy']}, depth={concept.get('depth')}",
                  concept=concept["key"], mastery=round(state.p_known, 2))

        if concept.get("checkpoint") and self._time_left() > self.time_plan.assessment_s * 0.5:
            self.phase = Phase.CHECK
        else:
            self._advance()
        return {"type": "explanation", "segment": segment}

    def _do_check(self) -> dict:
        concept = self.current_concept
        state = self.states[concept["key"]]
        difficulty = difficulty_for(self.states.values())
        question = assessment.make_question(
            concept, profile=self.profile, difficulty=difficulty,
            seconds=max(20, int(self.time_plan.checks_s / max(self.time_plan.checkpoint_count, 1))),
            asked_before=self.asked.get(concept["key"], []),
        )
        self.pending_question = question
        self.question_attempts = 0
        self.asked.setdefault(concept["key"], []).append(question["prompt"])
        self._log("stop and ask", f"checkpoint on '{concept['name']}' at difficulty={difficulty}",
                  concept=concept["key"], mastery=round(state.p_known, 2))
        self._record("question", concept["key"], question)
        return {"type": "question", "question": _public_question(question), "concept": concept["name"]}

    def _do_remediate(self) -> dict:
        concept = self.current_concept
        state = self.states[concept["key"]]
        last_answer = next((t for t in reversed(self.turns) if t.kind == "feedback"), None)
        tag = (last_answer.payload.get("misconception_tag") if last_answer else None) or "unknown"

        segment = teacher.remediate(
            concept, profile=self.profile, state=state, misconception_tag=tag,
            student_answer=(last_answer.payload.get("answer", "") if last_answer else ""),
            question_prompt=(last_answer.payload.get("question", "") if last_answer else ""),
            doc_id=self.doc_id,
        )
        segment["visual"] = choose_visual(
            concept, segment.get("narration", ""), subject=self.plan.get("subject", "general"), profile=self.profile
        )
        self.remediations[concept["key"]] = self.remediations.get(concept["key"], 0) + 1
        apply_teaching(state, self.params)
        self.elapsed_s += 45
        self._record("remediation", concept["key"], segment)
        self._log("re-teach with a new approach",
                  f"misconception={tag}, strategy={segment.get('strategy')} "
                  f"(attempt {self.remediations[concept['key']]}/{MAX_REMEDIATIONS_PER_CONCEPT})",
                  concept=concept["key"])

        follow_up = segment.get("follow_up_question")
        if follow_up and self.remediations[concept["key"]] <= MAX_REMEDIATIONS_PER_CONCEPT:
            self.pending_question = {
                "type": "short_answer", "prompt": follow_up, "options": [],
                "correct_option": None, "expected_answer": segment.get("expected_answer", ""),
                "accepts": [], "targets_concept": concept["key"],
                "probes_misconception": tag, "hint": None, "difficulty": "easy",
            }
            self.question_attempts = 0
            self._record("question", concept["key"], self.pending_question)
            return {"type": "remediation", "segment": segment,
                    "question": _public_question(self.pending_question)}

        self._advance()
        return {"type": "remediation", "segment": segment}

    def _do_assess(self) -> dict:
        if not self.assessment_queue and not self.assessment_results:
            if self.time_plan.assessment_s < 30:
                self._log("skip formal assessment", "time budget too short; checkpoints already graded")
                self.phase = Phase.REPORT
                return self.step()
            count = 3 if self.time_plan.mode == "standard" else 5 if self.time_plan.mode == "deep" else 2
            self.assessment_queue = assessment.make_assessment(
                self.plan, self.states, profile=self.profile, count=count
            )
            self._log("start final assessment", f"{len(self.assessment_queue)} questions, weighted to weak concepts")

        if self.assessment_queue:
            question = self.assessment_queue.pop(0)
            self.pending_question = question
            self.question_attempts = MAX_RETRIES_PER_QUESTION  # no retries in the graded assessment
            self._record("assessment", question.get("targets_concept"), question)
            return {"type": "assessment_question", "question": _public_question(question),
                    "remaining": len(self.assessment_queue)}

        self.phase = Phase.REPORT
        return self.step()

    def _do_report(self) -> dict:
        score = readiness_score(self.states.values())
        transcript = [
            {"kind": "answer", "question": t.payload.get("question"), "answer": t.payload.get("answer"),
             "verdict": t.payload.get("verdict"), "misconception_tag": t.payload.get("misconception_tag")}
            for t in self.turns if t.kind == "feedback"
        ]
        self.report = assessment.build_report(
            self.plan, self.states, transcript, profile=self.profile, score=score
        )
        self.report["mastery"] = round(overall_mastery(self.states.values()), 3)
        self.report["decision_log"] = self.decision_log
        self.report["revisit"] = sorted(set(self.flagged_for_revision + self.revisit_queue))
        self.phase = Phase.DONE
        self._record("report", None, self.report)
        self._log("close the lesson", f"readiness={score}")
        return {"type": "report", "report": self.report}

    # ------------------------------------------------------------ answering
    def submit_answer(self, answer: str) -> dict:
        """Grade a response and decide what the teacher does next."""
        if not self.pending_question:
            return {"type": "error", "message": "No question is open."}

        question = self.pending_question
        concept_key = question.get("targets_concept") or (self.current_concept or {}).get("key")
        concept = next((c for c in self.plan["concepts"] if c["key"] == concept_key), self.current_concept or {})
        state = self.states.get(concept_key)

        result = assessment.grade(question, answer, profile=self.profile, concept=concept)
        verdict = result["verdict"]
        correct = verdict == "correct"
        partial = verdict == "partially_correct"

        is_assessment = self.phase is Phase.ASSESS
        if state and result.get("misconception_tag") != "no_attempt":
            observe(state, correct, self.params, partial=partial)
            tag = result.get("misconception_tag")
            if tag and tag not in state.misconceptions:
                state.misconceptions.append(tag)

        self._record("feedback", concept_key, {
            "question": question.get("prompt"), "answer": answer, **result,
        })

        if is_assessment:
            self.assessment_results.append({
                "question": question.get("prompt"), "answer": answer,
                "verdict": verdict, "misconception_tag": result.get("misconception_tag"),
            })
            self.pending_question = None
            self._log("record assessment answer", f"verdict={verdict}", concept=concept_key)
            return {"type": "feedback", "result": _public_feedback(result, question),
                    "mastery": self._mastery_snapshot(), "next": "assessment"}

        # --- the adaptive decision ----------------------------------------
        self.pending_question = None
        tag = result.get("misconception_tag")
        retries_left = self.question_attempts < MAX_RETRIES_PER_QUESTION
        remediated = self.remediations.get(concept_key, 0)

        if correct:
            if state and state.mastered:
                self._log("move on", f"mastery {state.p_known:.2f} above threshold", concept=concept_key)
            else:
                self._log("move on", "answered correctly", concept=concept_key)
            self._advance()

        elif tag == "no_attempt" and retries_left:
            self.question_attempts += 1
            self.pending_question = question
            self._log("offer a hint and re-ask", "student did not attempt", concept=concept_key)
            return {"type": "feedback", "result": _public_feedback(result, question),
                    "mastery": self._mastery_snapshot(),
                    "retry_question": _public_question(question)}

        elif tag == "language_barrier":
            self._log("re-ask in simpler wording", "answer suggests the question, not the concept, was the problem",
                      concept=concept_key)
            self.phase = Phase.REMEDIATE

        elif tag == "prerequisite_gap":
            prereqs = concept.get("prerequisites", [])
            if prereqs:
                self.revisit_queue.extend(
                    p for p in prereqs
                    if p not in self.revisit_queue and p not in self.flagged_for_revision
                )
                self._log("jump back to a prerequisite", f"gap detected in {prereqs}", concept=concept_key)
                self._jump_to(prereqs[0])
            else:
                self.phase = Phase.REMEDIATE

        elif tag == "procedural_slip" and partial:
            self._log("move on", "concept is understood; the slip was in execution", concept=concept_key)
            self._advance()

        elif remediated >= MAX_REMEDIATIONS_PER_CONCEPT:
            if state:
                state.misconceptions.append("unresolved")
            # Deliberately NOT revisit_queue: that queue is for in-lesson jumps
            # and would send us straight back into the concept we just gave up
            # on. This one goes to the report instead.
            self._flag(concept_key)
            self._log("move on and flag for revision",
                      f"already re-taught {remediated} times; parking it for the report", concept=concept_key)
            self._advance()

        elif self._time_left() < self.time_plan.assessment_s:
            self._flag(concept_key)
            self._log("move on and flag for revision", "not enough time left to re-teach properly",
                      concept=concept_key)
            self._advance()

        else:
            self._log("re-teach this concept", f"misconception={tag}, mastery={state.p_known:.2f}" if state else f"misconception={tag}",
                      concept=concept_key)
            self.phase = Phase.REMEDIATE

        return {"type": "feedback", "result": _public_feedback(result, question),
                "mastery": self._mastery_snapshot(), "next": self.phase.value}

    def ask_follow_up(self, question: str) -> dict:
        """Student interrupts. Answer without losing the lesson position."""
        recent = next((t.payload.get("narration", "") for t in reversed(self.turns)
                       if t.kind in {"explanation", "remediation"}), "")
        answer = teacher.answer_follow_up(
            question, profile=self.profile, plan=self.plan,
            current_concept_key=(self.current_concept or {}).get("key"),
            doc_id=self.doc_id, recent_narration=recent,
        )
        gap = answer.get("reveals_gap_in")
        if gap and gap in self.states:
            self._flag(gap)
            self.states[gap].p_known = min(self.states[gap].p_known, 0.45)
            self._log("note a gap from the student's question", f"question suggests '{gap}' is not solid", concept=gap)
        self._record("follow_up", (self.current_concept or {}).get("key"), {"question": question, **answer})
        return {"type": "follow_up", "answer": answer, "resume_phase": self.phase.value}

    def switch_language(self, lang: str, label: str | None = None) -> dict:
        """Change teaching language mid-lesson; the plan and state are untouched."""
        old = self.profile.get("teaching_language")
        self.profile["teaching_language"] = lang
        self.profile["language_label"] = label or lang
        self._log("switch teaching language", f"{old} -> {lang}; lesson plan and mastery state preserved")
        return {"type": "language_switched", "from": old, "to": lang,
                "resume_at": (self.current_concept or {}).get("name")}

    # ------------------------------------------------------------ internals
    def _advance(self) -> None:
        while self.revisit_queue:
            key = self.revisit_queue.pop(0)
            if key in self.flagged_for_revision:
                continue
            if self.states.get(key) and not self.states[key].mastered:
                self._jump_to(key)
                return
        self.cursor += 1
        self.phase = Phase.EXPLAIN if self.current_concept else Phase.ASSESS

    def _flag(self, concept_key: str | None) -> None:
        """Mark a concept as unresolved so it reaches the learning report."""
        if concept_key and concept_key not in self.flagged_for_revision:
            self.flagged_for_revision.append(concept_key)

    def _jump_to(self, concept_key: str) -> None:
        for i, c in enumerate(self.plan["concepts"]):
            if c["key"] == concept_key:
                self.cursor = i
                self.phase = Phase.EXPLAIN
                return
        self.phase = Phase.EXPLAIN

    def _mastery_snapshot(self) -> list[dict]:
        return [
            {"key": s.key, "name": s.name, "mastery": round(s.p_known, 3),
             "attempts": s.attempts, "struggling": s.struggling, "mastered": s.mastered}
            for s in self.states.values()
        ]

    def _intro_narration(self, concepts: list[str]) -> str:
        lang = self.profile.get("teaching_language", "en")
        joined = ", ".join(concepts[:4])
        minutes = self.profile.get("minutes")
        if lang == "hi":
            return (f"नमस्ते। आज हम {self.plan['title']} पढ़ेंगे, लगभग {minutes} मिनट में। "
                    f"हम {joined} को समझेंगे। बीच-बीच में मैं आपसे सवाल पूछूँगा, तो तैयार रहिए।")
        if lang == "hinglish":
            return (f"Chalo shuru karte hain. Aaj hum {self.plan['title']} padhenge, around {minutes} minutes mein. "
                    f"Hum {joined} cover karenge. Beech mein main aapse sawal bhi poochunga, so ready rehna.")
        return (f"Let's get started. Today we're covering {self.plan['title']}, in about {minutes} minutes. "
                f"We'll work through {joined}. I'll stop and ask you questions along the way, so stay with me.")

    # ---------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "plan": self.plan, "profile": self.profile, "doc_id": self.doc_id,
            "phase": self.phase.value, "cursor": self.cursor, "elapsed_s": self.elapsed_s,
            "states": {k: vars(s) | {"strategies_used": [x.value for x in s.strategies_used]}
                       for k, s in self.states.items()},
            "turns": [{"index": t.index, "kind": t.kind, "concept_key": t.concept_key, "payload": t.payload}
                      for t in self.turns],
            "decision_log": self.decision_log,
            "pending_question": self.pending_question,
            "question_attempts": self.question_attempts,
            "remediations": self.remediations, "asked": self.asked,
            "assessment_queue": self.assessment_queue, "assessment_results": self.assessment_results,
            "revisit_queue": self.revisit_queue,
            "flagged_for_revision": self.flagged_for_revision, "report": self.report,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeachingSession":
        from backend.agents.pedagogy import Strategy

        s = cls(data["plan"], data["profile"], doc_id=data.get("doc_id"))
        s.phase = Phase(data.get("phase", "intro"))
        s.cursor = data.get("cursor", 0)
        s.elapsed_s = data.get("elapsed_s", 0.0)
        for key, raw in (data.get("states") or {}).items():
            st = ConceptState(key=raw["key"], name=raw["name"])
            for field_name, value in raw.items():
                if field_name == "strategies_used":
                    st.strategies_used = [Strategy(v) for v in value]
                elif hasattr(st, field_name):
                    setattr(st, field_name, value)
            s.states[key] = st
        s.turns = [Turn(index=t["index"], kind=t["kind"], concept_key=t.get("concept_key"), payload=t["payload"])
                   for t in data.get("turns", [])]
        s.decision_log = data.get("decision_log", [])
        s.pending_question = data.get("pending_question")
        s.question_attempts = data.get("question_attempts", 0)
        s.remediations = data.get("remediations", {})
        s.asked = data.get("asked", {})
        s.assessment_queue = data.get("assessment_queue", [])
        s.assessment_results = data.get("assessment_results", [])
        s.revisit_queue = data.get("revisit_queue", [])
        s.flagged_for_revision = data.get("flagged_for_revision", [])
        s.report = data.get("report")
        return s


def _public_question(q: dict) -> dict:
    """Never send the answer key to the browser."""
    return {
        "type": q.get("type"), "prompt": q.get("prompt"), "options": q.get("options", []),
        "hint": q.get("hint"), "difficulty": q.get("difficulty"),
        "targets_concept": q.get("targets_concept"),
    }


def _public_feedback(result: dict, question: dict) -> dict:
    out = {
        "verdict": result.get("verdict"),
        "feedback": result.get("feedback"),
        "what_they_got_right": result.get("what_they_got_right"),
        "misconception": result.get("misconception_tag"),
        "graded_by": result.get("graded_by"),
    }
    if result.get("reveal_answer"):
        out["correct_answer"] = question.get("expected_answer")
    return out
