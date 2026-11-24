"""Persistent task scheduler for Household Tasks."""
from __future__ import annotations
import asyncio
import logging
import math
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timedelta, time as datetime_time
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from .scheduling import fixed_schedule
from .date_rules import completion_due_date, offset_due_date, legacy_calendar_days
from .const import DOMAIN, PLATFORMS, DEFAULTS

_LOGGER = logging.getLogger(__name__)

def parse_dt(value):
    if not value:
        return None
    try:
        d = dt_util.parse_datetime(str(value)) or datetime.fromisoformat(str(value))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt_util.get_default_time_zone())
        return dt_util.as_local(d)
    except (ValueError, TypeError, OverflowError):
        return None

def local_midnight(value):
    """Return local midnight for a date or datetime, independent of its time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        day = dt_util.as_local(value).date() if value.tzinfo else value.date()
    else:
        day = value
    return datetime.combine(day, datetime_time.min, dt_util.get_default_time_zone())


def finite_number(value):
    """Return a finite numeric reading, or None for invalid data."""
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def numeric_condition(value, op, threshold):
    """Return True/False for valid comparisons, None for invalid inputs."""
    x, y = finite_number(value), finite_number(threshold)
    if x is None or y is None:
        return None
    return {"below": x < y, "below_or_equal": x <= y, "above": x > y,
            "above_or_equal": x >= y, "equal": x == y}.get(op)


def compare(value, op, threshold):
    """Keep the existing boolean comparison interface."""
    return numeric_condition(value, op, threshold) is True

class Engine:
    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, "household_tasks.history")
        self.history = {}
        self.entries = {}
        self.listeners = []
        self.lock = asyncio.Lock()
        self.unsub_timer = None
        self.unsub_states = None
        self.unsub_registry = None
        self.calendar_cache = {}
        self.evaluation_errors = set()

    async def start(self):
        self.history = await self.store.async_load() or {}
        self.unsub_timer = async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))
        self.unsub_states = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_event)
        self.unsub_registry = self.hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, self._registry_event)
        for name in ("complete", "complete_early", "reopen", "skip", "snooze"):
            self.hass.services.async_register(DOMAIN, name, getattr(self, "_service_" + name))
        self.publish_open_tasks()

    def state(self, entry):
        return self.history.setdefault(entry.entry_id, {
            "created": dt_util.now().isoformat(), "open": False,
            "last_completed": None, "last_opened": None,
            "completed_token": None, "active_token": None,
            "snooze_until": None, "events": []
        })

    def conf(self, entry):
        return {**DEFAULTS, **entry.data, **entry.options}

    def opening_signature(self, entry):
        """Persist only settings that actually affect the opening rule."""
        c = self.conf(entry)
        kind = c["trigger_type"]
        signature = {"kind": kind}
        integer_fields = {
            "completion": ("interval_days",), "weekly": ("weekday",),
            "monthly": ("month_day", "schedule_interval"),
            "yearly": ("year_month", "month_day", "schedule_interval"),
            "date_entity": ("offset_days",),
        }
        for key in integer_fields.get(kind, ()):
            signature[key] = int(c.get(key, DEFAULTS[key]))
        if kind in ("numeric", "state", "calendar", "date_entity"):
            signature["trigger_entity"] = c.get("trigger_entity")
        if kind == "completion":
            date = c.get("first_due_date")
            signature["first_due_date"] = str(date) if date else None
        elif kind == "numeric":
            threshold = c.get("threshold", DEFAULTS["threshold"])
            # Non-finite floats must never be persisted in the signature.
            signature["threshold"] = finite_number(threshold)
            signature["operator"] = c.get("operator", DEFAULTS["operator"])
        elif kind == "state":
            signature["trigger_state"] = c.get("trigger_state", DEFAULTS["trigger_state"])
        elif kind == "calendar":
            raw = {**getattr(entry, "data", {}), **getattr(entry, "options", {})}
            signature["calendar_offset_days"] = legacy_calendar_days(raw)
            signature["calendar_match"] = str(c.get("calendar_match", "")).casefold().strip()
        return signature

    def reconcile_opening_rule(self, entry, state, now):
        """Reset obsolete occurrence state without erasing task history."""
        signature = self.opening_signature(entry)
        previous = state.get("opening_signature")
        if previous == signature:
            return False
        state["opening_signature"] = signature
        # Upgrades from older versions establish a baseline, never reset history.
        if previous is None:
            return False
        for key in ("active_token", "completed_token", "next_token", "calendar_end",
                    "latched", "schedule_anchor", "pending_rebind", "carried_open"):
            state.pop(key, None)
        if state.get("open"):
            state["pending_rebind"] = True
            state["carried_open"] = True
        else:
            state.pop("due_at_open", None)
            state.pop("due", None)
        if signature["kind"] in ("weekly", "monthly", "yearly"):
            # Start changed fixed schedules on the edit date, not a past slot.
            state["schedule_anchor"] = local_midnight(now).isoformat()
        return True

    def publish_open_tasks(self):
        names = sorted((self.conf(e).get("friendly_name") or e.title
                        for e in self.entries.values()
                        if self.state(e).get("open")), key=str.casefold)
        if not names:
            announcement = "There are no outstanding household tasks."
        elif len(names) == 1:
            announcement = "One household task is outstanding: " + names[0] + "."
        else:
            announcement = f"{len(names)} household tasks are outstanding: " + ", ".join(names[:-1]) + ", and " + names[-1] + "."
        self.hass.states.async_set("sensor.household_tasks_open_names", len(names), {
            "friendly_name": "Household Tasks Open List",
            "icon": "mdi:format-list-checks",
            "names": names, "tasks": names, "announcement": announcement, "count": len(names)
        })

        # The newest outstanding task is selected by its persisted opening time,
        # not by its name or the order in which config entries were loaded.
        open_entries = [e for e in self.entries.values() if self.state(e).get("open")]
        latest = max(
            open_entries,
            key=lambda e: (parse_dt(self.state(e).get("last_opened")) or
                           datetime.min.replace(tzinfo=dt_util.get_default_time_zone()),
                           e.entry_id),
            default=None,
        )
        if latest is None:
            latest_name = "none"
            latest_attributes = {
                "friendly_name": "Household Tasks Latest Open",
                "icon": "mdi:clipboard-text-clock-outline",
                "task_name": None,
                "opened_at": None, "open_count": 0,
            }
        else:
            latest_name = self.conf(latest).get("friendly_name") or latest.title
            latest_attributes = {
                "friendly_name": "Household Tasks Latest Open",
                "icon": "mdi:clipboard-text-clock-outline",
                "task_name": latest_name,
                "opened_at": self.state(latest).get("last_opened"),
                "open_count": len(open_entries),
            }
        self.hass.states.async_set(
            "sensor.household_tasks_latest_open", latest_name, latest_attributes
        )

    def notify(self, entry_id):
        self.publish_open_tasks()
        for listener in list(self.listeners):
            listener(entry_id)

    async def save(self):
        await self.store.async_save(self.history)

    def record(self, s, action, now):
        s.setdefault("events", []).append({"action": action, "at": now.isoformat()})
        s["events"] = s["events"][-100:]

    @staticmethod
    def calendar_window_end(now, offset_days):
        """Cover at least 90 days of events and 30 days of upcoming reminders."""
        # Query through the entire final local calendar day. An event later
        # that day can produce a reminder due at midnight.
        horizon = max(90, offset_days + 30)
        return local_midnight(now) + timedelta(days=horizon + 1)

    async def calendar_events(self, entity_id, now, offset_days=0):
        """Share a sufficiently wide calendar response across loaded tasks."""
        max_offset = offset_days
        for other in self.entries.values():
            config = self.conf(other)
            if config["trigger_type"] != "calendar" or config.get("trigger_entity") != entity_id:
                continue
            try:
                other_offset = legacy_calendar_days({**other.data, **other.options})
            except (ValueError, TypeError, OverflowError):
                # A malformed sibling task must not block valid calendar tasks.
                continue
            if 0 <= other_offset <= 365:
                max_offset = max(max_offset, other_offset)
        end = self.calendar_window_end(now, max_offset)
        cached = self.calendar_cache.get(entity_id)
        covers_window = (cached is not None and cached[0] <= now
                         and cached[2] >= end)
        if covers_window and now - cached[0] < timedelta(minutes=5):
            return cached[1]
        if not self.hass.services.has_service("calendar", "get_events"):
            return None
        try:
            response = await self.hass.services.async_call(
                "calendar", "get_events",
                {"entity_id": entity_id,
                 "start_date_time": (now - timedelta(days=3)).isoformat(),
                 "end_date_time": end.isoformat()},
                blocking=True, return_response=True,
            )
            events = response.get(entity_id, {}).get("events", [])
            self.calendar_cache[entity_id] = (now, events, end)
            return events
        except Exception:
            # Do not represent a narrower response as covering a longer offset.
            # Stale-data trust and expiry are addressed separately.
            task_end = self.calendar_window_end(now, offset_days)
            covers_task = (cached is not None and cached[0] <= now
                           and cached[2] >= task_end)
            return cached[1] if covers_task else None

    async def calculate_calendar(self, entry, now):
        c, s = self.conf(entry), self.state(entry)
        entity_id = c.get("trigger_entity", "")
        offset_days = legacy_calendar_days({**entry.data, **entry.options})
        if not 0 <= offset_days <= 365:
            raise ValueError("Calendar reminder offset must be between 0 and 365 days")
        window_end = self.calendar_window_end(now, offset_days)
        events = await self.calendar_events(entity_id, now, offset_days)
        if events is None:
            if s.get("open") and not s.get("pending_rebind"):
                return (parse_dt(s.get("due_at_open")) or parse_dt(s.get("due")),
                        s.get("active_token"), False, parse_dt(s.get("calendar_end")))
            return None, None, False, None
        match = str(c.get("calendar_match", "")).casefold().strip()
        offset = timedelta(days=offset_days)
        candidates = []
        for event in events:
            title = str(event.get("summary") or event.get("message") or "")
            if match and match not in title.casefold():
                continue
            start = parse_dt(event.get("start"))
            end = parse_dt(event.get("end"))
            if not start:
                continue
            token = "calendar:" + start.isoformat() + ":" + title
            candidates.append((local_midnight(offset_due_date(start.date(), offset.days)), token, start, end))
        candidates.sort(key=lambda item: item[0])
        # Keep an open task tied to its original event, even when the
        # calendar starts advertising a later collection.
        if s.get("open") and not s.get("pending_rebind"):
            current = next((item for item in candidates if item[1] == s.get("active_token")), None)
            original_due = parse_dt(s.get("due_at_open")) or parse_dt(s.get("due"))
            if current:
                due, token, _, end = current
                return original_due or due, token, now >= due and (end is None or now < end), end
            # An outstanding occurrence must not be replaced by a newer event,
            # even if its original event is absent or has no recorded end.
            return original_due, s.get("active_token"), False, parse_dt(s.get("calendar_end"))
        # Do not reopen a completed occurrence. Select the next matching event.
        for due, token, start, end in candidates:
            if start >= window_end or token == s.get("completed_token") or (end is not None and end <= now):
                continue
            return due, token, now >= due, end
        return None, None, False, None

    def calculate(self, entry, now):
        c, s = self.conf(entry), self.state(entry)
        kind = c["trigger_type"]
        due = token = end = None
        active = False
        if kind == "completion":
            completed = parse_dt(s.get("last_completed"))
            base = completed or parse_dt(s["created"]) or now
            first_due = parse_dt(c.get("first_due_date")) if not completed else None
            due = local_midnight(first_due) if first_due else local_midnight(completion_due_date(base.date(), c["interval_days"]))
            token = "completion:" + (base.isoformat() if completed or not first_due else "initial:" + first_due.isoformat())
            active = now >= due
        elif kind in ("monthly", "yearly"):
            due, token = fixed_schedule(
                now, c,
                parse_dt(s.get("schedule_anchor")) or
                (None if s.get("last_completed") else parse_dt(s.get("created"))),
                s.get("completed_token"),
            )
            active = now >= due
        elif kind == "weekly":
            slot = now.replace(hour=0, minute=0, second=0, microsecond=0)
            slot -= timedelta(days=(slot.weekday() - int(c["weekday"])) % 7)
            # The most recent slot is due only once its time has arrived.
            if slot > now:
                slot -= timedelta(days=7)
            previous_token = "weekly:" + slot.isoformat()
            completed_slot = parse_dt(s.get("completed_token", "")[7:]) if str(s.get("completed_token", "")).startswith("weekly:") else None
            if completed_slot and completed_slot >= slot:
                slot = completed_slot + timedelta(days=7)
            elif not completed_slot:
                anchor = parse_dt(s.get("schedule_anchor")) or (
                    parse_dt(s.get("created")) if s.get("last_completed") is None else None)
                if anchor and anchor > slot:
                    slot += timedelta(days=7)
            due = slot
            token = "weekly:" + slot.isoformat()
            active = now >= slot
        elif kind == "date_entity":
            entity = self.hass.states.get(c.get("trigger_entity", ""))
            raw = entity.state if entity else None
            if entity and raw in ("unknown", "unavailable", ""):
                raw = entity.attributes.get("date") or entity.attributes.get("timestamp")
            date = parse_dt(raw)
            if date:
                due = local_midnight(offset_due_date(date.date(), c["offset_days"]))
                token = "date:" + date.isoformat()
                active = now >= due
        elif kind in ("numeric", "state"):
            entity = self.hass.states.get(c.get("trigger_entity", ""))
            value = entity.state if entity else None
            active = (numeric_condition(value, c["operator"], c["threshold"]) if kind == "numeric"
                      else None if value in (None, "unknown", "unavailable", "")
                      else value == c["trigger_state"])
            token = "entity:" + str(c.get("trigger_entity"))
            if active is None:
                # Invalid readings are not a new occurrence or an inactive edge.
                due = parse_dt(s.get("due"))
            elif active:
                # Keep the current occurrence's due time stable while active,
                # including after completion or while snoozed.
                due = parse_dt(s.get("due_at_open")) or parse_dt(s.get("due")) or now
        return due, token, active, end

    def close_condition(self, entry, now, calendar_end):
        c = self.conf(entry)
        rule = c["close_type"]
        if rule == "manual":
            return False
        if "trigger_inactive" in rule:
            if c["trigger_type"] not in ("numeric", "state"):
                return False
            entity = self.hass.states.get(c.get("trigger_entity", ""))
            if entity is None or entity.state in ("unknown", "unavailable", ""):
                return False
            if c["trigger_type"] == "numeric":
                return numeric_condition(entity.state, c["operator"], c["threshold"]) is False
            return entity.state != c["trigger_state"]
        if "calendar_end" in rule:
            return calendar_end is not None and now >= calendar_end
        entity = self.hass.states.get(c.get("close_entity", ""))
        if not entity or entity.state in ("unknown", "unavailable", ""):
            return False
        if "entity_numeric" in rule:
            return compare(entity.state, c["close_operator"], c["close_threshold"])
        if "entity_state" in rule:
            return entity.state == c["close_state"]
        return False

    def entity_warnings(self, entry):
        """Return missing/unavailable dependencies without changing task state."""
        c = self.conf(entry)
        fields = []
        if c["trigger_type"] in ("calendar", "date_entity", "numeric", "state"):
            fields.append(("trigger_entity", "Opening trigger"))
        if "entity_state" in c["close_type"] or "entity_numeric" in c["close_type"]:
            fields.append(("close_entity", "Closing condition"))
        warnings = []
        for key, label in fields:
            entity_id = c.get(key)
            state = self.hass.states.get(entity_id) if entity_id else None
            if not entity_id or state is None:
                warnings.append(f"{label}: entity missing ({entity_id or 'not configured'})")
            elif state.state in ("unknown", "unavailable", ""):
                warnings.append(f"{label}: {entity_id} is {state.state or 'empty'}")
            elif ((key == "trigger_entity" and c["trigger_type"] == "numeric")
                  or (key == "close_entity" and "entity_numeric" in c["close_type"])):
                if finite_number(state.state) is None:
                    warnings.append(f"{label}: {entity_id} has an invalid numeric reading")
                threshold_key = "threshold" if key == "trigger_entity" else "close_threshold"
                operator_key = "operator" if key == "trigger_entity" else "close_operator"
                if numeric_condition(0, c.get(operator_key), c.get(threshold_key)) is None:
                    warnings.append(f"{label}: invalid numeric condition configuration")
        return warnings

    @staticmethod
    def completed_early_today(state, now):
        """Check persisted history in Home Assistant's local calendar day."""
        today = dt_util.as_local(now).date()
        return any(
            event.get("action") == "completed_early"
            and (at := parse_dt(event.get("at"))) is not None
            and at.date() == today
            for event in state.get("events", [])
        )

    async def evaluate(self, entry):
        async with self.lock:
            now = dt_util.now()
            s = self.state(entry)
            old = deepcopy(s)
            try:
                rule_changed = self.reconcile_opening_rule(entry, s, now)
                if self.conf(entry)["trigger_type"] == "calendar":
                    due, token, active, end = await self.calculate_calendar(entry, now)
                else:
                    due, token, active, end = self.calculate(entry, now)
            except (Exception, asyncio.CancelledError):
                # Retry the configuration reconciliation on the next evaluation
                # if the new rule could not be calculated.
                s.clear()
                s.update(old)
                raise
            # A carried-over open task keeps its original opening date. Bind
            # it to the new rule only when an actual occurrence becomes active.
            if s.get("open") and s.get("pending_rebind"):
                if active and token is not None:
                    s["active_token"] = token
                    s.pop("pending_rebind", None)
                    if self.conf(entry)["trigger_type"] in ("numeric", "state"):
                        s["latched"] = True
                else:
                    end = None
            if s.get("open") and s.get("carried_open"):
                due = parse_dt(s.get("due_at_open")) or due
            s["completed_early_today"] = self.completed_early_today(s, now)
            s["entity_warnings"] = self.entity_warnings(entry)
            s["due"] = due.isoformat() if due else None
            if self.conf(entry)["trigger_type"] == "calendar" and s.get("open") and end is not None:
                s["calendar_end"] = end.isoformat()
            s["next_token"] = token
            if s.get("snooze_until") and (parse_dt(s["snooze_until"]) or now) <= now:
                s["snooze_until"] = None
                if self.conf(entry)["trigger_type"] in ("numeric", "state"):
                    s["latched"] = False
                    s["completed_token"] = None
            snoozed = bool(s.get("snooze_until"))
            if s.get("open") and not rule_changed and self.close_condition(entry, now, end):
                self._finish(s, now, "auto_completed")
                if s.get("active_token"):
                    s["completed_token"] = s["active_token"]
            if not s.get("open") and active and not snoozed and token != s.get("completed_token"):
                if self.conf(entry)["trigger_type"] not in ("numeric", "state") or not s.get("latched"):
                    s["open"] = True
                    s["last_opened"] = now.isoformat()
                    if self.conf(entry)["trigger_type"] in ("numeric", "state"):
                        due = now
                        s["due"] = now.isoformat()
                    s["due_at_open"] = due.isoformat() if due else None
                    s["active_token"] = token
                    s.pop("pending_rebind", None)
                    s.pop("carried_open", None)
                    if self.conf(entry)["trigger_type"] == "calendar":
                        # Only persist an end time belonging to this occurrence.
                        s["calendar_end"] = end.isoformat() if end else None
                    self.record(s, "opened", now)
                    if self.conf(entry)["trigger_type"] in ("numeric", "state"):
                        s["latched"] = True
            if self.conf(entry)["trigger_type"] == "calendar" and not s.get("open") and not snoozed:
                if s.get("calendar_end") is not None:
                    s["calendar_end"] = None
            if active is False and self.conf(entry)["trigger_type"] in ("numeric", "state"):
                s["latched"] = False
                s["completed_token"] = None
            if old != s:
                # Calculated display fields do not warrant a history write.
                # They are recomputed on setup and every evaluation.
                transient = {"due", "next_token", "entity_warnings", "completed_early_today"}
                if ("due" not in old or
                        {k: v for k, v in old.items() if k not in transient}
                        != {k: v for k, v in s.items() if k not in transient}):
                    await self.save()
                self.notify(entry.entry_id)

    def _finish(self, s, now, action):
        s["open"] = False
        s.pop("pending_rebind", None)
        s.pop("carried_open", None)
        s["last_completed"] = now.isoformat()
        s["snooze_until"] = None
        self.record(s, action, now)

    async def act(self, entry_id, action, days=1):
        entry = self.entries.get(entry_id)
        if not entry:
            raise ValueError("Unknown task entry")
        async with self.lock:
            s, now = self.state(entry), dt_util.now()
            close_type = self.conf(entry)["close_type"]
            if action in ("complete", "complete_early") and not (close_type == "manual" or close_type.startswith("manual_or_")):
                raise ValueError("This task is configured for automatic completion only")
            if action == "complete_early":
                # Block duplicate early completions on the same local day.
                if self.completed_early_today(s, now):
                    raise ValueError("Task already completed early today")
                kind = self.conf(entry)["trigger_type"]
                if kind not in ("completion", "weekly", "monthly", "yearly"):
                    raise ValueError("Early completion is only supported for completion and fixed schedules")
                if s.get("open"):
                    raise ValueError("Task is already open; use normal completion")
                due, token, active, _ = self.calculate(entry, now)
                if not due or active or due <= now:
                    raise ValueError("Task has no future scheduled occurrence to complete early")
                # Record the future occurrence as satisfied without opening it.
                # Completion-relative tasks will recalculate from this timestamp.
                self._finish(s, now, "completed_early")
                s["completed_token"] = token
                s["next_token"] = token
                s["completed_early_today"] = True
            elif action in ("complete", "skip"):
                if not s.get("open"):
                    return
                token = s.get("active_token")
                self._finish(s, now, "completed" if action == "complete" else "skipped")
                s["completed_token"] = token
            elif action == "reopen":
                s["open"] = True
                s["completed_token"] = None
                s["latched"] = True
                s["last_opened"] = now.isoformat()
                s["due_at_open"] = s.get("due")
                self.record(s, "reopened", now)
            elif action == "snooze":
                if not s.get("open"):
                    return
                if days < 1:
                    raise ValueError("Snooze days must be positive")
                s["open"] = False
                s["snooze_until"] = (now + timedelta(days=days)).isoformat()
                self.record(s, "snoozed", now)
            await self.save()
            self.notify(entry_id)
        await self.evaluate(entry)

    def find_entry(self, call):
        entry_id = call.data.get("entry_id")
        if entry_id in self.entries:
            return entry_id
        task = call.data.get("task")
        for entry in self.entries.values():
            if entry.unique_id == task or entry.title == task:
                return entry.entry_id
        raise ValueError("Specify a valid entry_id or task name")

    async def _service_complete(self, call):
        await self.act(self.find_entry(call), "complete")

    async def _service_complete_early(self, call):
        await self.act(self.find_entry(call), "complete_early")

    async def _service_reopen(self, call):
        await self.act(self.find_entry(call), "reopen")

    async def _service_skip(self, call):
        await self.act(self.find_entry(call), "skip")

    async def _service_snooze(self, call):
        await self.act(self.find_entry(call), "snooze", int(call.data.get("days", 1)))

    async def _evaluate_background(self, entry):
        """Isolate background evaluation failures and retry on future ticks."""
        entry_id = entry.entry_id
        if self.entries.get(entry_id) is not entry:
            return
        try:
            await self.evaluate(entry)
        except Exception:
            # Cancellation is deliberately not caught. Keep warnings transient
            # and log the traceback only on transition into a failed state.
            if self.entries.get(entry_id) is not entry:
                return
            if entry_id not in self.evaluation_errors:
                self.evaluation_errors.add(entry_id)
                _LOGGER.exception("Unable to evaluate household task %s; will retry", entry_id)
                self._notify_evaluation_warning(entry_id)
        else:
            if entry_id in self.evaluation_errors:
                self.evaluation_errors.discard(entry_id)
                self._notify_evaluation_warning(entry_id)

    def _notify_evaluation_warning(self, entry_id):
        """Refresh task warnings without writing history or aggregate states."""
        for listener in list(self.listeners):
            try:
                listener(entry_id)
            except Exception:
                _LOGGER.exception("Unable to publish evaluation warning for task %s", entry_id)

    async def _tick(self, now):
        for entry in list(self.entries.values()):
            await self._evaluate_background(entry)

    @callback
    def _registry_event(self, event):
        """Follow entity_id renames reported by the entity registry."""
        data = event.data
        if data.get("action") != "update":
            return
        old_id = data.get("old_entity_id")
        new_id = data.get("entity_id")
        if not old_id or not new_id or old_id == new_id:
            return
        self.hass.async_create_task(self._apply_entity_rename(old_id, new_id))

    async def _apply_entity_rename(self, old_id, new_id):
        for entry in list(self.entries.values()):
            c = self.conf(entry)
            changes = {}
            if c.get("trigger_entity") == old_id:
                changes["trigger_entity"] = new_id
            if c.get("close_entity") == old_id:
                changes["close_entity"] = new_id
            if not changes:
                continue
            # A registry rename is the same source, not a new opening rule.
            # Update the persisted identity alongside the config entry.
            async with self.lock:
                state = self.state(entry)
                old_signature = self.opening_signature(entry)
                changed = False
                if "trigger_entity" in changes:
                    if state.get("opening_signature") == old_signature:
                        state["opening_signature"] = {
                            **old_signature, "trigger_entity": new_id}
                        changed = True
                    if old_signature["kind"] in ("numeric", "state"):
                        for key in ("active_token", "completed_token", "next_token"):
                            if state.get(key) == "entity:" + old_id:
                                state[key] = "entity:" + new_id
                                changed = True
                options = {**entry.options, **changes}
                self.hass.config_entries.async_update_entry(entry, options=options)
                if changed:
                    await self.save()

    @callback
    def _state_event(self, event):
        entity_id = event.data.get("entity_id")
        for entry in list(self.entries.values()):
            c = self.conf(entry)
            if entity_id in (c.get("trigger_entity"), c.get("close_entity")):
                if c.get("trigger_type") == "calendar":
                    self.calendar_cache.pop(entity_id, None)
                self.hass.async_create_task(self._evaluate_background(entry))

async def async_setup(hass: HomeAssistant, config):
    await hass.http.async_register_static_paths([
        StaticPathConfig(
            "/household_tasks/household-tasks-card.js",
            str(Path(__file__).parent / "frontend" / "household-tasks-card.js"),
            cache_headers=False,
        )
    ])
    engine = Engine(hass)
    hass.data[DOMAIN] = engine
    await engine.start()
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    engine = hass.data[DOMAIN]
    engine.entries[entry.entry_id] = entry
    engine.state(entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await engine.evaluate(entry)
    engine.publish_open_tasks()
    return True

async def _options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].entries.pop(entry.entry_id, None)
        hass.data[DOMAIN].evaluation_errors.discard(entry.entry_id)
        hass.data[DOMAIN].publish_open_tasks()
    return ok

async def async_remove_entry(hass, entry):
    engine = hass.data.get(DOMAIN)
    if engine:
        engine.history.pop(entry.entry_id, None)
        await engine.save()
