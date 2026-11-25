# Runtime and compatibility testing

The existing Python and JavaScript regression tests remain unchanged. The separate
Home Assistant runtime compatibility workflow executes a smoke test against
pinned official Home Assistant Container images, currently 2025.12.0 (minimum)
and 2026.11.3 (recent stable). Container tags are version-pinned, not
cryptographic digests. Update the recent-stable matrix entry deliberately.

Run locally with Docker from the repository root:

```sh
docker run --rm --mount "type=bind,source=$PWD,target=/workspace,readonly" \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2025.12.0 \
  /workspace/tests/runtime_smoke.py 2025.12.0
```

The smoke test starts actual Home Assistant Core, creates a task through the real
configuration flow, checks registry identities and aggregate sensors, changes
an entity state, exercises authorised and read-only users through the service
registry, reloads the entry and restarts Core in a new process with the same
disposable configuration. It checks persistence, the standard completion button,
removal of the final task, and early completion after creating a new task.

These are tests of a real Core runtime, not a complete installation or upgrade via Marketplace, HACS or Supervisor.
No credentials or files from a personal Home Assistant installation are needed.
No ports are published. Do not run against a production configuration.

## Manual release checks still required

- Back up a disposable installation running the previous stable release, install
  via Marketplace (2026.11 or later) or HACS (earlier supported versions), upgrade and restart; verify task history and entity IDs survive.
- Perform a fresh installation through each applicable installer, configure the bundled dashboard resource
  and verify it on desktop and mobile browsers.
- Check existing announcements and automations, provider-specific calendars,
  entity rename, optional settings and real service permissions via the UI.
- Test unavailable/recovering calendars and sensors, including date entities.
- Test damaged or unwritable history only in a disposable test environment.
- Record the Home Assistant versions and outcomes in the release PR.

A green matrix is evidence for the tested scenarios only. It does not establish
compatibility with every Home Assistant release or every calendar provider.
