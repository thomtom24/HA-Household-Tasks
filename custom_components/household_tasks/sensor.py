"""Task status sensors for Home Assistant."""
from copy import deepcopy
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from .const import DOMAIN

class TaskStatusSensor(SensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(self, engine, entry):
        self.engine, self.entry = engine, entry
        self._last_payload = None
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_name = f"{engine.conf(entry).get('friendly_name') or entry.title} status"

    @property
    def native_value(self):
        return "open" if self.engine.state(self.entry).get("open") else "closed"

    @property
    def extra_state_attributes(self):
        s = self.engine.state(self.entry)
        c = self.engine.conf(self.entry)
        completed_early_today = self.engine.completed_early_today(s, dt_util.now())
        warnings = list(s.get("entity_warnings", []))
        if self.engine.storage_failed:
            warnings.append("Task history is not saved to disk. Automatic retry enabled; avoid restarting Home Assistant.")
        if self.entry.entry_id in self.engine.cyclic_entries:
            warnings.append("Circular task dependency detected. Automatic transitions paused; edit the task rules to resolve the loop.")
        if self.entry.entry_id in self.engine.evaluation_errors:
            warnings.append("Task evaluation failed; check Home Assistant logs. Automatic retry enabled.")
        return {
            "completed_early_today": completed_early_today,
            "task_entry_id": self.entry.entry_id,
            "friendly_name": c.get("friendly_name") or self.entry.title,
            "category": c.get("category"), "area_id": c.get("area_id"),
            "trigger_type": c.get("trigger_type"),
            "close_type": c.get("close_type", "manual"),
            "manual_completion_allowed": c.get("close_type", "manual") == "manual" or c.get("close_type", "").startswith("manual_or_"),
            "due": s.get("due"), "due_at_open": s.get("due_at_open"), "last_completed": s.get("last_completed"),
            "last_opened": s.get("last_opened"), "history": s.get("events", [])[-20:],
            "entity_warnings": warnings,
            "has_entity_warning": bool(warnings),
        }

    async def async_added_to_hass(self):
        self._last_payload = deepcopy((self.native_value, self.extra_state_attributes))
        self.engine.listeners.append(self._changed)

    async def async_will_remove_from_hass(self):
        if self._changed in self.engine.listeners:
            self.engine.listeners.remove(self._changed)

    def _changed(self, entry_id):
        if entry_id == self.entry.entry_id:
            payload = (self.native_value, self.extra_state_attributes)
            if payload != self._last_payload:
                self.async_write_ha_state()
                self._last_payload = deepcopy(payload)

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TaskStatusSensor(hass.data[DOMAIN], entry)])

class AggregateTaskSensor(SensorEntity):
    """A domain-level sensor independent of individual task config entries."""

    _attr_should_poll = False

    def __init__(self, engine, key, name, icon):
        self.engine = engine
        self.key = key
        self.entity_id = f"sensor.household_tasks_{key}"
        self._attr_unique_id = f"household_tasks_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._snapshot = engine.aggregate_snapshot()[key]

    @property
    def native_value(self):
        return self._snapshot[0]

    @property
    def extra_state_attributes(self):
        return self._snapshot[1]

    async def async_added_to_hass(self):
        # Refresh if a task changed while HA was registering this entity.
        self._snapshot = self.engine.aggregate_snapshot()[self.key]
        self.engine.aggregate_listeners.append(self._changed)

    async def async_will_remove_from_hass(self):
        if self._changed in self.engine.aggregate_listeners:
            self.engine.aggregate_listeners.remove(self._changed)

    @callback
    def _changed(self, snapshot):
        value = snapshot[self.key]
        if value != self._snapshot:
            self._snapshot = value
            self.async_write_ha_state()


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up aggregate sensors once via domain discovery, not per task."""
    if not discovery_info or not discovery_info.get("aggregate_sensors"):
        return
    engine = hass.data[DOMAIN]
    async_add_entities([
        AggregateTaskSensor(engine, "open_names", "Household Tasks Open List",
                            "mdi:format-list-checks"),
        AggregateTaskSensor(engine, "latest_open", "Household Tasks Latest Open",
                            "mdi:clipboard-text-clock-outline"),
    ])
