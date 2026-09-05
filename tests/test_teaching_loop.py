"""The controller must behave like a teacher, not a quiz engine.

These run offline with LLM_PROVIDER=echo (set in conftest).
"""

from backend.agents.controller import TeachingSession
from backend.agents.pedagogy import Phase


def make_plan(n=3, checkpoints=(1,)):
    return {
        "title": "Test Lesson", "summary": "", "subject": "physics",
        "assumed_prerequisites": [], "gaps": [],
        "time_plan": {"mode": "standard", "total_seconds": 1200, "intro_s": 84,
                      "teaching_s": 660, "checks_s": 180, "assessment_s": 180, "recap_s": 96},
        "concepts": [
            {"key": f"c{i}", "name": f"Concept {i}", "objective": "understand it",
             "depth": "explain", "seconds": 120, "difficulty": "medium",
             "prerequisites": [f"c{i-1}"] if i else [], "common_errors": [],
             "source_chunks": [], "checkpoint": i in checkpoints, "order": i}
            for i in range(n)
        ],
    }


PROFILE = {"level": "beginner", "teaching_language": "en", "language_label": "English",
           "minutes": 20, "topic": "Testing"}


def run_until(session, predicate, limit=40):
    for _ in range(limit):
        result = session.step()
        if predicate(result):
            return result
        if result.get("type") == "awaiting_answer":
            raise AssertionError("blocked waiting for an answer")
    raise AssertionError("never reached the expected state")


def test_lesson_opens_with_an_introduction_and_roadmap():
    s = TeachingSession(make_plan(), PROFILE)
    first = s.step()
    assert first["type"] == "explanation"
    assert first["segment"]["roadmap"] == ["Concept 0", "Concept 1", "Concept 2"]


def test_wrong_answer_triggers_re_teaching_not_just_a_correction():
    s = TeachingSession(make_plan(), PROFILE)
    run_until(s, lambda r: r.get("type") == "question")
    s.submit_answer("it increases")            # stub grades this as inverse_relationship
    assert s.phase is Phase.REMEDIATE
    result = s.step()
    assert result["type"] == "remediation"
    assert result["segment"]["misconception_tag"] == "inverse_relationship"


def test_re_explanation_uses_a_different_strategy():
    s = TeachingSession(make_plan(), PROFILE)
    run_until(s, lambda r: r.get("type") == "question")
    concept_key = s.current_concept["key"]
    first_strategy = s.states[concept_key].strategies_used[0]
    s.submit_answer("it increases")
    s.step()
    assert s.states[concept_key].strategies_used[1] != first_strategy


def test_correct_answer_moves_the_lesson_forward():
    s = TeachingSession(make_plan(), PROFILE)
    run_until(s, lambda r: r.get("type") == "question")
    before = s.cursor
    s.submit_answer("it decreases")
    assert s.cursor > before


def test_teacher_gives_up_re_teaching_and_flags_for_revision():
    s = TeachingSession(make_plan(n=2, checkpoints=(0,)), PROFILE)
    run_until(s, lambda r: r.get("type") == "question")
    key = s.current_concept["key"]
    for _ in range(6):
        if s.pending_question:
            s.submit_answer("it increases")
        else:
            s.step()
        if s.remediations.get(key, 0) >= 2 and key in s.flagged_for_revision:
            break
    assert key in s.flagged_for_revision, "an unresolved concept must reach the report, not loop forever"


def test_no_attempt_offers_a_hint_before_grading_it_wrong():
    s = TeachingSession(make_plan(), PROFILE)
    run_until(s, lambda r: r.get("type") == "question")
    key = s.current_concept["key"]
    result = s.submit_answer("idk")
    assert result.get("retry_question") is not None
    assert s.states[key].attempts == 0, "a non-answer is not evidence about knowledge"


def test_language_switch_preserves_plan_and_progress():
    s = TeachingSession(make_plan(), PROFILE)
    s.step(); s.step()
    mastery_before = {k: v.p_known for k, v in s.states.items()}
    cursor_before = s.cursor
    s.switch_language("hi", "Hindi")
    assert s.profile["teaching_language"] == "hi"
    assert s.cursor == cursor_before
    assert {k: v.p_known for k, v in s.states.items()} == mastery_before


def test_lesson_reaches_a_report():
    s = TeachingSession(make_plan(n=2, checkpoints=()), PROFILE)
    for _ in range(30):
        r = s.step()
        if r.get("type") == "assessment_question":
            s.submit_answer("b")
        if r.get("type") == "report":
            assert 0 <= r["report"]["score"] <= 100
            return
    raise AssertionError("lesson never finished")


def test_every_decision_is_explained():
    s = TeachingSession(make_plan(), PROFILE)
    run_until(s, lambda r: r.get("type") == "question")
    s.submit_answer("it increases")
    assert all(d.get("because") for d in s.decision_log)


def test_session_survives_serialisation():
    s = TeachingSession(make_plan(), PROFILE)
    run_until(s, lambda r: r.get("type") == "question")
    s.submit_answer("it increases")
    restored = TeachingSession.from_dict(s.to_dict())
    assert restored.phase is s.phase
    assert restored.cursor == s.cursor
    assert restored.states.keys() == s.states.keys()
    assert restored.states["c1"].strategies_used == s.states["c1"].strategies_used


def test_answer_key_never_leaves_the_server():
    s = TeachingSession(make_plan(), PROFILE)
    q = run_until(s, lambda r: r.get("type") == "question")["question"]
    assert "correct_option" not in q and "expected_answer" not in q


def test_hinglish_is_transcribed_as_hindi():
    """Whisper keeps code-mixed vocabulary far better when told the audio is
    Hindi than when told it is English."""
    from backend.media.stt import WHISPER_LANG

    assert WHISPER_LANG["hinglish"] == "hi"


def test_voice_and_typed_answers_take_the_same_grading_path():
    """The voice endpoint transcribes then calls submit_answer — it must not
    have a parallel grading implementation that could drift."""
    import inspect

    import pytest

    pytest.importorskip("fastapi")
    from backend.api import routes

    src = inspect.getsource(routes.answer_by_voice)
    assert "teaching.submit_answer" in src
    assert "stt.transcribe" in src
