"""Regression tests for isolated background task evaluation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from custom_components.household_tasks import Engine


def make_engine():
    engine = Engine.__new__(Engine)
    bad = SimpleNamespace(entry_id="bad")
    good = SimpleNamespace(entry_id="good")
    engine.entries = {"bad": bad, "good": good}
    engine.evaluation_errors = set()
    engine.listeners = [Mock()]
    engine.save = AsyncMock()
    return engine, bad, good


def test_failed_task_does_not_block_others_and_recovers(caplog):
    async def scenario():
        engine, bad, good = make_engine()
        engine.evaluate = AsyncMock(side_effect=[ValueError("invalid task"), None,
                                                 ValueError("invalid task"), None,
                                                 None, None])
        await engine._tick(None)
        assert engine.evaluate.await_args_list == [call(bad), call(good)]
        assert engine.evaluation_errors == {"bad"}
        engine.listeners[0].assert_called_once_with("bad")
        await engine._tick(None)
        assert engine.evaluate.await_count == 4
        assert engine.listeners[0].call_count == 1
        assert sum("Unable to evaluate household task bad" in r.message
                   for r in caplog.records) == 1
        await engine._tick(None)
        assert engine.evaluation_errors == set()
        assert engine.evaluate.await_count == 6
        assert engine.listeners[0].call_args_list == [call("bad"), call("bad")]
        engine.save.assert_not_awaited()

    asyncio.run(scenario())


def test_warning_listener_failure_does_not_block_next_task():
    async def scenario():
        engine, bad, good = make_engine()
        engine.listeners.insert(0, Mock(side_effect=RuntimeError("listener failed")))
        engine.evaluate = AsyncMock(side_effect=[ValueError("bad"), None])
        await engine._tick(None)
        assert engine.evaluate.await_args_list == [call(bad), call(good)]
        engine.listeners[1].assert_called_once_with("bad")

    asyncio.run(scenario())


def test_cancellation_is_not_swallowed():
    async def scenario():
        engine, bad, _ = make_engine()
        engine.evaluate = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await engine._evaluate_background(bad)
        assert not engine.evaluation_errors

    asyncio.run(scenario())


def test_removed_entry_is_not_evaluated():
    async def scenario():
        engine, bad, _ = make_engine()
        engine.entries.pop("bad")
        engine.evaluate = AsyncMock()
        await engine._evaluate_background(bad)
        engine.evaluate.assert_not_awaited()

    asyncio.run(scenario())
