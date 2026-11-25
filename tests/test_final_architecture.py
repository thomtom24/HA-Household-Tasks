"""Regression tests for storage recovery, cache races and publication."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError

from custom_components.household_tasks import Engine, _options_updated
from custom_components.household_tasks.const import DOMAIN
from custom_components.household_tasks.sensor import TaskStatusSensor

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
CALENDAR = "calendar.test"


def fixture():
    hass = SimpleNamespace(state=CoreState.running, data={},
                           config_entries=SimpleNamespace())
    with patch("custom_components.household_tasks.Store"):
        engine = Engine(hass)
    hass.data[DOMAIN] = engine
    engine.refresh_runtime_listeners = Mock()
    entry = SimpleNamespace(entry_id="a", title="Task", data={}, options={})
    engine.entries["a"] = entry
    engine.history["a"] = {"open": True, "last_opened": NOW.isoformat(), "events": []}
    engine.store.async_save = AsyncMock()
    engine.store.async_load = AsyncMock(side_effect=lambda: deepcopy(engine.history))
    return engine, entry


@pytest.mark.parametrize("failure", ["exception", "mismatch"])
def test_storage_failure_keeps_history_and_retries(failure):
    async def scenario():
        engine, entry = fixture()
        original = deepcopy(engine.history)
        if failure == "exception":
            engine.store.async_save.side_effect = OSError("disk full")
        else:
            engine.store.async_load.side_effect = None
            engine.store.async_load.return_value = {}
        with patch("custom_components.household_tasks.dt_util.utcnow", return_value=NOW):
            assert await engine.save() is False
        assert engine.storage_dirty and engine.storage_failed
        assert engine.history == original
        assert TaskStatusSensor(engine, entry).extra_state_attributes["has_entity_warning"]
        await engine.retry_storage(NOW)
        assert engine.store.async_save.await_count == 1
        engine.store.async_save.side_effect = None
        engine.store.async_load.side_effect = lambda: deepcopy(engine.history)
        await engine.retry_storage(NOW + timedelta(minutes=1))
        assert engine.store.async_save.await_count == 2
        assert not engine.storage_dirty and not engine.storage_failed
        assert engine.history == original
    asyncio.run(scenario())


def test_storage_snapshot_is_independent_of_live_history():
    async def scenario():
        engine, _ = fixture()
        assert await engine.save()
        snapshot = engine.store.async_save.call_args.args[0]
        engine.history["a"]["open"] = False
        assert snapshot["a"]["open"] is True
    asyncio.run(scenario())


def test_manual_completion_reports_unsaved_history_without_duplicate_event():
    async def scenario():
        engine, _ = fixture()
        engine.store.async_save.side_effect = OSError("disk full")
        with pytest.raises(HomeAssistantError, match="not saved"):
            await engine.act("a", "complete")
        assert engine.history["a"]["open"] is False
        assert len(engine.history["a"]["events"]) == 1
        await engine.act("a", "complete")
        assert len(engine.history["a"]["events"]) == 1
    asyncio.run(scenario())


def test_shutdown_deferred_write_is_not_read_back():
    async def scenario():
        engine, _ = fixture()
        engine.hass.state = CoreState.stopping
        assert await engine.save()
        engine.store.async_load.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [[], "invalid"])
def test_invalid_loaded_history_is_not_overwritten(bad):
    async def scenario():
        engine, _ = fixture()
        engine.store.async_load.side_effect = None
        engine.store.async_load.return_value = bad
        with pytest.raises(HomeAssistantError, match="Invalid household task history"):
            await engine.start()
        engine.store.async_save.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True])
def test_invalidated_calendar_request_cannot_restore_old_cache(failure):
    async def scenario():
        engine, entry = fixture()
        entry.data = {"trigger_type": "calendar", "trigger_entity": CALENDAR}
        started, release = asyncio.Event(), asyncio.Event()
        async def provider(*args, **kwargs):
            started.set()
            await release.wait()
            if failure:
                raise OSError("offline")
            return {CALENDAR: {"events": []}}
        engine.hass.states = SimpleNamespace(get=lambda _: SimpleNamespace(state="off"))
        engine.hass.services = SimpleNamespace(has_service=lambda *args: True, async_call=provider)
        fetch = asyncio.create_task(engine.calendar_events(CALENDAR, NOW))
        await started.wait()
        engine.invalidate_calendar(CALENDAR)
        release.set()
        assert await fetch is None
        assert not engine.calendar_cache
        assert not engine.calendar_failures
        assert not engine.calendar_requests
    asyncio.run(scenario())


def test_pruning_removes_expired_and_unused_calendars():
    engine, entry = fixture()
    entry.data = {"trigger_type": "calendar", "trigger_entity": CALENDAR}
    end = NOW + timedelta(days=90)
    engine.calendar_cache = {CALENDAR: (NOW - timedelta(minutes=30), [], end),
                             "calendar.unused": (NOW, [], end)}
    engine.calendar_failures = {"calendar.unused": (NOW, NOW, 1, "offline")}
    engine.calendar_requests = {"calendar.unused": {"invalidated": False}}
    engine.prune_calendar_cache(NOW)
    assert not engine.calendar_cache and not engine.calendar_failures
    assert engine.calendar_requests["calendar.unused"]["invalidated"]


def test_status_sensor_skips_unchanged_payload_but_detects_history_and_warnings():
    async def scenario():
        engine, entry = fixture()
        sensor = TaskStatusSensor(engine, entry)
        sensor.async_write_ha_state = Mock()
        await sensor.async_added_to_hass()
        sensor._changed("a")
        sensor.async_write_ha_state.assert_not_called()
        engine.history["a"]["events"].append({"action": "opened", "at": NOW.isoformat()})
        sensor._changed("a")
        sensor.async_write_ha_state.assert_called_once()
        sensor._changed("a")
        sensor.async_write_ha_state.assert_called_once()
        engine.storage_failed = True
        sensor._changed("a")
        assert sensor.async_write_ha_state.call_count == 2
        await sensor.async_will_remove_from_hass()
    asyncio.run(scenario())


@pytest.mark.parametrize("rename", [False, True])
def test_options_reload_does_not_temporarily_remove_open_task(rename):
    async def scenario():
        engine, entry = fixture()
        listener = Mock()
        engine.aggregate_listeners.append(listener)
        engine.publish_open_tasks()
        if rename:
            entry.options = {"friendly_name": "New name"}
        async def reload(_):
            del engine.entries["a"]
            engine.publish_open_tasks()
            assert engine.aggregate_snapshot()["latest_open"][0] == "Task"
            engine.entries["a"] = entry
            engine.publish_open_tasks()
            assert engine.aggregate_snapshot()["open_names"][0] == 1
            return True
        engine.hass.config_entries.async_reload = reload
        await _options_updated(engine.hass, entry)
        assert not engine.reloading_entries
        assert engine.aggregate_snapshot()["latest_open"][0] == ("New name" if rename else "Task")
        assert all(call.args[0]["open_names"][0] == 1 for call in listener.call_args_list)
    asyncio.run(scenario())


def test_failed_options_reload_releases_held_membership():
    async def scenario():
        engine, entry = fixture()
        async def reload(_):
            del engine.entries["a"]
            raise RuntimeError("reload failed")
        engine.hass.config_entries.async_reload = reload
        with pytest.raises(RuntimeError, match="reload failed"):
            await _options_updated(engine.hass, entry)
        assert not engine.reloading_entries
        assert engine.aggregate_snapshot()["open_names"][0] == 0
    asyncio.run(scenario())
