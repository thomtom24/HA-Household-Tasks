# Installation and upgrade guide

[Back to README](../README.md) · [Latest stable release](https://github.com/thomtom24/HA-Household-Tasks/releases/latest)

## Before you begin

- Home Assistant 2025.12.0 or later. See [runtime testing](runtime-testing.md) for the tested versions and scope.
- An administrator account for installing integrations and editing dashboard resources.
- [HACS installed and configured](https://www.hacs.xyz/docs/use/) for the recommended installation method.
- A current Home Assistant backup, including configuration and task history if upgrading. Keep the backup encryption key safely available if your backups are encrypted.

Household Tasks is an early-release custom integration. It is not a built-in Home Assistant integration or currently listed in the default HACS catalogue. Test in your environment; do not depend on it alone for essential reminders.

## Install via HACS (recommended)

1. Open **HACS** in the Home Assistant sidebar.
2. Open the three-dot menu and choose **Custom repositories**.
3. Enter the repository URL: `https://github.com/thomtom24/HA-Household-Tasks`.
4. Choose **Integration** as the repository type, then add it.
5. Search HACS for **Household Tasks**, open the repository and download the latest stable release.
6. After installation finishes, restart Home Assistant Core using the restart option in Home Assistant. Do not interrupt an active backup.
7. Return to Home Assistant after it starts.

For help with adding custom repositories, see the [HACS documentation](https://www.hacs.xyz/docs/faq/custom_repositories/). The dashboard card is bundled with the integration: do not install it as a separate HACS dashboard repository.

## Create your first task

1. Open **Settings → Devices & services → Add integration** and search for **Household Tasks**.
2. Complete **Task details** using the following example:

   | Field | Example |
   | --- | --- |
   | Internal name | `change_bedding` |
   | Friendly name | Change bedding |
   | Category | Household |
   | Area (optional) | Bedroom, if it exists in your installation |

3. For **Opening trigger**, select **Time since last completion**.
4. Set **Days after last completion** to 7. Choose today's date as the **First due date (optional)** if you want to try an immediately due task, or choose a future date to see it under Upcoming.
5. For **Closing rule**, select **Manual completion**.
6. Finish the wizard. Home Assistant creates the task status sensor and completion button. Their actual entity IDs can be found in the task's integration entry.

The internal name identifies the task in completion actions; the friendly name is displayed in the card. Each task has its own integration entry. To create another, repeat Add integration. To edit a task, open its existing integration entry's options rather than deleting and recreating it.

## Set up the dashboard

### Register the bundled resource (once)

1. Open **Settings → Dashboards**.
2. Open the three-dot menu and select **Resources**. If the menu is hidden, check that you have administrator access and enable Advanced mode in your profile if needed.
3. Add the resource URL `/household_tasks/household-tasks-card.js`.
4. Select **JavaScript module** and save.
5. Reload the Home Assistant page.

If an existing resource already uses this URL, do not add another. The integration serves the file itself; no copy into `www` is required.

If your installation manages dashboard resources in YAML rather than the UI, add the following resource to the existing `lovelace` configuration instead (merge it with your existing resources; do not replace other dashboards or resources):

```yaml
lovelace:
  resource_mode: yaml
  resources:
    - url: /household_tasks/household-tasks-card.js
      type: module
```

Reload YAML-managed dashboard resources using `lovelace.reload_resources` or restart Home Assistant, then refresh the page. See [Home Assistant's resource registration guide](https://contributors.home-assistant.io/frontend/custom-ui/resources/).

### Add the card

1. Open an editable dashboard and enter edit mode.
2. Choose **Add card** and search for **Household Tasks**.
3. If it does not appear in the card picker, select **Manual** and paste:

   ```yaml
   type: custom:household-tasks-card
   title: Household Tasks
   ```

4. Save the card and exit edit mode.

Tasks are discovered automatically; you do not need to configure entity IDs for each task.

### Set the default time ranges

Edit the card and choose its default Upcoming and History ranges in the visual editor where available. Alternatively:

```yaml
type: custom:household-tasks-card
title: Household Tasks
upcoming_range: "14"
history_range: "30"
```

| Option | Supported values | Default |
| --- | --- | --- |
| `upcoming_range` | `"7"`, `"14"`, `"month"`, `"all"` | `"all"` |
| `history_range` | `"7"`, `"14"`, `"30"`, `"90"`, `"all"` | `"30"` |

The dropdowns in the card change the displayed range temporarily without changing the saved configuration or task schedules. History is limited to the events exposed by each task status sensor (up to 20 per task); “All available” does not retrieve older Recorder data.

## Verify the installation

- Check your new task appears under Open or Upcoming as appropriate to its first due date.
- Complete an open manual task with **Done**, or use **Complete early** on an eligible upcoming task and confirm the prompt.
- Open History and check the new event and your Home Assistant account display name. An older event or an action without a known user will show “No user recorded”.
- Find the aggregate sensors in **Developer tools → States**. Their default IDs are `sensor.household_tasks_open_names`, `sensor.household_tasks_latest_open` and `sensor.household_tasks_errors`. Home Assistant may assign different IDs if a name is occupied or has been renamed.
- Confirm the error sensor is zero when all tasks are healthy; its `summary` and `errors` attributes contain details when there are warnings.

See the [README](../README.md#configuring-tasks) for additional trigger and closing rules and [automation examples](../README.md#sensors-and-automations).

## Updating an existing installation

1. Make a current backup and allow it to finish before restarting. Review the [release notes](https://github.com/thomtom24/HA-Household-Tasks/releases) for changes and any upgrade instructions.
2. Update Household Tasks through HACS to the desired stable release.
3. Restart Home Assistant Core.
4. Refresh the dashboard (including the companion app if used). If the browser still loads an older card, perform a hard reload or clear the affected frontend cache. A version query string on the resource URL, such as `?v=0.8.1`, can force a fresh load; update the existing registration rather than adding another.
5. Confirm tasks, history, entity IDs, completion actions and existing automations are working. Verify the installed version in the integration/HACS UI where shown.

Do not remove and re-add tasks simply to update the integration. Removing a task deletes its associated integration history.

### Upgrading an older dashboard resource

If you originally used `/local/household-tasks-card.js`, remove that resource registration (or update its URL) and use `/household_tasks/household-tasks-card.js` as the JavaScript module. Keep only one Household Tasks card resource. The old copy in `www` is not automatically replaced by HACS; you do not need to edit it. Refresh the frontend.

### Rollback

If an upgrade fails, capture relevant logs and check the release notes first. Where HACS offers an older version, you can download the previous known-good release and restart Home Assistant. A downgrade is not guaranteed to undo stored-data changes; use the pre-upgrade backup if necessary. Restoring a backup may roll back other Home Assistant changes too. Avoid manually editing the history store or deleting task entries to fix an upgrade.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Integration not in Add integration | Confirm HACS download completed and Home Assistant restarted. Check Settings → System → Logs for setup errors; refresh the interface. |
| Card not found or custom element error | Verify the integration has loaded, resource URL and JavaScript module type are correct, no legacy/duplicate resource is registered, and the dashboard is not in safe mode. Refresh the browser. |
| Open or Upcoming is empty | Check task status, first due date, trigger conditions and time range. Closed sensor-triggered tasks may have no predictable due date. |
| A warning appears | Read the named task's warning on the card or the error sensor's `summary` attribute. Check the referenced entity in Developer tools → States, the task's rules and HA logs. Warnings should clear after recovery. |
| Completion button missing | Check whether the task permits manual completion. Early completion is only available for eligible upcoming completion-relative or fixed schedule tasks. |
| Completion rejected | Check task eligibility and the user's permission to control its registered completion button. |
| History or username missing | Check the history time range and retained events. Previous completions and actions without an identifiable HA account cannot be retrospectively attributed. |

For malformed history or storage warnings, follow the [README recovery guidance](../README.md#troubleshooting-and-limitations). Do not edit or delete history files while Home Assistant is running.

## Removing the integration

If you intentionally want to uninstall, back up first. Removing individual task configuration entries deletes their integration history. Remove Household Tasks cards from your dashboards and the associated dashboard resource registration, remove the task entries through **Settings → Devices & services**, then remove the installed repository through HACS and restart Home Assistant. Recorder and backups may retain copies independently; see [privacy and data handling](../README.md#privacy-and-data-handling).

## Getting help

Report ordinary issues on [GitHub](https://github.com/thomtom24/HA-Household-Tasks/issues). Include Home Assistant and Household Tasks versions, installation method, reproduction steps, and relevant logs. Redact sensitive names, identifiers, calendar events and any secrets before posting. Follow the [security policy](../SECURITY.md) for security reports.
