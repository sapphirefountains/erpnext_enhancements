import unittest
from unittest.mock import MagicMock, AsyncMock, patch, call
import asyncio
from datetime import datetime
from sync_time_kiosk import TimeKioskSync
import httpx

class TestTimeKioskSync(unittest.TestCase):
    def setUp(self):
        # Patch env vars
        self.env_patcher = patch.dict('os.environ', {
            'ERPNEXT_URL': 'http://test.local',
            'API_KEY': 'key',
            'API_SECRET': 'secret'
        })
        self.env_patcher.start()
        self.syncer = TimeKioskSync()

        # Mock the HTTP client
        self.syncer.client = AsyncMock()

    def tearDown(self):
        self.env_patcher.stop()

    def test_aggregate_logs(self):
        logs = [
            {
                "name": "JI-1",
                "employee": "EMP-001",
                "project": "PROJ-A",
                "start_time": "2023-10-27 09:00:00",
                "end_time": "2023-10-27 12:00:00" # 3 hours
            },
            {
                "name": "JI-2",
                "employee": "EMP-001",
                "project": "PROJ-A",
                "start_time": "2023-10-27 13:00:00",
                "end_time": "2023-10-27 15:00:00" # 2 hours
            }
        ]

        aggregated = self.syncer.aggregate_logs(logs)

        key1 = ('EMP-001', 'PROJ-A', '2023-10-27')
        self.assertIn(key1, aggregated)
        self.assertEqual(aggregated[key1]['hours'], 5.0)
        self.assertEqual(aggregated[key1]['source_ids'], ['JI-1', 'JI-2'])
        # Earliest start time should be 09:00:00
        self.assertEqual(aggregated[key1]['start_dt'], datetime(2023, 10, 27, 9, 0, 0))

    async def async_test_sync_flow_success(self):
        data = {
            "employee": "EMP-001",
            "project": "PROJ-A",
            "date": "2023-10-27",
            "hours": 5.0,
            "start_dt": datetime(2023, 10, 27, 9, 0, 0),
            "source_ids": ["JI-1", "JI-2"]
        }

        self.syncer.find_existing_timesheet = AsyncMock(return_value=None)
        self.syncer.create_timesheet = AsyncMock()
        self.syncer.update_source_status = AsyncMock()

        result = await self.syncer.process_single_sync(data)

        self.assertTrue(result)
        self.syncer.create_timesheet.assert_awaited_once_with(data)
        self.syncer.update_source_status.assert_awaited_once_with(["JI-1", "JI-2"], "Synced")

    def test_sync_flow_success(self):
        asyncio.run(self.async_test_sync_flow_success())

    async def async_test_idempotency(self):
        data = {
            "employee": "EMP-001",
            "project": "PROJ-A",
            "date": "2023-10-27",
            "hours": 5.0,
            "start_dt": datetime(2023, 10, 27, 9, 0, 0),
            "source_ids": ["JI-1"]
        }

        # Existing log matches
        existing_doc = {
            "data": {
                "name": "TS-001",
                "time_logs": [
                    {
                        "project": "PROJ-A",
                        "hours": 5.0,
                        "from_time": "2023-10-27 09:00:00",
                        "to_time": "2023-10-27 14:00:00"
                    }
                ]
            }
        }

        # Setup mocks
        self.syncer.client.get.return_value = MagicMock(status_code=200, json=lambda: existing_doc)
        self.syncer.client.put = AsyncMock() # Should NOT be called

        # Call update directly
        await self.syncer.update_timesheet("TS-001", data)

        # Verify PUT was NOT called because it detected duplicate
        self.syncer.client.put.assert_not_called()

    def test_idempotency(self):
        asyncio.run(self.async_test_idempotency())

    async def async_test_retry_logic(self):
        # Mock a function that fails twice then succeeds
        mock_func = AsyncMock(side_effect=[
            httpx.TimeoutException("Timeout"),
            httpx.ConnectError("Connection Error"),
            "Success"
        ])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await self.syncer.retry_request(mock_func)

            self.assertEqual(result, "Success")
            self.assertEqual(mock_func.call_count, 3)
            self.assertEqual(mock_sleep.call_count, 2) # Slept twice

    def test_retry_logic(self):
        asyncio.run(self.async_test_retry_logic())

if __name__ == '__main__':
    unittest.main()
