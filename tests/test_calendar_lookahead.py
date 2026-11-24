"""Regression tests for offset-aware calendar retrieval and cache coverage."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.household_tasks import Engine, local_midnight

NOW = datetime(2026, 1, 14, 12, tzinfo=timezone.utc)
CALENDAR = "calendar.test"


def event(days, title="Collection"):
    start = local_midnight(NOW) + timedelta(days=days, hours=23)
    return {"summary": title, "start": start.isoformat(),
            "end": (start + timedelta(minutes=30)).isoformat()}


def make_entry(name, offset=None, **settings):
    data = {"trigger_type": "calendar", "trigger_entity": CALENDAR,
            "calendar_match": "Collection", **settings}
    if offset is not None:
        data["calendar_offset_days"] = offset
    return SimpleNamespace(entry_id=name, data=data, options={})


def make_engine(*entries, events=()):
    engine = Engine.__new__(Engine)
    engine.cyclic_entries = set()
    engine.history = {}
    engine.entries = {entry.entry_id: entry for entry in entries}
    engine.calendar_cache = {}
    engine.calendar_failures = {}

    async def get_events(domain, service, data, **kwargs):
        end = datetime.fromisoformat(data["end_date_time"])
        return {data["entity_id"]: {"events": [
            item for item in events
            if datetime.fromisoformat(item["start"]) < end]}}

    engine.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: SimpleNamespace(state="off")),
        services=SimpleNamespace(
        has_service=Mock(return_value=True),
        async_call=AsyncMock(side_effect=get_events)))
    return engine


@pytest.mark.parametrize("offset", [0, 1, 60, 90, 91, 180, 365])
def test_event_due_today_including_late_evening_is_found(offset):
    async def scenario():
        entry = make_entry("task", offset)
        engine = make_engine(entry, events=[event(offset)])
        due, token, active, end = await engine.calculate_calendar(entry, NOW)
        assert due == local_midnight(NOW)
        assert token.startswith("calendar:")
        assert active is True
        assert end is not None
        request = engine.hass.services.async_call.call_args.args[2]
        query_end = datetime.fromisoformat(request["end_date_time"])
        assert query_end == local_midnight(NOW) + timedelta(days=max(90, offset + 30) + 1)
        assert query_end > datetime.fromisoformat(event(offset)["start"])

    asyncio.run(scenario())


def test_maximum_offset_also_finds_upcoming_reminder():
    async def scenario():
        entry = make_entry("long", 365)
        engine = make_engine(entry, events=[event(395)])
        due, token, active, end = await engine.calculate_calendar(entry, NOW)
        assert due == local_midnight(NOW) + timedelta(days=30)
        assert token and end
        assert active is False

    asyncio.run(scenario())


@pytest.mark.parametrize("long_first", [False, True])
def test_shared_calendar_fetches_once_and_limits_each_tasks_window(long_first):
    async def scenario():
        short = make_entry("short", 1)
        long = make_entry("long", 365)
        engine = make_engine(short, long, events=[event(365)])
        for entry in ([long, short] if long_first else [short, long]):
            due, token, active, end = await engine.calculate_calendar(entry, NOW)
            if entry is long:
                assert due == local_midnight(NOW) and active
            else:
                assert (due, token, active, end) == (None, None, False, None)
        assert engine.hass.services.async_call.await_count == 1

    asyncio.run(scenario())


def test_new_long_offset_expands_existing_cache():
    async def scenario():
        short = make_entry("short", 1)
        engine = make_engine(short, events=[event(365)])
        await engine.calculate_calendar(short, NOW)
        long = make_entry("long", 365)
        engine.entries["long"] = long
        due, _, active, _ = await engine.calculate_calendar(long, NOW)
        assert due == local_midnight(NOW) and active
        assert engine.hass.services.async_call.await_count == 2
        await engine.calculate_calendar(short, NOW + timedelta(minutes=1))
        assert engine.hass.services.async_call.await_count == 2

    asyncio.run(scenario())


def test_failed_expansion_cannot_use_narrow_cache():
    async def scenario():
        short = make_entry("short", 1)
        engine = make_engine(short, events=[event(10)])
        await engine.calculate_calendar(short, NOW)
        long = make_entry("long", 365)
        engine.entries["long"] = long
        engine.hass.services.async_call.side_effect = RuntimeError("provider error")
        result = await engine.calculate_calendar(long, NOW)
        assert result == (None, None, False, None)
        assert engine.calendar_cache[CALENDAR][1] == [event(10)]
        due, token, active, _ = await engine.calculate_calendar(short, NOW)
        assert due == local_midnight(NOW) + timedelta(days=9)
        assert token and not active

    asyncio.run(scenario())


def test_legacy_hour_offset_expands_window():
    async def scenario():
        entry = make_entry("legacy", calendar_offset_hours=365 * 24)
        engine = make_engine(entry, events=[event(365)])
        due, _, active, _ = await engine.calculate_calendar(entry, NOW)
        assert due == local_midnight(NOW) and active

    asyncio.run(scenario())


def test_bad_sibling_offset_does_not_break_valid_task():
    async def scenario():
        short = make_entry("short", 1)
        broken = make_entry("broken", "not-a-number")
        engine = make_engine(short, broken, events=[event(1)])
        due, _, active, _ = await engine.calculate_calendar(short, NOW)
        assert due == local_midnight(NOW) and active

    asyncio.run(scenario())


@pytest.mark.parametrize("offset", [-1, 366])
def test_invalid_offset_rejected_before_provider_request(offset):
    async def scenario():
        entry = make_entry("invalid", offset)
        engine = make_engine(entry)
        with pytest.raises(ValueError, match="between 0 and 365"):
            await engine.calculate_calendar(entry, NOW)
        engine.hass.services.async_call.assert_not_awaited()

    asyncio.run(scenario())


def test_cache_refreshes_when_expired_or_window_advances():
    async def scenario():
        entry = make_entry("short", 1)
        engine = make_engine(entry)
        await engine.calculate_calendar(entry, NOW)
        await engine.calculate_calendar(entry, NOW + timedelta(minutes=4))
        assert engine.hass.services.async_call.await_count == 1
        await engine.calculate_calendar(entry, NOW + timedelta(minutes=5))
        assert engine.hass.services.async_call.await_count == 2
        midnight = local_midnight(NOW) + timedelta(days=1)
        await engine.calculate_calendar(entry, midnight - timedelta(minutes=1))
        await engine.calculate_calendar(entry, midnight)
        assert engine.hass.services.async_call.await_count == 4

    asyncio.run(scenario())
