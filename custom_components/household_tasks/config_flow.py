"""Multi-step task editor: only show settings relevant to selected rules."""
import math
import re
from datetime import date
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import valid_entity_id
from homeassistant.helpers import entity_registry as er, selector
from .const import DOMAIN, DEFAULTS
from .dependencies import cyclic_entries

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

class StrictNumberSelector(selector.NumberSelector):
    """Keep HA's serializable number selector while rejecting unsafe numbers."""

    def __init__(self, config, *, integer=False):
        super().__init__(config)
        self.integer = integer

    def __call__(self, data):
        if isinstance(data, bool):
            raise vol.Invalid("Expected a number, not a boolean")
        try:
            value = super().__call__(data)
        except (ValueError, TypeError, OverflowError) as err:
            raise vol.Invalid("Expected a number") from err
        if not math.isfinite(value):
            raise vol.Invalid("Expected a finite number")
        if self.integer:
            if not value.is_integer():
                raise vol.Invalid("Expected a whole number")
            return int(value)
        return value


def whole_number(minimum, maximum):
    return StrictNumberSelector(selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=1, mode=selector.NumberSelectorMode.BOX),
        integer=True)


def finite_threshold():
    return StrictNumberSelector(selector.NumberSelectorConfig(
        step="any", mode=selector.NumberSelectorMode.BOX))


def valid_text(value, limit, *, required=True):
    return (isinstance(value, str) and len(value) <= limit
            and (not required or bool(value.strip())))


def internal_key(value):
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")


def labelled_options(labels):
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=value, label=label)
                 for value, label in labels.items()],
        mode=selector.SelectSelectorMode.DROPDOWN,
    ))

