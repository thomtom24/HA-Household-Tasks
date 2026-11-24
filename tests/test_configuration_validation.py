"""Regression coverage for creation and options wizard input validation."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol

from custom_components.household_tasks.config_flow import (
    TaskSteps, trigger_schema, close_schema, close_choice_schema, details_schema,
    finite_threshold, whole_number,
)


def make_flow(values=None, editing=False):
    flow = TaskSteps()
    flow.initialize(values or {}, editing)
    flow.async_show_form = Mock(side_effect=lambda **kw: kw)
    flow._finish = AsyncMock(return_value="saved")
    return flow


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "NaN", "Infinity", True, False, None, [], {}])
def test_threshold_rejects_unsafe_values(value):
    with pytest.raises(vol.Invalid):
        finite_threshold()(value)


@pytest.mark.parametrize("value", [-2.5, 0, "1.25", 1e100])
def test_threshold_accepts_finite_values(value):
    assert finite_threshold()(value) == float(value)


@pytest.mark.parametrize("value", [0, 366, 1.1, "2.5", True, float("nan"), float("inf")])
def test_whole_number_rejects_invalid_values(value):
    with pytest.raises(vol.Invalid):
        whole_number(1, 365)(value)


@pytest.mark.parametrize("value", [1, 365, 3.0, "15"])
def test_whole_number_returns_int(value):
    result = whole_number(1, 365)(value)
    assert type(result) is int
    assert result == int(float(value))


def test_number_selector_serialization():
    assert whole_number(0, 6).serialize()["selector"]["number"]["max"] == 6
    assert finite_threshold().serialize()["selector"]["number"]["step"] == "any"


@pytest.mark.parametrize("kind,data", [
    ("completion", {"interval_days": 0}),
    ("completion", {"interval_days": 2.1}),
    ("completion", {"interval_days": 3651}),
    ("completion", {"interval_days": 7, "first_due_date": "2026-02-30"}),
    ("weekly", {"weekday": 7}),
    ("weekly", {"weekday": 1.5}),
    ("monthly", {"month_day": 0}),
    ("monthly", {"month_day": 31.5}),
    ("yearly", {"schedule_interval": 121, "year_month": "1", "month_day": 1}),
    ("yearly", {"schedule_interval": 1, "year_month": "13", "month_day": 1}),
    ("calendar", {"trigger_entity": "sensor.test", "calendar_offset_days": 1}),
    ("calendar", {"trigger_entity": "calendar.test", "calendar_offset_days": 366}),
    ("calendar", {"trigger_entity": "calendar.test", "calendar_offset_days": 0, "calendar_match": "x"*513}),
    ("date_entity", {"trigger_entity": "sensor.test", "offset_days": 3651}),
    ("date_entity", {"trigger_entity": "sensor.test", "offset_days": -3651}),
    ("numeric", {"trigger_entity": "sensor.test", "threshold": "nan", "operator": "below"}),
    ("numeric", {"trigger_entity": "sensor.test", "threshold": 1, "operator": "invalid"}),
    ("state", {"trigger_entity": "sensor.test", "trigger_state": " "}),
    ("state", {"trigger_entity": "sensor.test", "trigger_state": "x"*256}),
    ("state", {"trigger_entity": "a"*32, "trigger_state": "on"}),
    ("state", {"trigger_entity": "sensor.test", "trigger_state": "on", "close_type": "manual"}),
])
def test_invalid_opening_settings_leave_values_unchanged(kind, data):
    async def scenario():
        flow = make_flow({"trigger_type": kind}, True)
        before = deepcopy(flow.values)
        result = await flow.async_step_trigger_settings(data)
        assert result["step_id"] == "trigger_settings"
        assert result["errors"]
        assert flow.values == before
        flow._finish.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,data", [
    ("completion", {"interval_days": 3650}),
    ("weekly", {"weekday": 6}),
    ("monthly", {"month_day": 31}),
    ("yearly", {"schedule_interval": 120, "year_month": "2", "month_day": 29}),
    ("calendar", {"trigger_entity": "calendar.offline", "calendar_offset_days": 365}),
    ("date_entity", {"trigger_entity": "sensor.offline", "offset_days": -7}),
    ("numeric", {"trigger_entity": "sensor.offline", "threshold": -2.5, "operator": "below"}),
    ("state", {"trigger_entity": "sensor.offline", "trigger_state": "on"}),
])
def test_valid_opening_settings_advance(kind, data):
    async def scenario():
        flow = make_flow({"trigger_type": kind}, True)
        result = await flow.async_step_trigger_settings(data)
        assert result["step_id"] == "close_choice"
        for key, value in data.items():
            assert flow.values[key] == value
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,close", [
    ("weekly", "calendar_end"), ("completion", "manual_or_calendar_end"),
    ("calendar", "trigger_inactive"), ("monthly", "manual_or_trigger_inactive"),
    ("numeric", "skip"), ("state", "invalid"),
])
def test_incompatible_closing_rule_rejected(kind, close):
    async def scenario():
        flow = make_flow({"trigger_type": kind}, True)
        before = deepcopy(flow.values)
        result = await flow.async_step_close_choice({"close_type": close})
        assert result["errors"]
        assert flow.values == before
        flow._finish.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,close", [
    ("calendar", "calendar_end"), ("calendar", "manual_or_calendar_end"),
    ("numeric", "trigger_inactive"), ("state", "manual_or_trigger_inactive"),
    ("weekly", "manual"),
])
def test_compatible_closing_rule_saves(kind, close):
    async def scenario():
        flow = make_flow({"trigger_type": kind}, True)
        assert await flow.async_step_close_choice({"close_type": close}) == "saved"
        flow._finish.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,data", [
    ("entity_numeric", {"close_entity": "sensor.test", "close_threshold": float("nan"), "close_operator": "above"}),
    ("manual_or_entity_numeric", {"close_entity": "sensor.test", "close_threshold": True, "close_operator": "above"}),
    ("entity_numeric", {"close_entity": "sensor.test", "close_threshold": 1, "close_operator": "invalid"}),
    ("entity_state", {"close_entity": "sensor.test", "close_state": ""}),
    ("manual_or_entity_state", {"close_entity": "a"*32, "close_state": "off"}),
])
def test_invalid_closing_settings_do_not_save(kind, data):
    async def scenario():
        flow = make_flow({"close_type": kind}, True)
        before = deepcopy(flow.values)
        result = await flow.async_step_close_settings(data)
        assert result["errors"]
        assert flow.values == before
        flow._finish.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("key,value", [
    ("name", "!!!"), ("name", "a"*129), ("friendly_name", ""),
    ("friendly_name", "x"*129), ("category", " "), ("category", "x"*129),
])
def test_invalid_task_details_do_not_advance(key, value):
    async def scenario():
        flow = make_flow()
        before = deepcopy(flow.values)
        data = {"name": "my task", "friendly_name": "My task", "category": "Household"}
        data[key] = value
        result = await flow.async_step_details(data)
        assert result["errors"]
        assert flow.values == before
    asyncio.run(scenario())


@pytest.mark.parametrize("editing", [False, True])
def test_valid_details_preserve_text_and_internal_name(editing):
    async def scenario():
        flow = make_flow({"name": "existing", "area_id": "kitchen"}, editing)
        data = {"friendly_name": "My task", "category": "Household"}
        if not editing:
            data["name"] = "new task"
        result = await flow.async_step_details(data)
        assert result["step_id"] == "trigger_choice"
        assert flow.values["name"] == ("existing" if editing else "new task")
        assert flow.values["area_id"] is None
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,field,old,data,expected", [
    ("completion", "first_due_date", "2026-12-01", {"interval_days": 7}, None),
    ("calendar", "calendar_match", "collection", {"trigger_entity": "calendar.test", "calendar_offset_days": 1}, ""),
])
def test_optional_values_clear_after_real_schema_validation(kind, field, old, data, expected):
    async def scenario():
        flow = make_flow({"trigger_type": kind, field: old}, True)
        validated = trigger_schema(flow.values)(data)
        assert field not in validated
        await flow.async_step_trigger_settings(validated)
        assert flow.values[field] == expected
    asyncio.run(scenario())
