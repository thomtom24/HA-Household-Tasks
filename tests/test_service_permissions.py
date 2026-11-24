"""Regression tests for custom completion service authorization."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized, UnknownUser

from custom_components.household_tasks import Engine
from custom_components.household_tasks.const import DOMAIN


ACTIONS = ["complete", "complete_early"]
ENTRY_ID = "task-entry"
USER_ID = "test-user"
BUTTON = "button.renamed_task_completion"


def make_engine():
    engine = Engine.__new__(Engine)
    entry = SimpleNamespace(entry_id=ENTRY_ID, unique_id="test_task", title="Test Task")
    other = SimpleNamespace(entry_id="other", unique_id="other_task", title="Other Task")
    engine.entries = {ENTRY_ID: entry, "other": other}
    engine.act = AsyncMock()
    user = SimpleNamespace(is_admin=False, is_active=True,
                           permissions=SimpleNamespace(check_entity=Mock(return_value=True)))
    engine.hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)))
    registry = SimpleNamespace(
        async_get_entity_id=Mock(return_value=BUTTON),
        async_get=Mock(return_value=SimpleNamespace(disabled_by=None, config_entry_id=ENTRY_ID)),
    )
    return engine, user, registry


def call(data=None, user_id=USER_ID):
    return SimpleNamespace(data=data if data is not None else {"entry_id": ENTRY_ID},
                           context=Context(user_id=user_id))


async def invoke(engine, action, request):
    await getattr(engine, "_service_" + action)(request)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("selector", [{"entry_id": ENTRY_ID}, {"task": "test_task"}, {"task": "Test Task"}])
def test_authorized_non_admin_can_complete_using_current_registry_identity(action, selector):
    async def scenario():
        engine, user, registry = make_engine()
        with patch("custom_components.household_tasks.er.async_get", return_value=registry):
            await invoke(engine, action, call(selector))
        registry.async_get_entity_id.assert_called_once_with("button", DOMAIN, f"{ENTRY_ID}_complete")
        registry.async_get.assert_called_once_with(BUTTON)
        user.permissions.check_entity.assert_called_once_with(BUTTON, POLICY_CONTROL)
        engine.act.assert_awaited_once_with(ENTRY_ID, action)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("failure", ["denied", "unregistered", "missing_registry_entry", "disabled", "wrong_config_entry"])
def test_non_admin_fails_closed_before_any_mutation(action, failure):
    async def scenario():
        engine, user, registry = make_engine()
        if failure == "denied":
            user.permissions.check_entity.return_value = False
        elif failure == "unregistered":
            registry.async_get_entity_id.return_value = None
        elif failure == "missing_registry_entry":
            registry.async_get.return_value = None
        elif failure == "disabled":
            registry.async_get.return_value.disabled_by = "user"
        else:
            registry.async_get.return_value.config_entry_id = "other"
        with patch("custom_components.household_tasks.er.async_get", return_value=registry):
            with pytest.raises(Unauthorized):
                await invoke(engine, action, call())
        engine.act.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("actor", ["admin", "internal"])
def test_admin_and_internal_automation_calls_remain_supported(action, actor):
    async def scenario():
        engine, user, registry = make_engine()
        user.is_admin = True
        registry.async_get_entity_id.return_value = None
        with patch("custom_components.household_tasks.er.async_get", return_value=registry) as get_registry:
            await invoke(engine, action, call(user_id=None if actor == "internal" else USER_ID))
        get_registry.assert_not_called()
        if actor == "internal":
            engine.hass.auth.async_get_user.assert_not_awaited()
        else:
            engine.hass.auth.async_get_user.assert_awaited_once_with(USER_ID)
        engine.act.assert_awaited_once_with(ENTRY_ID, action)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("actor", ["unknown", "inactive", "inactive_admin"])
def test_unknown_and_inactive_users_cannot_complete(action, actor):
    async def scenario():
        engine, user, registry = make_engine()
        if actor == "unknown":
            engine.hass.auth.async_get_user.return_value = None
        else:
            user.is_active = False
            user.is_admin = actor == "inactive_admin"
        with patch("custom_components.household_tasks.er.async_get", return_value=registry):
            with pytest.raises(UnknownUser if actor == "unknown" else Unauthorized):
                await invoke(engine, action, call())
        engine.act.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ACTIONS)
def test_service_data_cannot_override_authenticated_user_or_target_button(action):
    async def scenario():
        engine, user, registry = make_engine()
        user.permissions.check_entity.side_effect = lambda entity_id, _: entity_id == "button.allowed_other"
        request = call({"entry_id": ENTRY_ID, "user_id": None,
                        "entity_id": "button.allowed_other", "task": "other_task"})
        with patch("custom_components.household_tasks.er.async_get", return_value=registry):
            with pytest.raises(Unauthorized):
                await invoke(engine, action, request)
        engine.hass.auth.async_get_user.assert_awaited_once_with(USER_ID)
        user.permissions.check_entity.assert_called_once_with(BUTTON, POLICY_CONTROL)
        engine.act.assert_not_awaited()

    asyncio.run(scenario())
