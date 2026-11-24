"""Regression tests for entry cleanup, shared listeners and pending writes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import (
    Engine, async_setup_entry, async_unload_entry, async_remove_entry,
)
from custom_components.household_tasks.const import DOMAIN, PLATFORMS


def fixture():
    hass = SimpleNamespace(
        data={}, bus=SimpleNamespace(async_listen=Mock(return_value=Mock())),
        services=SimpleNamespace(async_register=Mock()),
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
            async_update_entry=Mock(),
        ),
        async_create_task=lambda coro, **kw: asyncio.create_task(coro),
    )
    with patch("custom_components.household_tasks.Store"):
        engine = Engine(hass)
    hass.data[DOMAIN] = engine
    engine.evaluate = AsyncMock()
    engine.save = AsyncMock()
    engine.publish_open_tasks = Mock()
    return hass, engine


def task(key):
    return SimpleNamespace(entry_id=key, data={}, options={},
                           async_on_unload=Mock(), add_update_listener=Mock())


def test_shared_listeners_start_once_stop_on_last_unload_and_restart():
    async def scenario():
        hass, engine = fixture()
        a, b = task("a"), task("b")
        first_timer, second_timer = Mock(), Mock()
        first_registry, second_registry = Mock(), Mock()
        hass.bus.async_listen.side_effect = [first_registry, second_registry]
        engine.store.async_load = AsyncMock(return_value={})
        with patch("custom_components.household_tasks.async_track_time_interval",
                   side_effect=[first_timer, second_timer]) as timer:
            await engine.start()
            timer.assert_not_called()
            hass.bus.async_listen.assert_not_called()
            await async_setup_entry(hass, a)
            await async_setup_entry(hass, b)
            assert timer.call_count == 1
            assert hass.bus.async_listen.call_count == 1
            await async_unload_entry(hass, a)
            first_timer.assert_not_called()
            first_registry.assert_not_called()
            await async_unload_entry(hass, b)
            first_timer.assert_called_once()
            first_registry.assert_called_once()
            assert engine.unsub_timer is None and engine.unsub_registry is None
            assert not engine.entries
            await async_setup_entry(hass, a)
            assert timer.call_count == 2
            assert engine.unsub_timer is second_timer
            assert engine.unsub_registry is second_registry
            assert hass.services.async_register.call_count == 2
    asyncio.run(scenario())


def test_unload_drains_running_worker_without_cancelling_it():
    async def scenario():
        hass, engine = fixture()
        entry = task("a")
        engine.entries["a"] = entry
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def evaluate(current):
            calls.append(current)
            async with engine.lock:
                started.set()
                await release.wait()
        engine.evaluate = evaluate
        worker = engine.request_evaluation(entry)
        await started.wait()
        engine.request_evaluation(entry)
        unload = asyncio.create_task(async_unload_entry(hass, entry))
        await asyncio.sleep(0)
        assert "a" not in engine.entries
        assert not unload.done()
        assert engine.request_evaluation(entry) is None
        assert not worker.cancelled()
        release.set()
        assert await unload
        assert calls == [entry]
        assert not engine.evaluation_jobs
        assert not worker.cancelled()
    asyncio.run(scenario())


def test_unload_waits_for_direct_history_mutation():
    async def scenario():
        hass, engine = fixture()
        entry = task("a")
        engine.entries["a"] = entry
        async with engine.lock:
            unload = asyncio.create_task(async_unload_entry(hass, entry))
            await asyncio.sleep(0)
            assert not unload.done()
            assert not engine.entries
        assert await unload
    asyncio.run(scenario())


def test_failed_unload_retains_entry_and_runtime_listeners():
    async def scenario():
        hass, engine = fixture()
        entry = task("a")
        engine.entries["a"] = entry
        engine.unsub_timer = Mock()
        engine.unsub_registry = Mock()
        hass.config_entries.async_unload_platforms.return_value = False
        assert not await async_unload_entry(hass, entry)
        assert engine.entries["a"] is entry
        engine.unsub_timer.assert_not_called()
        engine.unsub_registry.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["forward", "evaluate"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_setup_failure_attempts_platform_cleanup_and_preserves_error(stage, cleanup_failure):
    async def scenario():
        hass, engine = fixture()
        entry = task("a")
        operation = hass.config_entries.async_forward_entry_setups if stage == "forward" else engine.evaluate
        operation.side_effect = RuntimeError("original setup error")
        if cleanup_failure:
            hass.config_entries.async_unload_platforms.side_effect = RuntimeError("cleanup error")
        with patch("custom_components.household_tasks.async_track_time_interval", return_value=Mock()):
            with pytest.raises(RuntimeError, match="original setup error"):
                await async_setup_entry(hass, entry)
        hass.config_entries.async_unload_platforms.assert_awaited_once_with(entry, PLATFORMS)
        assert not engine.entries
        assert engine.unsub_timer is None and engine.unsub_registry is None
        entry.add_update_listener.assert_not_called()
    asyncio.run(scenario())


def test_removal_waits_for_lock_and_preserves_other_history():
    async def scenario():
        hass, engine = fixture()
        entry = task("a")
        engine.history = {"a": {"events": []}, "b": {"open": True}}
        async with engine.lock:
            removal = asyncio.create_task(async_remove_entry(hass, entry))
            await asyncio.sleep(0)
            assert "a" in engine.history
            engine.save.assert_not_awaited()
        await removal
        assert engine.history == {"b": {"open": True}}
        engine.save.assert_awaited_once()
    asyncio.run(scenario())


def test_stale_detach_and_removal_do_not_touch_replacement():
    async def scenario():
        hass, engine = fixture()
        old, new = task("a"), task("a")
        engine.entries["a"] = new
        engine.history["a"] = {"open": True}
        await engine.detach_entry(old)
        await async_remove_entry(hass, old)
        assert engine.entries["a"] is new
        assert engine.history["a"] == {"open": True}
        engine.save.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("changed", ["removed", "new_reference"])
def test_queued_registry_rename_rechecks_entry_and_config(changed):
    async def scenario():
        hass, engine = fixture()
        entry = task("a")
        entry.data = {"trigger_type": "numeric", "trigger_entity": "sensor.old"}
        engine.entries["a"] = entry
        async with engine.lock:
            rename = asyncio.create_task(engine._apply_entity_rename("sensor.old", "sensor.renamed"))
            await asyncio.sleep(0)
            if changed == "removed":
                del engine.entries["a"]
            else:
                entry.options = {"trigger_entity": "sensor.other"}
        await rename
        hass.config_entries.async_update_entry.assert_not_called()
        assert not engine.history
        engine.save.assert_not_awaited()
    asyncio.run(scenario())
