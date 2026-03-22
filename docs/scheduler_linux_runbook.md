# Scheduler Linux Runbook

## Purpose

This runbook is the fastest path to running the standalone ingestion scheduler on a Linux machine with `systemd`.

Use this when you want:
- the scheduler to start automatically at boot
- a single long-running process instead of cron or Airflow
- a quick way to check the Python runtime, credentials, and service logs

## Prerequisites

- Repo checked out locally
- `python3` available on the machine
- `sudo` access for service installation
- `credentials_brainrot.json` present in the repo root, or `GOOGLE_APPLICATION_CREDENTIALS` exported to another path
- `kalshi_private_key.pem` present in the repo root, or `KALSHI_PRIVATE_KEY_PATH` exported to another path

## Recommended Setup

From the repo root:

```bash
make api-setup
make scheduler-smoke
make scheduler-install
```

What each command does:
- `make api-setup`: rebuilds `apps/api/.venv` for the current machine and installs runtime dependencies
- `make scheduler-smoke`: validates the venv interpreter, direct Python imports, scheduler module import, and the expected credential/key files
- `make scheduler-install`: installs and enables a machine-specific `systemd` unit for the scheduler

## Service Management

Check service status:

```bash
sudo systemctl status ever-evolving-scheduler --no-pager
```

Tail live logs:

```bash
journalctl -u ever-evolving-scheduler -f
```

Restart after code or dependency changes:

```bash
sudo systemctl restart ever-evolving-scheduler
```

Stop the service:

```bash
sudo systemctl stop ever-evolving-scheduler
```

## Healthy Log Pattern

Expected startup logs:

```text
=== Scheduler starting ===
Scheduled jobs:
=== Scheduler running. Press Ctrl+C to stop. ===
```

Expected job logs:

```text
>>> Starting kalshi_market_ingest.py
<<< kalshi_market_ingest.py completed in ... (exit 0)
Job kalshi_market_ingest finished successfully.
```

## Common Failures

### `status=203/EXEC`

Meaning:
- `systemd` could not execute the configured interpreter or script

Typical cause in this repo:
- `apps/api/.venv` was copied from another OS and points to a non-existent Python binary

Fix:

```bash
make api-setup
make scheduler-install
```

### `ModuleNotFoundError`

Meaning:
- the current venv does not contain one of the runtime dependencies used by the scheduled scripts

Fix:

```bash
make api-setup
make scheduler-smoke
sudo systemctl restart ever-evolving-scheduler
```

### Credentials or key file missing

Meaning:
- the scheduler service cannot find `GOOGLE_APPLICATION_CREDENTIALS` or `KALSHI_PRIVATE_KEY_PATH`

Fix:
- place the files in the repo root using the default names, or
- export those environment variables before running `make scheduler-install`

Example:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/secure/path/credentials.json
export KALSHI_PRIVATE_KEY_PATH=/secure/path/kalshi_private_key.pem
make scheduler-install
```

The installer will embed those paths into the generated `systemd` unit.
