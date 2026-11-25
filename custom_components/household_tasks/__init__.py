"""Persistent task scheduler for Household Tasks."""
from __future__ import annotations
import asyncio
import logging
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta, time as datetime_time
import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import HomeAssistant, callback, CoreState
from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv, discovery, entity_registry as er
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.setup import async_when_setup
from homeassistant.util import dt as dt_util
from .scheduling import fixed_schedule
from .date_rules import completion_due_date, offset_due_date, legacy_calendar_days
from .const import DOMAIN, PLATFORMS, DEFAULTS
from .dependencies import active_entity_ids, cyclic_entries

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _nonblank_selector(value):
    """Validate an identifier without coercing or rewriting it."""
    if not isinstance(value, str) or not value.strip():
        raise vol.Invalid("Task selector must be a non-blank string")
    return value


def _require_task_selector(data):
    """Require exactly one unambiguous selector field."""
    if ("entry_id" in data) == ("task" in data):
        raise vol.Invalid("Specify exactly one of entry_id or task")
    return data


COMPLETION_SERVICE_SCHEMA = vol.Schema(vol.All(
    vol.Schema({
        vol.Optional("entry_id"): _nonblank_selector,
        vol.Optional("task"): _nonblank_selector,
    }, extra=vol.PREVENT_EXTRA),
    _require_task_selector,
))

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

def date_entity_value(entity):
    """Never trust retained attributes of an unavailable/unknown entity."""
    if entity is None or entity.state in (None, "unknown", "unavailable", ""):
        return None
    date = parse_dt(entity.state)
    if date is not None:
        return date
    # Available attribute-based sources (for example a template sensor) are
    # supported when their primary state is not itself a date.
    attributes = getattr(entity, "attributes", {})
    return parse_dt(attributes.get("date") or attributes.get("timestamp"))


