"""Regression tests for invalid readings and recovery."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.household_tasks import Engine, numeric_condition

INVALID = [None, "", "unknown", "unavailable", "bad", "NaN", "inf", "-inf", "1e999"]


@pytest.mark.parametrize("value", INVALID)
def test_invalid_comparisons(value):
    for operator in ("below", "below_or_equal", "above", "above_or_equal", "equal"):
        assert numeric_condition(value, operator, 1) is None
        assert numeric_condition(1, operator, value) is None


def make_engine(close_type="manual"):
    engine = Engine.__new__(Engine)
    engine.storage_dirty = False
    engine.storage_failed = False
    engine.storage_retry = None
    engine.reloading_entries = {}
    engine.last_aggregate_names = {}
    engine.calendar_requests = {}
    engine.cyclic_entries = set()
    engine.history = {}
    engine.lock = asyncio.Lock()
    engine.save = AsyncMock()
    engine.notify = Mock()
    source = SimpleNamespace(state="0.9")
    engine.hass = SimpleNamespace(states=SimpleNamespace(
        get=lambda _: source if source.state is not None else None))
    entry = SimpleNamespace(entry_id="task")
    engine.entries = {"task": entry}
    config = {
        "trigger_type": "numeric", "trigger_entity": "sensor.source",
        "operator": "below", "threshold": 1.0, "close_type": close_type,
        "close_entity": "sensor.source", "close_operator": "above",
        "close_threshold": 1.0,
    }
    engine.conf = lambda _: config
    return engine, entry, source


@pytest.mark.parametrize("value", INVALID)
def test_invalid_reading_does_not_rearm_completed_task(value):
    async def scenario():
        engine, entry, source = make_engine()
        await engine.evaluate(entry)
        state = engine.state(entry)
        assert state["open"]
        engine._finish(state, dt_util.now(), "completed")
        state["completed_token"] = state["active_token"]
        await engine.save()
        previous_saves = engine.save.await_count
        source.state = value
        await engine.evaluate(entry)
        assert state["latched"]
        assert state["completed_token"] == state["active_token"]
        assert state["entity_warnings"]
        assert engine.save.await_count == previous_saves
        source.state = "0.9"
        await engine.evaluate(entry)
        assert not state["open"]
        assert not state["entity_warnings"]
        source.state = "1.5"
        await engine.evaluate(entry)
        assert not state["latched"]
        source.state = "0.9"
        await engine.evaluate(entry)
        assert state["open"]

    asyncio.run(scenario())


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("rule", ["trigger_inactive", "entity_numeric"])
def test_invalid_reading_cannot_complete_open_task(value, rule):
    async def scenario():
        engine, entry, source = make_engine(rule)
        await engine.evaluate(entry)
        state = engine.state(entry)
        assert state["open"]
        previous_due = state["due"]
        previous_saves = engine.save.await_count
        source.state = value
        await engine.evaluate(entry)
        assert state["open"]
        assert state["due"] == previous_due
        assert state["last_completed"] is None
        assert state["entity_warnings"]
        assert engine.save.await_count == previous_saves
        source.state = "1.5"
        await engine.evaluate(entry)
        assert not state["open"]
        assert state["last_completed"] is not None
        assert not state["entity_warnings"]

    asyncio.run(scenario())
