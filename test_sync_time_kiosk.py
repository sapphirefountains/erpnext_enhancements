import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, call, patch

import httpx

from sync_time_kiosk import TimeKioskSync


class FakeResponse:
	def __init__(self, data=None, status_code=200):
		self._data = data if data is not None else {}
		self.status_code = status_code

	def json(self):
		return self._data

	def raise_for_status(self):
		if self.status_code >= 400:
			request = httpx.Request("GET", "http://test.local")
			response = httpx.Response(self.status_code, request=request)
			raise httpx.HTTPStatusError("HTTP error", request=request, response=response)


class TestTimeKioskSync(unittest.IsolatedAsyncioTestCase):
	def setUp(self):
		self.env_patcher = patch.dict(
			os.environ,
			{
				"ERPNEXT_URL": "http://test.local",
				"API_KEY": "key",
				"API_SECRET": "secret",
			},
		)
		self.env_patcher.start()

		self.client = Mock()
		self.client.get = AsyncMock()
		self.client.post = AsyncMock()
		self.client.put = AsyncMock()
		self.client.aclose = AsyncMock()

		self.client_patcher = patch("sync_time_kiosk.httpx.AsyncClient", return_value=self.client)
		self.mock_async_client = self.client_patcher.start()

		self.syncer = TimeKioskSync()

	def tearDown(self):
		self.client_patcher.stop()
		self.env_patcher.stop()

	def test_initializes_http_client_from_environment(self):
		self.assertEqual(self.syncer.erpnext_url, "http://test.local")
		self.assertEqual(
			self.syncer.get_headers(),
			{
				"Authorization": "token key:secret",
				"Content-Type": "application/json",
				"Accept": "application/json",
			},
		)
		self.mock_async_client.assert_called_once_with(
			base_url="http://test.local",
			timeout=30.0,
			headers=self.syncer.get_headers(),
		)

	def test_aggregate_logs_groups_by_employee_project_and_start_date(self):
		logs = [
			{
				"name": "JI-1",
				"employee": "EMP-001",
				"project": "PROJ-A",
				"start_time": "2023-10-27 09:00:00",
				"end_time": "2023-10-27 12:00:00",
			},
			{
				"name": "JI-2",
				"employee": "EMP-001",
				"project": "PROJ-A",
				"start_time": "2023-10-27 08:30:00",
				"end_time": "2023-10-27 10:30:00",
				"total_paused_seconds": 1800,
			},
			{
				"name": "JI-3",
				"employee": "EMP-001",
				"project": "PROJ-B",
				"start_time": "2023-10-27T13:00:00",
				"end_time": "2023-10-27T14:00:00",
			},
			{
				"name": "JI-4",
				"employee": "EMP-002",
				"project": "PROJ-A",
				"start_time": "2023-10-28 09:00:00",
				"end_time": "2023-10-28 10:00:00",
			},
		]

		aggregated = self.syncer.aggregate_logs(logs)

		self.assertEqual(set(aggregated), {
			("EMP-001", "PROJ-A", "2023-10-27"),
			("EMP-001", "PROJ-B", "2023-10-27"),
			("EMP-002", "PROJ-A", "2023-10-28"),
		})
		main_entry = aggregated[("EMP-001", "PROJ-A", "2023-10-27")]
		self.assertEqual(main_entry["hours"], 4.5)
		self.assertEqual(main_entry["source_ids"], ["JI-1", "JI-2"])
		self.assertEqual(main_entry["start_dt"], datetime(2023, 10, 27, 8, 30))

	def test_aggregate_logs_skips_incomplete_or_invalid_rows_and_clamps_negative_duration(self):
		logs = [
			{"name": "MISSING", "employee": "EMP-001", "project": "PROJ-A"},
			{
				"name": "BAD-DATE",
				"employee": "EMP-001",
				"project": "PROJ-A",
				"start_time": "not a date",
				"end_time": "2023-10-27 12:00:00",
			},
			{
				"name": "NEGATIVE",
				"employee": "EMP-001",
				"project": "PROJ-A",
				"start_time": "2023-10-27 09:00:00",
				"end_time": "2023-10-27 10:00:00",
				"total_paused_seconds": 7200,
			},
		]

		aggregated = self.syncer.aggregate_logs(logs)

		self.assertEqual(len(aggregated), 1)
		entry = aggregated[("EMP-001", "PROJ-A", "2023-10-27")]
		self.assertEqual(entry["hours"], 0.0)
		self.assertEqual(entry["source_ids"], ["NEGATIVE"])

	def test_get_time_log_entry_uses_start_time_and_aggregated_hours(self):
		entry = self.syncer._get_time_log_entry(
			{
				"project": "PROJ-A",
				"hours": 2.25,
				"start_dt": datetime(2023, 10, 27, 9, 15),
			}
		)

		self.assertEqual(
			entry,
			{
				"project": "PROJ-A",
				"hours": 2.25,
				"activity_type": "Execution",
				"from_time": "2023-10-27 09:15:00",
				"to_time": "2023-10-27 11:30:00",
			},
		)

	async def test_fetch_pending_intervals_returns_data_and_expected_filters(self):
		self.client.get.return_value = FakeResponse({"data": [{"name": "JI-1"}]})

		result = await self.syncer.fetch_pending_intervals(limit=25)

		self.assertEqual(result, [{"name": "JI-1"}])
		self.client.get.assert_awaited_once()
		path, = self.client.get.call_args.args
		self.assertEqual(path, "/api/resource/Job Interval")
		params = self.client.get.call_args.kwargs["params"]
		self.assertEqual(params["limit_page_length"], 25)
		self.assertIn('"sync_status", "=", "Pending"', params["filters"])

	async def test_fetch_pending_intervals_returns_empty_list_on_http_error(self):
		self.client.get.side_effect = httpx.ConnectError("connection failed")

		result = await self.syncer.fetch_pending_intervals()

		self.assertEqual(result, [])

	async def test_find_existing_timesheet_returns_first_draft_match(self):
		self.client.get.return_value = FakeResponse({"data": [{"name": "TS-001"}, {"name": "TS-002"}]})

		result = await self.syncer.find_existing_timesheet("EMP-001", "2023-10-27")

		self.assertEqual(result, "TS-001")
		params = self.client.get.call_args.kwargs["params"]
		self.assertEqual(params["doctype"], "Timesheet")
		self.assertIn('"employee", "=", "EMP-001"', params["filters"])
		self.assertIn('"status", "=", "Draft"', params["filters"])

	async def test_find_existing_timesheet_returns_none_without_matches(self):
		self.client.get.return_value = FakeResponse({"data": []})

		result = await self.syncer.find_existing_timesheet("EMP-001", "2023-10-27")

		self.assertIsNone(result)

	async def test_create_timesheet_posts_employee_dates_and_time_logs(self):
		self.client.post.return_value = FakeResponse({"data": {"name": "TS-001"}})
		data = {
			"employee": "EMP-001",
			"project": "PROJ-A",
			"date": "2023-10-27",
			"hours": 3.0,
			"start_dt": datetime(2023, 10, 27, 9, 0),
		}

		result = await self.syncer.create_timesheet(data)

		self.assertEqual(result, "TS-001")
		self.client.post.assert_awaited_once()
		self.assertEqual(self.client.post.call_args.args, ("/api/resource/Timesheet",))
		self.assertEqual(
			self.client.post.call_args.kwargs["json"],
			{
				"employee": "EMP-001",
				"start_date": "2023-10-27",
				"end_date": "2023-10-27",
				"time_logs": [
					{
						"project": "PROJ-A",
						"hours": 3.0,
						"activity_type": "Execution",
						"from_time": "2023-10-27 09:00:00",
						"to_time": "2023-10-27 12:00:00",
					}
				],
			},
		)

	async def test_update_timesheet_skips_duplicate_time_log(self):
		self.client.get.return_value = FakeResponse(
			{
				"data": {
					"name": "TS-001",
					"time_logs": [
						{
							"project": "PROJ-A",
							"hours": 5.004,
							"from_time": "2023-10-27 09:00:00",
						}
					],
				}
			}
		)

		await self.syncer.update_timesheet(
			"TS-001",
			{
				"employee": "EMP-001",
				"project": "PROJ-A",
				"date": "2023-10-27",
				"hours": 5.0,
				"start_dt": datetime(2023, 10, 27, 9, 0),
			},
		)

		self.client.put.assert_not_awaited()

	async def test_update_timesheet_appends_new_time_log(self):
		self.client.get.return_value = FakeResponse({"data": {"name": "TS-001", "time_logs": []}})
		self.client.put.return_value = FakeResponse()
		data = {
			"employee": "EMP-001",
			"project": "PROJ-A",
			"date": "2023-10-27",
			"hours": 1.5,
			"start_dt": datetime(2023, 10, 27, 15, 0),
		}

		await self.syncer.update_timesheet("TS-001", data)

		self.client.put.assert_awaited_once()
		self.assertEqual(self.client.put.call_args.args, ("/api/resource/Timesheet/TS-001",))
		updated_doc = self.client.put.call_args.kwargs["json"]
		self.assertEqual(updated_doc["time_logs"][0]["project"], "PROJ-A")
		self.assertEqual(updated_doc["time_logs"][0]["hours"], 1.5)

	async def test_update_timesheet_initializes_missing_time_logs_list(self):
		self.client.get.return_value = FakeResponse({"data": {"name": "TS-001"}})
		self.client.put.return_value = FakeResponse()

		await self.syncer.update_timesheet(
			"TS-001",
			{
				"employee": "EMP-001",
				"project": "PROJ-A",
				"date": "2023-10-27",
				"hours": 1.0,
				"start_dt": datetime(2023, 10, 27, 9, 0),
			},
		)

		self.assertEqual(len(self.client.put.call_args.kwargs["json"]["time_logs"]), 1)

	async def test_fetch_daily_intervals_limits_query_to_requested_day(self):
		self.client.get.return_value = FakeResponse({"data": [{"project": "PROJ-A", "description": "Install"}]})

		result = await self.syncer.fetch_daily_intervals("EMP-001", "2023-10-27")

		self.assertEqual(result, [{"project": "PROJ-A", "description": "Install"}])
		params = self.client.get.call_args.kwargs["params"]
		self.assertIn("2023-10-27 00:00:00", params["filters"])
		self.assertIn("2023-10-27 23:59:59.999999", params["filters"])

	async def test_fetch_daily_intervals_returns_empty_list_on_failure(self):
		self.client.get.return_value = FakeResponse(status_code=500)

		result = await self.syncer.fetch_daily_intervals("EMP-001", "2023-10-27")

		self.assertEqual(result, [])

	async def test_update_daily_note_combines_descriptions_in_start_time_order(self):
		self.syncer.fetch_daily_intervals = AsyncMock(
			return_value=[
				{"project": "PROJ-A", "description": "Pump inspection"},
				{"project": "PROJ-B", "description": ""},
				{"project": "PROJ-C", "description": "Valve replacement"},
			]
		)
		self.client.put.return_value = FakeResponse()

		await self.syncer.update_daily_note("TS-001", "EMP-001", "2023-10-27")

		self.client.put.assert_awaited_once_with(
			"/api/resource/Timesheet/TS-001",
			json={"note": "PROJ-A - Pump inspection\nPROJ-C - Valve replacement"},
		)

	async def test_update_daily_note_does_not_write_when_no_descriptions_exist(self):
		self.syncer.fetch_daily_intervals = AsyncMock(return_value=[{"project": "PROJ-A", "description": None}])

		await self.syncer.update_daily_note("TS-001", "EMP-001", "2023-10-27")

		self.client.put.assert_not_awaited()

	async def test_process_single_sync_creates_timesheet_updates_note_and_sources(self):
		data = {
			"employee": "EMP-001",
			"project": "PROJ-A",
			"date": "2023-10-27",
			"hours": 5.0,
			"start_dt": datetime(2023, 10, 27, 9, 0),
			"source_ids": ["JI-1", "JI-2"],
		}
		self.syncer.find_existing_timesheet = AsyncMock(return_value=None)
		self.syncer.create_timesheet = AsyncMock(return_value="TS-001")
		self.syncer.update_timesheet = AsyncMock()
		self.syncer.update_daily_note = AsyncMock()
		self.syncer.update_source_status = AsyncMock()
		self.syncer.handle_sync_failure = AsyncMock()

		result = await self.syncer.process_single_sync(data)

		self.assertTrue(result)
		self.syncer.create_timesheet.assert_awaited_once_with(data)
		self.syncer.update_timesheet.assert_not_awaited()
		self.syncer.update_daily_note.assert_awaited_once_with("TS-001", "EMP-001", "2023-10-27")
		self.syncer.update_source_status.assert_awaited_once_with(["JI-1", "JI-2"], "Synced")
		self.syncer.handle_sync_failure.assert_not_awaited()

	async def test_process_single_sync_updates_existing_timesheet(self):
		data = {
			"employee": "EMP-001",
			"project": "PROJ-A",
			"date": "2023-10-27",
			"hours": 5.0,
			"start_dt": datetime(2023, 10, 27, 9, 0),
			"source_ids": ["JI-1"],
		}
		self.syncer.find_existing_timesheet = AsyncMock(return_value="TS-001")
		self.syncer.create_timesheet = AsyncMock()
		self.syncer.update_timesheet = AsyncMock()
		self.syncer.update_daily_note = AsyncMock()
		self.syncer.update_source_status = AsyncMock()
		self.syncer.handle_sync_failure = AsyncMock()

		result = await self.syncer.process_single_sync(data)

		self.assertTrue(result)
		self.syncer.update_timesheet.assert_awaited_once_with("TS-001", data)
		self.syncer.create_timesheet.assert_not_awaited()

	async def test_process_single_sync_marks_sources_failed_on_exception(self):
		data = {
			"employee": "EMP-001",
			"project": "PROJ-A",
			"date": "2023-10-27",
			"hours": 5.0,
			"start_dt": datetime(2023, 10, 27, 9, 0),
			"source_ids": ["JI-1"],
		}
		self.syncer.find_existing_timesheet = AsyncMock(side_effect=RuntimeError("boom"))
		self.syncer.update_source_status = AsyncMock()
		self.syncer.handle_sync_failure = AsyncMock()

		result = await self.syncer.process_single_sync(data)

		self.assertFalse(result)
		self.syncer.handle_sync_failure.assert_awaited_once_with(["JI-1"])
		self.syncer.update_source_status.assert_not_awaited()

	async def test_sync_batch_noops_without_pending_intervals(self):
		self.syncer.fetch_pending_intervals = AsyncMock(return_value=[])
		self.syncer.aggregate_logs = Mock()

		await self.syncer.sync_batch()

		self.syncer.aggregate_logs.assert_not_called()

	async def test_sync_batch_processes_each_aggregated_entry(self):
		aggregated = {
			("EMP-001", "PROJ-A", "2023-10-27"): {"employee": "EMP-001"},
			("EMP-002", "PROJ-B", "2023-10-27"): {"employee": "EMP-002"},
		}
		self.syncer.fetch_pending_intervals = AsyncMock(return_value=[{"name": "JI-1"}])
		self.syncer.aggregate_logs = Mock(return_value=aggregated)
		self.syncer.process_single_sync = AsyncMock(side_effect=[True, False])

		await self.syncer.sync_batch()

		self.assertEqual(self.syncer.process_single_sync.await_count, 2)

	async def test_update_source_status_updates_each_source_and_continues_after_failure(self):
		self.client.put.side_effect = [FakeResponse(), RuntimeError("failed"), FakeResponse()]

		await self.syncer.update_source_status(["JI-1", "JI-2", "JI-3"], "Synced")

		self.assertEqual(self.client.put.await_count, 3)
		self.assertEqual(
			self.client.put.call_args_list[0].args,
			("/api/resource/Job Interval/JI-1",),
		)
		self.assertEqual(self.client.put.call_args_list[0].kwargs["json"], {"sync_status": "Synced"})

	async def test_handle_sync_failure_keeps_source_pending_before_third_attempt(self):
		self.client.get.return_value = FakeResponse({"data": {"sync_attempts": 1}})
		self.client.put.return_value = FakeResponse()

		await self.syncer.handle_sync_failure(["JI-1"])

		self.client.put.assert_awaited_once_with(
			"/api/resource/Job Interval/JI-1",
			json={"sync_attempts": 2, "sync_status": "Pending"},
		)

	async def test_handle_sync_failure_marks_source_failed_on_third_attempt(self):
		self.client.get.return_value = FakeResponse({"data": {"sync_attempts": 2}})
		self.client.put.return_value = FakeResponse()

		await self.syncer.handle_sync_failure(["JI-1"])

		self.client.put.assert_awaited_once_with(
			"/api/resource/Job Interval/JI-1",
			json={"sync_attempts": 3, "sync_status": "Failed"},
		)

	async def test_retry_request_retries_transient_network_errors_with_exponential_backoff(self):
		request = AsyncMock(side_effect=[httpx.TimeoutException("timeout"), httpx.ConnectError("connect"), "ok"])

		with patch("sync_time_kiosk.asyncio.sleep", new=AsyncMock()) as sleep:
			result = await self.syncer.retry_request(request, "path", params={"a": "b"})

		self.assertEqual(result, "ok")
		self.assertEqual(request.await_count, 3)
		sleep.assert_has_awaits([call(1), call(2)])

	async def test_retry_request_retries_http_503(self):
		request = httpx.Request("GET", "http://test.local")
		response = httpx.Response(503, request=request)
		http_503 = httpx.HTTPStatusError("service unavailable", request=request, response=response)
		func = AsyncMock(side_effect=[http_503, FakeResponse({"data": []})])

		with patch("sync_time_kiosk.asyncio.sleep", new=AsyncMock()) as sleep:
			result = await self.syncer.retry_request(func)

		self.assertIsInstance(result, FakeResponse)
		sleep.assert_awaited_once_with(1)

	async def test_retry_request_does_not_retry_non_503_http_errors(self):
		request = httpx.Request("GET", "http://test.local")
		response = httpx.Response(404, request=request)
		http_404 = httpx.HTTPStatusError("not found", request=request, response=response)
		func = AsyncMock(side_effect=http_404)

		with self.assertRaises(httpx.HTTPStatusError):
			await self.syncer.retry_request(func)

		self.assertEqual(func.await_count, 1)

	async def test_retry_request_raises_after_final_retry(self):
		func = AsyncMock(side_effect=httpx.ConnectError("no route"))

		with patch("sync_time_kiosk.asyncio.sleep", new=AsyncMock()):
			with self.assertRaises(httpx.ConnectError):
				await self.syncer.retry_request(func)

		self.assertEqual(func.await_count, 3)

	async def test_close_closes_http_client(self):
		await self.syncer.close()

		self.client.aclose.assert_awaited_once()


if __name__ == "__main__":
	unittest.main()
