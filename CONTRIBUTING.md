# Contributing

Household Tasks is licensed under MIT. Open an issue for significant changes before proposing implementation. Keep changes focused, preserve entity identities and stored history, and include regression tests.

Use Python 3.13 and Node.js with the built-in test runner. Install the test dependencies with `python -m pip install -r requirements-test.txt`, run `python -m pytest tests` and `node --test tests/frontend/*.test.cjs`. Run `python -m compileall -q custom_components` before submitting. GitHub Actions also validates HACS and hassfest metadata.

Do not include private household data in issues, tests or logs. Describe any migration, behavioural change, compatibility limitation and live testing performed in the pull request. Automated tests are not a substitute for a real Home Assistant upgrade test.

For real Home Assistant Core smoke tests and the manual release checklist, see [runtime and compatibility testing](docs/runtime-testing.md).
