"""Household Tasks configuration flow."""
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN, DEFAULTS

TRIGGERS = ["completion", "weekly", "calendar", "date_entity", "numeric", "state"]
CLOSES = ["manual", "entity_state", "entity_numeric", "calendar_end",
          "manual_or_entity_state", "manual_or_entity_numeric", "manual_or_calendar_end"]

def schema(current=None):
    d = {**DEFAULTS, **(current or {})}
    fields = {
        "name": str, "friendly_name": str, "category": str,
        "area_id": selector.AreaSelector(),
        "pause_in_holiday_mode": bool,
        "pause_entities": selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
        "trigger_type": vol.In(TRIGGERS),
        "interval_days": vol.All(vol.Coerce(int), vol.Range(min=1, max=3650)),
        "weekday": vol.All(vol.Coerce(int), vol.Range(min=0, max=6)),
        "hour": vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        "minute": vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        "trigger_entity": str, "trigger_state": str,
        "threshold": vol.Coerce(float), "operator": vol.In(["below", "above", "equal"]),
        "offset_days": vol.Coerce(int),
        "calendar_offset_hours": vol.All(vol.Coerce(int), vol.Range(min=0, max=720)),
        "calendar_match": str, "close_type": vol.In(CLOSES),
        "close_entity": str, "close_state": str, "close_threshold": vol.Coerce(float),
        "close_operator": vol.In(["below", "above", "equal"]),
    }
    optional = {"area_id", "pause_entities"}
    return vol.Schema({
        (vol.Optional(k, default=d[k]) if k in optional and k in d
         else vol.Optional(k) if k in optional
         else vol.Required(k, default=d.get(k, "New task" if k == "name" else ""))): v
        for k, v in fields.items()
    })

class HouseholdTasksConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            key = re.sub(r"[^a-z0-9_]+", "_", user_input["name"].lower()).strip("_")
            if not key:
                return self.async_show_form(step_id="user", data_schema=schema(),
                                            errors={"name": "invalid_name"})
            await self.async_set_unique_id(key)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get("friendly_name") or user_input["name"],
                data=user_input,
            )
        return self.async_show_form(step_id="user", data_schema=schema())

    @staticmethod
    def async_get_options_flow(config_entry):
        return HouseholdTasksOptionsFlow(config_entry)

class HouseholdTasksOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=schema({**self.entry.data, **self.entry.options})
        )
