# Installation and upgrade guide

[Back to README](../README.md) · [Latest stable release](https://github.com/thomtom24/HA-Household-Tasks/releases/latest)

## Before you begin

- Home Assistant 2025.12.0 or later. See [runtime testing](runtime-testing.md) for the tested versions and scope.
- An administrator account for installing integrations and editing dashboard resources.
- Home Assistant 2026.11 or later: use the built-in [Marketplace](https://www.home-assistant.io/integrations/marketplace/).
- Home Assistant 2025.12–2026.10: use [HACS installed and configured](https://www.hacs.xyz/docs/use/).
- A current Home Assistant backup, including configuration and task history if upgrading. Keep the backup encryption key safely available if your backups are encrypted.

Household Tasks is an early-release community integration, not a built-in Home Assistant integration. Test in your environment; do not depend on it alone for essential reminders.

## Install Household Tasks

[Open Household Tasks in your Home Assistant](https://my.home-assistant.io/redirect/hacs_repository/?owner=thomtom24&repository=HA-Household-Tasks&category=integration)

The same link works with Marketplace and HACS. Search the catalogue first; adding a custom repository is only necessary if Household Tasks is not listed. Install Household Tasks as an integration, not as a separate dashboard repository: the dashboard card is bundled.

### Marketplace (Home Assistant 2026.11 or later)

Marketplace is included in Home Assistant. You do not need to install HACS on these versions.

1. Open **Settings → Marketplace** as an administrator. If prompted, review the community-content warning before proceeding.
2. Search for **Household Tasks**. If you find it, open the repository and continue to step 4.
3. **Only if the repository is not listed:** select **Add from link**, enter `https://github.com/thomtom24/HA-Household-Tasks` and select **Add**. Connect GitHub if prompted, using the device sign-in instructions shown in Home Assistant. Select **Integration** if a content type is requested. Open the added repository.
4. Select **Install**, review the offered version and confirm installation.
5. If Home Assistant opens the integration setup wizard, continue with [Create your first task](#create-your-first-task). If a restart is required, follow the repair or restart prompt first. Do not interrupt an active backup.

A connected GitHub account is not normally necessary to install or update a catalogue entry, but is required for adding a custom repository and checking its updates. If a repository you added manually later joins the catalogue, Marketplace recognises it automatically; no reinstallation is needed.

For more information, see the [official Marketplace guide](https://www.home-assistant.io/integrations/marketplace/).

### HACS (Home Assistant 2025.12–2026.10)

1. Open **HACS** in the Home Assistant sidebar and search for **Household Tasks**.
2. **Only if the repository is not listed:** open the three-dot menu, select **Custom repositories**, enter `https://github.com/thomtom24/HA-Household-Tasks`, choose **Integration** and add it.
3. Open the Household Tasks repository in HACS and download the latest stable release.
4. After installation finishes, restart Home Assistant Core. Do not interrupt an active backup.
5. Return to Home Assistant after it starts.

For help with custom repositories, see the [HACS documentation](https://www.hacs.xyz/docs/faq/custom_repositories/).

## Create your first task

1. If Marketplace has already opened the integration setup wizard, continue there. Otherwise open **Settings → Devices & services → Add integration** and search for **Household Tasks**.
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

## Moving from HACS to Marketplace

When upgrading Home Assistant to 2026.11, follow the [official HACS migration guidance](https://www.home-assistant.io/integrations/marketplace/#coming-from-hacs). Marketplace takes over installed and custom repositories and update entities. Do not remove your Household Tasks entries or reinstall the integration simply to change installers. Check your tasks, dashboard and automations after the migration.

## Updating an existing installation

1. Make a current backup and allow it to finish before restarting. Review the [release notes](https://github.com/thomtom24/HA-Household-Tasks/releases) for changes and any upgrade instructions.
2. On 2026.11 or later, install the available Marketplace update under **Settings → Updates**. On earlier supported versions, update Household Tasks through HACS.
3. Restart Home Assistant Core.
4. Refresh the dashboard (including the companion app if used). If the browser still loads an older card, perform a hard reload or clear the affected frontend cache. A version query string on the resource URL, such as `?v=0.8.1`, can force a fresh load; update the existing registration rather than adding another.
5. Confirm tasks, history, entity IDs, completion actions and existing automations are working. Verify the installed version in Marketplace or HACS where shown.

Do not remove and re-add tasks simply to update the integration. Removing a task deletes its associated integration history.

### Upgrading an older dashboard resource

If you originally used `/local/household-tasks-card.js`, remove that resource registration (or update its URL) and use `/household_tasks/household-tasks-card.js` as the JavaScript module. Keep only one Household Tasks card resource. The old copy in `www` is not automatically replaced by the integration update; you do not need to edit it. Refresh the frontend.

### Rollback

If an upgrade fails, capture relevant logs and check the release notes first. Restore the pre-upgrade backup when rolling back a failed update, particularly on Marketplace installations. Installing an older release alone does not undo stored-data changes and is not a supported rollback method in Marketplace. Restoring a backup may roll back other Home Assistant changes too. Avoid manually editing the history store or deleting task entries to fix an upgrade.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Integration not in Add integration | Confirm the Marketplace installation or HACS download completed and Home Assistant restarted if required. Check Settings → System → Logs for setup errors; refresh the interface. |
| Card not found or custom element error | Verify the integration has loaded, resource URL and JavaScript module type are correct, no legacy/duplicate resource is registered, and the dashboard is not in safe mode. Refresh the browser. |
| Open or Upcoming is empty | Check task status, first due date, trigger conditions and time range. Closed sensor-triggered tasks may have no predictable due date. |
| A warning appears | Read the named task's warning on the card or the error sensor's `summary` attribute. Check the referenced entity in Developer tools → States, the task's rules and HA logs. Warnings should clear after recovery. |
| Completion button missing | Check whether the task permits manual completion. Early completion is only available for eligible upcoming completion-relative or fixed schedule tasks. |
| Completion rejected | Check task eligibility and the user's permission to control its registered completion button. |
| History or username missing | Check the history time range and retained events. Previous completions and actions without an identifiable HA account cannot be retrospectively attributed. |

For malformed history or storage warnings, follow the [README recovery guidance](../README.md#troubleshooting-and-limitations). Do not edit or delete history files while Home Assistant is running.

## Removing the integration

If you intentionally want to uninstall, back up first. Removing individual task configuration entries deletes their integration history. Remove Household Tasks cards from your dashboards and the associated dashboard resource registration, remove the task entries through **Settings → Devices & services**, then uninstall the repository through Marketplace (2026.11 or later) or remove it through HACS (earlier supported versions) and restart Home Assistant. Recorder and backups may retain copies independently; see [privacy and data handling](../README.md#privacy-and-data-handling).

## Getting help

Report ordinary issues on [GitHub](https://github.com/thomtom24/HA-Household-Tasks/issues). Include Home Assistant and Household Tasks versions, installation method, reproduction steps, and relevant logs. Redact sensitive names, identifiers, calendar events and any secrets before posting. Follow the [security policy](../SECURITY.md) for security reports.
