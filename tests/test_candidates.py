import unittest

from recruit_server.core.errors import APIError
from recruit_server.services.recruiting import Recruiting
from tests.support import client, page, response


class CandidateSummaryTests(unittest.TestCase):
    def test_summary_combines_profile_and_details_and_reuses_cache(self):
        api, session = client([
            response({"CandidateNumber": "C1", "PersonId": 10, "FileUrl": "private"}),
            response(page([{"SchoolName": "Example", "links": []}])),
            response(page([{"EmployerName": "Example"}])),
            response(page([{"SkillName": "Python"}])),
        ])
        for _ in range(2):
            result = Recruiting(api).candidate_summary("C1", retrieve_all=False, limit=10, offset=0)
            self.assertEqual(result["data"]["profile"]["CandidateNumber"], "C1")
            self.assertNotIn("FileUrl", result["data"]["profile"])
            self.assertNotIn("links", result["data"]["education"]["data"][0])
            self.assertEqual(result["data"]["skills"]["data"][0]["SkillName"], "Python")
            self.assertEqual(result["metadata"]["retrieval_status"], "retrieved")
        self.assertEqual(session.request.call_count, 4)
        for call in session.request.call_args_list[1:]:
            self.assertEqual(call.kwargs["params"]["limit"], 10)
            self.assertEqual(call.kwargs["params"]["offset"], 0)

    def test_unavailable_detail_preserves_profile_and_successful_sections(self):
        api, session = client([
            response({"CandidateNumber": "C1", "PersonId": 10}), response(status=403),
            response(page([])), response(page([{"SkillName": "Python"}])),
        ])
        result = Recruiting(api).candidate_summary("C1")
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")
        self.assertEqual(result["metadata"]["failed_details"], ["education"])
        self.assertEqual(result["data"]["education"]["metadata"]["retrieval_status"], "unavailable")
        self.assertEqual(result["data"]["profile"]["CandidateNumber"], "C1")
        self.assertEqual(result["data"]["skills"]["data"][0]["SkillName"], "Python")
        self.assertEqual(session.request.call_count, 4)

    def test_wrong_candidate_identity_stops_before_fetching_details(self):
        api, session = client([response({"CandidateNumber": "OTHER", "PersonId": 10})])
        with self.assertRaises(APIError):
            Recruiting(api).candidate_summary("C1")
        self.assertEqual(session.request.call_count, 1)
