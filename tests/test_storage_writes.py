"""Regression tests for redundant scheduler storage writes (requires Home Assistant)."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import Engine


@pytest.mark.parametrize("kind,reading,inactive", [
    ("numeric", "0.9", "1.5"),
    ("state", "on", "off"),
])
def test_active_trigger_does_not_rewrite_history(kind, reading, inactive):
    async def scenario():
        engine = Engine.__new__(Engine)
        engine.history = {}
        engine.lock = asyncio.Lock()
        engine.save = AsyncMock()
        engine.notify = Mock()
        source = SimpleNamespace(state=reading)
        engine.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: source))
        entry = SimpleNamespace(entry_id="task", data={}, options={})
        engine.entries = {"task": entry}
        engine.conf = lambda _: {
            "trigger_type": kind, "trigger_entity": "sensor.source",
            "operator": "below", "threshold": 1.0, "trigger_state": "on",
            "close_type": "manual",
        }
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = start
            await engine.evaluate(entry)
            assert engine.save.await_count == 1
            assert engine.state(entry)["open"]
            due = engine.state(entry)["due"]
            engine.notify.reset_mock()
            for minute in range(1, 6):
                clock.return_value = start + timedelta(minutes=minute)
                await engine.evaluate(entry)
            assert engine.state(entry)["due"] == due
            assert engine.save.await_count == 1
            engine.notify.assert_not_called()

            # Completing while the source remains active must not reopen it.
            state = engine.state(entry)
            engine._finish(state, clock.return_value, "completed")
            state["completed_token"] = state["active_token"]
            await engine.evaluate(entry)
            assert not state["open"]
            assert engine.save.await_count == 1

            # A genuinely new occurrence gets a fresh opening timestamp.
            source.state = inactive
            await engine.evaluate(entry)
            assert engine.save.await_count == 2
            source.state = reading
            clock.return_value = start + timedelta(minutes=10)
            await engine.evaluate(entry)
            assert state["open"]
            assert state["due_at_open"] == clock.return_value.isoformat()
            assert engine.save.await_count == 3

    asyncio.run(scenario())


def test_new_inactive_task_persists_creation_baseline():
    async def scenario():
        engine = Engine.__new__(Engine)
        engine.history = {}
        engine.lock = asyncio.Lock()
        engine.save = AsyncMock()
        engine.notify = Mock()
        engine.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: None))
        entry = SimpleNamespace(entry_id="task", data={}, options={})
        engine.entries = {"task": entry}
        engine.conf = lambda _: {
            "trigger_type": "completion", "interval_days": 7,
            "close_type": "manual",
        }
        await engine.evaluate(entry)
        assert engine.save.await_count == 1
        assert not engine.state(entry)["open"]
        await engine.evaluate(entry)
        assert engine.save.await_count == 1

    asyncio.run(scenario())
