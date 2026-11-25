"""Standalone real-HA smoke test, run in the official HA container.

No mocked scheduler, auth, config entries, registry, service bus or Store.
Use two fresh processes with the same disposable config directory to verify
persistence across a genuine Core restart with the old helper manifest
reclassified as a service. Not collected by pytest.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

DOMAIN = "household_tasks"
SOURCE = "sensor.runtime_test_source"
OPEN_LIST = "sensor.household_tasks_open_names"
LATEST = "sensor.household_tasks_latest_open"
ERRORS = "sensor.household_tasks_errors"


async def wait_for(hass, predicate, description):
    """Allow HA to settle asynchronous platform discovery and state updates."""
    async with asyncio.timeout(30):
        while True:
            await hass.async_block_till_done()
            if predicate():
                return
            await asyncio.sleep(0.05)


def state_is(hass, entity_id, expected):
    state = hass.states.get(entity_id)
    return state is not None and state.state == expected


async def wait_state(hass, entity_id, expected):
    try:
        await wait_for(hass, lambda: state_is(hass, entity_id, expected),
                       f"{entity_id} = {expected}")
    except TimeoutError as err:
        raise AssertionError(
            f"{entity_id} expected {expected!r}, got {hass.states.get(entity_id)!r}"
        ) from err


def entities(hass, entry_id):
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    status = registry.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_status")
    button = registry.async_get_entity_id("button", DOMAIN, f"{entry_id}_complete")
    assert status and button, (status, button)
    assert registry.async_get(status).config_entry_id == entry_id
    assert registry.async_get(button).config_entry_id == entry_id
    return status, button


async def create_task(hass, name, friendly_name, kind, settings):
    """Drive the real config flow manager, including schema validation."""
    from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType

    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    for step, data in [
        ("details", {"name": name, "friendly_name": friendly_name, "category": "Household"}),
        ("trigger_choice", {"trigger_type": kind}),
        ("trigger_settings", settings),
        ("close_choice", {"close_type": "manual"}),
    ]:
        assert result["type"] == FlowResultType.FORM, result
        assert result["step_id"] == step, result
        result = await flow.async_configure(result["flow_id"], data)
    assert result["type"] == FlowResultType.CREATE_ENTRY, result
    entry = result["result"]
    await wait_for(hass, lambda: entry.state is ConfigEntryState.LOADED, "entry loaded")
    return entry


async def check_aggregates(hass, count, latest, *, error_count=0):
    await wait_state(hass, OPEN_LIST, str(count))
    await wait_state(hass, LATEST, latest)
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "household_tasks_open_names") == OPEN_LIST
    assert registry.async_get_entity_id("sensor", DOMAIN, "household_tasks_latest_open") == LATEST
    await wait_state(hass, ERRORS, str(error_count))
    assert registry.async_get_entity_id("sensor", DOMAIN, "household_tasks_errors") == ERRORS
    assert len(hass.states.get(ERRORS).attributes["errors"]) == error_count
    assert hass.states.get(ERRORS).attributes["count"] == error_count
    attrs = hass.states.get(OPEN_LIST).attributes
    assert attrs["count"] == count
    assert len(attrs["names"]) == count
    assert hass.states.get(LATEST).attributes["open_count"] == count


async def complete(hass, entry_id, context=None, *, early=False):
    from homeassistant.core import Context

    await hass.services.async_call(
        DOMAIN, "complete_early" if early else "complete",
        {"entry_id": entry_id}, blocking=True, context=context or Context()
    )


async def create_phase(hass, directory):
    from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY, GROUP_ID_USER
    from homeassistant.core import Context
    import voluptuous as vol
    from homeassistant.exceptions import Unauthorized, ServiceValidationError

    assert not hass.config_entries.async_entries(DOMAIN)
    hass.states.async_set(SOURCE, "0")
    entry = await create_task(hass, "runtime_task", "Runtime task", "numeric", {
        "trigger_entity": SOURCE, "threshold": 5, "operator": "above",
    })
    status, button = entities(hass, entry.entry_id)
    await wait_state(hass, status, "closed")
    await check_aggregates(hass, 0, "none")

    # A closed sensor-triggered task must still report a broken dependency.
    hass.states.async_set(SOURCE, "unavailable")
    await wait_state(hass, ERRORS, "1")
    assert state_is(hass, status, "closed")
    warning = hass.states.get(ERRORS).attributes["errors"][0]
    assert warning["task_name"] == "Runtime task"
    assert warning["entry_id"] == entry.entry_id
    assert SOURCE in hass.states.get(ERRORS).attributes["summary"]
    assert "unavailable" in warning["warnings"][0]
    hass.states.async_set(SOURCE, "0")
    await wait_state(hass, ERRORS, "0")
    assert hass.states.get(ERRORS).attributes["errors"] == []

    # Real state bus and targeted subscription, not a call to Engine.evaluate.
    hass.states.async_set(SOURCE, "10")
    await wait_state(hass, status, "open")
    await check_aggregates(hass, 1, "Runtime task")

    # First user becomes owner, so deliberately create the administrator first.
    admin = await hass.auth.async_create_user("CI administrator", group_ids=[GROUP_ID_ADMIN])
    reader = await hass.auth.async_create_user("CI reader", group_ids=[GROUP_ID_READ_ONLY])
    member = await hass.auth.async_create_user("CI member", group_ids=[GROUP_ID_USER])
    assert admin.is_admin and not reader.is_admin and not member.is_admin
    try:
        await complete(hass, entry.entry_id, Context(user_id=reader.id))
    except Unauthorized:
        pass
    else:
        raise AssertionError("Read-only user was allowed to complete a task")
    assert state_is(hass, status, "open")

    try:
        await hass.services.async_call(DOMAIN, "complete", {}, blocking=True)
    except (vol.Invalid, ServiceValidationError) as err:
        assert "Specify exactly one of entry_id or task" in str(err), str(err)
    else:
        raise AssertionError("Missing service selector was accepted")
    assert state_is(hass, status, "open")

    await complete(hass, entry.entry_id, Context(user_id=member.id))
    await wait_state(hass, status, "closed")
    await check_aggregates(hass, 0, "none")
    completed = hass.states.get(status).attributes["last_completed"]
    assert completed
    assert hass.states.get(status).attributes["history"][-1]["completed_by"] == member.name

    # Rearm through actual state changes. Wait for the closed occurrence's
    # due field to clear before the next change so coalescing cannot skip it.
    hass.states.async_set(SOURCE, "0")
    await wait_for(hass, lambda: hass.states.get(status).attributes.get("due") is None,
                   "numeric task rearmed")
    hass.states.async_set(SOURCE, "10")
    await wait_state(hass, status, "open")
    await check_aggregates(hass, 1, "Runtime task")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await wait_state(hass, status, "open")
    await check_aggregates(hass, 1, "Runtime task")
    assert entities(hass, entry.entry_id) == (status, button)

    snapshot = hass.states.get(status).attributes
    assert snapshot["last_completed"] == completed
    assert snapshot["last_opened"]
    assert [event["action"] for event in snapshot["history"]] == ["opened", "completed", "opened"]
    expected = {"entry_id": entry.entry_id, "status": status, "button": button,
                "last_opened": snapshot["last_opened"], "last_completed": completed,
                "history": snapshot["history"]}
    await hass.async_add_executor_job(
        (directory / "runtime-expected.json").write_text, json.dumps(expected), "utf-8")
    print("PASS: config flow, entities, permissions, state transitions, reload", flush=True)


async def restart_phase(hass, directory):
    from homeassistant.core import Context
    from homeassistant.util import dt as dt_util

    expected = json.loads(await hass.async_add_executor_job(
        (directory / "runtime-expected.json").read_text, "utf-8"))
    entry_id = expected["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    status, button = entities(hass, entry_id)
    assert (status, button) == (expected["status"], expected["button"])
    await wait_state(hass, status, "open")
    # The synthetic source is an in-memory state, not a restored entity.
    # Verify that diagnostics accurately report its absence after restart.
    assert hass.states.get(SOURCE) is None
    await check_aggregates(hass, 1, "Runtime task", error_count=1)
    warning = hass.states.get(ERRORS).attributes["errors"][0]
    assert warning["entry_id"] == entry_id
    assert any("entity missing" in message and SOURCE in message
               for message in warning["warnings"])
    attrs = hass.states.get(status).attributes
    for key in ("last_opened", "last_completed", "history"):
        assert attrs[key] == expected[key], (key, attrs[key], expected[key])

    # Exercise standard HA entity services with a real authorised user.
    member = next(user for user in await hass.auth.async_get_users() if user.name == "CI member")
    hass.states.async_set(SOURCE, "10")
    # Wait for the dependency recovery, not merely for the service bus to settle.
    await check_aggregates(hass, 1, "Runtime task")
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True,
        context=Context(user_id=member.id))
    await wait_state(hass, status, "closed")
    assert hass.states.get(status).attributes["history"][-1]["completed_by"] == member.name
    await check_aggregates(hass, 0, "none")
    result = await hass.config_entries.async_remove(entry_id)
    assert not result["require_restart"], result
    assert hass.config_entries.async_get_entry(entry_id) is None
    await wait_for(hass, lambda: hass.states.get(status) is None, "status removed")
    await check_aggregates(hass, 0, "none")

    # The scheduler and services must remain usable after the last task is removed.
    first_due = (dt_util.now().date() + timedelta(days=7)).isoformat()
    new_entry = await create_task(hass, "replacement_task", "Replacement task",
                                  "completion", {"interval_days": 14, "first_due_date": first_due})
    new_status, _ = entities(hass, new_entry.entry_id)
    await wait_state(hass, new_status, "closed")
    await complete(hass, new_entry.entry_id, Context(user_id=member.id), early=True)
    await wait_for(hass, lambda: bool(hass.states.get(new_status).attributes.get("last_completed")),
                   "early completion persisted")
    assert hass.states.get(new_status).attributes["history"][-1]["action"] == "completed_early"
    assert hass.states.get(new_status).attributes["history"][-1]["completed_by"] == member.name
    await check_aggregates(hass, 0, "none")
    print("PASS: restart persistence, standard button, last-task removal, recreation, early completion",
          flush=True)


async def run_phase(directory, phase, version):
    from homeassistant import bootstrap, loader
    from homeassistant.const import __version__
    from homeassistant.core import HomeAssistant

    assert __version__ == version, (__version__, version)
    hass = HomeAssistant(str(directory))
    loader.async_setup(hass)
    config = {
        "homeassistant": {"name": "Household Tasks CI", "time_zone": "UTC",
                          "latitude": 0, "longitude": 0, "elevation": 0,
                          "unit_system": "metric", "currency": "GBP", "country": "GB"},
        "http": {"server_port": 8123},
    }
    try:
        assert await bootstrap.async_from_config_dict(config, hass) is not None
        await hass.async_start()
        integration = await loader.async_get_integration(hass, DOMAIN)
        assert integration.integration_type == ("helper" if phase == "create" else "service")
        if phase == "create":
            await create_phase(hass, directory)
        else:
            await restart_phase(hass, directory)
    finally:
        await hass.async_stop(force=True)


def main():
    version = sys.argv[1]
    if len(sys.argv) == 4:
        asyncio.run(run_phase(Path(sys.argv[2]), sys.argv[3], version))
        return
    with TemporaryDirectory(prefix="household-tasks-ci-") as temp:
        directory = Path(temp)
        source = Path(__file__).resolve().parents[1] / "custom_components"
        shutil.copytree(source, directory / "custom_components")
        (directory / "configuration.yaml").write_text("homeassistant:\nhttp:\n", encoding="utf-8")
        manifest_path = directory / "custom_components" / DOMAIN / "manifest.json"
        current_manifest = manifest_path.read_text(encoding="utf-8")
        previous_manifest = json.loads(current_manifest)
        assert previous_manifest["integration_type"] == "service"
        # Exercise the manifest-only classification change in an existing
        # installation. Both phases use the current implementation; this
        # is not a full installation of the historical release via HACS.
        previous_manifest["integration_type"] = "helper"
        previous_manifest["version"] = "0.8.1"
        manifest_path.write_text(json.dumps(previous_manifest), encoding="utf-8")
        subprocess.run([sys.executable, __file__, version, temp, "create"],
                       check=True, timeout=240)
        manifest_path.write_text(current_manifest, encoding="utf-8")
        subprocess.run([sys.executable, __file__, version, temp, "restart"],
                       check=True, timeout=240)
    print(f"PASS: Home Assistant {version} runtime smoke test", flush=True)


if __name__ == "__main__":
    main()
