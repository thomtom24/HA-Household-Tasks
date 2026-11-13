"""Multi-step task editor: only show settings relevant to selected rules."""
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN, DEFAULTS

TRIGGERS = ["completion", "weekly", "calendar", "date_entity", "numeric", "state"]
CLOSES = ["manual", "entity_state", "entity_numeric", "calendar_end",
          "manual_or_entity_state", "manual_or_entity_numeric", "manual_or_calendar_end"]

TRIGGER_LABELS = {
    "completion": "Time since last completion",
    "weekly": "Weekly schedule",
    "calendar": "Calendar event",
    "date_entity": "Date from an entity",
    "numeric": "Numeric sensor threshold",
    "state": "Entity state",
}
CLOSE_LABELS = {
    "manual": "Manual completion",
    "entity_state": "Entity reaches a state",
    "entity_numeric": "Numeric sensor condition",
    "calendar_end": "Calendar event ends",
    "manual_or_entity_state": "Manual or entity state",
    "manual_or_entity_numeric": "Manual or numeric condition",
    "manual_or_calendar_end": "Manual or calendar event ends",
}

def labelled_options(labels):
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=value, label=label)
                 for value, label in labels.items()],
        mode=selector.SelectSelectorMode.DROPDOWN,
    ))

def field(key, value, required=True):
    return vol.Required(key, default=value) if required else (
        vol.Optional(key, default=value) if value not in (None, "", []) else vol.Optional(key)
    )

def details_schema(d, editing=False):
    s = {}
    if not editing:
        s[field("name", d.get("name", ""), True)] = str
    s[field("friendly_name", d.get("friendly_name", ""), True)] = str
    s[field("category", d.get("category", "Household"))] = str
    s[field("area_id", d.get("area_id"), False)] = selector.AreaSelector()
    return vol.Schema(s)

def trigger_choice_schema(d):
    return vol.Schema({field("trigger_type", d.get("trigger_type", "completion")): labelled_options(TRIGGER_LABELS)})

def trigger_schema(d):
    kind = d["trigger_type"]
    s = {}
    if kind == "completion":
        s[field("interval_days", d.get("interval_days", 14))] = vol.All(vol.Coerce(int), vol.Range(min=1, max=3650))
    elif kind == "weekly":
        s[field("weekday", d.get("weekday", 0))] = vol.All(vol.Coerce(int), vol.Range(min=0, max=6))
        s[field("hour", d.get("hour", 9))] = vol.All(vol.Coerce(int), vol.Range(min=0, max=23))
        s[field("minute", d.get("minute", 0))] = vol.All(vol.Coerce(int), vol.Range(min=0, max=59))
    elif kind == "calendar":
        s[field("trigger_entity", d.get("trigger_entity", ""))] = selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar"))
        s[field("calendar_offset_hours", d.get("calendar_offset_hours", 24))] = vol.All(vol.Coerce(int), vol.Range(min=0, max=720))
        s[field("calendar_match", d.get("calendar_match", ""), False)] = str
    elif kind == "date_entity":
        s[field("trigger_entity", d.get("trigger_entity", ""))] = selector.EntitySelector()
        s[field("offset_days", d.get("offset_days", 45))] = vol.Coerce(int)
    elif kind in ("numeric", "state"):
        s[field("trigger_entity", d.get("trigger_entity", ""))] = selector.EntitySelector()
        if kind == "numeric":
            s[field("threshold", d.get("threshold", 3.0))] = vol.Coerce(float)
            s[field("operator", d.get("operator", "below"))] = vol.In(["below", "above", "equal"])
        else:
            s[field("trigger_state", d.get("trigger_state", "on"))] = str
    return vol.Schema(s)

def close_choice_schema(d):
    return vol.Schema({field("close_type", d.get("close_type", "manual")): labelled_options(CLOSE_LABELS)})

def close_schema(d):
    kind = d["close_type"]
    s = {}
    if "entity_state" in kind or "entity_numeric" in kind:
        s[field("close_entity", d.get("close_entity", ""))] = selector.EntitySelector()
        if "entity_numeric" in kind:
            s[field("close_threshold", d.get("close_threshold", 0.0))] = vol.Coerce(float)
            s[field("close_operator", d.get("close_operator", "above"))] = vol.In(["below", "above", "equal"])
        else:
            s[field("close_state", d.get("close_state", "off"))] = str
    return vol.Schema(s)

class TaskSteps:
    """Shared steps for creation and editing."""
    def initialize(self, values, editing):
        self.values = {**DEFAULTS, **{k: v for k, v in values.items() if k not in ("pause_in_holiday_mode", "pause_entities")}}
        self.editing = editing

    async def async_step_details(self, user_input=None):
        if user_input is not None:
            self.values.update(user_input)
            if not self.values.get("friendly_name", "").strip():
                return self.async_show_form(step_id="details", data_schema=details_schema(self.values, self.editing), errors={"base": "required"})
            return await self.async_step_trigger_choice()
        return self.async_show_form(step_id="details", data_schema=details_schema(self.values, self.editing))

    async def async_step_trigger_choice(self, user_input=None):
        if user_input is not None:
            self.values.update(user_input)
            return await self.async_step_trigger_settings()
        return self.async_show_form(step_id="trigger_choice", data_schema=trigger_choice_schema(self.values))

    async def async_step_trigger_settings(self, user_input=None):
        if user_input is not None:
            self.values.update(user_input)
            return await self.async_step_close_choice()
        return self.async_show_form(step_id="trigger_settings", data_schema=trigger_schema(self.values))

    async def async_step_close_choice(self, user_input=None):
        if user_input is not None:
            self.values.update(user_input)
            return await self.async_step_close_settings()
        return self.async_show_form(step_id="close_choice", data_schema=close_choice_schema(self.values))

    async def async_step_close_settings(self, user_input=None):
        if self.values["close_type"] in ("manual", "calendar_end", "manual_or_calendar_end"):
            return await self._finish()
        if user_input is not None:
            self.values.update(user_input)
            return await self._finish()
        return self.async_show_form(step_id="close_settings", data_schema=close_schema(self.values))

class HouseholdTasksConfigFlow(TaskSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        self.initialize({}, False)
        return await self.async_step_details(user_input)

    async def _finish(self):
        key = re.sub(r"[^a-z0-9_]+", "_", self.values["name"].lower()).strip("_")
        if not key:
            return self.async_abort(reason="invalid_name")
        await self.async_set_unique_id(key)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self.values["friendly_name"], data={k: v for k, v in self.values.items() if k not in ("pause_in_holiday_mode", "pause_entities")})

    @staticmethod
    def async_get_options_flow(config_entry):
        return HouseholdTasksOptionsFlow(config_entry)

class HouseholdTasksOptionsFlow(TaskSteps, config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.entry = config_entry
        self.initialize({**config_entry.data, **config_entry.options}, True)

    async def async_step_init(self, user_input=None):
        return await self.async_step_details(user_input)

    async def _finish(self):
        return self.async_create_entry(title="", data={k: v for k, v in self.values.items() if k not in ("pause_in_holiday_mode", "pause_entities")})
