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

### Editing existing tasks

Editing a task's opening rule recalculates its schedule without deleting completion history or changing its entity ID. Cosmetic edits and equivalent opening settings leave the current occurrence unchanged. Changed fixed weekly, monthly and yearly schedules start from the date of the edit; completion-relative schedules continue to use the last actual completion.

An outstanding task retains its original opening timestamp and due date. When the new opening rule becomes active, the existing task is associated with that occurrence rather than opening a duplicate. If the new rule is not active yet, the outstanding task remains open until completed or its applicable closing rule is met. An opening-rule edit does not itself automatically complete an outstanding task during that evaluation; subsequent evaluations apply the configured closing rules. A closing-only edit applies its new closing condition without resetting the opening rule.

Home Assistant entity-registry renames are treated as the same source rather than a newly configured trigger. A closed task may open again after a genuine opening-rule change if the new condition is already met.

### Closing conditions

- Manual completion.
- An entity reaches a specified state.
- A numeric entity meets a closing threshold (below, above or equal).
- A matched calendar event ends (calendar tasks only).
- The opening trigger is no longer true (numeric or state-triggered tasks only).
- Manual completion combined with any applicable automatic closing condition.

An open calendar task remains tied to its original occurrence until its configured closing condition is satisfied. The event ending only completes a task configured for calendar-end completion. If another matching event becomes due while a task is still open, the original occurrence is retained; after completion, the scheduler can open the next eligible occurrence. Individual missed occurrences are not queued.

### Calendar example

To track multiple collection types in a single calendar, create separate tasks using the same calendar entity and different **Calendar event match** filters, such as Rubbish Collection, Recycling Collection and Food Recycling.

Matching is case-insensitive and checks whether the event title contains the configured text. Set **Days before event** to 1 to open the task at midnight on the preceding day. Event-end completion uses the actual event end time.

Calendar reminders support offsets from 0 to 365 days. The scheduler searches at least 90 calendar days ahead, extending the search as necessary to cover the configured offset plus 30 days of upcoming reminders. Responses are cached for five minutes and shared across tasks using the same calendar. Each task retains its own search window. Calendar providers must support retrieval across the requested date range; provider-specific limitations may still affect long-offset reminders.

### Calendar provider outages

Calendar responses are normally trusted for five minutes. If the calendar entity or service becomes unavailable, a request fails, or the returned data is malformed, calendar-triggered opening and calendar-end automatic completion pause until a successful refresh. A valid empty calendar response is distinct from an invalid response.

A recent cached response with adequate date coverage may still supply upcoming due dates for display, but is not used for automatic calendar transitions after a failure. Cached responses expire after 30 minutes; calendar entity updates may invalidate them sooner. Outstanding tasks retain their original due date and occurrence history. Manual completion and independent entity-based closing rules remain available.

Failed retrievals are shared across tasks using the same calendar. Retries back off by 1, 2, 4, 8 and then up to 15 minutes. Individual calendar service calls have a 15-second timeout. Successful retrieval clears the warning and resumes normal scheduling. Task status sensors expose calendar failures through their existing `entity_warnings` and `has_entity_warning` attributes.

Tasks that would have opened and ended entirely during an outage are not queued retrospectively. On recovery, the scheduler evaluates the current eligible occurrences. If a previously opened occurrence is absent from a successful response, calendar-end completion retains the existing recorded-end fallback.

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

The integration registers backend actions for completion and early completion. The unfinished skip, snooze and reopen actions have been removed. Previously snoozed outstanding tasks are restored on evaluation after upgrade; existing historical records are retained.

### Task configuration validation

New and edited tasks validate the active rules before saving. Internal names, friendly names and categories are limited to 128 characters; state values to 255 and optional calendar filters to 512. Numeric thresholds must be finite. Schedule numbers must be whole numbers within the displayed limits. Date-entity offsets support -3650 to 3650 days; calendar offsets remain 0 to 365 days. Calendar-end rules require a calendar trigger and trigger-inactive rules require a numeric or state trigger.

Entity IDs must be valid Home Assistant entity IDs, but devices need not be online to configure a task. Optional first due dates, calendar filters and areas can be cleared. Existing stored configurations are not automatically rewritten; if an older value exceeds the new limits, correct it when editing the task.

### Completion action inputs

Both `household_tasks.complete` and `household_tasks.complete_early` accept exactly one non-blank text field: `entry_id` (the exact config entry ID, available as `task_entry_id` on the status sensor) or `task` (the exact internal task name or config entry title). The built-in dashboard uses `entry_id`. For example:

```yaml
action: household_tasks.complete
data:
  task: change_bedding
```

Names are case-sensitive. If a name matches multiple tasks, use the exact `entry_id` instead. Unknown identifiers, both selectors together, non-text or blank values and unexpected fields are rejected. Update any automation that previously relied on ignored extra fields or fallback between selectors.

### Completion action permissions

