import io
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import requests

from recruit_server.infrastructure.cache import Cache
from recruit_server.core.errors import APIError
from tests.support import BASE, client, page, response, settings


class TransportTests(unittest.TestCase):
    def test_all_pages_preserve_filters_and_advance_by_returned_rows(self):
        api, session = client([response(page([{"id": 1}], True)), response(page([{"id": 2}]))])
        result = api.collection("recruitingJobApplications", {"q": "RequisitionNumber='R1'"}, limit=5)
        self.assertEqual(len(result["data"]), 2)
        self.assertTrue(result["metadata"]["pagination_complete"])
        calls = session.request.call_args_list
        self.assertEqual([call.kwargs["params"]["offset"] for call in calls], [0, 1])
        for call in calls:
            self.assertEqual(call.kwargs["params"]["q"], "RequisitionNumber='R1'")
            self.assertTrue(call.kwargs["verify"])
            self.assertFalse(call.kwargs["allow_redirects"])

    def test_page_mode_stops_and_exposes_next_offset(self):
        api, session = client([response(page([{"id": 1}], True))])
        result = api.collection("recruitingCandidates", retrieve_all=False, offset=3)
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(result["metadata"]["next_offset"], 4)
        self.assertFalse(result["metadata"]["pagination_complete"])

    def test_failed_later_page_retains_rows_and_is_not_cached(self):
        api, session = client([response(page([{"id": 1}], True)), response(status=403),
                               response(page([{"id": 2}]))])
        result = api.collection("recruitingCandidates")
        self.assertEqual(result["data"], [{"id": 1}])
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")
        self.assertEqual(result["metadata"]["next_offset"], 1)
        api.collection("recruitingCandidates")
        self.assertEqual(session.request.call_count, 3)

    def test_next_link_retains_missing_scope_and_rejects_changed_scope(self):
        safe = BASE + "recruitingCandidates?offset=1&limit=200"
        api, session = client([response(page([{"id": 1}], True, links=[{"rel": "next", "href": safe}])),
                               response(page([{"id": 2}]))])
        result = api.collection("recruitingCandidates", {"q": "CandidateType='EXTERNAL'"})
        self.assertTrue(result["metadata"]["pagination_complete"])
        self.assertEqual(session.request.call_args.kwargs["params"]["q"], "CandidateType='EXTERNAL'")
        bad = safe + "&q=changed"
        api, session = client([response(page([{"id": 1}], True, links=[{"rel": "next", "href": bad}]))])
        result = api.collection("recruitingCandidates", {"q": "original"})
        self.assertEqual(result["metadata"]["errors"][0]["code"], "unsafe_pagination")
        self.assertEqual(session.request.call_count, 1)

    def test_url_rejects_cross_origin_traversal_and_userinfo(self):
        api, _ = client([])
        for path in ("https://evil.example/steal", "http://fusion.example/", BASE + "%252e%252e/workers",
                     BASE + "../workers", BASE + "workers#fragment", BASE.replace("fusion.example", "u:p@fusion.example")):
            with self.subTest(path=path), self.assertRaises(APIError):
                api.url(path)

    def test_unsafe_next_link_retains_current_page(self):
        api, session = client([response(page([{"id": 1}], True, links=[{"rel": "next", "href": "https://evil.example/"}]))])
        result = api.collection("recruitingCandidates")
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")
        self.assertEqual(session.request.call_count, 1)

    def test_loop_empty_page_and_page_bounds(self):
        for payloads, config in (
            ([response(page([{"id": 1}], True)), response(page([{"id": 1}], True))], settings()),
            ([response(page([], True))], settings()),
            ([response(page([{"id": 1}], True))], settings(max_pages=1)),
        ):
            api, _ = client(payloads, config=config)
            result = api.collection("recruitingCandidates")
            self.assertTrue(result["metadata"]["errors"])
            self.assertFalse(result["metadata"]["pagination_complete"])

    def test_bounded_read_retry_and_redaction(self):
        api, session = client([requests.Timeout("SECRET employee payload"), response(status=503), response(page([]))])
        self.assertEqual(api.collection("workers")["metadata"]["errors"], [])
        self.assertEqual(session.request.call_count, 3)
        api, session = client([response(status=503)] * 3)
        result = api.collection("workers")
        self.assertEqual(session.request.call_count, 3)
        self.assertNotIn("fusion-secret", str(result))

    def test_post_timeout_not_retried_and_cache_invalidated(self):
        api, session = client([requests.Timeout("sensitive")], config=settings(enable_updates=True))
        api.cache.load("key", lambda: {"old": True})
        with self.assertRaises(APIError) as raised:
            api.move("ORACLE_KEY", {"phaseId": 1, "stateId": 2})
        self.assertEqual(raised.exception.outcome, "unknown")
        self.assertEqual(session.request.call_count, 1)
        self.assertFalse(api.cache.entries)

    def test_binary_size_and_incomplete_content(self):
        for headers in ({"Content-Length": "100"}, {"Content-Length": "5"}):
            api, _ = client([response(raw=b"ab", headers=headers)])
            with self.assertRaises(APIError):
                api.copy_binary("recruitingCandidates/C1/child/attachments/K/enclosure/FileContents", io.BytesIO(), 10)

    def test_redirect_rejected_and_json_bounded(self):
        api, session = client([response(status=302)])
        self.assertEqual(api.collection("workers")["metadata"]["errors"][0]["oracle_status"], 302)
        self.assertEqual(session.request.call_count, 1)
        api, _ = client([response(raw=b"x" * 2048)], config=settings(max_json_bytes=1024))
        self.assertEqual(api.collection("workers")["metadata"]["errors"][0]["code"], "size_limit")

    def test_missing_has_more_retains_valid_rows_with_explicit_error(self):
        api, _ = client([response({"items": [{"id": 1}]})])
        result = api.collection("workers")
        self.assertEqual(result["data"], [{"id": 1}])
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")

    def test_aggregate_result_bound_keeps_whole_rows_and_resume_offset(self):
        api, _ = client([response(page([{"id": 1}, {"id": 2}]))], config=settings(max_result_bytes=12))
        result = api.collection("workers")
        self.assertEqual(result["data"], [{"id": 1}])
        self.assertEqual(result["metadata"]["next_offset"], 1)
        self.assertEqual(result["metadata"]["errors"][0]["code"], "result_limit")

    def test_oracle_429_retry_after_is_bounded(self):
        api, session = client([response(status=429, headers={"Retry-After": "99999"}), response(page([]))],
                               config=settings(max_backoff=2))
        with patch("recruit_server.infrastructure.oracle.time.sleep") as sleep:
            api.collection("workers")
        sleep.assert_called_once_with(2)

    def test_move_5xx_is_not_retried(self):
        api, session = client([response(status=503)], config=settings(enable_updates=True))
        with self.assertRaises(APIError) as raised:
            api.move("APP_KEY", {"phaseId": 1, "stateId": 2})
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(raised.exception.outcome, "unknown")


