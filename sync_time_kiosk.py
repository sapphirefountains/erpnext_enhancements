import asyncio
import os
import sys
import logging
import json
import httpx
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TimeKioskSync")

class TimeKioskSync:
    def __init__(self):
        # Configuration from Environment Variables
        self.erpnext_url = os.getenv('ERPNEXT_URL', 'http://localhost:8000')
        self.api_key = os.getenv('API_KEY')
        self.api_secret = os.getenv('API_SECRET')

        if not self.api_key or not self.api_secret:
            logger.warning("API_KEY or API_SECRET not set. Authentication will fail unless running against a public endpoint (unlikely).")

        # Concurrency Limiter
        self.semaphore = asyncio.Semaphore(5)

        # HTTP Client
        self.client = httpx.AsyncClient(
            base_url=self.erpnext_url,
            timeout=30.0,
            headers=self.get_headers()
        )

    def get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"token {self.api_key}:{self.api_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    async def fetch_pending_intervals(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetches Job Intervals with status='Completed' and sync_status='Pending'.
        """
        params = {
            "doctype": "Job Interval",
            "fields": '["name", "employee", "project", "start_time", "end_time"]',
            "filters": '[["status", "=", "Completed"], ["sync_status", "=", "Pending"]]',
            "limit_page_length": limit,
            "order_by": "creation asc"
        }

        try:
            logger.info("Fetching pending intervals...")
            response = await self.client.get("/api/resource/Job Interval", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch intervals: {e}")
            return []

    def aggregate_logs(self, logs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Aggregates logs by Employee + Project + Date.
        Returns a dict keyed by the composite key, containing accumulated hours and source IDs.
        """
        aggregated = {}

        for log in logs:
            try:
                employee = log.get("employee")
                project = log.get("project")
                start_time_str = log.get("start_time")
                end_time_str = log.get("end_time")
                name = log.get("name")

                if not (employee and project and start_time_str and end_time_str):
                    logger.warning(f"Skipping incomplete log {name}")
                    continue

                # Parse datetimes
                start_dt = datetime.fromisoformat(start_time_str)
                end_dt = datetime.fromisoformat(end_time_str)

                # Calculate duration in hours
                duration_seconds = (end_dt - start_dt).total_seconds()
                hours = duration_seconds / 3600.0

                # Group Key: (Employee, Project, Date)
                date_key = start_dt.date().isoformat()
                key = (employee, project, date_key)

                if key not in aggregated:
                    aggregated[key] = {
                        "employee": employee,
                        "project": project,
                        "date": date_key,
                        "hours": 0.0,
                        "start_dt": start_dt, # Track earliest start time
                        "source_ids": [],
                    }

                # Update hours
                aggregated[key]["hours"] += hours
                aggregated[key]["source_ids"].append(name)

                # Keep earliest start time for mapping
                if start_dt < aggregated[key]["start_dt"]:
                    aggregated[key]["start_dt"] = start_dt

            except Exception as e:
                logger.error(f"Error processing log {log.get('name')}: {e}")

        return aggregated

    async def sync_batch(self):
        """
        Main orchestration function for a batch.
        """
        intervals = await self.fetch_pending_intervals()
        if not intervals:
            logger.info("No pending intervals found.")
            return

        aggregated_data = self.aggregate_logs(intervals)
        logger.info(f"Aggregated into {len(aggregated_data)} timesheet entries.")

        tasks = []
        for key, data in aggregated_data.items():
            tasks.append(self.process_single_sync(data))

        # Run syncs concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Summary
        success_count = sum(1 for r in results if r is True)
        logger.info(f"Batch completed. Success: {success_count}/{len(results)}")

    async def process_single_sync(self, data: Dict[str, Any]) -> bool:
        """
        Handles the sync for a single aggregated entry (Employee+Project+Date).
        Returns True if successful, False otherwise.
        """
        async with self.semaphore:
            try:
                # 1. Check for existing Draft Timesheet
                timesheet_name = await self.find_existing_timesheet(data["employee"], data["date"])

                if timesheet_name:
                    # Branch A: Update
                    await self.update_timesheet(timesheet_name, data)
                else:
                    # Branch B: Create
                    await self.create_timesheet(data)

                # 2. Update Source Job Intervals
                await self.update_source_status(data["source_ids"], "Synced")
                return True

            except Exception as e:
                logger.error(f"Sync failed for {data['employee']} on {data['date']}: {e}")
                # Update failure count/status
                await self.handle_sync_failure(data["source_ids"])
                return False

    async def find_existing_timesheet(self, employee: str, date: str) -> str:
        """
        Finds a draft timesheet for the employee overlapping the date.
        """
        filters = [
            ["employee", "=", employee],
            ["status", "=", "Draft"],
            ["start_date", "<=", date],
            ["end_date", ">=", date]
        ]
        params = {
            "doctype": "Timesheet",
            "filters": json.dumps(filters),
            "fields": '["name"]'
        }
        response = await self.retry_request(self.client.get, "/api/resource/Timesheet", params=params)
        response.raise_for_status()
        result = response.json()
        if result.get("data"):
            return result["data"][0]["name"]
        return None

    def _get_time_log_entry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Constructs the time_log entry with dynamic from/to times based on aggregated hours.
        """
        start_dt = data["start_dt"]
        hours = data["hours"]
        # Calculate to_time based on from_time + hours
        end_dt = start_dt + timedelta(hours=hours)

        return {
            "project": data["project"],
            "hours": hours,
            "activity_type": "Execution",
            "from_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "to_time": end_dt.strftime("%Y-%m-%d %H:%M:%S")
        }

    async def create_timesheet(self, data: Dict[str, Any]):
        """
        Creates a new Timesheet.
        """
        payload = {
            "employee": data["employee"],
            "start_date": data["date"],
            "end_date": data["date"],
            "time_logs": [self._get_time_log_entry(data)]
        }
        response = await self.retry_request(self.client.post, "/api/resource/Timesheet", json=payload)
        response.raise_for_status()

    async def update_timesheet(self, timesheet_name: str, data: Dict[str, Any]):
        """
        Appends to an existing Timesheet. Enforces basic idempotency.
        """
        # Fetch existing
        get_res = await self.retry_request(self.client.get, f"/api/resource/Timesheet/{timesheet_name}")
        get_res.raise_for_status()
        doc = get_res.json().get("data")

        new_log = self._get_time_log_entry(data)

        # Idempotency Check: Don't add if identical log exists
        existing_logs = doc.get("time_logs", [])
        for log in existing_logs:
            # Compare basic fields (allowing for slight float differences or time format differences if needed)
            # ERPNext time strings might differ slightly, but let's check basic equality for now.
            if (log.get("project") == new_log["project"] and
                abs(log.get("hours", 0) - new_log["hours"]) < 0.01 and
                str(log.get("from_time")) == str(new_log["from_time"])):
                logger.info(f"Skipping duplicate entry for {data['employee']} on {data['date']}")
                return

        if "time_logs" not in doc:
            doc["time_logs"] = []
        doc["time_logs"].append(new_log)

        # Update
        put_res = await self.retry_request(self.client.put, f"/api/resource/Timesheet/{timesheet_name}", json=doc)
        put_res.raise_for_status()

    async def update_source_status(self, ids: List[str], status: str):
        """
        Updates Job Interval status.
        """
        for name in ids:
            try:
                payload = {"sync_status": status}
                await self.retry_request(self.client.put, f"/api/resource/Job Interval/{name}", json=payload)
            except Exception as e:
                logger.error(f"Failed to update source status for {name}: {e}")

    async def handle_sync_failure(self, ids: List[str]):
        """
        Increments retry count and sets to Failed if too many attempts.
        """
        for name in ids:
            try:
                res = await self.retry_request(self.client.get, f"/api/resource/Job Interval/{name}")
                if res.status_code == 200:
                    doc = res.json().get("data")
                    attempts = doc.get("sync_attempts", 0) + 1
                    status = "Failed" if attempts >= 3 else "Pending"

                    payload = {
                        "sync_attempts": attempts,
                        "sync_status": status
                    }
                    await self.retry_request(self.client.put, f"/api/resource/Job Interval/{name}", json=payload)
            except Exception as e:
                logger.error(f"Failed to handle sync failure for {name}: {e}")

    async def retry_request(self, func, *args, **kwargs):
        """
        Executes an async request with exponential backoff for transient errors.
        """
        retries = 3
        base_delay = 1
        for attempt in range(retries):
            try:
                return await func(*args, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                # Only retry on 503 or network issues
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code != 503:
                    raise e

                if attempt == retries - 1:
                    raise e

                delay = base_delay * (2 ** attempt)
                logger.warning(f"Request failed ({e}). Retrying in {delay}s...")
                await asyncio.sleep(delay)
            except Exception as e:
                raise e

    async def close(self):
        await self.client.aclose()

async def main():
    syncer = TimeKioskSync()
    try:
        await syncer.sync_batch()
    finally:
        await syncer.close()

if __name__ == "__main__":
    asyncio.run(main())