def valid_history_record(record):
    """Validate scheduling-critical values without rewriting stored history."""
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("created"), str) or parse_dt(record["created"]) is None:
        return False
    if not isinstance(record.get("open"), bool) or not isinstance(record.get("events"), list):
        return False
    for key in ("last_completed", "last_opened", "due", "due_at_open",
                "calendar_end", "schedule_anchor", "snooze_until"):
        value = record.get(key)
        if value is not None and (not isinstance(value, str) or parse_dt(value) is None):
            return False
    for key in ("active_token", "completed_token", "next_token"):
        value = record.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return False
    for key in ("latched", "pending_rebind", "carried_open", "completed_early_today"):
        if key in record and not isinstance(record[key], bool):
            return False
    signature = record.get("opening_signature")
    if signature is not None and (not isinstance(signature, dict)
                                  or not isinstance(signature.get("kind"), str)):
        return False
    warnings = record.get("entity_warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
        return False
    return all(isinstance(event, dict)
               and isinstance(event.get("action"), str)
               and bool(event["action"].strip())
               and isinstance(event.get("at"), str)
               and parse_dt(event["at"]) is not None
               for event in record["events"])


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

@dataclass
class EvaluationJob:
    """One coalescing background worker per task entry ID."""
    entry: ConfigEntry
    pending: bool = True
    task: asyncio.Task | None = None


class Engine:
    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, "household_tasks.history", private=True, atomic_writes=True)
        self.history = {}
        self.invalid_history = set()
        self.storage_dirty = False
        self.storage_failed = False
        self.storage_retry = None
        self.reloading_entries = {}
        self.last_aggregate_names = {}
        self.calendar_requests = {}
        self.entries = {}
        self.listeners = []
        self.aggregate_listeners = []
        self.lock = asyncio.Lock()
        self.unsub_timer = None
        self.entity_dependencies = {}
        self.entity_subscriptions = {}
        self.unsub_registry = None
        self.calendar_cache = {}
        self.calendar_failures = {}
        self.evaluation_errors = set()
        self.cyclic_entries = set()
        self.evaluation_jobs = {}
        self.tick_running = False

    async def start(self):
        loaded = await self.store.async_load()
        if loaded is not None and not isinstance(loaded, dict):
            raise HomeAssistantError("Invalid household task history; restore from backup")
        self.history = loaded or {}
        self.invalid_history = {key for key, record in self.history.items()
                                if not valid_history_record(record)}
        for key in sorted(self.invalid_history):
            _LOGGER.error("Malformed household task history for entry %s; task paused. Restore a valid backup.", key)
        for name in ("complete", "complete_early"):
            self.hass.services.async_register(
                DOMAIN, name, getattr(self, "_service_" + name),
                schema=COMPLETION_SERVICE_SCHEMA,
            )

    @callback
    def refresh_runtime_listeners(self):
        """Keep shared scheduler listeners only while tasks are loaded."""
        if self.entries or self.storage_dirty:
            if self.unsub_timer is None:
                self.unsub_timer = async_track_time_interval(
                    self.hass, self._tick, timedelta(minutes=1))
        elif self.unsub_timer is not None:
            unsubscribe, self.unsub_timer = self.unsub_timer, None
            unsubscribe()
        if self.entries and self.unsub_registry is None:
            self.unsub_registry = self.hass.bus.async_listen(
                EVENT_ENTITY_REGISTRY_UPDATED, self._registry_event)
        elif not self.entries and self.unsub_registry is not None:
            unsubscribe, self.unsub_registry = self.unsub_registry, None
            unsubscribe()

    async def detach_entry(self, entry):
        """Stop routing work to an entry and drain existing writes before reload."""
        entry_id = entry.entry_id
        if self.entries.get(entry_id) is not entry:
            return
        self.entries.pop(entry_id)
        self.evaluation_errors.discard(entry_id)
        self.refresh_entity_subscriptions()
        self.refresh_runtime_listeners()
        self.prune_calendar_cache(dt_util.now())
        self.publish_open_tasks()
        job = self.evaluation_jobs.get(entry_id)
        if job is not None and job.task is not None:
            job.pending = False
            # Do not cancel a worker which may be writing shared history.
            await asyncio.gather(asyncio.shield(job.task), return_exceptions=True)
        # Direct service calls and registry changes also use the shared lock.
        # Let any earlier mutation finish before permitting a reload/removal.
        async with self.lock:
            pass

    def state(self, entry):
        if entry.entry_id in self.invalid_history:
            # Safe display projection only; preserve the original record verbatim.
            return {"open": False, "events": []}
        return self.history.setdefault(entry.entry_id, {
            "created": dt_util.now().isoformat(), "open": False,
            "last_completed": None, "last_opened": None,
            "completed_token": None, "active_token": None,
            "events": []
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

    def aggregate_name(self, entry):
        held = self.reloading_entries.get(entry.entry_id)
        return held[1] if held is not None else self.conf(entry).get("friendly_name") or entry.title

    def aggregate_snapshot(self):
        """Return the existing aggregate sensor states and attributes."""
        # Keep a task's last published aggregate membership during its options reload.
        open_entries = [e for e in self.entries.values()
                        if e.entry_id not in self.reloading_entries and self.state(e).get("open")]
        open_entries.extend(e for e, _ in self.reloading_entries.values() if self.state(e).get("open"))
        names = sorted((self.aggregate_name(e)
                        for e in open_entries), key=str.casefold)
        if not names:
            announcement = "There are no outstanding household tasks."
        elif len(names) == 1:
            announcement = "One household task is outstanding: " + names[0] + "."
        else:
            announcement = f"{len(names)} household tasks are outstanding: " + ", ".join(names[:-1]) + ", and " + names[-1] + "."
        open_attributes = {
            "names": names, "tasks": list(names), "announcement": announcement, "count": len(names)
        }

        # Select the newest outstanding occurrence using the existing ordering.
        latest = max(
            open_entries,
            key=lambda e: (parse_dt(self.state(e).get("last_opened")) or
                           datetime.min.replace(tzinfo=dt_util.get_default_time_zone()),
                           e.entry_id),
            default=None,
        )
        if latest is None:
            latest_name = "none"
            latest_attributes = {"task_name": None, "opened_at": None, "open_count": 0}
        else:
            latest_name = self.aggregate_name(latest)
            latest_attributes = {
                "task_name": latest_name,
                "opened_at": self.state(latest).get("last_opened"),
                "open_count": len(open_entries),
            }
        return {
            "open_names": (len(names), open_attributes),
            "latest_open": (latest_name[:MAX_LENGTH_STATE_STATE], latest_attributes),
        }

    def publish_open_tasks(self):
        """Notify registered aggregate entities, never write raw states."""
        self.last_aggregate_names = {
            entry_id: self.aggregate_name(entry) for entry_id, entry in self.entries.items()
        }
        if not self.aggregate_listeners:
            return
        snapshot = self.aggregate_snapshot()
        for listener in list(self.aggregate_listeners):
            try:
                listener(snapshot)
            except Exception:
                _LOGGER.error("Unable to update a household task aggregate sensor")
                _LOGGER.debug("Aggregate sensor update traceback", exc_info=True)

    def notify(self, entry_id):
        self.publish_open_tasks()
        self._notify_evaluation_warning(entry_id)

    def _storage_warning(self, failed):
        if self.storage_failed == failed:
            return
        self.storage_failed = failed
        for entry_id in list(self.entries):
            self._notify_evaluation_warning(entry_id)

    async def save(self):
        """Keep unsaved history in memory and retry detectable write failures.

        Store can swallow some write errors. Verify through its public load API
        after each write, except when HA deliberately defers writes at shutdown.
        The caller holds the shared history lock.
        """
        self.storage_dirty = True
        snapshot = deepcopy(self.history)
        try:
            await self.store.async_save(snapshot)
            if self.hass.state is not CoreState.stopping:
                if await self.store.async_load() != snapshot:
                    raise HomeAssistantError("Household task history write not verified")
        except asyncio.CancelledError:
            self._storage_warning(True)
            raise
        except Exception:
            if not self.storage_failed:
                _LOGGER.error("Household task history could not be saved; retrying")
            self.storage_retry = dt_util.utcnow() + timedelta(minutes=1)
            self._storage_warning(True)
            self.refresh_runtime_listeners()
            return False
        self.storage_dirty = False
        self.storage_retry = None
        self._storage_warning(False)
        self.refresh_runtime_listeners()
        return True

    async def retry_storage(self, now):
        if not self.storage_dirty:
            return
        async with self.lock:
            if self.storage_dirty and (self.storage_retry is None or
                                       dt_util.as_utc(now) >= self.storage_retry):
                await self.save()

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

    def _calendar_failed(self, entity_id, now, reason):
        """Back off per calendar, sharing failures across dependent tasks."""
        now = dt_util.as_utc(now)
        previous = self.calendar_failures.get(entity_id)
        count = min(previous[2] + 1, 5) if previous else 1
        retry = now + timedelta(minutes=min(2 ** (count - 1), 15))
        self.calendar_failures[entity_id] = (now, retry, count, reason)
        if previous is None:
            _LOGGER.warning("Calendar data unavailable for %s; will retry", entity_id)

    @staticmethod
    def _validated_calendar_events(response, entity_id):
        """Reject incomplete or malformed responses, not as empty calendars."""
        if not isinstance(response, dict):
            raise ValueError("Invalid calendar response")
        payload = response.get(entity_id)
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ValueError("Missing calendar events")
        events = payload["events"]
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Invalid calendar event")
            start = parse_dt(event.get("start"))
            end = parse_dt(event.get("end"))
            if (start is None or
                    (event.get("end") is not None and end is None) or
                    (end is not None and end < start)):
                raise ValueError("Invalid calendar event dates")
            for key in ("summary", "message"):
                if event.get(key) is not None and not isinstance(event[key], str):
                    raise ValueError("Invalid calendar event title")
        # Keep only fields used for matching and scheduling. Do not retain
        # descriptions, locations, attendees or provider-specific metadata.
        return [{key: event[key] for key in ("start", "end", "summary", "message")
                 if key in event} for event in events]

    def prune_calendar_cache(self, now):
        """Discard expired data and calendars no longer referenced by tasks."""
        active = {self.conf(entry).get("trigger_entity") for entry in self.entries.values()
                  if self.conf(entry)["trigger_type"] == "calendar"}
        for entity_id, cached in list(self.calendar_cache.items()):
            age = dt_util.as_utc(now) - dt_util.as_utc(cached[0])
            if entity_id not in active or not timedelta(0) <= age < timedelta(minutes=30):
                self.calendar_cache.pop(entity_id, None)
        for entity_id in self.calendar_failures.keys() - active:
            self.calendar_failures.pop(entity_id, None)
        for entity_id in self.calendar_requests.keys() - active:
            self.calendar_requests[entity_id]["invalidated"] = True

    def invalidate_calendar(self, entity_id):
        self.calendar_cache.pop(entity_id, None)
        if request := self.calendar_requests.get(entity_id):
            request["invalidated"] = True

    async def calendar_events(self, entity_id, now, offset_days=0):
        """Return fresh events, or recent cached events for display during failure."""
        max_offset = offset_days
        for other in self.entries.values():
            config = self.conf(other)
            if config["trigger_type"] != "calendar" or config.get("trigger_entity") != entity_id:
                continue
            try:
                other_offset = legacy_calendar_days({**other.data, **other.options})
            except (ValueError, TypeError, OverflowError):
                continue
            if 0 <= other_offset <= 365:
                max_offset = max(max_offset, other_offset)
        end = self.calendar_window_end(now, max_offset)
        task_end = self.calendar_window_end(now, offset_days)
        cached = self.calendar_cache.get(entity_id)
        age = dt_util.as_utc(now) - dt_util.as_utc(cached[0]) if cached else None
        if age is not None and (age < timedelta(0) or age >= timedelta(minutes=30)):
            self.calendar_cache.pop(entity_id, None)
            cached = None

        def fallback():
            return cached[1] if cached is not None and cached[2] >= task_end else None

        failure = self.calendar_failures.get(entity_id)
        if failure and failure[0] <= dt_util.as_utc(now) < failure[1]:
            return fallback()
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            self._calendar_failed(entity_id, now, "calendar entity unavailable")
            return fallback()
        if not self.hass.services.has_service("calendar", "get_events"):
            self._calendar_failed(entity_id, now, "calendar service unavailable")
            return fallback()
        if not failure and cached is not None and age < timedelta(minutes=5) and cached[2] >= end:
            return cached[1]
        # The shared evaluation lock serializes normal calendar callers.
        # An event can still invalidate a request while the provider is awaited.
        request = {"invalidated": False}
        self.calendar_requests[entity_id] = request
        try:
            async with asyncio.timeout(15):
                response = await self.hass.services.async_call(
                    "calendar", "get_events",
                    {"entity_id": entity_id,
                     "start_date_time": (now - timedelta(days=3)).isoformat(),
                     "end_date_time": end.isoformat()},
                    blocking=True, return_response=True,
                )
            events = self._validated_calendar_events(response, entity_id)
        except ValueError:
            if request["invalidated"]:
                return None
            self._calendar_failed(entity_id, now, "invalid calendar response")
            return fallback()
        except Exception:
            if request["invalidated"]:
                return None
            self._calendar_failed(entity_id, now, "calendar request failed")
            return fallback()
        finally:
            if self.calendar_requests.get(entity_id) is request:
                self.calendar_requests.pop(entity_id)
        if request["invalidated"]:
            return None
        self.calendar_cache[entity_id] = (now, events, end)
        self.calendar_failures.pop(entity_id, None)
        return events

    async def calculate_calendar(self, entry, now):
        c, s = self.conf(entry), self.state(entry)
        entity_id = c.get("trigger_entity", "")
        offset_days = legacy_calendar_days({**entry.data, **entry.options})
        if not 0 <= offset_days <= 365:
            raise ValueError("Calendar reminder offset must be between 0 and 365 days")
        window_end = self.calendar_window_end(now, offset_days)
        events = await self.calendar_events(entity_id, now, offset_days)
        untrusted = events is None or entity_id in self.calendar_failures
        if untrusted and s.get("open"):
            # Retain the outstanding occurrence, but do not use its stored end
            # to complete it while the calendar cannot be verified.
            return (parse_dt(s.get("due_at_open")) or parse_dt(s.get("due")),
                    s.get("active_token"), False, None)
        if events is None:
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
            return due, token, now >= due and not untrusted, None if untrusted else end
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
            date = date_entity_value(entity)
            if date:
                due = local_midnight(offset_due_date(date.date(), c["offset_days"]))
                token = "date:" + date.isoformat()
                active = now >= due
            else:
                # Preserve the last display date, but do not create an occurrence.
                due = parse_dt(s.get("due_at_open") if s.get("open") else s.get("due"))
                active = None
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
                # including after completion.
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
            elif key == "trigger_entity" and c["trigger_type"] == "date_entity":
                if date_entity_value(state) is None:
                    warnings.append(f"{label}: {entity_id} has an invalid date or timestamp")
            elif ((key == "trigger_entity" and c["trigger_type"] == "numeric")
                  or (key == "close_entity" and "entity_numeric" in c["close_type"])):
                if finite_number(state.state) is None:
                    warnings.append(f"{label}: {entity_id} has an invalid numeric reading")
                threshold_key = "threshold" if key == "trigger_entity" else "close_threshold"
                operator_key = "operator" if key == "trigger_entity" else "close_operator"
                if numeric_condition(0, c.get(operator_key), c.get(threshold_key)) is None:
                    warnings.append(f"{label}: invalid numeric condition configuration")
        if c["trigger_type"] == "calendar":
            failure = self.calendar_failures.get(c.get("trigger_entity"))
            if failure:
                warnings.append(f"Calendar data: {failure[3]}; automatic calendar transitions paused")
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
            # A queued evaluation may have outlived its original config entry.
            if (self.entries.get(entry.entry_id) is not entry
                    or entry.entry_id in self.cyclic_entries
                    or entry.entry_id in self.invalid_history):
                return
            now = dt_util.now()
            s = self.state(entry)
            old = deepcopy(s)
            # One-time cleanup of legacy snooze state. Restore the original
            # outstanding occurrence without fabricating an opening or completion.
            legacy_snooze = s.pop("snooze_until", None)
            if legacy_snooze:
                s["open"] = True
                s["carried_open"] = True
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
            if (self.entries.get(entry.entry_id) is not entry
                    or entry.entry_id in self.cyclic_entries):
                # The calendar request may have yielded during unload or a graph change.
                s.clear()
                s.update(old)
                return
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
            if s.get("open") and not rule_changed and not legacy_snooze and self.close_condition(entry, now, end):
                self._finish(s, now, "auto_completed")
                if s.get("active_token"):
                    s["completed_token"] = s["active_token"]
            if not s.get("open") and active and token != s.get("completed_token"):
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
            if self.conf(entry)["trigger_type"] == "calendar" and not s.get("open"):
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
        self.record(s, action, now)

    async def act(self, entry_id, action):
        if action not in ("complete", "complete_early"):
            raise ServiceValidationError("Unsupported task action")
        entry = self.entries.get(entry_id)
        if not entry:
            raise ServiceValidationError("Unknown task entry")
        async with self.lock:
            if self.entries.get(entry_id) is not entry:
                raise ServiceValidationError("Task entry changed or unloaded; retry the action")
            if entry_id in self.invalid_history:
                raise HomeAssistantError("Task history is malformed; restore a valid backup before completing this task")
            s, now = self.state(entry), dt_util.now()
            close_type = self.conf(entry)["close_type"]
            if action in ("complete", "complete_early") and not (close_type == "manual" or close_type.startswith("manual_or_")):
                raise ServiceValidationError("This task is configured for automatic completion only")
            if action == "complete_early":
                # Block duplicate early completions on the same local day.
                if self.completed_early_today(s, now):
                    raise ServiceValidationError("Task already completed early today")
                kind = self.conf(entry)["trigger_type"]
                if kind not in ("completion", "weekly", "monthly", "yearly"):
                    raise ServiceValidationError("Early completion is only supported for completion and fixed schedules")
                if s.get("open"):
                    raise ServiceValidationError("Task is already open; use normal completion")
                due, token, active, _ = self.calculate(entry, now)
                if not due or active or due <= now:
                    raise ServiceValidationError("Task has no future scheduled occurrence to complete early")
                # Record the future occurrence as satisfied without opening it.
                # Completion-relative tasks will recalculate from this timestamp.
                self._finish(s, now, "completed_early")
                s["completed_token"] = token
                s["next_token"] = token
                s["completed_early_today"] = True
            elif action == "complete":
                if not s.get("open"):
                    return
                token = s.get("active_token")
                self._finish(s, now, "completed")
                s["completed_token"] = token
            saved = await self.save()
            self.notify(entry_id)
        if saved is False:
            raise HomeAssistantError("Task updated in memory but history is not saved; automatic retry enabled")
        await self.evaluate(entry)

    def find_entry(self, call):
        """Resolve a validated selector to exactly one loaded task."""
        # Validate direct handler calls too, including internal integrations.
        try:
            data = COMPLETION_SERVICE_SCHEMA(dict(call.data))
        except vol.Invalid as err:
            raise ServiceValidationError(str(err)) from err
        if "entry_id" in data:
            entry_id = data["entry_id"]
            if entry_id in self.entries:
                return entry_id
            raise ServiceValidationError("Unknown task entry_id")

        task = data["task"]
        matches = [entry.entry_id for entry in self.entries.values()
                   if entry.unique_id == task or entry.title == task]
        if not matches:
            raise ServiceValidationError("Unknown task name")
        if len(matches) != 1:
            raise ServiceValidationError("Ambiguous task name; use entry_id instead")
        return matches[0]

    async def _authorize_service(self, entry_id, context):
        """Require control of the target task's actual completion button."""
        # HA-generated automation/internal contexts have no user ID.
        if context.user_id is None:
            return
        user = await self.hass.auth.async_get_user(context.user_id)
        if user is None:
            raise UnknownUser(context=context)
        if not user.is_active:
            raise Unauthorized(context=context)
        if user.is_admin:
            return
        # Resolve by stable registry identity, not a guessed entity name
        # or an entity ID supplied by the service caller.
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "button", DOMAIN, f"{entry_id}_complete")
        entity = registry.async_get(entity_id) if entity_id else None
        if (entity is None or entity.disabled_by is not None
                or entity.config_entry_id != entry_id
                or not user.permissions.check_entity(entity_id, POLICY_CONTROL)):
            raise Unauthorized(context=context, entity_id=entity_id,
                               config_entry_id=entry_id, permission=POLICY_CONTROL)

    async def _complete_service(self, call, action):
        entry_id = self.find_entry(call)
        await self._authorize_service(entry_id, call.context)
        await self.act(entry_id, action)

    async def _service_complete(self, call):
        await self._complete_service(call, "complete")

    async def _service_complete_early(self, call):
        await self._complete_service(call, "complete_early")

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
                _LOGGER.error("Unable to evaluate household task %s; will retry", entry_id)
                _LOGGER.debug("Household task evaluation traceback", exc_info=True)
                self._notify_evaluation_warning(entry_id)
        else:
            if self.entries.get(entry_id) is not entry:
                return
            if entry_id in self.evaluation_errors:
                self.evaluation_errors.discard(entry_id)
                self._notify_evaluation_warning(entry_id)

    def _notify_evaluation_warning(self, entry_id):
        """Refresh task warnings without writing history or aggregate states."""
        for listener in list(self.listeners):
            try:
                listener(entry_id)
            except Exception:
                _LOGGER.error("Unable to publish evaluation warning for task %s", entry_id)
                _LOGGER.debug("Household task warning publication traceback", exc_info=True)

    @callback
    def request_evaluation(self, entry):
        """Coalesce events into one running/queued worker and a dirty flag."""
        entry_id = entry.entry_id
        if self.entries.get(entry_id) is not entry:
            return None
        job = self.evaluation_jobs.get(entry_id)
        if job is not None and job.task is not None and not job.task.done():
            # If an entry was replaced, the next pass uses the replacement.
            job.entry = entry
            job.pending = True
            return job.task
        job = EvaluationJob(entry)
        self.evaluation_jobs[entry_id] = job
        job.task = self.hass.async_create_task(
            self._drain_evaluations(entry_id, job),
            name=f"household_tasks evaluate {entry_id}", eager_start=False)
        # Also clean up a task cancelled before its coroutine starts.
        job.task.add_done_callback(lambda _: self._forget_evaluation(entry_id, job))
        return job.task

    @callback
    def _forget_evaluation(self, entry_id, job):
        if self.evaluation_jobs.get(entry_id) is job:
            self.evaluation_jobs.pop(entry_id)

    async def _drain_evaluations(self, entry_id, job):
        try:
            while job.pending:
                job.pending = False
                entry = job.entry
                if self.entries.get(entry_id) is not entry:
                    return
                await self._evaluate_background(entry)
                if job.pending:
                    # Yield to other tasks even when evaluation has no I/O.
                    await asyncio.sleep(0)
        finally:
            self._forget_evaluation(entry_id, job)

    async def _tick(self, now):
        """Schedule one periodic sweep without overlapping timer sweeps."""
        if self.tick_running:
            return
        self.tick_running = True
        try:
            self.prune_calendar_cache(now or dt_util.now())
            await self.retry_storage(now or dt_util.now())
            jobs = [job for entry in list(self.entries.values())
                    if (job := self.request_evaluation(entry)) is not None]
            # A cancelled timer must not cancel event-driven workers it joined.
            if jobs:
                await asyncio.gather(*(asyncio.shield(job) for job in jobs),
                                     return_exceptions=True)
        finally:
            self.tick_running = False

    @callback
    def _registry_event(self, event):
        """Follow entity_id renames reported by the entity registry."""
        data = event.data
        old_id = data.get("old_entity_id")
        new_id = data.get("entity_id")
        if data.get("action") == "update" and old_id and new_id and old_id != new_id:
            self.hass.async_create_task(self._apply_entity_rename(old_id, new_id))
        elif new_id in self.entity_dependencies:
            # A referenced sensor may have been registered or removed.
            self.refresh_dependency_cycles()

    async def _apply_entity_rename(self, old_id, new_id):
        for entry in list(self.entries.values()):
            # A registry rename is the same source, not a new opening rule.
            # Recheck membership and references after acquiring the lock.
            async with self.lock:
                if self.entries.get(entry.entry_id) is not entry:
                    continue
                c = self.conf(entry)
                changes = {}
                if c.get("trigger_entity") == old_id:
                    changes["trigger_entity"] = new_id
                if c.get("close_entity") == old_id:
                    changes["close_entity"] = new_id
                if not changes:
                    continue
                if entry.entry_id in self.invalid_history:
                    self.hass.config_entries.async_update_entry(entry, options={**entry.options, **changes})
                    self.refresh_entity_subscriptions()
                    continue
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
                self.refresh_entity_subscriptions()
                if changed:
                    await self.save()
        self.refresh_dependency_cycles()

    def active_dependencies(self, entry):
        return active_entity_ids(self.conf(entry))

    @callback
    def refresh_dependency_cycles(self):
        """Refresh transient cycle warnings without changing persisted history."""
        configs = {entry_id: self.conf(entry) for entry_id, entry in self.entries.items()}
        updated = cyclic_entries(configs, er.async_get(self.hass)) if configs else set()
        changed = self.cyclic_entries ^ updated
        self.cyclic_entries = updated
        for entry_id in sorted(changed & self.entries.keys()):
            self._notify_evaluation_warning(entry_id)

    @callback
    def refresh_entity_subscriptions(self):
        """Maintain one indexed HA listener per actively referenced entity."""
        dependencies = {}
        for entry in self.entries.values():
            for entity_id in self.active_dependencies(entry):
                dependencies.setdefault(entity_id, set()).add(entry.entry_id)
        self.entity_dependencies = dependencies
        for entity_id in self.entity_subscriptions.keys() - dependencies.keys():
            self.entity_subscriptions.pop(entity_id)()
        for entity_id in dependencies.keys() - self.entity_subscriptions.keys():
            self.entity_subscriptions[entity_id] = async_track_state_change_event(
                self.hass, entity_id, self._state_event)
        self.refresh_dependency_cycles()

    @callback
    def _state_event(self, event):
        entity_id = event.data.get("entity_id")
        entries = []
        for entry_id in sorted(self.entity_dependencies.get(entity_id, ())):
            entry = self.entries.get(entry_id)
            # Ignore callbacks already queued before a rule change or unload.
            if entry is not None and entity_id in self.active_dependencies(entry):
                entries.append(entry)
        if any(self.conf(entry)["trigger_type"] == "calendar"
               and self.conf(entry).get("trigger_entity") == entity_id
               for entry in entries):
            self.invalidate_calendar(entity_id)
        for entry in entries:
            if entry.entry_id not in self.cyclic_entries:
                self.request_evaluation(entry)

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

    async def load_aggregates(hass, _component):
        # Load a separate domain-level sensor platform only after this
        # integration's initial config entries have completed setup. This
        # avoids a circular setup dependency and partial startup snapshots.
        await discovery.async_load_platform(
            hass, "sensor", DOMAIN, {"aggregate_sensors": True}, config)

    async_when_setup(hass, DOMAIN, load_aggregates)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    engine = hass.data[DOMAIN]
    engine.entries[entry.entry_id] = entry
    engine.state(entry)
    forwarding_started = False
    try:
        engine.refresh_entity_subscriptions()
        engine.refresh_runtime_listeners()
        forwarding_started = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        # New status sensors now have registry identities.
        engine.refresh_dependency_cycles()
        await engine.evaluate(entry)
        entry.async_on_unload(entry.add_update_listener(_options_updated))
    except (Exception, asyncio.CancelledError):
        await engine.detach_entry(entry)
        if forwarding_started:
            # A partially forwarded platform may already have added entities.
            # Best effort cleanup must not hide the original setup failure.
            try:
                if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
                    _LOGGER.error("Unable to unload all platforms after task setup failure")
            except Exception:
                _LOGGER.error("Unable to clean up platforms after task setup failure")
                _LOGGER.debug("Task platform cleanup traceback", exc_info=True)
        raise
    engine.publish_open_tasks()
    return True

async def _options_updated(hass, entry):
    engine = hass.data[DOMAIN]
    if entry.entry_id not in engine.entries or entry.entry_id in engine.reloading_entries:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    # Config entries are mutable; use the last published name if available,
    # not the already-updated options, while the entry is temporarily unloaded.
    name = engine.last_aggregate_names.get(entry.entry_id, engine.aggregate_name(entry))
    engine.reloading_entries[entry.entry_id] = (entry, name)
    try:
        await hass.config_entries.async_reload(entry.entry_id)
    finally:
        engine.reloading_entries.pop(entry.entry_id, None)
        engine.publish_open_tasks()

async def async_unload_entry(hass, entry):
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await hass.data[DOMAIN].detach_entry(entry)
    return ok

async def async_remove_entry(hass, entry):
    engine = hass.data.get(DOMAIN)
    if engine:
        await engine.detach_entry(entry)
        async with engine.lock:
            if entry.entry_id not in engine.entries:
                engine.history.pop(entry.entry_id, None)
                engine.invalid_history.discard(entry.entry_id)
                await engine.save()
