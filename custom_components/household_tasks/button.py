"""One-click task completion buttons."""
from homeassistant.components.button import ButtonEntity
from .const import DOMAIN

class TaskCompleteButton(ButtonEntity):
    _attr_icon = "mdi:check-circle-outline"
    def __init__(self, engine, entry):
        self.engine, self.entry = engine, entry
        self._attr_unique_id = f"{entry.entry_id}_complete"
        self._attr_name = f"{engine.conf(entry).get('friendly_name') or entry.title} complete"
    async def async_press(self):
        # HA sets this context before invoking the entity service. Capture it
        # before awaiting so a later press cannot change the attribution.
        context = self._context
        await self.engine.act(self.entry.entry_id, "complete", context=context)

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TaskCompleteButton(hass.data[DOMAIN], entry)])
