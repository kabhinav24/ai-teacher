"""The mastery model is the part that must be right — everything the teacher
decides is downstream of it."""
import pytest

from backend.agents.pedagogy import (
    BKTParams, ConceptState, Level, Strategy, budget, difficulty_for,
    next_strategy, observe, readiness_score, remediation_for, words_for_seconds,
)


def s(**kw):
    return ConceptState(key="k", name="Concept", **kw)


def test_correct_answer_raises_mastery():
    st = s(p_known=0.3)
    observe(st, True, BKTParams())
    assert st.p_known > 0.3
    assert st.correct == 1 and st.consecutive_wrong == 0


def test_wrong_answer_lowers_posterior_before_learning_gain():
    a, b = s(p_known=0.6), s(p_known=0.6)
    observe(a, True, BKTParams())
    observe(b, False, BKTParams())
    assert b.p_known < a.p_known


def test_partial_credit_is_weaker_evidence_than_a_wrong_answer():
    wrong, partial = s(p_known=0.5), s(p_known=0.5)
    observe(wrong, False, BKTParams())
    observe(partial, False, BKTParams(), partial=True)
    assert partial.p_known > wrong.p_known
    assert partial.consecutive_wrong == 0


def test_two_wrong_answers_marks_struggling():
    st = s(p_known=0.4)
    observe(st, False, BKTParams())
    observe(st, False, BKTParams())
    assert st.struggling


def test_repeated_correct_answers_reach_mastery():
    st = s(p_known=0.25)
    for _ in range(5):
        observe(st, True, BKTParams())
    assert st.mastered


def test_strategy_never_repeats_until_the_ladder_is_exhausted():
    st = s()
    seen = []
    for _ in range(6):
        strat = next_strategy(st, Level.BEGINNER)
        assert strat not in seen, "re-explaining with a strategy that already failed"
        seen.append(strat)
        st.strategies_used.append(strat)
    assert next_strategy(st, Level.BEGINNER) is Strategy.PEER_LANGUAGE


def test_beginner_and_advanced_get_different_first_explanations():
    assert next_strategy(s(), Level.BEGINNER) is not next_strategy(s(), Level.ADVANCED)


@pytest.mark.parametrize("minutes,mode", [(5, "micro"), (20, "standard"), (60, "deep"), (10080, "multiday")])
def test_time_budget_modes(minutes, mode):
    assert budget(minutes).mode == mode


def test_short_sessions_drop_assessment_before_explanation():
    micro, standard = budget(5), budget(20)
    assert micro.assessment_s == 0
    assert micro.teaching_s / micro.total_seconds > standard.teaching_s / standard.total_seconds


def test_budget_shares_stay_within_total():
    b = budget(20)
    assert b.intro_s + b.teaching_s + b.checks_s + b.assessment_s + b.recap_s <= b.total_seconds


def test_difficulty_follows_performance():
    strong = [s(attempts=4, correct=4)]
    weak = [s(attempts=4, correct=1)]
    assert difficulty_for(strong) == "hard"
    assert difficulty_for(weak) == "easy"
    assert difficulty_for([]) == "medium"


def test_unassessed_concepts_do_not_read_as_failures():
    tested = s(p_known=0.9, attempts=4, correct=4)
    untested = ConceptState(key="u", name="Untested", p_known=0.5)
    assert readiness_score([tested, untested]) > readiness_score([tested, s(p_known=0.2, attempts=4)])


def test_devanagari_speaks_slower_than_english():
    assert words_for_seconds(60, "hi") < words_for_seconds(60, "en")


def test_every_misconception_maps_to_a_distinct_instruction():
    tags = ["inverse_relationship", "procedural_slip", "prerequisite_gap", "no_attempt"]
    instructions = {remediation_for(t).instruction for t in tags}
    assert len(instructions) == len(tags)
    assert remediation_for("nonsense_tag").label == "Unclassified error"
