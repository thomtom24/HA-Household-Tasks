"""Regression tests for calendar occurrence identity and closing rules."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import Engine

START = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


def make_event(start=START, end=END):
    return {"summary": "Collection", "start": start.isoformat(),
            "end": end.isoformat() if end else None}


def make_engine(rule):
    engine = Engine.__new__(Engine)
    engine.history = {}
    engine.lock = asyncio.Lock()
    engine.save = AsyncMock()
    engine.notify = Mock()
    source = SimpleNamespace(state="off")
    engine.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: source))
    entry = SimpleNamespace(entry_id="calendar", data={
        "trigger_type": "calendar", "trigger_entity": "calendar.test",
        "calendar_match": "Collection", "calendar_offset_days": 0,
        "close_type": rule, "close_entity": "binary_sensor.test",
        "close_state": "on", "close_operator": "above", "close_threshold": 1,
    }, options={})
    engine.entries = {entry.entry_id: entry}
    engine.calendar_events = AsyncMock(return_value=[make_event()])
    return engine, entry, source


@pytest.mark.parametrize("rule", ["manual", "entity_state", "manual_or_entity_state",
                                  "entity_numeric", "manual_or_entity_numeric"])
@pytest.mark.parametrize("response", ["present", "missing", "unavailable"])
def test_event_end_does_not_complete_independently_closed_tasks(rule, response):
    async def scenario():
        engine, entry, source = make_engine(rule)
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = START
            await engine.evaluate(entry)
            state = engine.state(entry)
            assert state["open"]
            before = deepcopy(state)
            writes = engine.save.await_count
            clock.return_value = END + timedelta(days=4)
            if response == "missing":
                engine.calendar_events.return_value = [make_event(
                    START + timedelta(days=7), END + timedelta(days=7))]
            elif response == "unavailable":
                engine.calendar_events.return_value = None
            await engine.evaluate(entry)
            assert state["open"]
            for field in ("due", "due_at_open", "active_token", "last_opened",
                          "last_completed", "events", "calendar_end"):
                assert state[field] == before[field]
            assert engine.save.await_count == writes
            # Independent automatic rules still work after the calendar ends.
            if "entity" in rule:
                source.state = "2" if "numeric" in rule else "on"
                await engine.evaluate(entry)
                assert not state["open"]
                assert state["events"][-1]["action"] == "auto_completed"
            else:
                await engine.act(entry.entry_id, "complete")
                assert not state["open"]
                assert state["events"][-1]["action"] == "completed"

    asyncio.run(scenario())


@pytest.mark.parametrize("rule", ["calendar_end", "manual_or_calendar_end"])
@pytest.mark.parametrize("response", ["present", "missing", "unavailable"])
def test_calendar_end_rules_complete_once(rule, response):
    async def scenario():
        engine, entry, _ = make_engine(rule)
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = START
            await engine.evaluate(entry)
            state = engine.state(entry)
            token = state["active_token"]
            assert state["open"]
            clock.return_value = END - timedelta(seconds=1)
            await engine.evaluate(entry)
            assert state["open"]
            if response == "missing":
                engine.calendar_events.return_value = []
            elif response == "unavailable":
                engine.calendar_events.return_value = None
            clock.return_value = END
            await engine.evaluate(entry)
            assert not state["open"]
            assert state["completed_token"] == token
            assert state["events"][-1]["action"] == "auto_completed"
            assert len(state["events"]) == 2
            await engine.evaluate(entry)
            assert len(state["events"]) == 2

    asyncio.run(scenario())


def test_missing_original_end_cannot_be_replaced_by_next_event():
    async def scenario():
        engine, entry, _ = make_engine("calendar_end")
        engine.calendar_events.return_value = [make_event(end=None)]
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = START
            await engine.evaluate(entry)
            state = engine.state(entry)
            token = state["active_token"]
            due = state["due"]
            assert state["open"] and state["calendar_end"] is None
            engine.calendar_events.return_value = [make_event(
                START + timedelta(days=7), END + timedelta(days=7))]
            clock.return_value = START + timedelta(days=7)
            await engine.evaluate(entry)
            assert state["open"]
            assert state["active_token"] == token
            assert state["due"] == due
            assert state["calendar_end"] is None

    asyncio.run(scenario())


def test_late_manual_completion_preserves_history_and_allows_next_occurrence():
    async def scenario():
        engine, entry, _ = make_engine("manual")
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = START
            await engine.evaluate(entry)
            original = engine.state(entry)["active_token"]
            # Simulate restored persistent history after a restart.
            engine.history = deepcopy(engine.history)
            state = engine.state(entry)
            next_start, next_end = START + timedelta(days=7), END + timedelta(days=7)
            engine.calendar_events.return_value = [make_event(next_start, next_end)]
            clock.return_value = next_start
            await engine.evaluate(entry)
            assert state["open"] and state["active_token"] == original
            assert len(state["events"]) == 1
            await engine.act(entry.entry_id, "complete")
            assert state["completed_token"] == original
            assert state["open"] and state["active_token"] != original
            assert state["calendar_end"] == next_end.isoformat()
            assert [event["action"] for event in state["events"]] == [
                "opened", "completed", "opened"]

    asyncio.run(scenario())
