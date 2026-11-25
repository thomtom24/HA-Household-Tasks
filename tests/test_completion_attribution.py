"""Verify authenticated completion attribution without modifying legacy history."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.core import Context

from custom_components.household_tasks import Engine, valid_history_record
from custom_components.household_tasks.button import TaskCompleteButton

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def test_completion_display_name_is_optional_and_legacy_records_remain_valid():
    async def scenario():
        engine = Engine.__new__(Engine)
        engine.hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(
            return_value=SimpleNamespace(name="Household member"))))
        assert await engine.completion_user_name(Context(user_id="account")) == "Household member"
        engine.hass.auth.async_get_user.assert_awaited_once_with("account")
        assert await engine.completion_user_name(Context()) is None
        assert await engine.completion_user_name(None) is None
        engine.hass.auth.async_get_user.return_value = None
        assert await engine.completion_user_name(Context(user_id="deleted")) is None
        engine.hass.auth.async_get_user.side_effect = RuntimeError("lookup failed")
        assert await engine.completion_user_name(Context(user_id="account")) is None
        state = {"created": NOW.isoformat(), "open": True, "events": []}
        engine.record(state, "opened", NOW)
        engine._finish(state, NOW, "completed", completed_by="Household member")
        engine._finish(state, NOW, "completed_early")
        assert state["events"] == [
            {"action": "opened", "at": NOW.isoformat()},
            {"action": "completed", "at": NOW.isoformat(), "completed_by": "Household member"},
            {"action": "completed_early", "at": NOW.isoformat()},
        ]
        assert valid_history_record(state)
    asyncio.run(scenario())


def test_standard_button_passes_current_trusted_context():
    async def scenario():
        engine = SimpleNamespace(conf=Mock(return_value={}), act=AsyncMock())
        entry = SimpleNamespace(entry_id="task", title="Task")
        button = TaskCompleteButton(engine, entry)
        context = Context(user_id="account")
        button.async_set_context(context)
        await button.async_press()
        engine.act.assert_awaited_once_with("task", "complete", context=context)
    asyncio.run(scenario())
