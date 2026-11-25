"""Regression tests for v0.7.1 maintenance fixes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.household_tasks import Engine, date_entity_value, valid_history_record
from custom_components.household_tasks.sensor import TaskStatusSensor

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


def fixture():
    hass = SimpleNamespace(states=SimpleNamespace(get=Mock()), services=SimpleNamespace(async_register=Mock()))
    with patch("custom_components.household_tasks.Store"):
        engine = Engine(hass)
    engine.save = AsyncMock(return_value=True)
    engine.notify = Mock()
    a = SimpleNamespace(entry_id="a", title="Task A", data={"trigger_type":"date_entity", "trigger_entity":"sensor.date", "offset_days":0}, options={})
    b = SimpleNamespace(entry_id="b", title="Task B", data={}, options={})
    engine.entries = {"a":a, "b":b}
    return engine, a, b


def record():
    return {"created":STAMP,"open":False,"events":[],"active_token":None,"completed_token":None}


@pytest.mark.parametrize("state", ["unknown", "unavailable", "", None])
@pytest.mark.parametrize("attribute", ["date", "timestamp"])
def test_untrusted_date_entity_does_not_open_task(state, attribute):
    async def scenario():
        engine, a, _ = fixture()
        engine.hass.states.get.return_value = SimpleNamespace(state=state, attributes={attribute:"2026-11-24"})
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(a)
            assert not engine.state(a)["open"]
            assert engine.state(a)["entity_warnings"]
            assert engine.state(a)["events"] == []
            engine.hass.states.get.return_value = SimpleNamespace(state="2026-11-24", attributes={})
            await engine.evaluate(a)
            assert engine.state(a)["open"]
            assert len(engine.state(a)["events"]) == 1
            engine.hass.states.get.return_value = SimpleNamespace(state=state, attributes={attribute:"2026-11-23"})
            await engine.evaluate(a)
            assert engine.state(a)["open"]
            assert len(engine.state(a)["events"]) == 1
            assert engine.state(a)["due"] == engine.state(a)["due_at_open"]
    asyncio.run(scenario())


@pytest.mark.parametrize("state,attributes", [
    ("2026-11-24", {}),
    ("2026-11-24T12:00:00+00:00", {}),
    ("ready", {"date":"2026-11-24"}),
    ("ready", {"timestamp":"2026-11-24T12:00:00+00:00"}),
])
def test_available_date_sources(state, attributes):
    assert date_entity_value(SimpleNamespace(state=state, attributes=attributes)) is not None


def test_invalid_available_date_warns_without_opening():
    async def scenario():
        engine,a,_=fixture()
        engine.hass.states.get.return_value=SimpleNamespace(state="not a date",attributes={})
        await engine.evaluate(a)
        assert not engine.state(a)["open"]
        assert "invalid date" in engine.state(a)["entity_warnings"][0]
    asyncio.run(scenario())


@pytest.mark.parametrize("length", [128,255,256,300])
def test_latest_name_length_preserves_full_attributes(length):
    engine,a,b=fixture()
    name="é"*length
    a.options={"friendly_name":name}
    engine.history={"a":{**record(),"open":True,"last_opened":STAMP},"b":record()}
    value,attrs=engine.aggregate_snapshot()["latest_open"]
    assert len(value)==min(length,MAX_LENGTH_STATE_STATE)
    assert attrs["task_name"]==name
    assert engine.aggregate_snapshot()["open_names"][1]["names"]==[name]
    warnings=TaskStatusSensor(engine,a).extra_state_attributes["entity_warnings"]
    assert bool(warnings)==(length>MAX_LENGTH_STATE_STATE)
    assert a.options["friendly_name"]==name


@pytest.mark.parametrize("bad", [
    [],
    {"created":STAMP,"open":False},
    {**record(),"open":"false"},
    {**record(),"events":"invalid"},
    {**record(),"events":[None]},
    {**record(),"events":[{"action":"opened","at":"invalid"}]},
    {**record(),"last_completed":"invalid"},
    {**record(),"active_token":[]},
    {**record(),"completed_token":""},
    {**record(),"opening_signature":[]},
    {**record(),"opening_signature":{}},
    {**record(),"latched":"yes"},
    {**record(),"entity_warnings":[42]},
])
def test_invalid_records_are_isolated_and_preserved(bad):
    async def scenario():
        engine,a,b=fixture()
        raw={"a":deepcopy(bad),"b":record()}
        engine.store.async_load=AsyncMock(return_value=raw)
        await engine.start()
        assert engine.invalid_history=={"a"}
        assert engine.history==raw
        assert TaskStatusSensor(engine,a).native_value=="unknown"
        attrs=TaskStatusSensor(engine,a).extra_state_attributes
        assert attrs["history_invalid"] and attrs["has_entity_warning"]
        assert not attrs["manual_completion_allowed"]
        await engine.evaluate(a)
        with pytest.raises(HomeAssistantError,match="malformed"):
            await engine.act("a","complete")
        engine.save.assert_not_awaited()
        # A healthy task remains operational and cannot overwrite the bad record.
        b.options={"trigger_type":"completion","first_due_date":"2026-11-24"}
        with patch("custom_components.household_tasks.dt_util.now",return_value=NOW):
            await engine.evaluate(b)
        assert engine.state(b)["open"]
        assert engine.history["a"]==bad
        assert engine.aggregate_snapshot()["open_names"][1]["names"]==["Task B"]
    asyncio.run(scenario())


def test_valid_legacy_events_and_unknown_fields_are_retained():
    raw={**record(),"events":[{"action":"skipped","at":STAMP}],"unknown_legacy_field":{"data":"keep"}}
    assert valid_history_record(raw)


@pytest.mark.parametrize("action,options,message", [
    ("complete",{"close_type":"entity_state"},"automatic completion only"),
    ("complete_early",{},"only supported"),
    ("unsupported",{},"Unsupported task action"),
])
def test_user_action_errors_are_service_validation_errors(action,options,message):
    async def scenario():
        engine,a,_=fixture()
        a.options=options
        with pytest.raises(ServiceValidationError,match=message):
            await engine.act("a",action)
        engine.save.assert_not_awaited()
    asyncio.run(scenario())
