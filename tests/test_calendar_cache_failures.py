"""Regression tests for calendar failures, backoff and safe recovery."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import Engine, local_midnight

NOW = datetime(2026, 1, 14, 12, tzinfo=timezone.utc)
CALENDAR = "calendar.test"


def event(start, end, title="Collection"):
    return {"summary": title, "start": start.isoformat(), "end": end.isoformat()}


def response(*events):
    return {CALENDAR: {"events": list(events)}}


def make_engine(rule="manual", offset=0):
    engine = Engine.__new__(Engine)
    engine.history = {}
    engine.lock = asyncio.Lock()
    engine.calendar_cache = {}
    engine.calendar_failures = {}
    engine.save = AsyncMock()
    engine.notify = Mock()
    sources = {CALENDAR: SimpleNamespace(state="off"),
               "binary_sensor.done": SimpleNamespace(state="off")}
    services = SimpleNamespace(has_service=Mock(return_value=True),
                               async_call=AsyncMock(return_value=response()))
    engine.hass = SimpleNamespace(states=SimpleNamespace(get=sources.get),
                                  services=services)
    entry = SimpleNamespace(entry_id="task", data={
        "trigger_type": "calendar", "trigger_entity": CALENDAR,
        "calendar_offset_days": offset, "calendar_match": "Collection",
        "close_type": rule, "close_entity": "binary_sensor.done", "close_state": "on",
    }, options={})
    engine.entries = {entry.entry_id: entry}
    return engine, entry, sources, services


@pytest.mark.parametrize("bad", [
    None, [], {}, {CALENDAR: {}}, {CALENDAR: None},
    {CALENDAR: {"events": None}}, {CALENDAR: {"events": {}}},
    {CALENDAR: {"events": ["bad"]}}, {CALENDAR: {"events": [{}]}},
    {CALENDAR: {"events": [{"start": "invalid"}]}},
    {CALENDAR: {"events": [{"start": NOW.isoformat(), "end": "invalid"}]}},
    response(event(NOW, NOW - timedelta(minutes=1))),
    response({**event(NOW, NOW + timedelta(hours=1)), "summary": []}),
])
def test_invalid_response_is_not_treated_as_empty_calendar(bad):
    async def scenario():
        engine, entry, _, services = make_engine()
        services.async_call.return_value = bad
        result = await engine.calculate_calendar(entry, NOW)
        assert result == (None, None, False, None)
        assert CALENDAR not in engine.calendar_cache
        assert engine.calendar_failures[CALENDAR][3] == "invalid calendar response"
        assert any("invalid calendar response" in w for w in engine.entity_warnings(entry))

    asyncio.run(scenario())


def test_retries_are_shared_and_backoff_is_bounded():
    async def scenario():
        engine, entry, _, services = make_engine()
        sibling = SimpleNamespace(entry_id="sibling", data=dict(entry.data), options={})
        engine.entries["sibling"] = sibling
        services.async_call.side_effect = RuntimeError("offline")
        now = NOW
        for index, delay in enumerate([1, 2, 4, 8, 15, 15], start=1):
            assert await engine.calendar_events(CALENDAR, now) is None
            assert services.async_call.await_count == index
            assert engine.calendar_failures[CALENDAR][1] == now + timedelta(minutes=delay)
            await engine.calculate_calendar(sibling, now + timedelta(seconds=1))
            await engine.calculate_calendar(entry, now + timedelta(seconds=2))
            assert services.async_call.await_count == index
            now += timedelta(minutes=delay)
        services.async_call.side_effect = None
        assert await engine.calendar_events(CALENDAR, now) == []
        assert CALENDAR not in engine.calendar_failures
        assert not engine.entity_warnings(entry)
        assert services.async_call.await_count == 7

    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", ["unavailable", "unknown", "", None])
def test_unavailable_entity_does_not_query_provider(unavailable):
    async def scenario():
        engine, entry, sources, services = make_engine()
        if unavailable is None:
            sources.pop(CALENDAR)
        else:
            sources[CALENDAR].state = unavailable
        assert await engine.calendar_events(CALENDAR, NOW) is None
        services.async_call.assert_not_awaited()
        assert engine.calendar_failures[CALENDAR][3] == "calendar entity unavailable"
        sources[CALENDAR] = SimpleNamespace(state="off")
        assert await engine.calendar_events(CALENDAR, NOW + timedelta(minutes=1)) == []
        assert not engine.entity_warnings(entry)

    asyncio.run(scenario())


def test_service_unavailable_and_timeout_have_retry_policy():
    async def scenario():
        engine, _, _, services = make_engine()
        services.has_service.return_value = False
        await engine.calendar_events(CALENDAR, NOW)
        assert engine.calendar_failures[CALENDAR][3] == "calendar service unavailable"
        services.async_call.assert_not_awaited()
        services.has_service.return_value = True
        services.async_call.side_effect = TimeoutError()
        await engine.calendar_events(CALENDAR, NOW + timedelta(minutes=1))
        assert engine.calendar_failures[CALENDAR][3] == "calendar request failed"
        assert engine.calendar_failures[CALENDAR][1] == NOW + timedelta(minutes=3)

    asyncio.run(scenario())


def test_recent_cached_reminder_is_display_only_then_expires():
    async def scenario():
        engine, entry, _, services = make_engine(offset=1)
        midnight = local_midnight(NOW) + timedelta(days=1)
        initial = midnight - timedelta(minutes=5)
        item = event(midnight + timedelta(days=1, hours=12),
                     midnight + timedelta(days=1, hours=13))
        # Use an identical query horizon on both sides of midnight, so this
        # exercises stale data rather than insufficient date-range coverage.
        cache_end = engine.calendar_window_end(midnight, 1)
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = initial
            services.async_call.return_value = response(item)
            await engine.evaluate(entry)
            state = engine.state(entry)
            assert not state["open"]
            engine.calendar_cache[CALENDAR] = (initial, [item], cache_end)
            history = deepcopy(state["events"])
            writes = engine.save.await_count
            services.async_call.side_effect = RuntimeError("offline")
            clock.return_value = midnight
            await engine.evaluate(entry)
            assert state["due"] == midnight.isoformat()
            assert not state["open"] and state["events"] == history
            assert state["entity_warnings"]
            assert engine.save.await_count == writes
            clock.return_value = initial + timedelta(minutes=30)
            await engine.evaluate(entry)
            assert state["due"] is None and not state["open"]
            assert CALENDAR not in engine.calendar_cache
            services.async_call.side_effect = None
            clock.return_value += timedelta(minutes=15)
            await engine.evaluate(entry)
            assert state["open"]
            assert state["entity_warnings"] == []
            assert state["events"][-1]["action"] == "opened"

    asyncio.run(scenario())


@pytest.mark.parametrize("rule", ["calendar_end", "manual_or_calendar_end"])
def test_calendar_end_completion_pauses_during_failure(rule):
    async def scenario():
        engine, entry, _, services = make_engine(rule)
        end = NOW + timedelta(minutes=5)
        services.async_call.return_value = response(event(NOW, end))
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = NOW
            await engine.evaluate(entry)
            state = engine.state(entry)
            original = deepcopy(state)
            writes = engine.save.await_count
            services.async_call.side_effect = RuntimeError("offline")
            clock.return_value = end
            await engine.evaluate(entry)
            assert state["open"] and state["events"] == original["events"]
            assert state["calendar_end"] == original["calendar_end"]
            assert state["due"] == original["due"]
            assert state["entity_warnings"]
            assert engine.save.await_count == writes
            # Successful refresh shows the event's end was postponed.
            postponed = end + timedelta(hours=1)
            services.async_call.side_effect = None
            services.async_call.return_value = response(event(NOW, postponed))
            clock.return_value = end + timedelta(minutes=1)
            await engine.evaluate(entry)
            assert state["open"] and state["entity_warnings"] == []
            assert state["calendar_end"] == postponed.isoformat()
            clock.return_value = postponed
            await engine.evaluate(entry)
            assert not state["open"]
            assert state["events"][-1]["action"] == "auto_completed"
            assert len(state["events"]) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("rule", ["manual_or_calendar_end", "entity_state"])
def test_manual_and_independent_entity_completion_remain_available(rule):
    async def scenario():
        engine, entry, sources, services = make_engine(rule)
        services.async_call.return_value = response(event(NOW, NOW + timedelta(hours=1)))
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = NOW
            await engine.evaluate(entry)
            state = engine.state(entry)
            clock.return_value = NOW + timedelta(minutes=5)
            services.async_call.side_effect = RuntimeError("offline")
            await engine.evaluate(entry)
            assert state["open"] and state["entity_warnings"]
            if rule == "manual_or_calendar_end":
                await engine.act("task", "complete")
                assert state["events"][-1]["action"] == "completed"
            else:
                sources["binary_sensor.done"].state = "on"
                await engine.evaluate(entry)
                assert state["events"][-1]["action"] == "auto_completed"
            assert not state["open"]

    asyncio.run(scenario())


def test_unavailable_state_invalidates_trust_even_with_fresh_cache():
    async def scenario():
        engine, entry, sources, services = make_engine("calendar_end")
        end = NOW + timedelta(minutes=2)
        services.async_call.return_value = response(event(NOW, end))
        with patch("custom_components.household_tasks.dt_util.now") as clock:
            clock.return_value = NOW
            await engine.evaluate(entry)
            sources[CALENDAR].state = "unavailable"
            clock.return_value = end
            await engine.evaluate(entry)
            assert engine.state(entry)["open"]
            assert engine.state(entry)["entity_warnings"]
            assert services.async_call.await_count == 1

    asyncio.run(scenario())


def test_cancellation_propagates_without_creating_failure():
    async def scenario():
        engine, _, _, services = make_engine()
        services.async_call.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await engine.calendar_events(CALENDAR, NOW)
        assert not engine.calendar_failures

    asyncio.run(scenario())
