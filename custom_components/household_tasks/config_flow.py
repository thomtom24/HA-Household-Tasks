"""Multi-step task editor: only show settings relevant to selected rules."""
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN, DEFAULTS

TRIGGERS = ["completion", "weekly", "monthly", "yearly", "calendar", "date_entity", "numeric", "state"]
CLOSES = ["manual", "entity_state", "entity_numeric", "calendar_end",
          "manual_or_entity_state", "manual_or_entity_numeric", "manual_or_calendar_end",
          "trigger_inactive", "manual_or_trigger_inactive"]

TRIGGER_LABELS = {
    "completion": "Time since last completion",
    "weekly": "Weekly schedule",
    "monthly": "Monthly schedule",
    "yearly": "Yearly schedule",
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
    "trigger_inactive": "Trigger condition no longer true",
    "manual_or_trigger_inactive": "Manual or trigger condition no longer true",
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
        s[field("first_due_date", d.get("first_due_date"), False)] = selector.DateSelector()
    elif kind in ("monthly", "yearly"):
        if kind == "yearly":
            s[field("schedule_interval", d.get("schedule_interval", 1))] = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=120, step=1, mode=selector.NumberSelectorMode.BOX))
            s[field("year_month", d.get("year_month", 1))] = selector.SelectSelector(selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=str(m), label=label) for m, label in enumerate(
                    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)],
                mode=selector.SelectSelectorMode.DROPDOWN))
        s[field("month_day", d.get("month_day", 1))] = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=31, step=1, mode=selector.NumberSelectorMode.BOX))
        s[field("hour", d.get("hour", 9))] = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=23, step=1, mode=selector.NumberSelectorMode.BOX))
        s[field("minute", d.get("minute", 0))] = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=59, step=1, mode=selector.NumberSelectorMode.BOX))
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
            s[field("operator", d.get("operator", "below"))] = labelled_options({"below": "Below (<)", "below_or_equal": "Below or equal (≤)", "above": "Above (>)", "above_or_equal": "Above or equal (≥)", "equal": "Equal (=)"})
        else:
            s[field("trigger_state", d.get("trigger_state", "on"))] = str
    return vol.Schema(s)

def close_choice_schema(d):
    choices = {key: label for key, label in CLOSE_LABELS.items()
               if ("calendar_end" not in key or d.get("trigger_type") == "calendar")
               and ("trigger_inactive" not in key or d.get("trigger_type") in ("numeric", "state"))}
    selected = d.get("close_type", "manual")
    if selected not in choices:
        selected = "manual"
    return vol.Schema({field("close_type", selected): labelled_options(choices)})

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
            old_type = self.values.get("trigger_type")
            self.values.update(user_input)
            if old_type != self.values["trigger_type"]:
                # Avoid keeping stale completion tokens after changing trigger families.
                self.values["_reset_trigger_state"] = True
            return await self.async_step_trigger_settings()
        return self.async_show_form(step_id="trigger_choice", data_schema=trigger_choice_schema(self.values))

    async def async_step_trigger_settings(self, user_input=None):
        if user_input is not None:
            self.values.update(user_input)
            if self.values.get("trigger_type") == "monthly":
                self.values["schedule_interval"] = 1
            return await self.async_step_close_choice()
        return self.async_show_form(step_id="trigger_settings", data_schema=trigger_schema(self.values))

    async def async_step_close_choice(self, user_input=None):
        if user_input is not None:
            self.values.update(user_input)
            if self.values["trigger_type"] != "calendar" and "calendar_end" in self.values["close_type"]:
                return self.async_show_form(step_id="close_choice", data_schema=close_choice_schema(self.values),
                                            errors={"base": "invalid_calendar_close"})
            return await self.async_step_close_settings()
        return self.async_show_form(step_id="close_choice", data_schema=close_choice_schema(self.values))

    async def async_step_close_settings(self, user_input=None):
        if self.values["close_type"] in ("manual", "calendar_end", "manual_or_calendar_end", "trigger_inactive", "manual_or_trigger_inactive"):
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
        return self.async_create_entry(title=self.values["friendly_name"], data={k: v for k, v in self.values.items() if k not in ("pause_in_holiday_mode", "pause_entities", "_reset_trigger_state")})

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
        values = {k: v for k, v in self.values.items()
                  if k not in ("pause_in_holiday_mode", "pause_entities", "_reset_trigger_state")}
        return self.async_create_entry(title="", data=values)
