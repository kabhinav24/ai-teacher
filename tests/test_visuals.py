"""Subject-aware visual choice must be explainable, not vibes."""
import pytest

from backend.agents.visuals import shortlist_renderers


@pytest.mark.parametrize("subject,narration,expected", [
    ("mathematics", "We derive the formula x = (-b + sqrt(b^2 - 4ac)) / 2a step by step.", "equation"),
    ("history", "In 1857 the revolt began, by 1919 Jallianwala Bagh, and in 1947 independence came.", "timeline"),
    ("programming", "def factorial(n): the function calls itself, import sys first.", "code"),
    ("biology", "The process happens in steps: light is absorbed, then water splits, then glucose forms.", "flow"),
    ("geography", "The region is located between the plateau and the river, north of the border.", "map"),
])
def test_subject_and_text_pick_the_right_renderer(subject, narration, expected):
    shortlist, _ = shortlist_renderers({"name": "X"}, narration, subject=subject)
    assert shortlist[0] == expected


def test_bullets_is_a_last_resort():
    shortlist, _ = shortlist_renderers({"name": "Ohm's Law"}, "The circuit diagram shows the parts.", subject="physics")
    assert shortlist[0] != "bullets"
    assert "bullets" in shortlist, "there must always be a fallback"


def test_the_choice_carries_its_evidence():
    _, trace = shortlist_renderers({"name": "Quadratics"}, "Solve the equation x^2 = 4.", subject="mathematics")
    assert trace["text_signals"], "a renderer choice must be traceable to evidence"
    assert "scores" in trace and "subject_prior" in trace
