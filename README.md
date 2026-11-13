# HA Household Tasks

Experimental Home Assistant custom integration for rule-driven household task tracking.

**Development status: v0.2.0 (unverified). Do not rely on it for critical safety or MOT reminders until tested.**

## Features
- One task per Home Assistant integration configuration entry; edit through integration options.
- Completion-relative intervals and fixed weekly recurrence.
- Date entity, numeric entity, state entity, and basic calendar entity triggers.
- Manual completion, skip, snooze, reopen, and entity/calendar-based auto completion.
- Home Assistant area selector, including no area (N/A).
- Optional holiday pause using one or more entity IDs (for example `input_boolean.holiday_mode`).
- Persistent history in Home Assistant storage.
- `sensor.household_tasks_open_names`: count as state, `names` as a list, `announcement` as an Alexa-ready sentence.
- Dashboard card in `www/household-tasks-card.js`.

## Installation (development testing)
1. In HACS, add `https://github.com/thomtom24/HA-Household-Tasks` as a custom **Integration** repository.
2. Download the integration and restart Home Assistant.
3. Go to Settings → Devices & services → Add integration → Household Tasks.
4. Create a task. Each task is a separate configuration entry.
5. For the dashboard card, **manually** copy `www/household-tasks-card.js` into Home Assistant `/config/www/household-tasks-card.js`, then add `/local/household-tasks-card.js` as a JavaScript module dashboard resource.
6. Add a manual dashboard card:
```yaml
type: custom:household-tasks-card
title: Household Tasks
```

The custom card is **not automatically installed by HACS** in this layout. The integration's `hacs.json` describes the integration only.

## Alexa
Use:
```jinja2
{{ state_attr('sensor.household_tasks_open_names', 'announcement') }}
```
Or retrieve the list:
```jinja2
{{ state_attr('sensor.household_tasks_open_names', 'names') }}
```

## Important limitations
- Calendar logic currently uses calendar entity state attributes; many calendar integrations only expose the currently active or next event, so reliable 24-hour lookahead requires calendar event API work.
- The task editor exposes fields for all trigger types together, rather than conditionally hiding irrelevant fields.
- Weekly tasks have a single weekday setting; multiple weekday recurrence is not implemented.
- Some edge cases involving date changes, time zones, and sensor rearming need live Home Assistant testing.
- The Lovelace card is a separate manual resource.
- There is no automated test suite or validated Home Assistant runtime compatibility yet.

Source is versioned in GitHub; no release has been published yet.
