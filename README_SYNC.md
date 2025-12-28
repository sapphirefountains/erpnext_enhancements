# Time Kiosk Sync Service

This service synchronizes `Job Interval` records from ERPNext (Time Kiosk) to `Timesheet` records using a decoupled asynchronous microservice architecture.

## Overview

The `sync_time_kiosk.py` script:
1.  Fetches `Job Interval` records with status `Completed` and sync_status `Pending`.
2.  Aggregates them by Employee, Project, and Date.
3.  Checks for existing Draft Timesheets in ERPNext.
4.  Creates new Timesheets or appends to existing ones.
5.  Updates the `sync_status` of the source `Job Interval` records to `Synced` upon success.
6.  Handles failures by incrementing `sync_attempts` and setting `sync_status` to `Failed` after 3 retries.

## Prerequisites

*   Python 3.10+
*   `httpx` library

## Installation

```bash
pip install httpx
```

## Configuration

Set the following environment variables:

*   `ERPNEXT_URL`: The base URL of your ERPNext instance (e.g., `https://example.erpnext.com` or `http://localhost:8000`).
*   `API_KEY`: The API Key for a System User.
*   `API_SECRET`: The API Secret for the System User.

## Usage

Run the script as a standalone process or via a cron job/systemd service.

```bash
export ERPNEXT_URL="http://localhost:8000"
export API_KEY="your_api_key"
export API_SECRET="your_api_secret"

python3 sync_time_kiosk.py
```

## Schema Changes

The service relies on the following custom fields in the `Job Interval` DocType:
*   `sync_status` (Select: Pending, Synced, Failed)
*   `sync_attempts` (Int)

These are defined in `erpnext_enhancements/doctype/job_interval/job_interval.json`. Ensure your ERPNext instance is migrated to include these fields.
