"""Compatibility and lifecycle tests for domain-level aggregate sensors."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.household_tasks import Engine, async_setup
from custom_components.household_tasks.const import DOMAIN
from custom_components.household_tasks.sensor import AggregateTaskSensor, TaskStatusSensor, async_setup_platform, async_setup_entry


def engine_with_tasks(*tasks):
    engine = Engine.__new__(Engine)
    engine.invalid_history = set()
    engine.storage_dirty = False
    engine.storage_failed = False
    engine.storage_retry = None
    engine.reloading_entries = {}
    engine.last_aggregate_names = {}
    engine.calendar_requests = {}
    engine.cyclic_entries = set()
    engine.entries = {}
    engine.history = {}
    engine.aggregate_listeners = []
    engine.listeners = []
    for entry_id, name, opened in tasks:
        entry = SimpleNamespace(entry_id=entry_id, title=name, data={}, options={})
        engine.entries[entry_id] = entry
        engine.history[entry_id] = {"open": opened is not None, "last_opened": opened}
    return engine


def test_empty_snapshot_preserves_legacy_contract():
    engine = engine_with_tasks()
    assert engine.aggregate_snapshot() == {
        "open_names": (0, {"names": [], "tasks": [], "count": 0,
                           "announcement": "There are no outstanding household tasks."}),
        "latest_open": ("none", {"task_name": None, "opened_at": None, "open_count": 0}),
    }


def test_snapshot_sorting_and_latest_order_independent_of_name():
    engine = engine_with_tasks(("a", "Zebra", "2026-11-24T10:00:00+00:00"),
                               ("b", "apple", "2026-11-24T09:00:00+00:00"),
                               ("c", "Closed", None))
    snapshot = engine.aggregate_snapshot()
    assert snapshot["open_names"] == (2, {
        "names": ["apple", "Zebra"], "tasks": ["apple", "Zebra"], "count": 2,
        "announcement": "2 household tasks are outstanding: apple, and Zebra."})
    assert snapshot["latest_open"] == ("Zebra", {
        "task_name": "Zebra", "opened_at": "2026-11-24T10:00:00+00:00", "open_count": 2})
    engine.history["a"]["open"] = False
    snapshot = engine.aggregate_snapshot()
    assert snapshot["latest_open"][0] == "apple"
    assert snapshot["open_names"][1]["announcement"] == "One household task is outstanding: apple."


def test_equal_opening_times_preserve_entry_id_tiebreaker_and_duplicate_names():
    engine = engine_with_tasks(("a", "Same", "2026-11-24T10:00:00+00:00"),
                               ("b", "Same", "2026-11-24T10:00:00+00:00"))
    assert engine.aggregate_snapshot()["open_names"][1]["names"] == ["Same", "Same"]
    engine.entries["b"].options = {"friendly_name": "Renamed"}
    assert engine.aggregate_snapshot()["latest_open"][0] == "Renamed"


def test_discovery_creates_two_standard_entities_with_original_ids():
    async def scenario():
        engine = engine_with_tasks()
        hass = SimpleNamespace(data={DOMAIN: engine})
        add = Mock()
        await async_setup_platform(hass, {}, add, None)
        add.assert_not_called()
        await async_setup_platform(hass, {}, add, {"aggregate_sensors": True})
        first, second = add.call_args.args[0]
        assert isinstance(first, AggregateTaskSensor)
        assert isinstance(second, AggregateTaskSensor)
        assert first.entity_id == "sensor.household_tasks_open_names"
        assert second.entity_id == "sensor.household_tasks_latest_open"
        assert first.unique_id == "household_tasks_open_names"
        assert second.unique_id == "household_tasks_latest_open"
        assert first.name == "Household Tasks Open List"
        assert second.name == "Household Tasks Latest Open"
        assert first.should_poll is False and second.should_poll is False
        assert first.native_value == 0 and second.native_value == "none"
    asyncio.run(scenario())


def test_individual_task_platform_does_not_add_aggregate_entities():
    async def scenario():
        engine = engine_with_tasks(("a", "Task", None))
        add = Mock()
        await async_setup_entry(SimpleNamespace(data={DOMAIN: engine}), engine.entries["a"], add)
        entities = add.call_args.args[0]
        assert len(entities) == 1
        assert isinstance(entities[0], TaskStatusSensor)
        assert entities[0].unique_id == "a_status"
    asyncio.run(scenario())


def test_sensor_updates_only_on_changed_payload_and_removes_listener():
    async def scenario():
        engine = engine_with_tasks(("a", "Task", None))
        sensor = AggregateTaskSensor(engine, "open_names", "List", "mdi:clipboard")
        sensor.async_write_ha_state = Mock()
        await sensor.async_added_to_hass()
        assert len(engine.aggregate_listeners) == 1
        engine.publish_open_tasks()
        sensor.async_write_ha_state.assert_not_called()
        engine.history["a"]["open"] = True
        engine.publish_open_tasks()
        assert sensor.native_value == 1
        assert sensor.extra_state_attributes["names"] == ["Task"]
        sensor.async_write_ha_state.assert_called_once()
        engine.publish_open_tasks()
        sensor.async_write_ha_state.assert_called_once()
        engine.entries.clear()
        engine.publish_open_tasks()
        assert sensor.native_value == 0
        assert sensor.async_write_ha_state.call_count == 2
        await sensor.async_will_remove_from_hass()
        assert engine.aggregate_listeners == []
    asyncio.run(scenario())


def test_listener_refreshes_snapshot_before_first_write():
    async def scenario():
        engine = engine_with_tasks(("a", "Task", None))
        sensor = AggregateTaskSensor(engine, "latest_open", "Latest", "mdi:clipboard")
        engine.history["a"]["open"] = True
        engine.history["a"]["last_opened"] = "2026-11-24T10:00:00+00:00"
        await sensor.async_added_to_hass()
        assert sensor.native_value == "Task"
        assert sensor.extra_state_attributes["open_count"] == 1
    asyncio.run(scenario())


def test_aggregate_listener_failure_does_not_block_other_sensor():
    engine = engine_with_tasks()
    failing = Mock(side_effect=RuntimeError("failure"))
    healthy = Mock()
    engine.aggregate_listeners = [failing, healthy]
    engine.publish_open_tasks()
    healthy.assert_called_once_with(engine.aggregate_snapshot())


def test_domain_setup_defers_discovery_until_component_loaded():
    async def scenario():
        hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock()))
        engine = Mock()
        engine.start = AsyncMock()
        config = {}
        with patch("custom_components.household_tasks.Engine", return_value=engine), \
             patch("custom_components.household_tasks.async_when_setup") as when_setup, \
             patch("custom_components.household_tasks.discovery.async_load_platform", new_callable=AsyncMock) as discover:
            assert await async_setup(hass, config)
            assert hass.data[DOMAIN] is engine
            engine.start.assert_awaited_once()
            discover.assert_not_awaited()
            assert when_setup.call_args.args[:2] == (hass, DOMAIN)
            await when_setup.call_args.args[2](hass, DOMAIN)
            discover.assert_awaited_once_with(hass, "sensor", DOMAIN, {"aggregate_sensors": True}, config)
    asyncio.run(scenario())
