"""Direct dependency graph, configuration and runtime safety regressions."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.household_tasks import Engine
from custom_components.household_tasks.config_flow import TaskSteps
from custom_components.household_tasks.dependencies import cyclic_entries
from custom_components.household_tasks.sensor import TaskStatusSensor


def registry(mapping=None, occupied=()):
    mapping = mapping or {}
    return SimpleNamespace(
        async_get_entity_id=lambda domain, platform, unique: mapping.get(unique),
        async_get=lambda entity: object() if entity in occupied else None,
    )


def rule(entity):
    return {"trigger_type": "state", "trigger_entity": entity}


def entry(key, data):
    return SimpleNamespace(entry_id=key, data=data, options={}, title=key)


def test_direct_self_reference_uses_renamed_registry_id():
    assert cyclic_entries({"a": rule("sensor.renamed")}, registry({"a_status": "sensor.renamed"})) == {"a"}


def test_closing_condition_can_create_self_reference():
    config = {"trigger_type": "weekly", "close_type": "manual_or_entity_state",
              "close_entity": "sensor.a"}
    assert cyclic_entries({"a": config}, registry({"a_status": "sensor.a"})) == {"a"}


def test_two_task_cycle_excludes_dependent_but_noncyclic_task():
    configs = {"a": rule("sensor.b"), "b": rule("sensor.a"), "c": rule("sensor.a")}
    reg = registry({f"{key}_status": f"sensor.{key}" for key in configs})
    assert cyclic_entries(configs, reg) == {"a", "b"}


def test_longer_cycle_combines_opening_and_closing_rules():
    configs = {"a": rule("sensor.b"), "b": {"trigger_type": "weekly", "close_type": "entity_numeric",
                                          "close_entity": "sensor.c"}, "c": rule("sensor.a")}
    reg = registry({f"{key}_status": f"sensor.{key}" for key in configs})
    assert cyclic_entries(configs, reg) == set(configs)


def test_acyclic_chain_and_stale_fields_are_allowed():
    configs = {"a": rule("sensor.b"), "b": rule("sensor.c"), "c": {
        "trigger_type": "weekly", "trigger_entity": "sensor.a", "close_type": "manual", "close_entity": "sensor.c"}}
    reg = registry({f"{key}_status": f"sensor.{key}" for key in configs})
    assert not cyclic_entries(configs, reg)


@pytest.mark.parametrize("key", ["open_names", "latest_open"])
def test_aggregate_reference_is_circular_before_registration(key):
    assert cyclic_entries({"a": rule("sensor.household_tasks_" + key)}, registry()) == {"a"}


def test_renamed_aggregate_and_other_participant():
    configs = {"a": rule("sensor.renamed_count"), "b": rule("sensor.a"), "c": rule("sensor.external")}
    reg = registry({"household_tasks_open_names": "sensor.renamed_count", "a_status": "sensor.a"})
    assert cyclic_entries(configs, reg) == {"a", "b"}


def test_registered_unrelated_entity_at_default_id_is_not_a_cycle():
    default = "sensor.household_tasks_open_names"
    assert not cyclic_entries({"a": rule(default)}, registry(occupied=[default]))


def test_empty_graph_does_not_require_registry():
    assert not cyclic_entries({}, None)


@pytest.mark.parametrize("editing", [False, True])
def test_wizard_rejects_aggregate_dependency_and_returns_to_trigger_choice(editing):
    flow = TaskSteps()
    flow.initialize(rule("sensor.household_tasks_open_names"), editing)
    flow.entry = entry("a", {})
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=Mock(return_value=[flow.entry])))
    flow.async_show_form = Mock(side_effect=lambda **kw: kw)
    with patch("custom_components.household_tasks.config_flow.er.async_get", return_value=registry()):
        result = flow._cycle_error()
    assert result["step_id"] == "trigger_choice"
    assert result["errors"] == {"base": "circular_dependency"}
    assert flow.values["trigger_entity"] == "sensor.household_tasks_open_names"


def test_wizard_allows_edit_that_breaks_existing_cycle():
    a, b = entry("a", rule("sensor.b")), entry("b", rule("sensor.a"))
    flow = TaskSteps()
    flow.initialize({"trigger_type": "weekly", "trigger_entity": "sensor.b"}, True)
    flow.entry = a
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=Mock(return_value=[a, b])))
    reg = registry({"a_status": "sensor.a", "b_status": "sensor.b"})
    with patch("custom_components.household_tasks.config_flow.er.async_get", return_value=reg):
        assert flow._cycle_error() is None


def engine_fixture():
    engine = Engine.__new__(Engine)
    engine.invalid_history = set()
    engine.storage_dirty = False
    engine.storage_failed = False
    engine.storage_retry = None
    engine.reloading_entries = {}
    engine.last_aggregate_names = {}
    engine.calendar_requests = {}
    engine.hass = SimpleNamespace()
    engine.entries = {"a": entry("a", rule("sensor.a"))}
    engine.history = {"a": {"open": True, "events": [{"action": "opened", "at": "2026-11-24T12:00:00+00:00"}]}}
    engine.cyclic_entries = set()
    engine.evaluation_errors = set()
    engine.listeners = []
    engine.lock = asyncio.Lock()
    engine.save = AsyncMock()
    engine.notify = Mock()
    return engine


def test_refresh_warns_only_when_cycle_membership_changes_and_preserves_history():
    engine = engine_fixture()
    original = deepcopy(engine.history)
    listener = Mock()
    engine.listeners.append(listener)
    with patch("custom_components.household_tasks.er.async_get",
               return_value=registry({"a_status": "sensor.a"})):
        engine.refresh_dependency_cycles()
        engine.refresh_dependency_cycles()
        assert engine.cyclic_entries == {"a"}
        listener.assert_called_once_with("a")
        status = TaskStatusSensor(engine, engine.entries["a"])
        assert status.extra_state_attributes["has_entity_warning"]
        assert "Circular task dependency" in status.extra_state_attributes["entity_warnings"][0]
        engine.entries["a"].options = rule("sensor.external")
        engine.refresh_dependency_cycles()
        assert not engine.cyclic_entries
        assert listener.call_count == 2
        assert not status.extra_state_attributes["has_entity_warning"]
    assert engine.history == original
    engine.save.assert_not_awaited()


def test_cycle_pauses_automatic_transitions_but_allows_manual_completion():
    async def scenario():
        engine = engine_fixture()
        engine.cyclic_entries = {"a"}
        engine.calculate = Mock(side_effect=AssertionError("must not calculate"))
        original = deepcopy(engine.history)
        await engine.evaluate(engine.entries["a"])
        assert engine.history == original
        engine.save.assert_not_awaited()
        await engine.act("a", "complete")
        assert not engine.history["a"]["open"]
        assert engine.history["a"]["events"][-1]["action"] == "completed"
        engine.save.assert_awaited_once()
    asyncio.run(scenario())


def test_cyclic_task_ignores_source_events():
    engine = engine_fixture()
    engine.cyclic_entries = {"a"}
    engine.entity_dependencies = {"sensor.a": {"a"}}
    engine.request_evaluation = Mock()
    engine._state_event(SimpleNamespace(data={"entity_id": "sensor.a"}))
    engine.request_evaluation.assert_not_called()


def test_registry_creation_refreshes_cycle_detection():
    engine = engine_fixture()
    engine.entity_dependencies = {"sensor.a": {"a"}}
    with patch("custom_components.household_tasks.er.async_get",
               return_value=registry({"a_status": "sensor.a"})):
        engine._registry_event(SimpleNamespace(data={"action": "create", "entity_id": "sensor.a"}))
    assert engine.cyclic_entries == {"a"}


def test_cycle_appearing_during_calendar_await_rolls_back_reconciliation():
    async def scenario():
        engine = engine_fixture()
        task = engine.entries["a"]
        task.data = {"trigger_type": "calendar", "trigger_entity": "calendar.test"}
        original = deepcopy(engine.history)
        started, release = asyncio.Event(), asyncio.Event()
        async def calendar(*args):
            started.set()
            await release.wait()
            return None, None, False, None
        engine.calculate_calendar = calendar
        worker = asyncio.create_task(engine.evaluate(task))
        await started.wait()
        engine.cyclic_entries.add("a")
        release.set()
        await worker
        assert engine.history == original
        engine.save.assert_not_awaited()
    asyncio.run(scenario())
