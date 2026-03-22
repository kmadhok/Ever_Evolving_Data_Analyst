#!/usr/bin/env python3
"""APScheduler-based scheduler for all ingestion scripts.

Runs each pipeline script as a subprocess on its configured interval.
Replaces Airflow for simple cron-like scheduling.

Usage:
    export GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json
    python scripts/scheduler.py

    # Or with the project venv
    GOOGLE_APPLICATION_CREDENTIALS=./credentials_brainrot.json \
        ./apps/api/.venv/bin/python scripts/scheduler.py

Press Ctrl+C to stop.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _ROOT / "scripts"
_VENV_PYTHON = _ROOT / "apps" / "api" / ".venv" / "bin" / "python"

# Use venv python if available, otherwise fall back to current interpreter
PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scheduler")

# Quiet down apscheduler's internal logging
logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Job definitions
# ---------------------------------------------------------------------------

def _run_script(script_name: str) -> None:
    """Run a Python script as a subprocess and log its result."""
    script_path = _SCRIPTS_DIR / script_name
    if not script_path.exists():
        log.error("Script not found: %s", script_path)
        return

    log.info(">>> Starting %s", script_name)
    start = datetime.now(timezone.utc)

    try:
        result = subprocess.run(
            [PYTHON, str(script_path)],
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout per script
            env={**os.environ},  # inherit environment
        )

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        if result.returncode == 0:
            log.info("<<< %s completed in %.1fs (exit 0)", script_name, elapsed)
        else:
            log.error(
                "<<< %s FAILED in %.1fs (exit %d)\nstderr: %s",
                script_name, elapsed, result.returncode,
                result.stderr[-500:] if result.stderr else "(empty)",
            )
            raise RuntimeError(f"{script_name} exited with status {result.returncode}")

        # Log last few lines of stdout for visibility
        if result.stdout:
            last_lines = result.stdout.strip().split("\n")[-3:]
            for line in last_lines:
                log.info("    [%s] %s", script_name, line)

    except subprocess.TimeoutExpired:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        log.error("<<< %s TIMED OUT after %.1fs", script_name, elapsed)
        raise RuntimeError(f"{script_name} timed out after {elapsed:.1f}s")
    except RuntimeError:
        raise
    except Exception as exc:
        log.error("<<< %s raised exception: %s", script_name, exc)
        raise


def run_kalshi_market_ingest() -> None:
    _run_script("kalshi_market_ingest.py")


def run_kalshi_ws_ingest() -> None:
    _run_script("kalshi_ws_ingest.py")


def run_kalshi_signals() -> None:
    _run_script("kalshi_signals.py")


def run_odds_api_ingest() -> None:
    _run_script("odds_api_ingest.py")


# ---------------------------------------------------------------------------
# Scheduler event listener
# ---------------------------------------------------------------------------

def _job_listener(event) -> None:
    """Log scheduler-level events for monitoring."""
    if event.exception:
        log.error("Job %s failed: %s", event.job_id, event.exception)
    else:
        log.info("Job %s finished successfully.", event.job_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== Scheduler starting ===")
    log.info("Python: %s", PYTHON)
    log.info("Scripts dir: %s", _SCRIPTS_DIR)
    log.info("Working dir: %s", _ROOT)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_listener(_job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    # Kalshi market data: every 5 minutes
    scheduler.add_job(
        run_kalshi_market_ingest,
        "cron",
        minute="*/5",
        id="kalshi_market_ingest",
        name="Kalshi Market Data Ingestion",
        max_instances=1,
        misfire_grace_time=120,
    )

    # Kalshi WebSocket: every 5 minutes (offset by 1 min to avoid overlap)
    scheduler.add_job(
        run_kalshi_ws_ingest,
        "cron",
        minute="1-59/5",
        id="kalshi_ws_ingest",
        name="Kalshi WebSocket Ingestion",
        max_instances=1,
        misfire_grace_time=120,
    )

    # Kalshi signals: every 5 minutes (offset by 3 min, runs after market ingest)
    scheduler.add_job(
        run_kalshi_signals,
        "cron",
        minute="3-59/5",
        id="kalshi_signals",
        name="Kalshi Signals & Reports",
        max_instances=1,
        misfire_grace_time=120,
    )

    # Odds API: hourly at minute 0
    scheduler.add_job(
        run_odds_api_ingest,
        "cron",
        minute=0,
        id="odds_api_ingest",
        name="Odds API Ingestion",
        max_instances=1,
        misfire_grace_time=300,
    )

    log.info("Scheduled jobs:")
    for job in scheduler.get_jobs():
        if job.pending:
            next_run = "(pending until scheduler starts)"
        else:
            next_run = job.next_run_time
        log.info("  - %s (%s): next run %s", job.id, job.name, next_run)

    # Graceful shutdown
    def _shutdown(signum, frame):
        log.info("Received signal %d, shutting down ...", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("=== Scheduler running. Press Ctrl+C to stop. ===")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("=== Scheduler stopped. ===")


if __name__ == "__main__":
    main()
