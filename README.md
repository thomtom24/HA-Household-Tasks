# HA Household Tasks

A custom Home Assistant integration for tracking recurring household tasks, maintenance and reminders using schedules, calendars and entity conditions.

**Development status:** Experimental. Automated regression testing is in place, but compatibility and live Home Assistant behaviour are not comprehensively validated. Do not rely on this integration as your only reminder for safety-critical, legal or essential maintenance tasks.

## Features

- Create and edit individual tasks through the Home Assistant integration interface.
- Open tasks on recurring schedules, calendar events, dates from entities, numeric thresholds or entity states.
- Complete tasks manually, automatically or using a combination of both.
- Organise tasks by category and Home Assistant area.
- View open, overdue and upcoming tasks using the bundled dashboard card.
- Complete eligible scheduled tasks early.
- Retain task history across Home Assistant restarts.
- Use aggregate task sensors in automations and announcements.

## Installation

1. In HACS, add `https://github.com/thomtom24/HA-Household-Tasks` as a custom **Integration** repository.
2. Download the integration and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Household Tasks**.
4. Create your first task. Each task is a separate configuration entry and can be edited through its integration options.

The integration is not currently part of the default HACS catalogue.

### Dashboard card

In **Settings → Dashboards → three-dot menu → Resources**, add a JavaScript module resource:

```text
/household_tasks/household-tasks-card.js
```

Add a manual card to your dashboard:

```yaml
type: custom:household-tasks-card
title: Household Tasks
```

If you previously registered `/local/household-tasks-card.js`, remove that resource to avoid duplicate card registration. Refresh your browser after installation or upgrades. The bundled card is served by the integration; you only need to register its resource once.

## Configuring tasks

Each task has an internal name, friendly name, category and optional Home Assistant area. Choose an opening trigger and a closing condition using the configuration wizard.

### Opening triggers

| Trigger | Description |
| --- | --- |
| Time since last completion | Opens after a specified number of calendar days since completion. An optional first due date sets the initial occurrence. |
| Weekly schedule | Opens on the chosen weekday. |
| Monthly schedule | Opens on the chosen day of each month. |
| Yearly schedule | Opens on the chosen month and day, with a configurable repeat interval in years. |
| Calendar event | Opens a specified number of days before a matching calendar event. |
| Date from an entity | Opens a specified number of days before a date provided by an entity. |
| Numeric sensor threshold | Opens when a numeric reading meets the configured comparison. |
| Entity state | Opens when an entity reaches the specified state. |

Numeric opening comparisons support below, below or equal, above, above or equal, and equal, including decimal thresholds.

Fixed weekly, monthly and yearly schedules become due at midnight in Home Assistant's local timezone. Monthly and yearly dates beyond the end of the month are clamped to its last day (including 29 February in non-leap years). Fixed schedules retain their calendar alignment after late completion.

Completion-relative tasks recur from the actual completion date. For example, completing a seven-day task on Tuesday evening makes it due the following Tuesday at midnight. The first due date is optional and applies before the first completion.

### Closing conditions

- Manual completion.
- An entity reaches a specified state.
- A numeric entity meets a closing threshold (below, above or equal).
- A matched calendar event ends (calendar tasks only).
- The opening trigger is no longer true (numeric or state-triggered tasks only).
- Manual completion combined with any applicable automatic closing condition.

**Known calendar limitation:** the current scheduler can expire open calendar tasks when their matched event ends even when manual completion is configured. Do not depend on calendar tasks remaining open indefinitely after the event ends.

### Calendar example

To track multiple collection types in a single calendar, create separate tasks using the same calendar entity and different **Calendar event match** filters, such as Rubbish Collection, Recycling Collection and Food Recycling.

Matching is case-insensitive and checks whether the event title contains the configured text. Set **Days before event** to 1 to open the task at midnight on the preceding day. Event-end completion uses the actual event end time.

The scheduler currently retrieves calendar events up to 90 days ahead and caches responses for five minutes. Longer advance offsets may not open on time. Test the behaviour with your calendar provider.

### Completing tasks early

Eligible upcoming completion-relative, weekly, monthly and yearly tasks with manual completion enabled show a **Complete early** button. Completing a task early satisfies its next occurrence without opening or announcing it. Completion-relative tasks restart their interval from the actual completion date; fixed schedules retain their calendar alignment.

After early completion, the button shows **Completed today** and remains disabled until the next local calendar day. Open tasks use the normal **Done** button.

## Sensors and automations

Each task exposes a status sensor with open or closed state, due dates, opening and completion timestamps, recent history and entity warnings.

Two aggregate sensors are available:

| Entity | Purpose |
| --- | --- |
| `sensor.household_tasks_open_names` | Number of outstanding tasks, with `names`, `tasks`, `count` and `announcement` attributes. |
| `sensor.household_tasks_latest_open` | Friendly name of the most recently opened outstanding task, or `none`. Includes `task_name`, `opened_at` and `open_count`. |

Use the announcement text in an automation or voice assistant:

```jinja2
{{ state_attr('sensor.household_tasks_open_names', 'announcement') }}
```

Retrieve the list of outstanding task names:

```jinja2
{{ state_attr('sensor.household_tasks_open_names', 'names') }}
```

A change to the latest-open sensor does not necessarily mean a new task opened: it may also mean the newest task was completed. Consider the `open_count` and `opened_at` attributes when building notifications.

The integration also registers backend actions for completion, early completion, reopening, skipping and snoozing. Not all of these actions are exposed in the dashboard, and some lifecycle edge cases remain under development.

## Troubleshooting and limitations

- The dashboard resource must be added manually once.
- Weekly recurrence supports one weekday per task.
- Monthly recurrence is configured as every month in the current editor.
- When Home Assistant reports an entity ID rename, the integration attempts to update associated task references automatically.
- Missing or unavailable dependencies are exposed through `entity_warnings` and `has_entity_warning` on task status sensors. Check these attributes and Home Assistant logs if a task is not behaving as expected.
- Calendar retrieval, task editing, sensor recovery and timezone edge cases require further testing.
- Automated Python tests run on GitHub Actions, but passing tests do not establish full runtime compatibility across Home Assistant versions.

Task configuration and history are stored locally within Home Assistant. The integration does not intentionally send task data to external services.

For bug reports or feature requests, use [GitHub Issues](https://github.com/thomtom24/HA-Household-Tasks/issues). Release-specific changes are available through [GitHub Releases](https://github.com/thomtom24/HA-Household-Tasks/releases) and repository history.

## AI-assisted development disclaimer

This project was produced using ChatGPT in response to human prompts and direction. It was not created solely by a human developer. The project owner provides requirements, reviews changes and participates in testing, while ChatGPT generates and assists with implementation, documentation and automated tests.

AI-generated code may contain errors, security issues or unexpected behaviour. The integration is provided as-is, without guarantees of reliability or suitability for any particular purpose. Please review and test it in your own environment before relying on it.

## About and support

- Website: [www.tombale.me](https://www.tombale.me)
- If you find this project useful and would like to support its development, you can [buy me a beer](https://buymeacoffee.com/tombale).

Support is entirely optional.
