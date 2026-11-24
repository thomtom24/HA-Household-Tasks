"""Regression tests for removal of unfinished lifecycle actions."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import Engine

NOW = datetime(2026, 1, 14, 12, tzinfo=timezone.utc)


def make_engine(kind="numeric"):
    engine = Engine.__new__(Engine)
    engine.history = {}
    engine.lock = asyncio.Lock()
    engine.calendar_cache = {}
    engine.calendar_failures = {}
    engine.save = AsyncMock()
    engine.notify = Mock()
    source = SimpleNamespace(state="2", attributes={})
    engine.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: source))
    entry = SimpleNamespace(entry_id="task", data={
        "trigger_type": kind, "trigger_entity": "sensor.test",
        "operator": "below", "threshold": 3, "close_type": "manual",
        "interval_days": 7, "calendar_offset_days": 0,
    }, options={})
    engine.entries = {"task": entry}
    return engine, entry, source


@pytest.mark.parametrize("action", ["skip", "snooze", "reopen", "invalid"])
def test_removed_actions_are_rejected_without_changes(action):
    async def scenario():
        engine, entry, _ = make_engine()
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            before = deepcopy(engine.state(entry))
            writes = engine.save.await_count
            with pytest.raises(ValueError, match="Unsupported task action"):
                await engine.act("task", action)
            assert engine.state(entry) == before
            assert engine.save.await_count == writes

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["numeric", "state", "completion", "weekly", "monthly", "yearly", "date_entity", "calendar"])
@pytest.mark.parametrize("expired", [False, True])
def test_legacy_snoozed_occurrence_is_restored_without_history_changes(kind, expired):
    async def scenario():
        engine, entry, _ = make_engine(kind)
        original_due = (NOW - timedelta(days=2)).isoformat()
        original_open = (NOW - timedelta(days=1)).isoformat()
        token = "calendar:old" if kind == "calendar" else "original"
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            state = engine.state(entry)
            state.update({
                "open": False, "snooze_until": (NOW + timedelta(days=-1 if expired else 1)).isoformat(),
                "last_opened": original_open, "due_at_open": original_due,
                "due": original_due, "active_token": token, "latched": True,
                "events": [{"action": "opened", "at": original_open},
                           {"action": "snoozed", "at": original_open}],
            })
            if kind == "calendar":
                state["calendar_end"] = (NOW - timedelta(hours=1)).isoformat()
                engine.calendar_events = AsyncMock(return_value=[])
            history = deepcopy(state["events"])
            await engine.evaluate(entry)
            assert state["open"]
            assert "snooze_until" not in state
            assert state["last_opened"] == original_open
            assert state["due_at_open"] == original_due
            assert state["due"] == original_due
            assert state["active_token"] == token
            assert state["last_completed"] is None
            assert state["events"] == history
            assert engine.save.await_count == 1
            await engine.evaluate(entry)
            assert engine.save.await_count == 1
            assert state["events"] == history

    asyncio.run(scenario())


def test_legacy_null_snooze_field_is_removed_without_opening_completed_task():
    async def scenario():
        engine, entry, _ = make_engine()
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            await engine.act("task", "complete")
            state = engine.state(entry)
            state["snooze_until"] = None
            history = deepcopy(state["events"])
            await engine.evaluate(entry)
            assert "snooze_until" not in state
            assert not state["open"] and state["latched"]
            assert state["events"] == history

    asyncio.run(scenario())


def test_historical_skipped_and_reopened_events_are_preserved():
    async def scenario():
        engine, entry, source = make_engine()
        source.state = "5"
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            state = engine.state(entry)
            history = [{"action": "skipped", "at": NOW.isoformat()},
                       {"action": "reopened", "at": NOW.isoformat()}]
            state["events"] = deepcopy(history)
            await engine.evaluate(entry)
            assert state["events"] == history
            assert not state["open"]

    asyncio.run(scenario())


def test_restored_snooze_respects_automatic_closing_on_next_evaluation():
    async def scenario():
        engine, entry, source = make_engine()
        entry.data["close_type"] = "trigger_inactive"
        with patch("custom_components.household_tasks.dt_util.now", return_value=NOW):
            await engine.evaluate(entry)
            state = engine.state(entry)
            state["open"] = False
            state["snooze_until"] = (NOW + timedelta(days=1)).isoformat()
            source.state = "5"
            await engine.evaluate(entry)
            assert state["open"] and state["last_completed"] is None
            await engine.evaluate(entry)
            assert not state["open"]
            assert state["events"][-1]["action"] == "auto_completed"

    asyncio.run(scenario())
