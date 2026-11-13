# HA Household Tasks

Experimental Home Assistant custom integration for rule-driven household task tracking.

**Development status: v0.2.6 (testing). Do not rely on it for critical safety or MOT reminders until tested.**

## Features
- One task per Home Assistant integration configuration entry; edit through integration options.
- Completion-relative intervals and fixed weekly recurrence.
- Date entity, numeric entity, state entity, and basic calendar entity triggers.
- Manual completion, skip, snooze, reopen, and entity/calendar-based auto completion.
- Home Assistant area selector, including no area (N/A).
- Persistent history in Home Assistant storage.
- `sensor.household_tasks_open_names`: count as state, `tasks` and `names` as lists, and optional `announcement` text.
- Dashboard card bundled in `custom_components/household_tasks/frontend/household-tasks-card.js` and served by the integration.

## Installation (development testing)
1. In HACS, add `https://github.com/thomtom24/HA-Household-Tasks` as a custom **Integration** repository.
2. Download the integration and restart Home Assistant.
3. Go to Settings → Devices & services → Add integration → Household Tasks.
4. Create a task. Each task is a separate configuration entry.
5. In Home Assistant, open **Settings → Dashboards → three-dot menu → Resources** (or your dashboard's resource editor). Add a **JavaScript module** resource with URL:
   `/household_tasks/household-tasks-card.js`
6. If you previously installed `/local/household-tasks-card.js`, **remove that old resource** to avoid registering the same custom card twice. The old file in `/config/www` may be deleted after verifying the new card works.
7. Add a manual dashboard card:
```yaml
type: custom:household-tasks-card
title: Household Tasks
```
8. Refresh your browser after the upgrade. The bundled card is installed by HACS with the integration and is served by Home Assistant after a restart. You do **not** need to copy the JavaScript file on future releases. Browser cache headers are disabled for this route.

**Why is resource setup manual?** The integration serves the card but deliberately does not modify dashboard resource settings automatically. You only need to register the URL once; later HACS updates replace the bundled file.

## Using the open task list in automations
Use:
```jinja2
{{ state_attr('sensor.household_tasks_open_names', 'announcement') }}
```
Or retrieve the list:
```jinja2
{{ state_attr('sensor.household_tasks_open_names', 'names') }}
```

## v0.2.3 change
- Removed holiday-mode pausing from task configuration, scheduling, status sensors and task-list filtering. Existing tasks retain their open/closed state and recurrence history. Previously saved pause settings are ignored; editing a task removes them from its options.

## Entity renames and availability (v0.2.7 development)
- When Home Assistant reports an entity ID rename through its entity registry, Household Tasks updates task opening and closing entity references automatically. Renaming only the friendly name needs no update.
- Missing, `unknown`, and `unavailable` dependencies are exposed on each task status sensor as `entity_warnings` and `has_entity_warning`, and flagged in the bundled card.
- A missing or unavailable sensor does not by itself complete a task; the existing task state and history remain intact. Check the warning if an entity is removed or replaced rather than renamed.
- Numeric comparisons support `<`, `<=`, `>`, `>=`, and `=`. Numeric/state tasks can close when their trigger condition becomes false, with or without manual completion.

## Important limitations
- Calendar lookahead uses Home Assistant's calendar.get_events service; test with your calendar provider before relying on reminders.
- The task editor uses conditional multi-step configuration screens.
- Weekly tasks have a single weekday setting; multiple weekday recurrence is not implemented.
- Some edge cases involving date changes, time zones, and sensor rearming need live Home Assistant testing.
- The Lovelace card resource URL needs to be added manually once.
- There is no automated test suite or validated Home Assistant runtime compatibility yet.

Source is versioned in GitHub; v0.2.6 is not released until merged and tagged.


### Multiple event types in one calendar (v0.2.5 development)
Create one Household Task per event type, using the same Home Assistant calendar entity and a distinct **Calendar event match** filter. The match is case-insensitive and checks whether the event title contains the configured text. For example, with `calendar.rubbish_collection_2`, use `Rubbish Collection`, `Recycling Collection`, and `Food Recycling` for three separate tasks. Set **Hours before event** to `24` for a reminder starting at midnight the previous day for all-day events. The integration requests events up to 90 days ahead using Home Assistant's `calendar.get_events` service, and caches responses for five minutes. A task can be completed manually, automatically when its matched event ends, or either. This is development code and should be tested before release.

## First due date for completion-relative tasks (v0.2.9)
For **Time since last completion** tasks, the optional **First due date** field sets the initial reminder date (local midnight). Leave it empty to keep the existing behaviour of scheduling the first reminder after the interval from task creation. After the first completion, reminders always recur the configured number of days after the actual completion timestamp. Editing the first due date after a completion does not reset the completed task's recurrence history. An already-open task retains its original opening date until completed.
