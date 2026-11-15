import pytest

from app.services.calendar_intent import explicit_time_range


@pytest.mark.parametrize("text,expected", [
    ("skating at 3pm to 3.20pm also", (900, 920)),
    ("8pm to 9:30pm", (1200, 1290)),
    ("12am–12:30am", (0, 30)),
    ("12 PM until 1 PM", (720, 780)),
    ("11:45pm - 12:15am", (1425, 15)),
    ("3 p.m. to 3.20 p.m.", (900, 920)),
    ("from 3 to 4", None),
    ("11 to 1pm", None),
    ("3pm to 3.99pm", None),
    ("13pm to 14pm", None),
    ("3pm to 4pm and 5pm to 6pm", None),
])
def test_explicit_clock_ranges(text, expected):
    assert explicit_time_range(text) == expected
