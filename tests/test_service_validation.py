"""Validate service registration and task selection before authorizing completion."""
import asyncio
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol

from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError

from custom_components.household_tasks import COMPLETION_SERVICE_SCHEMA, Engine
from custom_components.household_tasks.const import DOMAIN


ACTIONS = ["complete", "complete_early"]
INVALID_DATA = [
    {},
    {"entry_id": None},
    {"task": None},
    {"entry_id": ""},
    {"task": " \t\n"},
    {"entry_id": 123},
    {"task": False},
    {"entry_id": ["first"]},
    {"task": {"name": "First"}},
    {"entry_id": "first", "task": "First"},
    {"entry_id": "first", "task": "Second"},
    {"entry_id": "missing", "task": "First"},
    {"entry_id": "first", "user_id": "admin"},
    {"task": "First", "entity_id": "button.first"},
    {"entry_id": "first", "unexpected": True},
    {"entity_id": "button.first"},
]


def engine_for_selection():
    engine = Engine.__new__(Engine)
    engine.cyclic_entries = set()
    engine.entries = {
        "first": SimpleNamespace(entry_id="first", unique_id="first_task", title="First"),
        "second": SimpleNamespace(entry_id="second", unique_id="second_task", title="Second"),
    }
    engine.act = AsyncMock()
    engine._authorize_service = AsyncMock()
    return engine


def request(data):
    return SimpleNamespace(data=MappingProxyType(data), context=Context(user_id="test-user"))


@pytest.mark.parametrize("data", INVALID_DATA)
def test_registered_schema_rejects_invalid_payload(data):
    with pytest.raises(vol.Invalid):
        COMPLETION_SERVICE_SCHEMA(data)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("data", INVALID_DATA)
def test_invalid_direct_handler_calls_never_authorize_or_mutate(action, data):
    async def scenario():
        engine = engine_for_selection()
        with pytest.raises(ServiceValidationError):
            await getattr(engine, "_service_" + action)(request(data))
        engine._authorize_service.assert_not_awaited()
        engine.act.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("selector", [{"entry_id": "first"}, {"task": "first_task"}, {"task": "First"}])
def test_valid_exact_selectors_are_preserved(action, selector):
    async def scenario():
        engine = engine_for_selection()
        call = request(selector)
        assert COMPLETION_SERVICE_SCHEMA(selector) == selector
        await getattr(engine, "_service_" + action)(call)
        engine._authorize_service.assert_awaited_once_with("first", call.context)
        engine.act.assert_awaited_once_with("first", action)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("data", [
    {"entry_id": "missing"}, {"task": "missing"},
    {"task": "first"}, {"task": " First "},
])
def test_unknown_selectors_fail_without_fallback(action, data):
    async def scenario():
        engine = engine_for_selection()
        with pytest.raises(ServiceValidationError, match="Unknown"):
            await getattr(engine, "_service_" + action)(request(data))
        engine._authorize_service.assert_not_awaited()
        engine.act.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("collision", ["title", "unique_id_title", "unique_id"])
def test_ambiguous_names_require_entry_id(action, collision):
    async def scenario():
        engine = engine_for_selection()
        first, second = engine.entries.values()
        if collision == "title":
            second.title = first.title
            name = first.title
        elif collision == "unique_id_title":
            second.title = first.unique_id
            name = first.unique_id
        else:
            second.unique_id = first.unique_id
            name = first.unique_id
        with pytest.raises(ServiceValidationError, match="Ambiguous"):
            await getattr(engine, "_service_" + action)(request({"task": name}))
        engine._authorize_service.assert_not_awaited()
        engine.act.assert_not_awaited()
        await getattr(engine, "_service_" + action)(request({"entry_id": "second"}))
        engine.act.assert_awaited_once_with("second", action)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
def test_same_entry_matching_title_and_unique_id_is_not_ambiguous(action):
    async def scenario():
        engine = engine_for_selection()
        engine.entries["first"].title = "first_task"
        await getattr(engine, "_service_" + action)(request({"task": "first_task"}))
        engine.act.assert_awaited_once_with("first", action)

    asyncio.run(scenario())


def test_engine_registers_both_services_with_validation():
    async def scenario():
        engine = engine_for_selection()
        engine.store = SimpleNamespace(async_load=AsyncMock(return_value={}))
        engine.hass = SimpleNamespace(
            bus=SimpleNamespace(async_listen=Mock()),
            services=SimpleNamespace(async_register=Mock()),
        )
        engine.publish_open_tasks = Mock()
        with patch("custom_components.household_tasks.async_track_time_interval"):
            await engine.start()
        assert engine.hass.services.async_register.call_count == 2
        for action in ACTIONS:
            engine.hass.services.async_register.assert_any_call(
                DOMAIN, action, getattr(engine, "_service_" + action),
                schema=COMPLETION_SERVICE_SCHEMA,
            )

    asyncio.run(scenario())
