"""Regression tests for numeric comparisons and closing behaviour.

These tests require pytest and Home Assistant dependencies.
"""
import pytest
from custom_components.household_tasks.__init__ import compare, Engine

@pytest.mark.parametrize(("value", "operator", "threshold", "expected"), [
    (3, "below", 3, False),
    (3, "below_or_equal", 3, True),
    (3, "above", 3, False),
    (3, "above_or_equal", 3, True),
    (3, "equal", 3, True),
    (2.9, "below_or_equal", 3, True),
    (3.1, "above_or_equal", 3, True),
    ("unavailable", "below_or_equal", 3, False),
])
def test_compare(value, operator, threshold, expected):
    assert compare(value, operator, threshold) is expected
