# Household Tasks for Home Assistant

A custom Home Assistant integration for tracking recurring household jobs and tasks opened by calendar events or entity conditions. Tasks stay open until their configured completion condition is met. Includes a Lovelace card with **Open** and **Upcoming** views.

**Current development version: 0.4.2.** This is experimental software; test it before relying on important reminders. Version 0.4.2 is on a development branch and is not yet a merged release.

## Features

- Create and edit tasks through **Settings → Devices & services → Household Tasks**. Each task is a separate integration entry.
- Assign a friendly name, category and optional Home Assistant area.
- Recurring tasks based on the date of their last completion, or fixed weekly, monthly and yearly schedules.
- Tasks triggered by Home Assistant calendar events, date entities, numeric thresholds or entity states.
- Manual completion, early completion for eligible upcoming tasks, skip, snooze and reopen actions.
- Optional automatic closing when an entity meets a condition, a trigger becomes inactive or a calendar event ends.
- Persistent task state and completion history, including exact timestamps.
- Task status sensors, missing/unavailable entity warnings and a summary sensor suitable for announcements.

## Installation

1. In **HACS**, add `https://github.com/thomtom24/HA-Household-Tasks` as a custom repository of type **Integration**.
2. Install Household Tasks and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Household Tasks** to create your first task. Add additional tasks by adding another Household Tasks integration entry.
4. Register the bundled dashboard card under **Settings → Dashboards → Resources** as a **JavaScript module**:

   ```text
   /household_tasks/household-tasks-card.js?v=0.4.2
   ```

5. Add a manual dashboard card:

   ```yaml
   type: custom:household-tasks-card
   title: Household Tasks
   ```

The card is served directly by the integration; you do not need to copy the JavaScript into `/config/www`. Remove any older duplicate `/local/household-tasks-card.js` resource. When updating the integration, restart Home Assistant and change the resource URL's `?v=` value to refresh browser and Android WebView caches.

**Development branches:** HACS normally installs from the repository's selected/default release source, not automatically from an open pull request. Make sure the installed integration actually contains the desired version before testing its card or services.

## Creating tasks

Choose an **opening trigger** and a **closing condition** when configuring each task.

| Opening trigger | How it works | Example |
| --- | --- | --- |
| Time since last completion | Due at local midnight, N calendar days after completion. Optional first due date; otherwise first due is calculated from task creation. | Change bedding every 14 days |
| Weekly schedule | Due at local midnight on the chosen weekday. | Weekly cleaning |
| Monthly schedule | Due on a chosen day of the month. Supports intervals such as every 3 months. | Clean washing machine filter |
| Yearly schedule | Due on a chosen month/day, optionally every N years. | Annual maintenance |
| Calendar event | Match an event title by case-insensitive substring and open at midnight N days before its start date. | Food recycling collection |
| Date from an entity | Open at midnight on the entity's date, adjusted by the configured day offset. | Date-based renewal |
| Numeric sensor threshold | Open when a numeric entity crosses the selected comparison. | Battery below 15% |
| Entity state | Open when an entity reaches a configured state. | Device reports a fault |

Fixed weekly, monthly and yearly schedules retain their calendar cadence when a task is completed late or early. Monthly/yearly dates past the end of a month are clamped to its final day (for example, 29 February becomes 28 February in a non-leap year). Monthly/yearly interval alignment is calendar-based.

Calendar tasks use Home Assistant's `calendar.get_events` action to look ahead up to 90 days and cache results for approximately five minutes. Use a separate task with a different event-title filter for each collection type. Calendar tasks configured with older hour-based offsets are supported using whole-day equivalents.

**Closing conditions** include manual completion; an entity reaching a specified state or numeric condition; the calendar event ending; the trigger becoming inactive (for numeric/state triggers); and supported manual-or-automatic combinations. Automatic-only tasks cannot be manually completed.

## Dashboard and early completion

The **Open** tab lists currently outstanding tasks and offers **Done** when manual completion is allowed. The **Upcoming** tab lists scheduled closed tasks, with a time-range filter (7 days, 14 days, 1 month or All).

For manually completable, completion-relative or fixed weekly/monthly/yearly tasks, Upcoming shows **Complete early**. Selecting it opens a confirmation prompt before any action is recorded. Cancel leaves the task unchanged.

- **Completion-relative example:** Complete a 14-day bedding task on day 9; its next due date becomes 14 calendar days after that early completion.
- **Fixed schedule example:** Complete next Saturday's task on Wednesday; that occurrence is satisfied and the next due date remains the following scheduled Saturday.
- After an early completion, the button displays **Completed today** and is disabled for the rest of the local calendar day. The backend also rejects duplicate early completions on that date.
- Calendar, date-entity, numeric and state-triggered tasks do not offer Complete early.

Task dates are displayed without times in the card. Completion/opening history retains exact timestamps. Tasks with missing or unavailable entity dependencies show warnings.

## Home Assistant actions

The integration registers these actions under the `household_tasks` domain:

| Action | Purpose |
| --- | --- |
| `household_tasks.complete` | Complete an open task when manual completion is permitted |
| `household_tasks.complete_early` | Complete an eligible Upcoming task before its due date |
| `household_tasks.reopen` | Reopen a task |
| `household_tasks.skip` | Skip an open task |
| `household_tasks.snooze` | Snooze an open task by a number of days (default 1) |

Specify either `entry_id` or `task` (the task's configured identifier/name, as accepted by the integration). For example:

```yaml
action: household_tasks.complete_early
data:
  task: change_bedding
```

## Status sensors and announcements

Each task exposes a status sensor whose state is `open` or `closed`. Attributes include its due date, last completion, history, trigger type, completion eligibility and entity warnings. The `completed_early_today` attribute supports the dashboard's disabled button state.

The summary entity `sensor.household_tasks_open_names` has the number of open tasks as its state and provides `names`, `tasks` and `announcement` attributes. To speak the current open-task announcement with Alexa Media Player, for example:

```yaml
action: notify.alexa_media_front_room
data:
  message: "{{ state_attr('sensor.household_tasks_open_names', 'announcement') }}"
  data:
    type: tts
```

## Notes and limitations

- The integration uses Home Assistant's local time zone for scheduled dates; time-based sensor triggers respond immediately.
- Weekly schedules support one weekday per task.
- Calendar behaviour depends on the connected calendar provider and its event data.
- Changes to an entity ID made through Home Assistant's entity registry are followed automatically; missing or unavailable entities generate warnings rather than silently completing tasks.
- The dashboard resource must be registered manually.
- This development version has not been fully validated against a live Home Assistant installation. Test early completion, recurrence boundaries, Android confirmation dialogs and restart persistence before relying on it for critical reminders.

For changes and version history, use the repository's GitHub pull requests, commits and releases rather than this README.
