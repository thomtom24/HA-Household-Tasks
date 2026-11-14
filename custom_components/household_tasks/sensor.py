"""Task status sensors for Home Assistant."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.util import dt as dt_util
from . import parse_dt
from .const import DOMAIN

class TaskStatusSensor(SensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:clipboard-check-outline"

    def __init__(self, engine, entry):
        self.engine, self.entry = engine, entry
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_name = f"{engine.conf(entry).get('friendly_name') or entry.title} status"

    @property
    def native_value(self):
        return "open" if self.engine.state(self.entry).get("open") else "closed"

    @property
    def extra_state_attributes(self):
        s = self.engine.state(self.entry)
        c = self.engine.conf(self.entry)
        today = dt_util.now().date()
        completed_early_today = any(
            event.get("action") == "completed_early"
            and (at := parse_dt(event.get("at"))) is not None
            and at.date() == today
            for event in s.get("events", [])
        )
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
            "entity_warnings": s.get("entity_warnings", []),
            "has_entity_warning": bool(s.get("entity_warnings")),
        }

    async def async_added_to_hass(self):
        self.engine.listeners.append(self._changed)

    async def async_will_remove_from_hass(self):
        if self._changed in self.engine.listeners:
            self.engine.listeners.remove(self._changed)

    def _changed(self, entry_id):
        if entry_id == self.entry.entry_id:
            self.async_write_ha_state()

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TaskStatusSensor(hass.data[DOMAIN], entry)])
