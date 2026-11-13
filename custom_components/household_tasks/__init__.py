"""Persistent task scheduler for Household Tasks."""
from __future__ import annotations
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, time as datetime_time
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from .const import DOMAIN, PLATFORMS, DEFAULTS

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

def compare(value, op, threshold):
    try:
        x, y = float(value), float(threshold)
        return {"below": x < y, "below_or_equal": x <= y, "above": x > y, "above_or_equal": x >= y, "equal": x == y}.get(op, False)
    except (ValueError, TypeError):
        return False

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
        self.calendar_cache = {}

    async def start(self):
        self.history = await self.store.async_load() or {}
        self.unsub_timer = async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))
        self.unsub_states = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_event)
        for name in ("complete", "reopen", "skip", "snooze"):
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

    def notify(self, entry_id):
        self.publish_open_tasks()
        for listener in list(self.listeners):
            listener(entry_id)

    async def save(self):
        await self.store.async_save(self.history)

    def record(self, s, action, now):
        s.setdefault("events", []).append({"action": action, "at": now.isoformat()})
        s["events"] = s["events"][-100:]

    async def calendar_events(self, entity_id, now):
        """Fetch all calendar events, not just the next entity attribute event."""
        cached = self.calendar_cache.get(entity_id)
        if cached and now - cached[0] < timedelta(minutes=5):
            return cached[1]
        if not self.hass.services.has_service("calendar", "get_events"):
            return None
        try:
            response = await self.hass.services.async_call(
                "calendar", "get_events",
                {"entity_id": entity_id,
                 "start_date_time": (now - timedelta(days=3)).isoformat(),
                 "end_date_time": (now + timedelta(days=90)).isoformat()},
                blocking=True, return_response=True,
            )
            events = response.get(entity_id, {}).get("events", [])
            self.calendar_cache[entity_id] = (now, events)
            return events
        except Exception:
            # Do not discard known events because of a transient calendar error.
            return cached[1] if cached else None

    async def calculate_calendar(self, entry, now):
        c, s = self.conf(entry), self.state(entry)
        entity_id = c.get("trigger_entity", "")
        events = await self.calendar_events(entity_id, now)
        if events is None:
            return None, None, False, None
        match = str(c.get("calendar_match", "")).casefold().strip()
        offset = timedelta(hours=int(c.get("calendar_offset_hours", 24)))
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
            candidates.append((start - offset, token, start, end))
        candidates.sort(key=lambda item: item[0])
        # Keep an open task tied to its original event, even when the
        # calendar starts advertising a later collection.
        if s.get("open"):
            current = next((item for item in candidates if item[1] == s.get("active_token")), None)
            if current:
                due, token, _, end = current
                return due, token, now >= due and (end is None or now < end), end
            end = parse_dt(s.get("calendar_end"))
            if end:
                return parse_dt(s.get("due_at_open")), s.get("active_token"), False, end
        # Do not reopen a completed occurrence. Select the next matching event.
        for due, token, start, end in candidates:
            if token == s.get("completed_token") or (end is not None and end <= now):
                continue
            return due, token, now >= due, end
        return None, None, False, None

    def calculate(self, entry, now):
        c, s = self.conf(entry), self.state(entry)
        kind = c["trigger_type"]
        due = token = end = None
        active = False
        if kind == "completion":
            base = parse_dt(s.get("last_completed")) or parse_dt(s["created"]) or now
            due = base + timedelta(days=int(c["interval_days"]))
            token = "completion:" + base.isoformat()
            active = now >= due
        elif kind == "weekly":
            slot = now.replace(hour=int(c["hour"]), minute=int(c["minute"]), second=0, microsecond=0)
            slot -= timedelta(days=(slot.weekday() - int(c["weekday"])) % 7)
            # The most recent slot is due only once its time has arrived.
            if slot > now:
                slot -= timedelta(days=7)
            previous_token = "weekly:" + slot.isoformat()
            if s.get("completed_token") == previous_token:
                slot += timedelta(days=7)
            elif s.get("last_completed") is None and parse_dt(s["created"]) > slot:
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
                due = date - timedelta(days=int(c["offset_days"]))
                token = "date:" + date.isoformat()
                active = now >= due
        elif kind in ("numeric", "state"):
            entity = self.hass.states.get(c.get("trigger_entity", ""))
            value = entity.state if entity else None
            active = (compare(value, c["operator"], c["threshold"]) if kind == "numeric"
                      else value == c["trigger_state"])
            token = "entity:" + str(c.get("trigger_entity"))
            if active:
                due = now
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
                try:
                    float(entity.state)
                except (ValueError, TypeError):
                    return False
                return not compare(entity.state, c["operator"], c["threshold"])
            return entity.state != c["trigger_state"]
        if "calendar_end" in rule:
            return calendar_end is not None and now >= calendar_end
        entity = self.hass.states.get(c.get("close_entity", ""))
        if not entity:
            return False
        if "entity_numeric" in rule:
            return compare(entity.state, c["close_operator"], c["close_threshold"])
        if "entity_state" in rule:
            return entity.state == c["close_state"]
        return False

    async def evaluate(self, entry):
        async with self.lock:
            now = dt_util.now()
            s = self.state(entry)
            if self.conf(entry)["trigger_type"] == "calendar":
                due, token, active, end = await self.calculate_calendar(entry, now)
            else:
                due, token, active, end = self.calculate(entry, now)
            old = dict(s)
            s["due"] = due.isoformat() if due else None
            if end is not None:
                s["calendar_end"] = end.isoformat()
            if self.conf(entry)["trigger_type"] == "calendar" and end is None and s.get("open"):
                end = parse_dt(s.get("calendar_end"))
            s["next_token"] = token
            if s.get("snooze_until") and (parse_dt(s["snooze_until"]) or now) <= now:
                s["snooze_until"] = None
                if self.conf(entry)["trigger_type"] in ("numeric", "state"):
                    s["latched"] = False
                    s["completed_token"] = None
            snoozed = bool(s.get("snooze_until"))
            if s.get("open") and self.close_condition(entry, now, end):
                self._finish(s, now, "auto_completed")
                if s.get("active_token"):
                    s["completed_token"] = s["active_token"]
            if self.conf(entry)["trigger_type"] == "calendar" and end and now >= end:
                if s.get("open"):
                    self._finish(s, now, "expired")
                if s.get("active_token"):
                    s["completed_token"] = s["active_token"]
                s["calendar_end"] = None
            if not s.get("open") and active and not snoozed and token != s.get("completed_token"):
                if self.conf(entry)["trigger_type"] not in ("numeric", "state") or not s.get("latched"):
                    s["open"] = True
                    s["last_opened"] = now.isoformat()
                    s["due_at_open"] = due.isoformat() if due else None
                    s["active_token"] = token
                    self.record(s, "opened", now)
                    if self.conf(entry)["trigger_type"] in ("numeric", "state"):
                        s["latched"] = True
            if not active and self.conf(entry)["trigger_type"] in ("numeric", "state"):
                source = self.hass.states.get(self.conf(entry).get("trigger_entity", ""))
                if source is not None and source.state not in ("unknown", "unavailable"):
                    s["latched"] = False
                    s["completed_token"] = None
            if old != s:
                await self.save()
                self.notify(entry.entry_id)

    def _finish(self, s, now, action):
        s["open"] = False
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
            if action == "complete" and not (close_type == "manual" or close_type.startswith("manual_or_")):
                raise ValueError("This task is configured for automatic completion only")
            if action in ("complete", "skip"):
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

    async def _service_reopen(self, call):
        await self.act(self.find_entry(call), "reopen")

    async def _service_skip(self, call):
        await self.act(self.find_entry(call), "skip")

    async def _service_snooze(self, call):
        await self.act(self.find_entry(call), "snooze", int(call.data.get("days", 1)))

    async def _tick(self, now):
        for entry in list(self.entries.values()):
            await self.evaluate(entry)

    @callback
    def _state_event(self, event):
        entity_id = event.data.get("entity_id")
        for entry in list(self.entries.values()):
            c = self.conf(entry)
            if entity_id in (c.get("trigger_entity"), c.get("close_entity")):
                if c.get("trigger_type") == "calendar":
                    self.calendar_cache.pop(entity_id, None)
                self.hass.async_create_task(self.evaluate(entry))

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
        hass.data[DOMAIN].publish_open_tasks()
    return ok

async def async_remove_entry(hass, entry):
    engine = hass.data.get(DOMAIN)
    if engine:
        engine.history.pop(entry.entry_id, None)
        await engine.save()
