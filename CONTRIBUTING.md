# Contributing

Contributions are welcome. Please keep captures, credentials, gateway IDs,
cookies, and account data out of issues, commits, and pull requests.

## Development checks

Run these checks before opening a pull request:

    python -m compileall -q custom_components/elco_remocon tests
    python tests/test_api_read.py

Validate all JSON files and test changes against sanitized fixtures. Never
commit HAR exports because they can contain active session cookies and personal
installation data.

## Write support

Every new write operation must be based on an observed request and must include:

- strict range or option validation;
- the previous values required by Remocon-Net;
- an uncached confirmation read;
- a focused regression test;
- a clear rollback path for manual live testing.
