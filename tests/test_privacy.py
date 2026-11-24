"""Privacy regressions for storage, calendar payloads and routine errors."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import Engine


def test_history_store_requests_private_file_permissions():
    hass = Mock()
    with patch("custom_components.household_tasks.Store") as store:
        Engine(hass)
    store.assert_called_once_with(hass, 1, "household_tasks.history", private=True)


@pytest.mark.parametrize("title_field", ["summary", "message"])
def test_calendar_projection_retains_only_required_fields(title_field):
    event = {
        "start": "2026-11-25T12:00:00+00:00",
        "end": "2026-11-25T13:00:00+00:00",
        title_field: "Collection",
        "description": "private notes",
        "location": "private address",
        "attendees": ["private attendee"],
        "provider_data": {"secret": "private"},
    }
    result = Engine._validated_calendar_events(
        {"calendar.test": {"events": [event]}}, "calendar.test")
    assert result == [{key: event[key] for key in ("start", "end", title_field)}]
    assert result[0] is not event
    assert "description" in event


def test_projection_preserves_missing_end_and_empty_title():
    event = {"start": "2026-11-25", "summary": "", "description": "private"}
    assert Engine._validated_calendar_events(
        {"calendar.test": {"events": [event]}}, "calendar.test") == [
            {"start": "2026-11-25", "summary": ""}]


@pytest.mark.parametrize("failure", ["evaluate", "listener"])
def test_routine_error_logs_do_not_include_exception_payload(caplog, failure):
    async def scenario():
        engine = Engine.__new__(Engine)
        engine.cyclic_entries = set()
        entry = SimpleNamespace(entry_id="task")
        engine.entries = {"task": entry}
        engine.evaluation_errors = set()
        engine.evaluate = AsyncMock(side_effect=RuntimeError("private event title"))
        engine.listeners = [Mock(side_effect=RuntimeError("private listener data"))] if failure == "listener" else []
        if failure == "listener":
            engine._notify_evaluation_warning("task")
        else:
            await engine._evaluate_background(entry)
            await engine._evaluate_background(entry)
        errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
        assert len(errors) == 1
        assert errors[0].exc_info is None
        assert "private" not in caplog.text

    with caplog.at_level(logging.INFO, logger="custom_components.household_tasks"):
        asyncio.run(scenario())


def test_debug_logging_can_still_show_traceback(caplog):
    async def scenario():
        engine = Engine.__new__(Engine)
        engine.cyclic_entries = set()
        entry = SimpleNamespace(entry_id="task")
        engine.entries = {"task": entry}
        engine.evaluation_errors = set()
        engine.listeners = []
        engine.evaluate = AsyncMock(side_effect=RuntimeError("diagnostic details"))
        await engine._evaluate_background(entry)

    with caplog.at_level(logging.DEBUG, logger="custom_components.household_tasks"):
        asyncio.run(scenario())
    assert any(record.levelno == logging.DEBUG and record.exc_info for record in caplog.records)