class CacheTests(unittest.TestCase):
    def test_hits_deepcopy_expiry_and_bounds(self):
        cache = Cache(10, 1, 100)
        with patch("recruit_server.infrastructure.cache.time.monotonic", return_value=1):
            value, hit = cache.load("a", lambda: {"x": []})
            value["x"].append(1)
            cached, hit = cache.load("a", lambda: self.fail("duplicate query"))
            self.assertTrue(hit)
            self.assertEqual(cached, {"x": []})
        with patch("recruit_server.infrastructure.cache.time.monotonic", return_value=12):
            _, hit = cache.load("a", lambda: {"new": True})
            self.assertFalse(hit)
        cache.load("b", lambda: {"value": True})
        self.assertLessEqual(len(cache.entries), 1)
        cache.load("huge", lambda: {"value": "x" * 1000})
        self.assertNotIn("huge", cache.entries)

    def test_inflight_read_cannot_repopulate_after_invalidation(self):
        cache = Cache(60, 5, 1000)
        started, release = threading.Event(), threading.Event()
        def loader():
            started.set()
            release.wait(5)
            return {"old": True}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(cache.load, "read", loader)
            self.assertTrue(started.wait(5))
            cache.clear()
            release.set()
            future.result(5)
        self.assertFalse(cache.entries)

    def test_single_flight(self):
        cache = Cache(60, 5, 1000)
        count = []
        def loader():
            count.append(1)
            return {"value": True}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: cache.load("same", loader), range(10)))
        self.assertEqual(len(count), 1)
        self.assertEqual(sum(hit for _, hit in results), 9)