The custom `household_tasks.complete` and `household_tasks.complete_early` actions respect Home Assistant user permissions. Active administrators can complete eligible tasks; other authenticated users must have control permission on the selected task's registered completion button. A missing or disabled completion button prevents non-administrators from using the custom actions. Renaming a button does not change the identity used for permission checks.

Home Assistant internal calls without a user ID, such as scheduled automations, remain supported. The standard `button.press` action uses Home Assistant's built-in entity permission checks. Task-specific completion rules still apply to both routes. These checks do not restrict visibility of task information; entity visibility and access are managed through Home Assistant.

## Troubleshooting and limitations

- The dashboard resource must be added manually once.
- Weekly recurrence supports one weekday per task.
- Monthly recurrence is configured as every month in the current editor.
- When Home Assistant reports an entity ID rename, the integration attempts to update associated task references automatically.
- Missing or unavailable dependencies are exposed through `entity_warnings` and `has_entity_warning` on task status sensors. Check these attributes and Home Assistant logs if a task is not behaving as expected.
- Calendar retrieval, task editing, sensor recovery and timezone edge cases require further testing.
- Automated Python tests run on GitHub Actions, but passing tests do not establish full runtime compatibility across Home Assistant versions.

## Privacy and data handling

Household Tasks processes task data inside Home Assistant. It has no integration-level telemetry, analytics or external network client. It uses Home Assistant's calendar service to retrieve events; the calendar provider may independently communicate with external services.

| Data | Location and retention |
| --- | --- |
| Task names, categories, areas, entity references and rule settings | Home Assistant config entries, until the entry is removed. |
| Task lifecycle timestamps, occurrence identifiers, rule signatures and completion records | `.storage/household_tasks.history`; up to the latest 100 lifecycle events per task when new events are recorded. Calendar occurrence identifiers include event titles and start times, and rule signatures may include calendar filters or expected entity states. |
| Calendar results | In-memory cache containing event start/end and summary/message only. Descriptions, locations, attendees and other provider metadata are not retained by the integration's cache. Fresh cache is reused for up to five minutes; during failures a cache may be consulted for display for less than 30 minutes. Expired entries are discarded when that calendar is next evaluated; an unused calendar's cache may remain in memory until restart. |
| Status and aggregate sensors | Task names, due dates, opening/completion times, recent history (up to 20 events), warnings and announcement text are exposed as Home Assistant entity states/attributes. |

The history store requests Home Assistant's private-file mode on writes. It is not encrypted by the integration, and this does not protect data from administrators, privileged add-ons or backups. Existing file permissions depend on the platform and when the file is next written.

Task deletion removes the task's integration history and saves the remaining history. Home Assistant Recorder, backups, logs and other automations may retain copies independently. Deleting a task does not erase those copies. Use Home Assistant's own retention and purge controls where appropriate.

Routine background evaluation and warning-publication errors log a task entry identifier without the exception payload. Detailed tracebacks are available with debug logging and may contain private information. Other Home Assistant components and service calls may generate their own logs.

Entity visibility is governed by Home Assistant. The card is not a separate privacy boundary. Automations that send task names or announcements to voice assistants, TVs, notifications or external services may disclose those values outside Home Assistant. Consider using generic task names for sensitive activities, restricting access to backups and logs, and redacting calendar titles, entity IDs and task details before sharing support information.

The integration does not automatically change Recorder settings, purge backups or migrate existing occurrence identifiers. Existing automations and task scheduling identities are preserved.

For bug reports or feature requests, use [GitHub Issues](https://github.com/thomtom24/HA-Household-Tasks/issues). Release-specific changes are available through [GitHub Releases](https://github.com/thomtom24/HA-Household-Tasks/releases) and repository history.

## Development workflow security

GitHub Actions dependencies are pinned to full commit SHAs and Dependabot proposes weekly action updates for review. Workflow repository permissions are read-only, and checkout steps do not persist Git credentials. The required test workflow runs both Python and JavaScript regression tests.

To regenerate branding PNGs, run **Generate Home Assistant brand assets** under GitHub Actions or update the source SVG in a pull request. Download the `household-tasks-brand-assets` artifact (retained for seven days), review the PNGs and include them under `custom_components/household_tasks/brand/` in a normal pull request. The workflow no longer pushes directly to the protected main branch.

## AI-assisted development disclaimer

This project was produced using ChatGPT in response to human prompts and direction. It was not created solely by a human developer. The project owner provides requirements, reviews changes and participates in testing, while ChatGPT generates and assists with implementation, documentation and automated tests.

AI-generated code may contain errors, security issues or unexpected behaviour. The integration is provided as-is, without guarantees of reliability or suitability for any particular purpose. Please review and test it in your own environment before relying on it.

## About and support

- Website: [www.tombale.me](https://www.tombale.me)
- If you find this project useful and would like to support its development, you can [buy me a beer](https://buymeacoffee.com/tombale).

Support is entirely optional.
