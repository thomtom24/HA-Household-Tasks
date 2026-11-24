"""Targeted subscription routing and configuration lifecycle regressions."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED

from custom_components.household_tasks import Engine, async_setup_entry, async_unload_entry
from custom_components.household_tasks.const import DOMAIN


def make_engine():
    engine = Engine.__new__(Engine)
    engine.history = {}
    engine.entries = {}
    engine.entity_dependencies = {}
    engine.entity_subscriptions = {}
    engine.calendar_cache = {}
    engine.evaluation_errors = set()
    engine.publish_open_tasks = Mock()
    engine.evaluate = AsyncMock()
    engine._evaluate_background = AsyncMock()
    jobs = []

    def enqueue(coro):
        jobs.append(coro)

    engine.hass = SimpleNamespace(
        async_create_task=Mock(side_effect=enqueue),
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        ),
    )
    engine.hass.data = {DOMAIN: engine}
    # Routing tests isolate the dispatcher; coalescing has dedicated tests.
    engine.request_evaluation = Mock(side_effect=lambda entry: enqueue(engine._evaluate_background(entry)))
    return engine, jobs


def task(entry_id, **data):
    return SimpleNamespace(entry_id=entry_id, data=data, options={},
                           async_on_unload=Mock(), add_update_listener=Mock())


def event(entity_id):
    return SimpleNamespace(data={"entity_id": entity_id})


@pytest.mark.parametrize("kind", ["calendar", "date_entity", "numeric", "state"])
def test_entity_opening_rules_are_subscribed(kind):
    engine, _ = make_engine()
    entry = task("a", trigger_type=kind, trigger_entity="sensor.source")
    assert engine.active_dependencies(entry) == {"sensor.source"}


@pytest.mark.parametrize("kind", ["completion", "weekly", "monthly", "yearly"])
def test_schedule_rules_ignore_stale_entity_fields(kind):
    engine, _ = make_engine()
    entry = task("a", trigger_type=kind, trigger_entity="sensor.old",
                 close_type="manual", close_entity="sensor.old_close")
    assert engine.active_dependencies(entry) == set()


@pytest.mark.parametrize("kind", ["entity_state", "entity_numeric",
                                 "manual_or_entity_state", "manual_or_entity_numeric"])
def test_entity_closing_rules_are_subscribed(kind):
    engine, _ = make_engine()
    entry = task("a", close_type=kind, close_entity="binary_sensor.done")
    assert engine.active_dependencies(entry) == {"binary_sensor.done"}


@pytest.mark.parametrize("bad", [None, "", "not_an_entity", [], {}, 42])
def test_invalid_legacy_dependencies_not_subscribed(bad):
    engine, _ = make_engine()
    entry = task("a", trigger_type="numeric", trigger_entity=bad)
    assert engine.active_dependencies(entry) == set()


def test_one_listener_per_entity_shared_across_tasks_and_rules():
    engine, _ = make_engine()
    engine.entries = {
        "a": task("a", trigger_type="numeric", trigger_entity="sensor.x",
                  close_type="entity_numeric", close_entity="sensor.x"),
        "b": task("b", trigger_type="state", trigger_entity="sensor.x"),
    }
    unsubscribe = Mock()
    with patch("custom_components.household_tasks.async_track_state_change_event",
               return_value=unsubscribe) as track:
        engine.refresh_entity_subscriptions()
        engine.refresh_entity_subscriptions()
        track.assert_called_once_with(engine.hass, "sensor.x", engine._state_event)
        assert engine.entity_dependencies == {"sensor.x": {"a", "b"}}
        del engine.entries["a"]
        engine.refresh_entity_subscriptions()
        unsubscribe.assert_not_called()
        del engine.entries["b"]
        engine.refresh_entity_subscriptions()
        unsubscribe.assert_called_once()
        assert not engine.entity_dependencies and not engine.entity_subscriptions


def test_rule_change_replaces_only_obsolete_subscriptions():
    engine, _ = make_engine()
    a = task("a", trigger_type="numeric", trigger_entity="sensor.old")
    b = task("b", trigger_type="numeric", trigger_entity="sensor.shared")
    engine.entries = {"a": a, "b": b}
    unsubscribers = {}
    def register(hass, entity_id, callback):
        unsubscribers[entity_id] = Mock()
        return unsubscribers[entity_id]
    with patch("custom_components.household_tasks.async_track_state_change_event",
               side_effect=register) as track:
        engine.refresh_entity_subscriptions()
        a.options = {"trigger_entity": "sensor.new"}
        engine.refresh_entity_subscriptions()
        assert track.call_count == 3
        unsubscribers["sensor.old"].assert_called_once()
        unsubscribers["sensor.shared"].assert_not_called()
        assert engine.entity_dependencies == {"sensor.new": {"a"}, "sensor.shared": {"b"}}


def test_event_routes_only_to_affected_tasks_and_invalidates_calendar_once():
    async def scenario():
        engine, jobs = make_engine()
        a = task("a", trigger_type="calendar", trigger_entity="calendar.x")
        b = task("b", trigger_type="numeric", trigger_entity="sensor.other",
                 close_type="entity_state", close_entity="calendar.x")
        c = task("c", trigger_type="calendar", trigger_entity="calendar.other")
        engine.entries = {"a": a, "b": b, "c": c}
        engine.calendar_cache = {"calendar.x": "cached", "calendar.other": "untouched"}
        with patch("custom_components.household_tasks.async_track_state_change_event", return_value=Mock()):
            engine.refresh_entity_subscriptions()
        engine._state_event(event("sensor.unrelated"))
        assert not jobs
        engine._state_event(event("calendar.x"))
        assert engine.calendar_cache == {"calendar.other": "untouched"}
        assert len(jobs) == 2
        await asyncio.gather(*jobs)
        assert {call.args[0].entry_id for call in engine._evaluate_background.await_args_list} == {"a", "b"}
    asyncio.run(scenario())


def test_stale_event_after_change_or_unload_is_ignored():
    engine, jobs = make_engine()
    a = task("a", trigger_type="numeric", trigger_entity="sensor.old")
    b = task("b", trigger_type="numeric", trigger_entity="sensor.old")
    engine.entries = {"a": a, "b": b}
    engine.entity_dependencies = {"sensor.old": {"a", "b"}}
    a.options = {"trigger_entity": "sensor.new"}
    del engine.entries["b"]
    engine._state_event(event("sensor.old"))
    assert not jobs


def test_setup_and_unload_refresh_subscriptions():
    async def scenario():
        engine, _ = make_engine()
        entry = task("a", trigger_type="numeric", trigger_entity="sensor.source")
        unsub = Mock()
        with patch("custom_components.household_tasks.async_track_state_change_event", return_value=unsub):
            assert await async_setup_entry(engine.hass, entry)
            assert engine.entity_dependencies == {"sensor.source": {"a"}}
            engine.hass.config_entries.async_unload_platforms.return_value = False
            assert not await async_unload_entry(engine.hass, entry)
            unsub.assert_not_called()
            engine.hass.config_entries.async_unload_platforms.return_value = True
            assert await async_unload_entry(engine.hass, entry)
            unsub.assert_called_once()
            assert not engine.entity_dependencies
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["forward", "evaluate"])
def test_failed_setup_removes_targeted_listeners(stage):
    async def scenario():
        engine, _ = make_engine()
        entry = task("a", trigger_type="numeric", trigger_entity="sensor.source")
        operation = engine.hass.config_entries.async_forward_entry_setups if stage == "forward" else engine.evaluate
        operation.side_effect = RuntimeError("setup failed")
        unsub = Mock()
        with patch("custom_components.household_tasks.async_track_state_change_event", return_value=unsub):
            with pytest.raises(RuntimeError, match="setup failed"):
                await async_setup_entry(engine.hass, entry)
            unsub.assert_called_once()
            assert not engine.entity_dependencies
            assert not engine.entries
    asyncio.run(scenario())


def test_engine_start_does_not_register_global_state_listener():
    async def scenario():
        engine, _ = make_engine()
        engine.store = SimpleNamespace(async_load=AsyncMock(return_value={}))
        engine.hass.bus = SimpleNamespace(async_listen=Mock())
        engine.hass.services = SimpleNamespace(async_register=Mock())
        with patch("custom_components.household_tasks.async_track_time_interval"):
            await engine.start()
        assert all(call.args[0] != EVENT_STATE_CHANGED for call in engine.hass.bus.async_listen.call_args_list)
    asyncio.run(scenario())
