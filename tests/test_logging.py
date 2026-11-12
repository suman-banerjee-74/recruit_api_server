import re
import unittest
from types import SimpleNamespace

from recruit_server.core.observability import request_id
from recruit_server.http.logging import RequestLogging
from tests.support import client, page, response


class RequestLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_logs_redact_inputs_and_correlate_response(self):
        messages = []
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"PRIVATE_RESPONSE"})
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "method": "GET", "path": "/applicants/PRIVATE_ID",
                 "query_string": b"password=SECRET", "headers": [(b"authorization", b"SECRET")]}
        routes = [SimpleNamespace(path="/applicants/{candidate}", path_regex=re.compile(r"/applicants/[^/]+"))]
        with self.assertLogs("recruit_server", level="INFO") as captured:
            await RequestLogging(app, routes)(scope, None, send)
        output = "\n".join(captured.output)
        self.assertIn("request_started", output)
        self.assertIn("route=/applicants/{candidate}", output)
        self.assertIn("status=200", output)
        self.assertIn("elapsed_ms=", output)
        for secret in ("PRIVATE_ID", "PRIVATE_RESPONSE", "SECRET", "password"):
            self.assertNotIn(secret, output)
        correlation = dict(messages[0]["headers"])[b"x-request-id"].decode()
        self.assertEqual(len(correlation), 32)
        self.assertTrue(all(correlation in line for line in captured.output))
        self.assertEqual(request_id.get(), "-")

    async def test_failed_request_is_logged_without_exception_details(self):
        async def app(scope, receive, send):
            raise RuntimeError("PRIVATE_EXCEPTION")
        with self.assertLogs("recruit_server", level="INFO") as captured:
            with self.assertRaises(RuntimeError):
                await RequestLogging(app)({"type": "http", "method": "GET", "path": "/PRIVATE_PATH"}, None, None)
        output = "\n".join(captured.output)
        self.assertIn("outcome=interrupted", output)
        self.assertNotIn("PRIVATE", output)
        self.assertEqual(request_id.get(), "-")


class OracleLoggingTests(unittest.TestCase):
    def test_oracle_status_and_cache_logs_omit_queries_and_response_records(self):
        api, session = client([response(status=429), response(page([{"CandidateNumber": "PRIVATE_RECORD"}]))])
        with self.assertLogs("recruit_server", level="INFO") as captured:
            for _ in range(2):
                api.collection("recruitingCandidates", {"q": "LastName='PRIVATE_FILTER'"})
        output = "\n".join(captured.output)
        self.assertIn("status=429", output)
        self.assertIn("status=200", output)
        self.assertIn("attempt=2", output)
        self.assertIn("cache_hit=True", output)
        self.assertIn("retrieval_status=retrieved", output)
        self.assertNotIn("PRIVATE", output)
        self.assertEqual(session.request.call_count, 2)
