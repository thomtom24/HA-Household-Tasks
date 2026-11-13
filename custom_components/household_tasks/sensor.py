"""Task status and Alexa-friendly aggregate sensor."""
from homeassistant.components.sensor import SensorEntity
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
        return "paused" if self.engine.is_paused(self.entry) else (
            "open" if self.engine.state(self.entry).get("open") else "closed"
        )

    @property
    def extra_state_attributes(self):
        s = self.engine.state(self.entry)
        c = self.engine.conf(self.entry)
        return {
            "task_entry_id": self.entry.entry_id,
            "friendly_name": c.get("friendly_name") or self.entry.title,
            "category": c.get("category"), "area_id": c.get("area_id"),
            "pause_in_holiday_mode": c.get("pause_in_holiday_mode", False),
            "pause_entities": c.get("pause_entities") or [],
            "paused": self.engine.is_paused(self.entry),
            "due": s.get("due"), "last_completed": s.get("last_completed"),
            "last_opened": s.get("last_opened"), "history": s.get("events", [])[-20:],
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