def field(key, value, required=True):
    return vol.Required(key, default=value) if required else (
        vol.Optional(key, description={"suggested_value": value}) if value not in (None, "", []) else vol.Optional(key)
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
        s[field("interval_days", d.get("interval_days", 14))] = whole_number(1, 3650)
        s[field("first_due_date", d.get("first_due_date"), False)] = selector.DateSelector()
    elif kind in ("monthly", "yearly"):
        if kind == "yearly":
            s[field("schedule_interval", d.get("schedule_interval", 1))] = whole_number(1, 120)
            s[field("year_month", str(d.get("year_month", 1)))] = selector.SelectSelector(selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=str(m), label=label) for m, label in enumerate(
                    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)],
                mode=selector.SelectSelectorMode.DROPDOWN))
        s[field("month_day", d.get("month_day", 1))] = whole_number(1, 31)
    elif kind == "weekly":
        s[field("weekday", d.get("weekday", 0))] = whole_number(0, 6)
    elif kind == "calendar":
        s[field("trigger_entity", d.get("trigger_entity", ""))] = selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar"))
        s[field("calendar_offset_days", d.get("calendar_offset_days", int(d.get("calendar_offset_hours", 24)) // 24))] = whole_number(0, 365)
        s[field("calendar_match", d.get("calendar_match", ""), False)] = str
    elif kind == "date_entity":
        s[field("trigger_entity", d.get("trigger_entity", ""))] = selector.EntitySelector()
        s[field("offset_days", d.get("offset_days", 45))] = whole_number(-3650, 3650)
    elif kind in ("numeric", "state"):
        s[field("trigger_entity", d.get("trigger_entity", ""))] = selector.EntitySelector()
        if kind == "numeric":
            s[field("threshold", d.get("threshold", 3.0))] = finite_threshold()
            s[field("operator", d.get("operator", "below"))] = labelled_options({"below": "Below (<)", "below_or_equal": "Below or equal (≤)", "above": "Above (>)", "above_or_equal": "Above or equal (≥)", "equal": "Equal (=)"})
        else:
            s[field("trigger_state", d.get("trigger_state", "on"))] = str
    return vol.Schema(s)

def allowed_closes(d):
    return {key: label for key, label in CLOSE_LABELS.items()
               if ("calendar_end" not in key or d.get("trigger_type") == "calendar")
               and ("trigger_inactive" not in key or d.get("trigger_type") in ("numeric", "state"))}


def close_choice_schema(d):
    choices = allowed_closes(d)
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
            s[field("close_threshold", d.get("close_threshold", 0.0))] = finite_threshold()
            s[field("close_operator", d.get("close_operator", "above"))] = vol.In(["below", "above", "equal"])
        else:
            s[field("close_state", d.get("close_state", "off"))] = str
    return vol.Schema(s)

class TaskSteps:
    """Shared steps for creation and editing."""
    def initialize(self, values, editing):
        self.values = {**DEFAULTS, **{k: v for k, v in values.items() if k not in ("pause_in_holiday_mode", "pause_entities")}}
        if "calendar_offset_days" not in values and "calendar_offset_hours" in values:
            self.values["calendar_offset_days"] = int(values["calendar_offset_hours"]) // 24
        self.editing = editing

    def _cycle_error(self):
        """Check the complete proposed rule set, not stale wizard fields."""
        configs = {entry.entry_id: {**DEFAULTS, **entry.data, **entry.options}
                   for entry in self.hass.config_entries.async_entries(DOMAIN)}
        candidate = self.entry.entry_id if self.editing else "<new>"
        configs[candidate] = self.values
        if candidate in cyclic_entries(configs, er.async_get(self.hass)):
            return self.async_show_form(
                step_id="trigger_choice", data_schema=trigger_choice_schema(self.values),
                errors={"base": "circular_dependency"})
        return None

    def _validated(self, step, schema, user_input):
        """Validate even direct step calls before modifying wizard state."""
        try:
            return schema(user_input), None
        except vol.Invalid:
            return None, self.async_show_form(
                step_id=step, data_schema=schema, errors={"base": "invalid_settings"})

    async def async_step_details(self, user_input=None):
        schema = details_schema(self.values, self.editing)
        if user_input is not None:
            data, error = self._validated("details", schema, user_input)
            if error is not None:
                return error
            errors = {}
            if not self.editing and (not valid_text(data["name"], 128)
                                    or not internal_key(data["name"])):
                errors["name"] = "invalid_task_name"
            for key in ("friendly_name", "category"):
                if not valid_text(data[key], 128):
                    errors[key] = "invalid_text"
            if errors:
                return self.async_show_form(step_id="details", data_schema=schema, errors=errors)
            self.values.update(data)
            self.values["area_id"] = data.get("area_id")
            return await self.async_step_trigger_choice()
        return self.async_show_form(step_id="details", data_schema=schema)

    async def async_step_trigger_choice(self, user_input=None):
        if user_input is not None:
            data, error = self._validated("trigger_choice", trigger_choice_schema(self.values), user_input)
            if error is not None:
                return error
            self.values.update(data)
            return await self.async_step_trigger_settings()
        return self.async_show_form(step_id="trigger_choice", data_schema=trigger_choice_schema(self.values))

    async def async_step_trigger_settings(self, user_input=None):
        if user_input is not None:
            schema = trigger_schema(self.values)
            data, error = self._validated("trigger_settings", schema, user_input)
            if error is not None:
                return error
            errors = {}
            if "trigger_entity" in data and not valid_entity_id(data["trigger_entity"]):
                errors["trigger_entity"] = "invalid_entity"
            if "trigger_state" in data and not valid_text(data["trigger_state"], 255):
                errors["trigger_state"] = "invalid_text"
            if "calendar_match" in data and not valid_text(data["calendar_match"], 512, required=False):
                errors["calendar_match"] = "invalid_text"
            if data.get("first_due_date"):
                try:
                    data["first_due_date"] = date.fromisoformat(str(data["first_due_date"])).isoformat()
                except ValueError:
                    errors["first_due_date"] = "invalid_settings"
            if errors:
                return self.async_show_form(step_id="trigger_settings", data_schema=schema, errors=errors)
            self.values.update(data)
            # An omitted optional field in the submitted trigger form means
            # it has been cleared. Explicit values override original entry data.
            if self.values.get("trigger_type") == "completion":
                self.values["first_due_date"] = data.get("first_due_date") or None
            if self.values.get("trigger_type") == "calendar":
                self.values["calendar_match"] = data.get("calendar_match") or ""
                self.values.pop("calendar_offset_hours", None)
            if self.values.get("trigger_type") in ("weekly", "monthly", "yearly"):
                self.values["hour"] = 0
                self.values["minute"] = 0
            if self.values.get("trigger_type") == "monthly":
                self.values["schedule_interval"] = 1
            return await self.async_step_close_choice()
        return self.async_show_form(step_id="trigger_settings", data_schema=trigger_schema(self.values))

    async def async_step_close_choice(self, user_input=None):
        if user_input is not None:
            schema = close_choice_schema(self.values)
            data, error = self._validated("close_choice", schema, user_input)
            if error is not None:
                return error
            self.values.update(data)
            return await self.async_step_close_settings()
        return self.async_show_form(step_id="close_choice", data_schema=close_choice_schema(self.values))

    async def async_step_close_settings(self, user_input=None):
        if self.values["close_type"] in ("manual", "calendar_end", "manual_or_calendar_end", "trigger_inactive", "manual_or_trigger_inactive"):
            return await self._finish()
        if user_input is not None:
            schema = close_schema(self.values)
            data, error = self._validated("close_settings", schema, user_input)
            if error is not None:
                return error
            errors = {}
            if not valid_entity_id(data["close_entity"]):
                errors["close_entity"] = "invalid_entity"
            if "close_state" in data and not valid_text(data["close_state"], 255):
                errors["close_state"] = "invalid_text"
            if errors:
                return self.async_show_form(step_id="close_settings", data_schema=schema, errors=errors)
            self.values.update(data)
            return await self._finish()
        return self.async_show_form(step_id="close_settings", data_schema=close_schema(self.values))

class HouseholdTasksConfigFlow(TaskSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        self.initialize({}, False)
        return await self.async_step_details(user_input)

    async def _finish(self):
        if (error := self._cycle_error()) is not None:
            return error
        key = internal_key(self.values["name"])
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
        if (error := self._cycle_error()) is not None:
            return error
        values = {k: v for k, v in self.values.items()
                  if k not in ("pause_in_holiday_mode", "pause_entities", "_reset_trigger_state")}
        return self.async_create_entry(title="", data=values)
