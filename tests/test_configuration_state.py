"""Regression tests for edits to effective task opening rules."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import Engine

NOW = datetime(2026, 1, 14, 12, tzinfo=timezone.utc)


def make_engine(**config):
    engine = Engine.__new__(Engine)
    engine.history = {}
    engine.lock = asyncio.Lock()
    engine.save = AsyncMock()
    engine.notify = Mock()
    sources = {"sensor.one": SimpleNamespace(state="2"),
               "sensor.two": SimpleNamespace(state="2")}
    engine.hass = SimpleNamespace(states=SimpleNamespace(get=sources.get))
    entry = SimpleNamespace(entry_id="task", data={
        "trigger_type": "numeric", "trigger_entity": "sensor.one",
        "operator": "below", "threshold": 3, "close_type": "manual",
        **config}, options={})
    engine.entries = {"task": entry}
    return engine, entry, sources


def test_existing_history_baseline_and_cosmetic_edits():
    async def scenario():
        engine, entry, _ = make_engine()
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            await engine.act("task", "complete")
            state = engine.state(entry)
            # Older installations have no opening signature.
            state.pop("opening_signature")
            before = deepcopy(state)
            await engine.evaluate(entry)
            assert not state["open"]
            assert state["latched"] and state["completed_token"] == before["completed_token"]
            assert state["events"] == before["events"]
            saved = engine.save.await_count
            entry.options = {"friendly_name": "Renamed", "area_id": "kitchen",
                             "category": "Testing", "threshold": 3.0,
                             "close_type": "manual_or_trigger_inactive"}
            await engine.evaluate(entry)
            assert not state["open"]
            assert state["events"] == before["events"]
            assert engine.save.await_count == saved

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [
    {"threshold": 4}, {"operator": "below_or_equal"},
    {"trigger_entity": "sensor.two"}, {"trigger_type": "state", "trigger_state": "2"},
])
def test_changed_closed_trigger_is_evaluated_again(edit):
    async def scenario():
        engine, entry, _ = make_engine()
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = NOW
            await engine.evaluate(entry)
            await engine.act("task", "complete")
            state = engine.state(entry)
            history = deepcopy(state["events"])
            last_completed = state["last_completed"]
            entry.options = edit
            clock.return_value = NOW + timedelta(minutes=1)
            await engine.evaluate(entry)
            assert state["open"] and state["latched"]
            assert state["last_completed"] == last_completed
            assert state["events"][:-1] == history
            assert state["events"][-1]["action"] == "opened"
            saved = engine.save.await_count
            await engine.evaluate(entry)
            assert engine.save.await_count == saved

    asyncio.run(scenario())


def test_open_task_rebinds_without_new_opening_or_lost_history():
    async def scenario():
        engine, entry, sources = make_engine()
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = NOW
            await engine.evaluate(entry)
            state = engine.state(entry)
            original = deepcopy(state)
            entry.options = {"trigger_entity": "sensor.two", "threshold": 1}
            clock.return_value = NOW + timedelta(minutes=1)
            await engine.evaluate(entry)
            assert state["open"] and state["pending_rebind"]
            assert not state.get("active_token")
            assert state["due"] == original["due"]
            assert state["events"] == original["events"]
            sources["sensor.two"].state = "0.5"
            await engine.evaluate(entry)
            assert state["active_token"] == "entity:sensor.two"
            assert state["latched"]
            assert not state.get("pending_rebind")
            assert state["due"] == original["due"]
            assert state["last_opened"] == original["last_opened"]
            assert state["events"] == original["events"]
            await engine.act("task", "complete")
            assert not state["open"]
            assert state["completed_token"] == "entity:sensor.two"

    asyncio.run(scenario())


def test_open_task_not_closed_during_opening_rule_edit():
    async def scenario():
        engine, entry, _ = make_engine(close_type="trigger_inactive")
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            state = engine.state(entry)
            entry.options = {"threshold": 1}
            await engine.evaluate(entry)
            assert state["open"]
            assert state["last_completed"] is None
            await engine.evaluate(entry)
            assert not state["open"]
            assert state["events"][-1]["action"] == "auto_completed"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind,settings", [
    ("weekly", {"weekday": 0}),
    ("monthly", {"month_day": 1}),
    ("yearly", {"year_month": 1, "month_day": 1}),
])
def test_changed_fixed_rule_does_not_open_past_slot(kind, settings):
    async def scenario():
        engine, entry, _ = make_engine()
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            await engine.act("task", "complete")
            state = engine.state(entry)
            history = deepcopy(state["events"])
            entry.options = {"trigger_type": kind, **settings}
            await engine.evaluate(entry)
            assert not state["open"]
            assert datetime.fromisoformat(state["due"]) > NOW
            assert state["events"] == history
            assert state["last_completed"] is not None

    asyncio.run(scenario())


def test_completion_interval_edit_uses_actual_last_completion():
    async def scenario():
        engine, entry, _ = make_engine()
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            await engine.act("task", "complete")
            state = engine.state(entry)
            entry.options = {"trigger_type": "completion", "interval_days": 7}
            await engine.evaluate(entry)
            assert not state["open"]
            assert datetime.fromisoformat(state["due"]).date() == (NOW + timedelta(days=7)).date()
            assert state["last_completed"] == NOW.isoformat()

    asyncio.run(scenario())


def test_calendar_edit_does_not_use_old_event_end():
    async def scenario():
        engine, entry, _ = make_engine(trigger_type="calendar",
            trigger_entity="calendar.old", calendar_offset_days=0,
            close_type="calendar_end")
        engine.calendar_events = AsyncMock(return_value=[{
            "summary": "Old", "start": NOW.isoformat(),
            "end": (NOW + timedelta(hours=1)).isoformat()}])
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = NOW
            await engine.evaluate(entry)
            state = engine.state(entry)
            original = deepcopy(state)
            entry.options = {"trigger_entity": "calendar.new"}
            start = NOW + timedelta(days=2)
            end = start + timedelta(hours=1)
            engine.calendar_events.return_value = [{
                "summary": "New", "start": start.isoformat(), "end": end.isoformat()}]
            clock.return_value = NOW + timedelta(hours=2)
            await engine.evaluate(entry)
            await engine.evaluate(entry)
            assert state["open"] and state["pending_rebind"]
            assert not state.get("calendar_end")
            assert state["due"] == original["due"]
            clock.return_value = start
            await engine.evaluate(entry)
            assert state["open"] and not state.get("pending_rebind")
            assert state["calendar_end"] == end.isoformat()
            assert state["events"] == original["events"]
            clock.return_value = end
            await engine.evaluate(entry)
            assert not state["open"]

    asyncio.run(scenario())


def test_registry_rename_preserves_completed_numeric_latch():
    async def scenario():
        engine, entry, sources = make_engine()
        def update_entry(target, options):
            target.options = options
        engine.hass.config_entries = SimpleNamespace(async_update_entry=Mock(side_effect=update_entry))
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            await engine.act("task", "complete")
            state = engine.state(entry)
            history = deepcopy(state["events"])
            sources["sensor.renamed"] = sources.pop("sensor.one")
            await engine._apply_entity_rename("sensor.one", "sensor.renamed")
            await engine.evaluate(entry)
            assert not state["open"] and state["latched"]
            assert state["completed_token"] == "entity:sensor.renamed"
            assert state["events"] == history
            assert state["opening_signature"]["trigger_entity"] == "sensor.renamed"

    asyncio.run(scenario())


def test_failed_calculation_restores_previous_signature():
    async def scenario():
        engine, entry, _ = make_engine()
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            state = engine.state(entry)
            before = deepcopy(state)
            entry.options = {"trigger_type": "calendar", "trigger_entity": "calendar.test"}
            engine.calendar_events = AsyncMock(side_effect=RuntimeError("failure"))
            with pytest.raises(RuntimeError):
                await engine.evaluate(entry)
            assert state == before

    asyncio.run(scenario())
