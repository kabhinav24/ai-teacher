"""The teaching model that sits underneath the prompts.

A chatbot decides what to say next by looking at the last message. A teacher
decides by looking at what the student has and hasn't mastered, which
explanations have already failed, and how much time is left. This module holds
that state so the LLM is used for language, not for bookkeeping.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class Level(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class Phase(str, Enum):
    """The teaching loop: Understand -> Plan -> Explain -> Demonstrate ->
    Question -> Evaluate -> Adapt -> Continue."""

    INTRO = "intro"
    EXPLAIN = "explain"
    DEMONSTRATE = "demonstrate"
    CHECK = "check"
    REMEDIATE = "remediate"
    ASSESS = "assess"
    REPORT = "report"
    DONE = "done"


class Strategy(str, Enum):
    """Distinct ways to explain the same idea.

    Tracked per concept so a re-explanation is genuinely different rather than
    the same paragraph reworded — the most common failure of naive tutors.
    """

    DEFINITION = "formal_definition"
    ANALOGY = "everyday_analogy"
    WORKED_EXAMPLE = "worked_example"
    VISUAL = "visual_walkthrough"
    CONTRAST = "contrast_with_common_error"
    FIRST_PRINCIPLES = "build_from_first_principles"
    STORY = "narrative_or_history"
    PEER_LANGUAGE = "simplest_possible_language"


# Ordered fallbacks. If the formal definition failed, an analogy is a bigger
# jump than another definition; if the analogy failed too, go concrete.
STRATEGY_LADDER: dict[Level, list[Strategy]] = {
    Level.BEGINNER: [
        Strategy.ANALOGY,
        Strategy.VISUAL,
        Strategy.WORKED_EXAMPLE,
        Strategy.PEER_LANGUAGE,
        Strategy.CONTRAST,
        Strategy.STORY,
    ],
    Level.INTERMEDIATE: [
        Strategy.DEFINITION,
        Strategy.WORKED_EXAMPLE,
        Strategy.VISUAL,
        Strategy.CONTRAST,
        Strategy.ANALOGY,
        Strategy.FIRST_PRINCIPLES,
    ],
    Level.ADVANCED: [
        Strategy.FIRST_PRINCIPLES,
        Strategy.DEFINITION,
        Strategy.CONTRAST,
        Strategy.WORKED_EXAMPLE,
        Strategy.VISUAL,
        Strategy.ANALOGY,
    ],
}


@dataclass
class BKTParams:
    """Bayesian Knowledge Tracing.

    p_init   prior probability the student already knows the concept
    p_transit probability of learning it from one teaching step
    p_slip   probability of answering wrong despite knowing it
    p_guess  probability of answering right without knowing it
    """

    p_init: float = 0.25
    p_transit: float = 0.35
    p_slip: float = 0.12
    p_guess: float = 0.20


LEVEL_PRIORS = {
    Level.BEGINNER: BKTParams(p_init=0.12, p_transit=0.30),
    Level.INTERMEDIATE: BKTParams(p_init=0.30, p_transit=0.38),
    Level.ADVANCED: BKTParams(p_init=0.50, p_transit=0.45),
}

MASTERY_THRESHOLD = 0.80
STRUGGLE_THRESHOLD = 0.35


@dataclass
class ConceptState:
    """Live knowledge estimate for one concept."""

    key: str
    name: str
    p_known: float = 0.25
    attempts: int = 0
    correct: int = 0
    partial: int = 0
    consecutive_wrong: int = 0
    strategies_used: list[Strategy] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)
    time_spent_s: float = 0.0

    @property
    def mastered(self) -> bool:
        return self.p_known >= MASTERY_THRESHOLD

    @property
    def struggling(self) -> bool:
        return self.consecutive_wrong >= 2 or (self.attempts >= 2 and self.p_known < STRUGGLE_THRESHOLD)

    @property
    def accuracy(self) -> float:
        if not self.attempts:
            return 0.0
        return (self.correct + 0.5 * self.partial) / self.attempts


def observe(state: ConceptState, correct: bool, params: BKTParams, *, partial: bool = False) -> ConceptState:
    """Update the knowledge estimate from one graded response.

    Standard BKT posterior, then the learning transition. A partially correct
    answer is treated as evidence at half weight rather than as a binary, which
    stops one fuzzy answer from collapsing the estimate.
    """
    p = min(max(state.p_known, 1e-4), 1 - 1e-4)

    if correct:
        num = p * (1 - params.p_slip)
        den = num + (1 - p) * params.p_guess
    else:
        num = p * params.p_slip
        den = num + (1 - p) * (1 - params.p_guess)
    posterior = num / den if den else p

    if partial:
        posterior = (posterior + p) / 2  # halve the strength of the evidence

    state.p_known = posterior + (1 - posterior) * params.p_transit
    state.attempts += 1
    if correct:
        state.correct += 1
        state.consecutive_wrong = 0
    elif partial:
        state.partial += 1
        state.consecutive_wrong = 0
    else:
        state.consecutive_wrong += 1
    return state


def apply_teaching(state: ConceptState, params: BKTParams) -> ConceptState:
    """Explaining a concept raises the estimate even before any question."""
    state.p_known = state.p_known + (1 - state.p_known) * (params.p_transit * 0.5)
    return state


def next_strategy(state: ConceptState, level: Level) -> Strategy:
    """Pick an explanation approach that has not already failed for this student."""
    ladder = STRATEGY_LADDER[level]
    for strat in ladder:
        if strat not in state.strategies_used:
            return strat
    # Exhausted the ladder: fall back to the simplest possible language.
    return Strategy.PEER_LANGUAGE


# --------------------------------------------------------------------------
# Misconception handling
# --------------------------------------------------------------------------

@dataclass
class Remediation:
    label: str
    instruction: str


#: Cross-subject misconception patterns. The evaluator classifies a wrong answer
#: into one of these buckets; each maps to a *different* corrective move, so the
#: teacher addresses the cause rather than repeating the answer louder.
MISCONCEPTION_PLAYBOOK: dict[str, Remediation] = {
    "inverse_relationship": Remediation(
        "Direction of the relationship is flipped",
        "The student has the variables related in the wrong direction. Hold one quantity "
        "fixed, walk the other to an extreme value, and let them see which way the result "
        "must move. Do not restate the rule until after the extreme case.",
    ),
    "formula_misapplied": Remediation(
        "Right formula, wrong conditions",
        "The student knows the formula but not when it applies. Show one case where it "
        "holds and one nearly identical case where it does not, and name the condition "
        "that separates them.",
    ),
    "definition_confusion": Remediation(
        "Two terms are being conflated",
        "The student is merging two neighbouring terms. Define them side by side against "
        "a single shared example, changing only one at a time.",
    ),
    "causal_reversal": Remediation(
        "Cause and effect are swapped",
        "Lay out the sequence in time order and ask which event could occur first.",
    ),
    "overgeneralisation": Remediation(
        "A special case is being treated as the general rule",
        "Give a counter-example that breaks the rule, then restate the correct boundary.",
    ),
    "procedural_slip": Remediation(
        "Concept understood, execution slipped",
        "Do not re-teach the concept. Acknowledge the understanding, point to the exact "
        "step that went wrong, and offer a similar problem immediately.",
    ),
    "unit_or_scale_error": Remediation(
        "Units, scale or magnitude mishandled",
        "Redo the final step with units carried through every line.",
    ),
    "prerequisite_gap": Remediation(
        "An earlier concept is missing",
        "Stop teaching the current concept. Teach the missing prerequisite briefly, "
        "confirm it, then return.",
    ),
    "language_barrier": Remediation(
        "Concept may be understood, wording was not",
        "Re-ask the same question in simpler wording or in the student's stronger "
        "language before concluding anything about their understanding.",
    ),
    "no_attempt": Remediation(
        "No usable answer given",
        "Do not grade this. Offer a hint and ask a smaller version of the question.",
    ),
    "unknown": Remediation(
        "Unclassified error",
        "Re-explain using a different representation and ask a simpler question.",
    ),
}


def remediation_for(tag: str) -> Remediation:
    return MISCONCEPTION_PLAYBOOK.get(tag, MISCONCEPTION_PLAYBOOK["unknown"])


# --------------------------------------------------------------------------
# Time budgeting
# --------------------------------------------------------------------------

#: Words of narration per minute, by language family. Devanagari narration is
#: slower per word than English at the same perceived pace.
SPEAKING_RATE_WPM = {"default": 140, "hi": 120, "bn": 120, "ta": 115, "te": 115, "mr": 122, "gu": 125, "kn": 115, "ml": 110, "pa": 125, "ur": 125}


def words_for_seconds(seconds: float, lang: str) -> int:
    wpm = SPEAKING_RATE_WPM.get(lang.split("-")[0].lower(), SPEAKING_RATE_WPM["default"])
    return max(20, int(seconds / 60.0 * wpm))


@dataclass
class TimePlan:
    """How a total time budget is split across the lesson."""

    total_seconds: int
    intro_s: int
    teaching_s: int
    checks_s: int
    assessment_s: int
    recap_s: int
    concept_count: int
    checkpoint_count: int
    mode: str  # micro | standard | deep | multiday


def budget(total_minutes: float) -> TimePlan:
    """Split a time budget the way a teacher would.

    Short sessions cut assessment and breadth before they cut explanation;
    long sessions add checks and a real assessment. Anything above a day
    becomes a study plan rather than a single lesson.
    """
    total_s = int(total_minutes * 60)

    if total_minutes <= 7:
        mode, shares, concepts, checks = "micro", (0.06, 0.74, 0.12, 0.0, 0.08), 2, 1
    elif total_minutes <= 30:
        mode, shares, concepts, checks = "standard", (0.07, 0.55, 0.15, 0.15, 0.08), 4, 3
    elif total_minutes <= 120:
        mode, shares, concepts, checks = "deep", (0.05, 0.52, 0.18, 0.18, 0.07), 7, 6
    else:
        mode, shares, concepts, checks = "multiday", (0.04, 0.56, 0.16, 0.18, 0.06), 12, 10

    # Scale concept count with time inside each band rather than jumping at edges.
    if mode == "standard":
        concepts = max(3, min(6, round(total_minutes / 6)))
        checks = max(2, concepts - 1)
    elif mode == "deep":
        concepts = max(5, min(10, round(total_minutes / 9)))
        checks = max(4, concepts)

    return TimePlan(
        total_seconds=total_s,
        intro_s=int(total_s * shares[0]),
        teaching_s=int(total_s * shares[1]),
        checks_s=int(total_s * shares[2]),
        assessment_s=int(total_s * shares[3]),
        recap_s=int(total_s * shares[4]),
        concept_count=concepts,
        checkpoint_count=checks,
        mode=mode,
    )


def difficulty_for(states: Iterable[ConceptState]) -> str:
    """Adjust question difficulty from recent performance."""
    states = list(states)
    if not states:
        return "medium"
    graded = [s for s in states if s.attempts]
    if not graded:
        return "medium"
    acc = sum(s.accuracy for s in graded) / len(graded)
    if acc >= 0.85:
        return "hard"
    if acc <= 0.45:
        return "easy"
    return "medium"


def overall_mastery(states: Iterable[ConceptState]) -> float:
    states = list(states)
    if not states:
        return 0.0
    return sum(s.p_known for s in states) / len(states)


def readiness_score(states: Iterable[ConceptState]) -> float:
    """0-100 summary used in the learning report.

    An evidence-weighted average: concepts the student was actually questioned
    on dominate the score, and concepts that were only explained contribute at
    a reduced weight. Without that weighting, a lesson that ran out of time to
    test every concept would report the student as having failed the ones it
    never asked about.
    """
    states = list(states)
    if not states:
        return 0.0
    total = weight_sum = 0.0
    for s in states:
        evidence = 1 - math.exp(-s.attempts / 2.0)   # 0 with no attempts, ~0.92 by 5
        weight = 0.25 + 0.75 * evidence              # never zero: teaching counts for something
        value = 100 * (0.65 * s.accuracy + 0.35 * s.p_known) if s.attempts else 100 * s.p_known
        total += value * weight
        weight_sum += weight
    return round(total / weight_sum, 1) if weight_sum else 0.0
