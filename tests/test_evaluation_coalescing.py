"""Concurrency regressions for bounded background evaluation workers."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.household_tasks import Engine


def make_engine():
    engine = Engine.__new__(Engine)
    engine.cyclic_entries = set()
    entry = SimpleNamespace(entry_id="task")
    engine.entries = {"task": entry}
    engine.history = {}
    engine.evaluation_errors = set()
    engine.evaluation_jobs = {}
    engine.tick_running = False
    engine.listeners = []
    engine.lock = asyncio.Lock()
    engine.save = AsyncMock()
    engine.notify = Mock()
    engine.hass = SimpleNamespace(async_create_task=Mock(
        side_effect=lambda coro, **kwargs: asyncio.create_task(coro)))
    return engine, entry


def test_burst_before_worker_starts_needs_only_one_evaluation():
    async def scenario():
        engine, entry = make_engine()
        engine.evaluate = AsyncMock()
        worker = engine.request_evaluation(entry)
        for _ in range(100):
            assert engine.request_evaluation(entry) is worker
        await worker
        engine.evaluate.assert_awaited_once_with(entry)
        assert not engine.evaluation_jobs
        engine.hass.async_create_task.assert_called_once()
    asyncio.run(scenario())


def test_burst_during_evaluation_requests_one_followup():
    async def scenario():
        engine, entry = make_engine()
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def evaluate(current):
            calls.append(current)
            if len(calls) == 1:
                started.set()
                await release.wait()
        engine.evaluate = evaluate
        worker = engine.request_evaluation(entry)
        await started.wait()
        for _ in range(100):
            assert engine.request_evaluation(entry) is worker
        release.set()
        await worker
        assert calls == [entry, entry]
        assert not engine.evaluation_jobs
    asyncio.run(scenario())


def test_overlapping_ticks_share_event_worker_and_do_not_queue_sweeps():
    async def scenario():
        engine, entry = make_engine()
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def evaluate(current):
            calls.append(current)
            started.set()
            await release.wait()
        engine.evaluate = evaluate
        worker = engine.request_evaluation(entry)
        await started.wait()
        tick = asyncio.create_task(engine._tick(None))
        await asyncio.sleep(0)
        assert engine.tick_running
        for _ in range(10):
            await engine._tick(None)
        assert engine.hass.async_create_task.call_count == 1
        release.set()
        await tick
        await worker
        assert calls == [entry, entry]
        assert not engine.tick_running and not engine.evaluation_jobs
    asyncio.run(scenario())


def test_cancelled_tick_does_not_cancel_joined_worker():
    async def scenario():
        engine, entry = make_engine()
        started, release = asyncio.Event(), asyncio.Event()
        async def evaluate(current):
            started.set()
            await release.wait()
        engine.evaluate = evaluate
        tick = asyncio.create_task(engine._tick(None))
        await started.wait()
        worker = engine.evaluation_jobs["task"].task
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick
        assert not worker.done()
        assert not engine.tick_running
        release.set()
        await worker
        assert not engine.evaluation_jobs
    asyncio.run(scenario())


@pytest.mark.parametrize("before_start", [True, False])
def test_cancelled_worker_can_be_rescheduled(before_start):
    async def scenario():
        engine, entry = make_engine()
        started = asyncio.Event()
        async def blocked(current):
            started.set()
            await asyncio.Event().wait()
        engine.evaluate = blocked
        worker = engine.request_evaluation(entry)
        if not before_start:
            await started.wait()
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        engine.evaluate = AsyncMock()
        await engine.request_evaluation(entry)
        engine.evaluate.assert_awaited_once_with(entry)
        assert not engine.evaluation_jobs
    asyncio.run(scenario())


def test_replaced_entry_uses_same_worker_for_followup_and_rejects_old_requests():
    async def scenario():
        engine, entry = make_engine()
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def evaluate(current):
            calls.append(current)
            if current is entry:
                started.set()
                await release.wait()
        engine.evaluate = evaluate
        worker = engine.request_evaluation(entry)
        await started.wait()
        replacement = SimpleNamespace(entry_id="task")
        engine.entries["task"] = replacement
        assert engine.request_evaluation(entry) is None
        assert engine.request_evaluation(replacement) is worker
        release.set()
        await worker
        assert calls == [entry, replacement]
        assert not engine.evaluation_jobs
    asyncio.run(scenario())


def test_removed_entry_waiting_for_lock_is_not_evaluated_or_recreated():
    async def scenario():
        engine, entry = make_engine()
        async with engine.lock:
            worker = engine.request_evaluation(entry)
            await asyncio.sleep(0)
            del engine.entries["task"]
        await worker
        assert not engine.history
        engine.save.assert_not_awaited()
        engine.notify.assert_not_called()
    asyncio.run(scenario())


def test_manual_action_waiting_for_lock_cannot_mutate_replaced_entry():
    async def scenario():
        engine, entry = make_engine()
        async with engine.lock:
            action = asyncio.create_task(engine.act("task", "complete"))
            await asyncio.sleep(0)
            engine.entries["task"] = SimpleNamespace(entry_id="task")
        with pytest.raises(ValueError, match="changed or unloaded"):
            await action
        assert not engine.history
        engine.save.assert_not_awaited()
    asyncio.run(scenario())


def test_unloaded_calendar_evaluation_rolls_back_in_memory_reconciliation():
    async def scenario():
        engine, entry = make_engine()
        started, release = asyncio.Event(), asyncio.Event()
        engine.history["task"] = {"open": False, "events": []}
        engine.conf = lambda _: {"trigger_type": "calendar"}
        def reconcile(current, state, now):
            state["opening_signature"] = {"kind": "calendar"}
            return True
        engine.reconcile_opening_rule = reconcile
        async def calendar(current, now):
            started.set()
            await release.wait()
            return None, None, False, None
        engine.calculate_calendar = calendar
        worker = engine.request_evaluation(entry)
        await started.wait()
        del engine.entries["task"]
        release.set()
        await worker
        assert engine.history["task"] == {"open": False, "events": []}
        engine.save.assert_not_awaited()
        engine.notify.assert_not_called()
    asyncio.run(scenario())
